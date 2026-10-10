"""
Backend para comparar precios de Steam vs Nintendo eShop Argentina, Epic
Games Store y GOG.

Cómo funciona el lado Nintendo: en vez de pegarle a la API de Nintendo y
mantener un catálogo de NSUIDs a mano, usamos DekuDeals
(dekudeals.com/app/<steam_appid>), que ya tiene el cruce Steam<->Switch
armado y, forzando el locale a Argentina (POST a /locale), nos devuelve
el precio en ARS y el NSUID del juego en una sola consulta. Cero trabajo
manual por juego.

DekuDeals bloquea con Cloudflare el scraping de su listado general de
ofertas (/eshop-sales), así que en vez de eso mantenemos una WATCHLIST
propia (watchlist.json: los juegos que te interesan) y la chequeamos
contra ambas plataformas para ver cuáles están en oferta ahora.

Epic Games Store y GOG salen de CheapShark (cheapshark.com/api), una API
pública gratuita sin key que agrega ~35 tiendas de PC. OJO: a diferencia
de Steam y Nintendo, estas tiendas NO tienen precio en pesos argentinos
en CheapShark — el precio viene en USD.

Xbox/Microsoft Store sale de emerald.xboxservices.com, el mismo backend
que usa xbox.com para su sección "Ofertas en juegos" -- no es una API
pública documentada, así que entra "solo como fila extra" (igual que
Switch): no tiene buscador por nombre, solo un listado paginado de
ofertas que se trae entero una vez al día (ver xbox_deals) y se cruza
por nombre exacto contra lo que ya se encontró en Steam/Epic/GOG.

Endpoints:
  GET  /api/steam/search?q=<texto>
  GET  /api/steam/price/<appid>
  GET  /api/steam/deals                    -> ofertas actuales de Steam (oficial)
  GET  /api/nintendo/check/<steam_appid>    -> ¿existe en Switch? precio ARS + nsuid
  GET  /api/pc/search?q=<texto>             -> busca en Epic + GOG (vía CheapShark, USD)
  GET  /api/compare?q=<texto>               -> arma la comparación completa (4 tiendas)
  GET  /api/watchlist                       -> lista de seguimiento
  POST /api/watchlist   {appid, name}       -> agregar a la lista
  DELETE /api/watchlist/<appid>             -> sacar de la lista
  GET  /api/watchlist/deals                 -> ¿cuáles de tu lista están en oferta?

Requisitos:
  pip install flask flask-cors requests

Correr:
  python app.py
  (levanta en http://localhost:5000)

IMPORTANTE -- este backend NO puede correr como función serverless (Vercel,
Lambda, etc.), solo como proceso persistente (gunicorn/systemd en un
server propio, ver descuentito-backend.service.example): scheduled_cached
y cached() (ver más abajo) guardan todo en variables de módulo (_SCHEDULED,
_CACHE) y actualizan esos datos con hilos de fondo que corren durante
HORAS -- una función serverless arranca un contenedor nuevo (o congela/
recicla el que ya tenía) en cada invocación, así que ese estado y esos
hilos nunca llegan a sobrevivir entre pedidos. El repo tuvo durante un
tiempo un api/index.py pensado para desplegar esto como función de Vercel
(reexportando este mismo `app`) -- SE SACÓ a propósito: con eso puesto, la
home cargaba pocos juegos y se quedaba pegada al azar (según a qué
contenedor, con qué estado, tocara cada pedido), exactamente el reporte
de Nico ("a veces deja de cargar juegos"). El frontend en Vercel apunta
ahora al backend real vía VITE_API_BASE (ver frontend/.env.example).
"""

import base64
import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from html import unescape

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
STEAM_DETAILS_URL = "https://store.steampowered.com/api/appdetails"

DEKUDEALS_LOCALE_URL = "https://www.dekudeals.com/locale"
DEKUDEALS_APP_URL = "https://www.dekudeals.com/app/{appid}"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PriceCompareBot/1.0)"}

# Cache en memoria simple: {key: (timestamp, data)}
_CACHE = {}
CACHE_TTL_SECONDS = 300  # 5 minutos

# Cada cuánto se reconstruye el pool completo de la home (home_candidates,
# el barrido a CheapShark) en segundo plano -- ver scheduled_cached. 1
# hora por default; las ofertas de las tiendas no cambian tan seguido
# como para necesitar menos, y es fácil subirlo todavía más (4 horas, una
# vez al día) si con esto alcanza para quedarse tranquilo con CheapShark.
HOME_CANDIDATES_REFRESH_SECONDS = 60 * 60

# Un lock por cada key de cache (no uno global, para no bloquear pedidos de
# cosas distintas entre sí), para evitar un "cache stampede": sin esto, si
# dos pedidos llegan casi juntos con la cache todavía fría (recién prendió
# el server, o justo venció el TTL), los DOS pasan el "if key in _CACHE" en
# false y los DOS ejecutan fetch_fn() por su cuenta, en paralelo -- y como
# varias de estas fuentes (Steam "specials" en particular, ver el comentario
# en home_candidates) no devuelven siempre el mismo orden/cantidad para el
# mismo pedido, las dos respuestas podían venir DISTINTAS. El front no tiene
# forma de saber cuál es "la buena", así que lo que se termina viendo en
# pantalla es: carga la lista, y un rato después se reemplaza sola por otra
# lista con otro orden -- el bug de "los juegos se mueven solos". Con el
# lock, el segundo pedido espera a que el primero termine de calcular y
# listo, reusa ese mismo resultado en vez de calcular el suyo propio.
_CACHE_LOCKS = {}
_CACHE_LOCKS_GUARD = threading.Lock()


def cached(key, fetch_fn, ttl=None):
    if ttl is None:
        ttl = CACHE_TTL_SECONDS
    now = time.time()
    if key in _CACHE:
        ts, data = _CACHE[key]
        if now - ts < ttl:
            return data

    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.setdefault(key, threading.Lock())

    with lock:
        # Mientras esperábamos el lock, otro hilo puede haber terminado de
        # calcular y guardar esto -- si ya está fresco, lo reusamos en vez
        # de calcularlo de nuevo (y de paso, en vez de arriesgarnos a traer
        # una versión distinta de la misma fuente).
        if key in _CACHE:
            ts, data = _CACHE[key]
            if time.time() - ts < ttl:
                return data
        data = fetch_fn()
        _CACHE[key] = (time.time(), data)
        return data


# Versión "el server lo calcula solo" de cached(): con cached(), un pedido
# ENTRANTE es lo que dispara el recálculo apenas vence el TTL -- así que la
# frecuencia con la que se le pega a una fuente externa depende de cuánto
# tráfico tenga el sitio, no de un intervalo fijo que uno elige. Con 3+
# visitas repartidas en una ventana corta, eso alcanza para varios
# recálculos (cada uno con sus propios pedidos salientes) sin que nadie
# "lo esté pidiendo mucho" -- justo lo que terminó bloqueando a Nico de
# CheapShark. scheduled_cached en cambio SIEMPRE devuelve lo último ya
# calculado (nunca bloquea ni recalcula en el camino de un pedido
# entrante) y dispara el PRÓXIMO cálculo en un hilo de fondo, aparte, apenas
# corresponde según el intervalo -- la cantidad de pedidos salientes queda
# fija de antemano, pase lo que pase con el tráfico del sitio. Mismo
# mecanismo que ya usa steam_global_top_sellers_deals, generalizado para
# reusar en cualquier otra fuente "cara" (ver home_candidates).
_SCHEDULED = {}
_SCHEDULED_GUARD = threading.Lock()


def scheduled_cached(key, fetch_fn, interval_seconds):
    entry = _SCHEDULED.setdefault(key, {"data": None, "ts": 0.0, "refreshing": False})
    stale = time.time() - entry["ts"] > interval_seconds
    if stale and not entry["refreshing"]:
        with _SCHEDULED_GUARD:
            if not entry["refreshing"]:
                entry["refreshing"] = True

                def _run():
                    try:
                        entry["data"] = fetch_fn()
                        entry["ts"] = time.time()
                    finally:
                        entry["refreshing"] = False

                threading.Thread(target=_run, daemon=True).start()
    # None antes de que el primer cálculo en segundo plano termine (recién
    # arrancó el server): quien llama decide qué devolver en ese caso.
    return entry["data"]


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------

def steam_search(query):
    def fetch():
        try:
            r = requests.get(
                STEAM_SEARCH_URL,
                params={"term": query, "l": "spanish", "cc": "ar"},
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            items = r.json().get("items", [])
        except (requests.RequestException, ValueError):
            return []
        return [
            {
                "appid": it["id"],
                "name": it["name"],
                "tiny_image": it.get("tiny_image"),
            }
            for it in items
        ]

    return cached(f"steam_search:{query}", fetch)


def steam_price(appid):
    def fetch():
        try:
            r = requests.get(
                STEAM_DETAILS_URL,
                params={"appids": appid, "cc": "ar", "l": "spanish"},
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            payload = r.json().get(str(appid), {})
        except (requests.RequestException, ValueError):
            return {"available": False}

        if not payload.get("success"):
            return {"available": False}

        data = payload["data"]
        if data.get("is_free"):
            return {"available": True, "is_free": True, "name": data.get("name")}

        overview = data.get("price_overview")
        if not overview:
            return {"available": False, "reason": "No disponible"}

        return {
            "available": True,
            "is_free": False,
            "name": data.get("name"),
            "currency": overview["currency"],
            "initial_price": overview["initial"] / 100,
            "final_price": overview["final"] / 100,
            "discount_percent": overview["discount_percent"],
        }

    return cached(f"steam_price:{appid}", fetch)


def _steam_featured_categories():
    """
    Un solo pedido al endpoint de categorías destacadas de Steam, de
    donde salen TANTO "specials" (ofertas) COMO "top_sellers" (más
    vendidos) -- se cachea una sola vez y de acá se arman las dos listas
    (steam_deals_all y steam_top_sellers), en vez de pedirlo dos veces.
    """

    def fetch():
        try:
            r = requests.get(
                "https://store.steampowered.com/api/featuredcategories",
                params={"cc": "ar", "l": "spanish"},
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return {}

    return cached("steam_featured_categories", fetch)


def _normalize_steam_items(items):
    """
    Misma forma de "item" para TODOS los endpoints de Steam que la usan
    (featuredcategories Y featured, ver abajo) -- Valve reusa esta misma
    estructura en los dos, así que un solo transform alcanza para ambos.
    """
    return [
        {
            "appid": it["id"],
            "name": it["name"],
            "currency": it.get("currency"),
            "initial_price": it.get("original_price", 0) / 100,
            "final_price": it.get("final_price", 0) / 100,
            "discount_percent": it.get("discount_percent", 0),
            "tiny_image": it.get("large_capsule_image") or it.get("header_image"),
        }
        for it in items
        if it.get("id") and it.get("name")
    ]


def _steam_items_from_category(category):
    items = _steam_featured_categories().get(category, {}).get("items", [])
    return _normalize_steam_items(items)


# Se probó traer "Destacados" desde /api/featured (la portada real de
# Steam, "large_capsules" + "featured_win" -- lo que ve cualquiera sin
# loguearse) en vez del ranking de más vendidos. En la práctica no
# devolvió nada utilizable (no se pudo confirmar la forma exacta del JSON
# sin poder probarlo en vivo desde acá) y se volvió a ESTE endpoint, que
# sí es estable: steam_top_sellers() (categoría "top_sellers" de
# featuredcategories, la misma fuente que ya veníamos usando).


def steam_deals_all():
    """
    TODAS las ofertas actuales de Steam (categoría "Specials" de la
    tienda), en ARS, sin recortar -- el recorte para paginar lo hace
    quien la llame.
    """
    return _steam_items_from_category("specials")


def steam_top_sellers():
    """
    Los más vendidos de Steam AHORA (categoría "top_sellers" de la
    misma tienda) -- es el orden real de Valve, no un invento nuestro;
    sirve como alternativa a "más descuento" para ordenar la pantalla
    de inicio. No todos están en oferta (discount_percent puede ser 0).
    """
    return _steam_items_from_category("top_sellers")


STEAM_GLOBAL_CHART_URL = "https://store.steampowered.com/charts/topselling/global"


_PRELOAD_IMG_RE = re.compile(
    r'href="(https://[^"]*?store_item_assets/steam/apps/(\d+)/[^"]*?)"'
)


def _steam_global_chart():
    """
    Un solo pedido a la página de charts (cacheado) del que sacamos DOS
    cosas, para no pedirla dos veces: el ranking de appids en orden (ver
    steam_global_top_sellers_ranked) y una imagen real por appid (el
    mismo <link rel="preload"> ya trae la URL completa del capsule, no
    hace falta adivinar cómo arma Steam esa URL).
    """

    def fetch():
        try:
            r = requests.get(
                STEAM_GLOBAL_CHART_URL, params={"cc": "ar", "l": "spanish"},
                headers=HEADERS, timeout=10,
            )
            r.raise_for_status()
            html = r.text
        except requests.RequestException:
            return {"ranked": [], "images": {}}

        seen = set()
        ranked = []
        images = {}
        for url, appid_str in _PRELOAD_IMG_RE.findall(html):
            appid = int(appid_str)
            if appid not in seen:
                seen.add(appid)
                ranked.append(appid)
            if "capsule_231x87" in url and appid not in images:
                images[appid] = url
        return {"ranked": ranked, "images": images}

    return cached("steam_global_top_sellers", fetch)


def steam_global_top_sellers_ranked():
    """
    El ranking REAL de más vendidos global de Steam (los 100 de
    store.steampowered.com/charts/topselling/global) -- a diferencia de
    steam_top_sellers() (categoría "top_sellers" de featuredcategories),
    que es una lista chica y curada de la portada vieja de la tienda y
    NO es el mismo ranking (confirmado a ojo: juegos como Counter-Strike
    2 o Red Dead Redemption 2, bien arriba en este chart real, ni
    aparecían en la lista chica).

    Esta página no tiene una API JSON pública y documentada -- lo que SÍ
    tiene (confirmado mirando el código fuente real con Nico) es que el
    HTML devuelve, en el mismo orden del ranking, dos <link
    rel="preload"> por juego con su appid en la URL
    (.../store_item_assets/steam/apps/<appid>/.../library_hero.jpg y
    .../capsule_231x87.jpg). Se extraen esos appids en el orden en que
    aparecen (sin repetir) -- se confirmó que coincide con el orden real
    comparándolo contra el pedido que hace la propia página a
    IStoreBrowseService/GetItems (ahí el primer appid de la lista,
    3393110, es justo el primero que aparece acá también).

    Devuelve una lista de appids (int), ya en orden de ranking.
    """
    return _steam_global_chart()["ranked"]


# Se probó una primera versión de esto que pedía, para cada uno de los
# appids del ranking global, su precio real vía appdetails (steam_price())
# con un ThreadPoolExecutor -- ~100 pedidos CASI SIMULTÁNEOS al mismo
# dominio de Steam en cada refresco de caché. Bastó para que Akamai (la
# protección anti-bot de Steam) bloqueara la IP de Nico ("Access Denied" /
# errors.edgesuite.net) -- un bloqueo temporal, no legal, pero real.
#
# Esta versión reemplaza esa: los mismos ~100 pedidos (el ranking global
# completo, no un recorte) pero SECUENCIALES (uno por vez, nunca en
# paralelo) con una pausa random de 1.5 a 3.5 segundos entre cada uno, y
# cacheados 24hs -- se hace como mucho una vez por día, no cada vez que
# vence la cache corta de 5 minutos del resto del sitio. Para que ningún
# pedido de un usuario tenga que esperar esos ~100 pedidos secuenciales
# (varios minutos en total), el refresco corre en un hilo de fondo
# separado: mientras no termina, se sigue devolviendo lo que ya estaba
# cacheado (o una lista vacía, la primera vez que arranca el server).
STEAM_GLOBAL_DEALS_TOP_N = 100
STEAM_GLOBAL_DEALS_TTL = 24 * 60 * 60  # 1 vez por día

_steam_global_deals_cache = {"data": [], "ts": 0.0}
_steam_global_deals_refreshing = False
_steam_global_deals_lock = threading.Lock()


def _refresh_steam_global_deals():
    global _steam_global_deals_refreshing
    try:
        appids = steam_global_top_sellers_ranked()[:STEAM_GLOBAL_DEALS_TOP_N]
        images = _steam_global_chart()["images"]
        results = []
        for i, appid in enumerate(appids):
            price = steam_price(appid)
            if price.get("available") and not price.get("is_free") and price.get(
                "discount_percent"
            ):
                # De paso, acá mismo (NO en route_home) se chequea si este
                # juego está también en Epic/GOG -- ver pc_store_exact_matches
                # más abajo. Esto reemplaza una versión anterior que hacía
                # esta misma consulta a CheapShark por cada candidato, en
                # cada página que pedía CUALQUIER visitante (10 en paralelo,
                # con max_workers=10) -- eso fue lo que terminó baneando a
                # Nico de CheapShark ("me estan baneando todo el rato").
                # Acá, en cambio, es UN pedido más por item, metido en el
                # mismo loop secuencial-con-pausas que ya existe para Steam
                # (se hace una vez por día, no una vez por página vista).
                name = price.get("name")
                pc_stores = pc_store_exact_matches(name, limit=10) if name else []
                results.append(
                    {
                        "appid": appid,
                        "name": name,
                        "tiny_image": images.get(appid),
                        "currency": price["currency"],
                        "initial_price": price["initial_price"],
                        "final_price": price["final_price"],
                        "discount_percent": price["discount_percent"],
                        "pc_stores": pc_stores,
                    }
                )
            if i < len(appids) - 1:
                time.sleep(random.uniform(1.5, 3.5))
        with _steam_global_deals_lock:
            _steam_global_deals_cache["data"] = results
            _steam_global_deals_cache["ts"] = time.time()
    finally:
        _steam_global_deals_refreshing = False


def steam_global_top_sellers_deals():
    """
    Precio real (con descuento activo) de los juegos del ranking global de
    más vendidos -- esto es lo que permite que un juego grande como
    Warhammer 40,000: Space Marine 2 o Forza Horizon 6 aparezca en el pool
    general aunque no esté en "specials" de Steam ni en lo que trae
    CheapShark. Ver el comentario grande arriba de STEAM_GLOBAL_DEALS_TOP_N
    sobre por qué esto es secuencial-con-pausas y se cachea 24hs, no cada
    5 minutos como el resto.
    """
    global _steam_global_deals_refreshing
    stale = time.time() - _steam_global_deals_cache["ts"] > STEAM_GLOBAL_DEALS_TTL
    if stale and not _steam_global_deals_refreshing:
        with _steam_global_deals_lock:
            if not _steam_global_deals_refreshing:
                _steam_global_deals_refreshing = True
                threading.Thread(target=_refresh_steam_global_deals, daemon=True).start()
    return _steam_global_deals_cache["data"]


def steam_deals(limit=30):
    """Compat: las primeras N ofertas (usado por /api/steam/deals)."""
    return steam_deals_all()[:limit]


# ---------------------------------------------------------------------------
# Epic Games Store + GOG (vía CheapShark, precios en USD)
# ---------------------------------------------------------------------------

CHEAPSHARK_STORES_URL = "https://www.cheapshark.com/api/1.0/stores"
CHEAPSHARK_DEALS_URL = "https://www.cheapshark.com/api/1.0/deals"

# Nombres tal cual los devuelve CheapShark en /stores, para ubicar sus IDs
# sin hardcodear números que podrían cambiar.
PC_STORE_NAMES = ["Epic Games Store", "GOG"]


def dolar_rates():
    """
    Cotizaciones actuales de todos los tipos de dólar en Argentina
    (oficial, blue, tarjeta, MEP, CCL, mayorista, cripto), vía
    dolarapi.com -- API pública, gratuita, sin key, mantenida.
    El "tarjeta" es el más realista para esto: ya incluye los impuestos
    de compra en el exterior (PAIS + percepción de Ganancias), que es
    justo lo que un pago en USD con tarjeta argentina termina pagando.
    """

    def fetch():
        try:
            r = requests.get("https://dolarapi.com/v1/dolares", headers=HEADERS, timeout=10)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return []

    return cached("dolar_rates", fetch)


def _usd_ars_rate():
    """
    Un solo número (ARS por USD, dólar tarjeta) para poder convertir
    precios de Steam (ARS) a USD y así compararlos con los de Epic/GOG
    (que CheapShark ya da en USD) en la misma escala -- lo usa
    home_candidates para el criterio de orden "precio". None si no se
    pudo conseguir la cotización (dolarapi caído, etc).
    """
    for d in dolar_rates():
        if d.get("casa") == "tarjeta" and d.get("venta"):
            return d["venta"]
    return None


def cheapshark_store_ids():
    """{'Epic Games Store': '25', 'GOG': '7', ...} -- ids reales, no hardcodeados."""

    def fetch():
        try:
            r = requests.get(CHEAPSHARK_STORES_URL, headers=HEADERS, timeout=10)
            r.raise_for_status()
            stores = r.json()
            return {s["storeName"]: s["storeID"] for s in stores if s.get("isActive")}
        except (requests.RequestException, ValueError):
            return {}

    return cached("cheapshark_stores", fetch)


def pc_store_search(query, limit=10):
    """
    Busca en Epic Games Store y GOG vía CheapShark. Devuelve precios en
    USD (estas tiendas no tienen precio en ARS).
    """
    store_ids = cheapshark_store_ids()
    wanted_ids = [store_ids[name] for name in PC_STORE_NAMES if name in store_ids]
    if not wanted_ids:
        return []

    def fetch():
        try:
            r = requests.get(
                CHEAPSHARK_DEALS_URL,
                params={
                    "title": query,
                    "storeID": ",".join(wanted_ids),
                    "limit": limit,
                    "sortBy": "Title",
                },
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            deals = r.json()
        except (requests.RequestException, ValueError):
            return []

        id_to_name = {v: k for k, v in store_ids.items()}
        return [
            {
                "store": id_to_name.get(d["storeID"], d["storeID"]),
                "name": d["title"],
                "currency": "USD",
                "initial_price": float(d["normalPrice"]),
                "final_price": float(d["salePrice"]),
                "discount_percent": round(float(d["savings"])),
                "deal_url": f"https://www.cheapshark.com/redirect?dealID={d['dealID']}",
                "thumb": d.get("thumb"),
                # Identifica al JUEGO en sí (no a esta oferta puntual) y no
                # cambia aunque deje de estar en oferta -- es lo que permite
                # "seguir" un resultado de Epic/GOG (ver cheapshark_game_deal).
                "game_id": d.get("gameID"),
            }
            for d in deals
        ]

    return cached(f"pc_store_search:{query}:{limit}", fetch)


def pc_store_exact_matches(name, limit=10):
    """
    Mismo pc_store_search, pero descarta lo que NO sea el juego pedido.
    CheapShark busca por título de forma difusa (sortBy=Title, sin exigir
    coincidencia exacta) -- buscar "Bodycam" también trae sus DLCs
    ("Bodycam - Supporter Pack", bundles, etc.), que CheapShark lista como
    juegos aparte con su propio precio. Eso es útil cuando el USUARIO
    busca a mano (quiere ver variantes relacionadas), pero cuando esto se
    usa para cruzar automáticamente "este candidato también está en
    Epic/GOG" (route_home, compare_steam_results), un DLC se mostraba
    como si fuera una oferta más del juego -- falso positivo (reportado
    por Nico: "aparecen listados los DLC"). Acá se compara el nombre
    normalizado (mismo criterio que upsert en home_candidates) y solo se
    devuelve lo que matchea EXACTO.
    """
    target = name.strip().lower()
    return [
        p for p in pc_store_search(name, limit=limit) if p["name"].strip().lower() == target
    ]


# ---------------------------------------------------------------------------
# Xbox / Microsoft Store
# ---------------------------------------------------------------------------
#
# La sección "Ofertas en juegos" de xbox.com (canal DynamicChannel.GameDeals)
# es un listado CURADO Y PAGINADO de lo que está en oferta ahora mismo, no
# un buscador: cada pedido trae ~25 juegos más un cursor opaco (EncodedCT,
# un JSON en base64 con "HasMore"/"SkipCount"/"TotalCount") que hay que
# volver a mandar para pedir la página siguiente. Por eso un juego
# "aparecía por casualidad" al buscarlo en esa página de Microsoft: si no
# estaba en la primera tanda (~25 de ~750 en total), no había forma de
# encontrarlo sin pedir el resto de las páginas (confirmado con Nico
# capturando el pedido real de "cargar más" en DevTools). Acá se pagina
# la lista COMPLETA una vez al día (mismo patrón que Steam/CheapShark:
# secuencial, con pausas, cacheado con scheduled_cached), así que cualquier
# juego en oferta en Xbox termina disponible para cruzar contra
# Steam/Epic/GOG sin depender de en qué posición lo haya puesto Microsoft
# esa semana.
#
# IMPORTANTE (acordado con Nico): a diferencia de Epic/GOG (CheapShark SÍ
# tiene buscador por título), acá no hay forma de buscar un juego puntual
# -- solo este listado de ofertas. Así que Xbox entra "solo como fila
# extra" (igual que Switch): nunca agrega un candidato nuevo al pool,
# solo se le pega como tienda extra a un candidato que YA existe por otra
# fuente (normalmente Steam), cuando el nombre coincide EXACTO (mismo
# criterio que pc_store_exact_matches, ver xbox_exact_matches más abajo).
XBOX_BROWSE_URL = "https://emerald.xboxservices.com/xboxcomfd/browse"
# es-AR, no es-CL (lo que traía la captura original de Nico) -- para que
# listPrice/msrp vengan en ARS, como el resto del sitio. El primer intento
# con es-AR tiraba 400 Bad Request, pero NO era por el mercado: el cuerpo
# real de esa respuesta (confirmado por Nico pegándole directo a la URL)
# es {"MissingHeader":["Header MS-CV is missing"]} -- el mismo 400 le
# pasa aunque pida es-CL sin ese header. "MS-CV" es un correlation vector
# (trazabilidad interna de Microsoft): hace falta que ESTÉ, con el
# formato correcto, pero no que coincida con ningún valor en particular
# -- se genera uno nuevo por pedido (ver _xbox_ms_cv más abajo), como
# hace el navegador.
XBOX_LOCALE = "es-AR"
XBOX_CHANNEL_ID = "DynamicChannel.GameDeals"
XBOX_CHANNEL_KEY = "BROWSE_CHANNELID=DYNAMICCHANNEL.GAMEDEALS_FILTERS="
XBOX_DEALS_TTL = 24 * 60 * 60  # 1 vez por día, mismo criterio que Steam/CheapShark
XBOX_MAX_PAGES = 40  # ~750 juegos / ~25 por página -- de sobra, con margen de ser humano se queda corto antes

XBOX_HEADERS = {
    "accept": "*/*",
    "accept-language": "es-AR,es;q=0.9,en;q=0.7",
    "content-type": "application/json",
    "origin": "https://www.xbox.com",
    "referer": "https://www.xbox.com/",
    "x-ms-api-version": "1.1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}


def _xbox_ms_cv():
    """
    Genera un correlation vector válido para el header MS-CV -- formato
    real de Microsoft: una raíz de 16 bytes en base64 (sin "="), seguida
    de ".0" (el contador de la cadena de pedidos, arranca en 0). El
    servidor exige que el header ESTÉ y tenga esta forma, no que valga
    algo en particular -- cada pedido genera el suyo, como hace el propio
    xbox.com en el navegador.
    """
    root = base64.b64encode(os.urandom(16)).decode("ascii").rstrip("=")
    return f"{root}.0"


def _xbox_fetch_page(encoded_ct=None):
    body = {
        "Filters": "e30=",  # base64("{}") -- sin filtros extra, mismo valor que manda la propia página
        "ReturnFilters": False,
        "ChannelKeyToBeUsedInResponse": XBOX_CHANNEL_KEY,
        "ChannelId": XBOX_CHANNEL_ID,
    }
    if encoded_ct:
        body["EncodedCT"] = encoded_ct
    headers = {**XBOX_HEADERS, "ms-cv": _xbox_ms_cv()}
    r = requests.post(
        XBOX_BROWSE_URL,
        params={"locale": XBOX_LOCALE},
        json=body,
        headers=headers,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# Último error real del barrido de Xbox -- para poder VER por qué
# xbox_deals() está dando 0 (¿pedido rechazado? ¿canal vacío para
# es-AR? ¿JSON distinto al esperado?) en vez de tener que adivinar
# desde acá, donde no se puede probar el pedido real (la red de este
# sandbox tiene bloqueada la salida a xboxservices.com). Ver
# /api/debug/xbox-deals.
_xbox_last_error = {"page": None, "error": None, "status_code": None}
# Cuántos de los xbox_deals() terminaron pegados a un candidato real en el
# ÚLTIMO fetch() de home_candidates (ver el cruce normalizado más abajo) --
# para distinguir "scrapeo mal" (count bajo en /api/debug/xbox-deals) de
# "scrapeo bien pero no cruza con nada" (count alto acá, matched bajo).
_xbox_last_matched = {"matched": None, "total": None, "ts": None}


def _fetch_all_xbox_deals():
    """
    fetch_fn de xbox_deals() (ver scheduled_cached): pagina TODO el canal
    "Ofertas en juegos" de Microsoft Store, de a ~25 por pedido, siguiendo
    el cursor EncodedCT hasta que la propia respuesta deja de traer
    productos nuevos o hasta XBOX_MAX_PAGES (lo que pase primero) --
    siempre secuencial, con pausa entre pedidos, nunca en paralelo, mismo
    criterio que el resto de los barridos "una vez por día" de esta app.
    """
    global _xbox_last_error
    _xbox_last_error = {"page": None, "error": None, "status_code": None}
    results = []
    seen_ids = set()
    encoded_ct = None
    for page_num in range(XBOX_MAX_PAGES):
        try:
            data = _xbox_fetch_page(encoded_ct)
        except requests.RequestException as e:
            resp = getattr(e, "response", None)
            _xbox_last_error = {
                "page": page_num,
                "error": str(e),
                "status_code": getattr(resp, "status_code", None),
                # El cuerpo de la respuesta (si la hubo) -- fue justo acá
                # donde se encontró el header MS-CV faltante, que
                # raise_for_status() por sí solo no mostraba.
                "response_body": (resp.text[:500] if resp is not None else None),
            }
            break
        except ValueError as e:
            _xbox_last_error = {"page": page_num, "error": f"JSON inválido: {e}", "status_code": None}
            break

        channel = (data.get("channels") or {}).get(XBOX_CHANNEL_KEY)
        if not channel:
            _xbox_last_error = {
                "page": page_num,
                "error": f"Sin canal '{XBOX_CHANNEL_KEY}' en la respuesta -- claves recibidas: {list((data.get('channels') or {}).keys())}",
                "status_code": None,
            }
            break

        products = channel.get("products") or []
        summaries = {
            s["productId"]: s
            for s in data.get("productSummaries", [])
            if s.get("productId")
        }

        new_this_page = 0
        for ref in products:
            pid = ref.get("productId")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            new_this_page += 1

            s = summaries.get(pid)
            if not s:
                continue
            prices = (s.get("specificPrices") or {}).get("purchaseable") or []
            if not prices:
                continue
            p = prices[0]
            discount = round(p.get("discountPercentage") or 0)
            if discount <= 0:
                continue

            images = s.get("images") or {}
            thumb = (images.get("boxArt") or images.get("poster") or {}).get("url")

            results.append(
                {
                    "store": "Xbox",
                    "name": (s.get("title") or "").strip(),
                    "currency": p.get("currency", "ARS"),
                    "initial_price": p.get("msrp"),
                    "final_price": p.get("listPrice"),
                    "discount_percent": discount,
                    "deal_url": f"https://www.xbox.com/es-ar/games/store/x/{pid}",
                    "thumb": thumb,
                    "game_id": pid,
                }
            )

        # Sin productos nuevos o sin cursor para la próxima -> se acabó la lista.
        encoded_ct = channel.get("encodedCT")
        if not encoded_ct or new_this_page == 0:
            break
        if page_num < XBOX_MAX_PAGES - 1:
            time.sleep(random.uniform(1.0, 2.0))

    return results


def xbox_deals():
    deals = scheduled_cached(
        "xbox_deals", _fetch_all_xbox_deals, interval_seconds=XBOX_DEALS_TTL
    )
    return deals if deals is not None else []


# Microsoft nombra sus fichas distinto a Steam para el MISMO juego --
# "Forza Horizon 6 Standard Edition" (Xbox) vs "Forza Horizon 6" (Steam),
# "The Witcher 3: Wild Hunt — Remastered" (Xbox) vs "The Witcher 3: Wild
# Hunt" (Steam), etc. Con el cruce por nombre EXACTO de antes, esto nunca
# pegaba -- confirmado con datos reales (xbox_deals() trayendo 663 juegos
# genuinos, pero 0 terminando pegados en la grilla). Acá se recorta el
# sufijo de "edición" MÁS COMÚN (y los símbolos de marca registrada) antes
# de comparar -- a diferencia de pc_store_exact_matches (que evita
# coincidencias de DLCs recortando NADA, solo comparando exacto), acá no
# hay DLCs sueltos en el canal de ofertas de Xbox -- son fichas de
# juegos completos, así que normalizar la edición es seguro.
_XBOX_TRADEMARK_RE = re.compile(r"[®™©]")
_XBOX_EDITION_SUFFIX_RE = re.compile(
    r"\s*[:\-–—]?\s*"
    r"(standard|deluxe|definitive|complete|ultimate|gold|anniversary|enhanced|digital|"
    r"game of the year|goty|remastered|remaster)"
    r"\s*(edition)?\s*$",
    re.IGNORECASE,
)


def _xbox_strip_trademark(name):
    # Como espacio, no como "": "Watch Dogs®2" sin esto queda "watch
    # dogs2" (no calza con el "Watch Dogs 2" de Steam); con espacio
    # queda "watch dogs 2" y el \s+ de abajo limpia los dobles espacios
    # para los casos donde el símbolo SÍ tenía espacio alrededor.
    return _XBOX_TRADEMARK_RE.sub(" ", name)


def _xbox_name_key(name):
    """Clave base (sin tocar ediciones) -- mismo criterio que el resto de la app: strip().lower()."""
    base = _xbox_strip_trademark(name).strip()
    base = re.sub(r"\s+", " ", base)
    return base.lower()


def _xbox_name_variants(name):
    """
    Devuelve el/los nombres normalizados a probar contra las claves de
    candidates (name.strip().lower()) -- el nombre tal cual (sin
    trademark) y, si tenía un sufijo de edición reconocible, también sin
    ese sufijo. Puede devolver el mismo valor una sola vez si no había
    sufijo que sacar.
    """
    base_key = _xbox_name_key(name)
    variants = {base_key}
    stripped = _XBOX_EDITION_SUFFIX_RE.sub("", _xbox_strip_trademark(name).strip())
    stripped = re.sub(r"\s+", " ", stripped).strip().lower()
    if stripped:
        variants.add(stripped)
    return variants


_xbox_variant_index_cache = {"ts": None, "index": {}}


def _xbox_variant_index():
    """
    Índice variante-normalizada -> [deals de Xbox con esa variante],
    reconstruido SOLO cuando xbox_deals() realmente cambió (su "ts" en
    _SCHEDULED se actualiza una vez por día) -- en vez de recalcular
    _xbox_name_variants() para los ~660 juegos de Xbox en CADA pedido.

    Esto es lo que hacía que "las búsquedas estén muy lentas" (reportado
    por Nico): xbox_exact_matches recorría xbox_deals() entero (663
    juegos, con dos regex cada uno) por CADA resultado de Steam de CADA
    búsqueda -- con 14 resultados como "the witcher", eso es ~9000
    cálculos de variantes repetidos en cada pedido a /api/compare, todos
    sobre el MISMO listado de 663 que no cambió. Con el índice, cada
    búsqueda hace lookups de diccionario (O(1)) en vez de recorrer todo.
    """
    entry = _SCHEDULED.get("xbox_deals")
    ts = entry["ts"] if entry else None
    if _xbox_variant_index_cache["ts"] != ts:
        index = {}
        for d in xbox_deals():
            for variant in _xbox_name_variants(d["name"]):
                index.setdefault(variant, []).append(d)
        _xbox_variant_index_cache["ts"] = ts
        _xbox_variant_index_cache["index"] = index
    return _xbox_variant_index_cache["index"]


def xbox_exact_matches(name):
    """Mismo criterio normalizado que el merge de home_candidates, para /api/compare (búsqueda manual)."""
    index = _xbox_variant_index()
    seen_ids = set()
    results = []
    for variant in _xbox_name_variants(name):
        for d in index.get(variant, ()):
            if d["game_id"] not in seen_ids:
                seen_ids.add(d["game_id"])
                results.append(d)
    return results


def _merge_xbox_into(candidates_list):
    """
    Pega la fila extra de Xbox sobre el pool de candidatos YA RESUELTO
    (steam/epic/gog, después de pasar por sort/filtro) -- a propósito
    FUERA de home_candidates/fetch() y de su caché de 1 hora
    (scheduled_cached), para no quedar pegado al snapshot de Xbox que
    había en el momento exacto de ESE cálculo.

    Por qué hace falta esto (confirmado con datos reales, no adivinado):
    con el cruce adentro de fetch(), la primera vez que el proceso
    arranca, home_candidates y xbox_deals disparan sus primeros cálculos
    en paralelo (_warm_home_candidates_cache) -- xbox_deals tarda 1-2 min
    en paginar las ~30 páginas del catálogo completo, bastante más que
    home_candidates. Si el fetch() de home termina primero, agarra
    xbox_deals() todavía vacío (recién arrancó, scheduled_cached devuelve
    None -> [] mientras no haya un primer cálculo), y ESE resultado
    completo (con cero cruces) queda cacheado un hora entera -- no hay
    forma de que se autocorrija sola, ni reiniciando (reiniciar reinicia
    TAMBIÉN xbox_deals, repitiendo la misma carrera). Nico lo confirmó
    así: /api/debug/xbox-deals mostraba count 663 pero
    matched_pool_total_xbox en 0, muchos minutos después.

    Al mergearse ACÁ (cada pedido a home_candidates, no solo 1 vez por
    hora) contra la versión MÁS RECIENTE de xbox_deals(), esto se
    autocorrige solo apenas termina el primer barrido de Xbox, sin
    esperar el próximo refresco de home_candidates ni pedir un
    force-refresh manual. El costo es bajo: son operaciones en memoria
    (sin red) sobre un pool ya filtrado/paginado, no sobre los miles de
    candidatos crudos. Además, como los candidatos son los MISMOS objetos
    mientras no venza el caché de 1 hora, un candidato que ya tiene
    "xbox" pegado se salta (no recalcula nada de nuevo para él).

    Además de pegar el precio sobre candidatos YA EXISTENTES (que llegaron
    por Steam/Epic/GOG), agrega como candidatos NUEVOS los juegos que
    están en oferta en Xbox pero no en ninguna de esas otras tres fuentes
    -- antes esos quedaban afuera del pool por completo: filtrar "solo
    Xbox" en el sidebar mostraba nada más que la intersección con las
    otras tiendas (Nico: "si Xbox devuelve todos los juegos de una,
    deberían aparecer más al filtrar"). Van sin steam_appid (no hay forma
    de saberlo solo con el nombre) -- route_home ya sabe saltear el
    chequeo de Switch cuando no hay appid, y "seguir" en esas tarjetas usa
    el mismo mecanismo que ya existe para Epic/GOG sin Steam (kind "pc",
    por game_id de Xbox). Es idempotente: una vez agregado, el candidato
    nuevo YA tiene "xbox" pegado, así que la próxima vuelta lo cuenta como
    "ya tiene game_id" y no lo vuelve a sumar.
    """
    index = _xbox_variant_index()
    matched_now = 0
    existing_game_ids = set()
    for c in candidates_list:
        if "xbox" in c:
            existing_game_ids.add(c["xbox"]["game_id"])
            continue
        for variant in _xbox_name_variants(c["name"]):
            hits = index.get(variant)
            if hits:
                c["xbox"] = hits[0]
                existing_game_ids.add(hits[0]["game_id"])
                matched_now += 1
                break

    for d in xbox_deals():
        if d["game_id"] in existing_game_ids:
            continue
        existing_game_ids.add(d["game_id"])
        candidates_list.append(
            {
                "name": d["name"],
                "tiny_image": d.get("thumb"),
                "steam_appid": None,
                "xbox": d,
            }
        )

    _xbox_last_matched["matched"] = sum(1 for c in candidates_list if "xbox" in c)
    _xbox_last_matched["total"] = len(xbox_deals())
    _xbox_last_matched["ts"] = time.time()
    return matched_now


# ---------------------------------------------------------------------------
# PlayStation Store
# ---------------------------------------------------------------------------
#
# La categoría "Todas las ofertas" (id fijo, PS_DEALS_CATEGORY_ID) de
# store.playstation.com es un GraphQL persisted query (categoryGridRetrieve)
# paginado por offset/size -- a diferencia de Xbox (que pagina con un
# cursor opaco), acá alcanza con pedir offset=0,24,48... hasta que la
# propia respuesta diga "isLast": true. Mismo patrón que el resto de los
# barridos "una vez por día" de esta app: secuencial, con pausa entre
# pedidos, cacheado con scheduled_cached. Capturado por Nico desde
# DevTools mientras navegaba esa categoría en la web real.
#
# El endpoint usa Automatic Persisted Queries (APQ): en vez de mandar la
# query GraphQL completa, se manda solo su hash SHA-256
# (PS_DEALS_QUERY_HASH) -- el server ya la tiene cacheada (es la misma
# query que usa la propia página), así que no hace falta reconstruirla acá.
#
# El server también exige el header "apollo-require-preflight" (chequeo
# anti-CSRF de Apollo Server para pedidos GET, documentado por Apollo) --
# sin este header devuelve 400 con un error de CSRF, aunque el
# "content-type: application/json" esté bien puesto (confirmado con Nico:
# el curl sin este header fallaba con ese error exacto).
#
# Mismo criterio que Xbox: PlayStation no tiene un buscador propio desde
# acá (esta query es solo el listado curado de ofertas), así que entra
# "solo como fila extra" -- se cruza por nombre contra el pool, nunca se
# busca en vivo (ver ps_exact_matches/_merge_ps_into más abajo).
PS_GRAPHQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
PS_DEALS_CATEGORY_ID = "3f772501-f6f8-49b7-abac-874a88ca4897"  # "cat.gma.AllDeals"
PS_DEALS_QUERY_HASH = "88c0b9a1273c6d320c51cd73e390924e21ae28bf09f01cde8b84b1034b16cd03"
PS_DEALS_TTL = 24 * 60 * 60  # 1 vez por día, mismo criterio que Steam/CheapShark/Xbox
PS_PAGE_SIZE = 24
PS_MAX_PAGES = 250  # ~5000 juegos / 24 por página (totalCount real visto) -- de sobra, con margen

PS_HEADERS = {
    "accept": "application/json",
    "accept-language": "es-AR,es;q=0.9,en;q=0.7",
    "apollographql-client-name": "@sie-ppr-web-store/app",
    "apollographql-client-version": "0.114.0",
    "content-type": "application/json",
    "apollo-require-preflight": "true",
    "origin": "https://store.playstation.com",
    "referer": "https://store.playstation.com/",
    "x-psn-store-locale-override": "es-AR",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
}


def _ps_fetch_page(offset):
    variables = {
        "id": PS_DEALS_CATEGORY_ID,
        "pageArgs": {"size": PS_PAGE_SIZE, "offset": offset},
        "sortBy": None,
        "filterBy": [],
        "facetOptions": [],
    }
    extensions = {"persistedQuery": {"version": 1, "sha256Hash": PS_DEALS_QUERY_HASH}}
    r = requests.get(
        PS_GRAPHQL_URL,
        params={
            "operationName": "categoryGridRetrieve",
            "variables": json.dumps(variables, separators=(",", ":")),
            "extensions": json.dumps(extensions, separators=(",", ":")),
        },
        headers=PS_HEADERS,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# Mismo propósito que _xbox_last_error -- poder ver desde /api/debug/
# playstation-deals por qué el barrido cortó donde cortó, sin tener que
# leer logs del server.
_ps_last_error = {"offset": None, "error": None, "status_code": None}
_ps_last_matched = {"matched": None, "total": None, "ts": None}


def _ps_parse_price(text):
    """'US$48.99' -> 48.99, 'US$1,234.56' -> 1234.56. None si no hay texto (ej. GRATIS)."""
    if not text:
        return None
    digits = re.sub(r"[^\d.]", "", text.replace(",", ""))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _ps_parse_discount(text):
    """'-30 %' -> 30. None si no hay texto (sin descuento activo)."""
    if not text:
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _fetch_all_playstation_deals():
    """
    fetch_fn de playstation_deals() (ver scheduled_cached): pagina TODA la
    categoría "Todas las ofertas" de la PlayStation Store, de a
    PS_PAGE_SIZE por pedido, siguiendo offset/pageInfo.isLast -- siempre
    secuencial, con pausa entre pedidos, nunca en paralelo, mismo criterio
    que el resto de los barridos "una vez por día" de esta app.
    """
    global _ps_last_error
    _ps_last_error = {"offset": None, "error": None, "status_code": None}
    results = []
    offset = 0
    for page_num in range(PS_MAX_PAGES):
        try:
            data = _ps_fetch_page(offset)
        except requests.RequestException as e:
            resp = getattr(e, "response", None)
            _ps_last_error = {
                "offset": offset,
                "error": str(e),
                "status_code": getattr(resp, "status_code", None),
                "response_body": (resp.text[:500] if resp is not None else None),
            }
            break
        except ValueError as e:
            _ps_last_error = {"offset": offset, "error": f"JSON inválido: {e}", "status_code": None}
            break

        if data.get("errors"):
            _ps_last_error = {"offset": offset, "error": str(data["errors"])[:500], "status_code": None}
            break

        grid = (data.get("data") or {}).get("categoryGridRetrieve")
        if not grid:
            _ps_last_error = {
                "offset": offset,
                "error": f"Sin categoryGridRetrieve en la respuesta -- claves recibidas: {list(data.keys())}",
                "status_code": None,
            }
            break

        for p in grid.get("products") or []:
            price = p.get("price") or {}
            # Se descartan los sin descuento activo (precio de lista
            # completo) y los gratis -- igual que Xbox, esto es un listado
            # de "ofertas", no el catálogo entero.
            discount = _ps_parse_discount(price.get("discountText"))
            if not discount or price.get("isFree"):
                continue

            thumb = None
            for m in p.get("media") or []:
                if m.get("type") == "IMAGE" and m.get("role") in ("MASTER", "GAMEHUB_COVER_ART"):
                    thumb = m.get("url")
                    break

            pid = p.get("id")
            if not pid:
                continue

            results.append(
                {
                    "store": "PlayStation",
                    "name": (p.get("name") or "").strip(),
                    # Siempre viene en USD (confirmado: a pesar de pedir
                    # es-AR en el locale, la store factura en dólares) --
                    # se convierte a ARS con el mismo conversor que ya usa
                    # el resto del sitio para Epic/GOG.
                    "currency": "USD",
                    "initial_price": _ps_parse_price(price.get("basePrice")),
                    "final_price": _ps_parse_price(price.get("discountedPrice")),
                    "discount_percent": discount,
                    "deal_url": f"https://store.playstation.com/es-ar/product/{pid}",
                    "thumb": thumb,
                    "game_id": pid,
                }
            )

        page_info = grid.get("pageInfo") or {}
        if page_info.get("isLast", True):
            break
        offset += PS_PAGE_SIZE
        if page_num < PS_MAX_PAGES - 1:
            time.sleep(random.uniform(1.0, 2.0))

    return results


def playstation_deals():
    deals = scheduled_cached(
        "playstation_deals", _fetch_all_playstation_deals, interval_seconds=PS_DEALS_TTL
    )
    return deals if deals is not None else []


# Mismo problema que Xbox (ver el comentario grande junto a
# _XBOX_TRADEMARK_RE): PlayStation nombra sus fichas distinto a Steam para
# el mismo juego ("Marvel's Spider-Man: Game of the Year Edition" vs
# "Marvel's Spider-Man"), así que se recorta el sufijo de edición antes de
# comparar. Se reutiliza la MISMA regex de sufijos que Xbox (son genéricos
# de la industria, no específicos de una tienda) pero con su propio
# símbolo de marca registrada por separado, para no atar un cambio futuro
# en uno a el otro por accidente.
_PS_TRADEMARK_RE = re.compile(r"[®™©]")


def _ps_strip_trademark(name):
    return _PS_TRADEMARK_RE.sub(" ", name)


def _ps_name_key(name):
    base = _ps_strip_trademark(name).strip()
    base = re.sub(r"\s+", " ", base)
    return base.lower()


def _ps_name_variants(name):
    base_key = _ps_name_key(name)
    variants = {base_key}
    stripped = _XBOX_EDITION_SUFFIX_RE.sub("", _ps_strip_trademark(name).strip())
    stripped = re.sub(r"\s+", " ", stripped).strip().lower()
    if stripped:
        variants.add(stripped)
    return variants


_ps_variant_index_cache = {"ts": None, "index": {}}


def _ps_variant_index():
    """Mismo propósito que _xbox_variant_index: evita recalcular variantes en cada pedido."""
    entry = _SCHEDULED.get("playstation_deals")
    ts = entry["ts"] if entry else None
    if _ps_variant_index_cache["ts"] != ts:
        index = {}
        for d in playstation_deals():
            for variant in _ps_name_variants(d["name"]):
                index.setdefault(variant, []).append(d)
        _ps_variant_index_cache["ts"] = ts
        _ps_variant_index_cache["index"] = index
    return _ps_variant_index_cache["index"]


def ps_exact_matches(name):
    """Mismo criterio normalizado que el merge de home_candidates, para /api/compare (búsqueda manual)."""
    index = _ps_variant_index()
    seen_ids = set()
    results = []
    for variant in _ps_name_variants(name):
        for d in index.get(variant, ()):
            if d["game_id"] not in seen_ids:
                seen_ids.add(d["game_id"])
                results.append(d)
    return results


def _merge_ps_into(candidates_list):
    """
    Mismo mecanismo que _merge_xbox_into (ver el comentario grande ahí
    para el porqué de cada decisión: por qué FUERA del caché de 1 hora,
    por qué agrega candidatos nuevos sin steam_appid para lo que no
    matchea con nada) -- acá solo el equivalente para PlayStation.
    """
    index = _ps_variant_index()
    matched_now = 0
    existing_game_ids = set()
    for c in candidates_list:
        if "playstation" in c:
            existing_game_ids.add(c["playstation"]["game_id"])
            continue
        for variant in _ps_name_variants(c["name"]):
            hits = index.get(variant)
            if hits:
                c["playstation"] = hits[0]
                existing_game_ids.add(hits[0]["game_id"])
                matched_now += 1
                break

    for d in playstation_deals():
        if d["game_id"] in existing_game_ids:
            continue
        existing_game_ids.add(d["game_id"])
        candidates_list.append(
            {
                "name": d["name"],
                "tiny_image": d.get("thumb"),
                "steam_appid": None,
                "playstation": d,
            }
        )

    _ps_last_matched["matched"] = sum(1 for c in candidates_list if "playstation" in c)
    _ps_last_matched["total"] = len(playstation_deals())
    _ps_last_matched["ts"] = time.time()
    return matched_now


CHEAPSHARK_PAGE_SIZE = 60  # tope real de la API de CheapShark -- pedir más por página no funciona, hay que paginar.


def cheapshark_browse_deals(store_name, limit=240):
    """
    Trae las mejores ofertas ACTUALES de una tienda (sin buscar nada
    puntual), ordenadas por descuento. CheapShark además devuelve
    'steamAppID' en cada oferta cuando ese juego también existe en
    Steam -- lo usamos para poder cruzarlo con Nintendo (que solo se
    puede consultar por ese ID) aunque la oferta en sí sea de Epic/GOG.

    CheapShark pagina de a 60 como máximo por pedido (pageSize más alto
    lo recorta solo), así que para traer más se piden VARIAS páginas
    seguidas con pageNumber=0,1,2... y se concatenan -- antes se pedía
    una sola página de 60 y ahí se cortaba, aunque hubiera muchas más
    ofertas reales disponibles. Esto era justo lo que hacía que "Ofertas
    para jugar más" se quedara corta en ~60 resultados y dejara de crecer
    con el scroll infinito en vez de seguir cargando páginas nuevas.
    """
    store_ids = cheapshark_store_ids()
    if store_name not in store_ids:
        return []
    store_id = store_ids[store_name]

    def fetch_page(page_number):
        try:
            r = requests.get(
                CHEAPSHARK_DEALS_URL,
                params={
                    "storeID": store_id,
                    "sortBy": "Savings",
                    "pageSize": CHEAPSHARK_PAGE_SIZE,
                    "pageNumber": page_number,
                    "onSale": 1,
                },
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            deals = r.json()
        except (requests.RequestException, ValueError):
            return []

        if not isinstance(deals, list):
            return []

        return [
            {
                "name": d["title"],
                "store": store_name,
                "currency": "USD",
                "initial_price": float(d["normalPrice"]),
                "final_price": float(d["salePrice"]),
                "discount_percent": round(float(d["savings"])),
                "deal_url": f"https://www.cheapshark.com/redirect?dealID={d['dealID']}",
                "thumb": d.get("thumb"),
                "steam_appid": int(d["steamAppID"]) if d.get("steamAppID") else None,
                "game_id": d.get("gameID"),
            }
            for d in deals
            if d.get("title") and d.get("dealID")
        ]

    pages_needed = -(-limit // CHEAPSHARK_PAGE_SIZE)  # redondeo para arriba

    CHEAPSHARK_PAGE_TTL = 30 * 60  # 30 minutos, no los 5 de siempre -- ver abajo

    def cached_page(page_number):
        return cached(
            f"cheapshark_browse:{store_name}:{page_number}",
            lambda: fetch_page(page_number),
            ttl=CHEAPSHARK_PAGE_TTL,
        )

    # Antes esto pedía TODAS las páginas en paralelo (ThreadPoolExecutor,
    # hasta 10 a la vez) -- con 3 tiendas (Epic, GOG, Steam) x hasta 4
    # páginas cada una, cada vez que vencía la cache de home_candidates
    # (cada 5 minutos) se disparaba una ráfaga de varios pedidos
    # simultáneos a CheapShark. Eso bastó para que te bloqueara la IP,
    # siendo prácticamente vos el único usuario -- mismo tipo de problema
    # que ya había pasado con Steam/Akamai, esta vez con CheapShark.
    # Ahora: SECUENCIAL (una página por vez) con una pausa corta entre
    # pedidos, Y cada página se cachea 30 minutos en vez de 5 -- el
    # barrido completo de una tienda se repite muchas menos veces por
    # hora, y cuando se repite, nunca le pega a CheapShark en ráfaga.
    all_deals = []
    for page_number in range(pages_needed):
        page_deals = cached_page(page_number)
        if not page_deals:
            break  # CheapShark ya no tenía más páginas para esta tienda
        all_deals.extend(page_deals)
        if len(page_deals) < CHEAPSHARK_PAGE_SIZE:
            break  # página incompleta: era la última
        if page_number < pages_needed - 1:
            time.sleep(random.uniform(0.4, 1.0))

    return all_deals[:limit]


EPIC_GOG_STEAM_PRICE_TTL = 6 * 60 * 60  # 4 veces por día -- si un juego falló (ej. Steam bloqueando en ese momento), se reintenta en unas horas, no al otro día
# Techo de seguridad, NO un tope activo -- con los ~480 nombres únicos
# que puede traer Epic+GOG (240+240), nunca debería llegar a tocarlo. Un
# EPIC_GOG_STEAM_PRICE_MAX bajo (200, la versión anterior de esto) cortaba
# la lista SIEMPRE en el mismo punto -- como el orden es estable, los
# mismos ~280 quedaban afuera para SIEMPRE, no "más tarde": nunca se
# llegaba a intentarlos (bug real, reportado por Nico). Esto corre en su
# propio hilo de fondo, sin bloquear nada, así que no hace falta cortar
# la lista -- tarda más (varios minutos en vez de uno), pero una vez
# cada 6 horas eso no le importa a nadie.
EPIC_GOG_STEAM_PRICE_MAX = 1000


def _fetch_epic_gog_steam_prices():
    """
    fetch_fn de epic_gog_steam_prices(): resuelve el precio de Steam para
    los juegos que llegan al pool de home_candidates SOLO por Epic/GOG
    (CheapShark expone su steamAppID, pero no su precio de Steam en sí).

    Job PROPIO y separado (no una resolución adentro de home_candidates) a
    propósito: el pool de Epic+GOG puede tener varios cientos de nombres
    únicos, y resolverlos uno por uno DENTRO del fetch() de
    home_candidates (que corre cada hora) significaba que ese fetch()
    completo tardaba varios minutos en terminar CADA VEZ -- mientras
    tanto, home_candidates() seguía devolviendo None (nada calculado
    todavía) y la home se veía con "0 ofertas encontradas" a pesar de que
    el log del server mostraba pedidos 200 OK normales (eran reales, solo
    que home_candidates nunca llegaba a terminar). Acá en cambio se
    cachea aparte, SIN cortar la lista (ver EPIC_GOG_STEAM_PRICE_MAX) --
    tarda lo que tenga que tardar, pero corre solo, en segundo plano.

    Totalmente SECUENCIAL, nunca en paralelo (la primera versión de esto
    hacía tandas de a 4 en paralelo -- con gunicorn corriendo varios
    workers, cada uno dispara su propia copia de este job por separado,
    así que "4 en paralelo" se multiplicaba por la cantidad de workers al
    mismo tiempo: exactamente lo que volvió a bloquear la IP de Nico en
    Steam. Mismo criterio que steam_global_top_sellers_deals y
    xbox_deals: uno por uno, con pausa entre cada pedido, pase lo que
    pase con la cantidad de workers). home_candidates() solo hace una
    lectura en memoria de lo que este job ya dejó resuelto, sin red.
    """
    appids = []
    seen = set()
    for store in ("Epic Games Store", "GOG"):
        for d in cheapshark_browse_deals(store, limit=240):
            appid = d.get("steam_appid")
            if appid and appid not in seen:
                seen.add(appid)
                appids.append(appid)
    appids = appids[:EPIC_GOG_STEAM_PRICE_MAX]

    prices = {}
    for i, appid in enumerate(appids):
        price = steam_price(appid)
        if price.get("available"):
            prices[appid] = price
        if i < len(appids) - 1:
            time.sleep(random.uniform(0.5, 1.0))

    return prices


def epic_gog_steam_prices():
    prices = scheduled_cached(
        "epic_gog_steam_prices",
        _fetch_epic_gog_steam_prices,
        interval_seconds=EPIC_GOG_STEAM_PRICE_TTL,
    )
    return prices if prices is not None else {}


def cheapshark_game_lookup(game_id):
    """
    Trae TODO lo que CheapShark sabe de un juego puntual por su gameID
    (estable -- no cambia aunque una oferta puntual desaparezca):
    su posible steamAppID (para cruzar con Steam/Nintendo sin tener que
    buscar por nombre) y sus ofertas actuales en TODAS las tiendas que
    trackea CheapShark. Cacheado una sola vez; de acá salen tanto el
    precio propio (cheapshark_game_deal) como la comparación contra las
    otras tiendas (cheapshark_game_pc_stores) de un item seguido directo
    en Epic/GOG.
    """

    def fetch():
        try:
            r = requests.get(
                "https://www.cheapshark.com/api/1.0/games",
                params={"id": game_id},
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return None

    return cached(f"cheapshark_game:{game_id}", fetch)


def cheapshark_game_deal(game_id, store_name):
    """Precio ACTUAL de un juego puntual en UNA tienda puntual (por gameID)."""
    store_ids = cheapshark_store_ids()
    store_id = store_ids.get(store_name)

    data = cheapshark_game_lookup(game_id)
    if not data:
        return {"available": False}

    info = data.get("info", {})
    deal = next(
        (d for d in data.get("deals", []) if d.get("storeID") == store_id), None
    )
    if not deal:
        return {"available": False, "name": info.get("title"), "thumb": info.get("thumb")}

    return {
        "available": True,
        "name": info.get("title"),
        "thumb": info.get("thumb"),
        "currency": "USD",
        "initial_price": float(deal.get("retailPrice", 0)),
        "final_price": float(deal.get("price", 0)),
        "discount_percent": round(float(deal.get("savings", 0))),
        "deal_url": f"https://www.cheapshark.com/redirect?dealID={deal['dealID']}",
    }


def cheapshark_game_pc_stores(game_id):
    """
    Para un juego seguido DIRECTO en Epic/GOG (kind "pc" en la
    watchlist): su precio en la/s OTRA/s tienda/s PC que trackea
    CheapShark para ese mismo gameID, más su steamAppID si lo tiene --
    así la tarjeta de origen (Epic o GOG) puede venir acompañada de la
    comparación contra Steam/Switch y la otra tienda PC, igual que pasa
    con un juego seguido por Steam. Devuelve (lista_de_tiendas, steam_appid).
    """
    store_ids = cheapshark_store_ids()
    id_to_name = {v: k for k, v in store_ids.items()}
    wanted_ids = {store_ids[n] for n in PC_STORE_NAMES if n in store_ids}

    data = cheapshark_game_lookup(game_id)
    if not data:
        return [], None

    info = data.get("info", {})
    steam_appid = info.get("steamAppID")
    steam_appid = int(steam_appid) if steam_appid else None

    stores = []
    for deal in data.get("deals", []):
        if deal.get("storeID") not in wanted_ids:
            continue
        stores.append(
            {
                "store": id_to_name.get(deal["storeID"], deal["storeID"]),
                "name": info.get("title"),
                "currency": "USD",
                "initial_price": float(deal.get("retailPrice", 0)),
                "final_price": float(deal.get("price", 0)),
                "discount_percent": round(float(deal.get("savings", 0))),
                "deal_url": f"https://www.cheapshark.com/redirect?dealID={deal['dealID']}",
                "thumb": info.get("thumb"),
                "game_id": game_id,
            }
        )
    return stores, steam_appid


# ---------------------------------------------------------------------------
# Nintendo (vía DekuDeals, locale forzado a Argentina)
# ---------------------------------------------------------------------------

_DEKU_SESSION = None


def _deku_session():
    """
    Sesión de requests con el locale de DekuDeals forzado a Argentina
    (independiente de en qué país esté corriendo este server). Se arma
    una sola vez y se reutiliza.
    """
    global _DEKU_SESSION
    if _DEKU_SESSION is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        try:
            s.post(DEKUDEALS_LOCALE_URL, data={"country": "ar"}, timeout=10)
        except requests.RequestException:
            pass  # si falla, seguimos igual; puede que ya ande por geo-IP
        _DEKU_SESSION = s
    return _DEKU_SESSION


def _parse_ar_price(raw):
    """'$84.999,00' -> 84999.0 (formato argentino: punto de miles, coma decimal)."""
    s = raw.strip().lstrip("$").strip()
    s = s.replace(".", "").replace(",", ".")
    return float(s)


_CARD_SPLIT_RE = re.compile(r"<div class='col d-block'>")
_CARD_IMAGE_RE = re.compile(r"src='(https://cdn\.dekudeals\.com/images/[^']+?\.jpg[^']*)'")
_CARD_TITLE_RE = re.compile(r"href='/items/([^']+)'>\s*<h6[^>]*>([^<]+)</h6>")
_CARD_PRICE_RE = re.compile(r"<strong>([^<]+)</strong>")

# Precio de lista (sin descuento), para calcular el % real de descuento
# -- aparece en la ficha de cada juego como "<strong>MSRP:</strong> $X".
_MSRP_RE = re.compile(r"<strong>MSRP:</strong>\s*\$?\s*([\d.,]+)")

# Heurística para detectar si el juego tiene versión/mejora para Switch 2:
# busca la mención "Switch 2" en cualquier parte del HTML de la ficha
# (DekuDeals suele listarlo en la sección de plataformas o en el nombre
# de la edición, ej. "Nintendo Switch 2 Edition"). NO está verificado a
# fondo -- puede dar falsos positivos si el texto aparece en otro
# contexto (una review, un comentario, etc.). Probar contra un juego
# confirmado con versión Switch 2 antes de confiar en este dato.
_SWITCH2_RE = re.compile(r"switch\s*2", re.IGNORECASE)



def dekudeals_search(query, limit=10):
    """
    Busca DIRECTO en el catálogo de DekuDeals por nombre -- a diferencia
    de dekudeals_check (que solo confirma un steam_appid puntual), esto
    encuentra cualquier juego que DekuDeals tenga catalogado, exista o
    no en Steam. Es lo que permite que un exclusivo de Nintendo (Zelda,
    Mario, etc.) aparezca al buscarlo, aunque nunca vaya a cruzar con
    Steam/Epic/GOG. A diferencia de /eshop-sales, esta página de
    búsqueda no está bloqueada por Cloudflare.

    Separa la respuesta por tarjeta (cada resultado empieza con un
    '<div class='col d-block'>' propio) y busca imagen/título/precio
    DENTRO de cada una por separado, en vez de un único regex gigante
    que intenta capturar las tres cosas encadenadas -- así, si a algún
    juego le falta la imagen (o cualquier otro dato), no rompe el
    resultado entero ni hace que ese campo salga vacío en cascada.

    OJO: esta vista de resultados no trae el HTML completo de la ficha
    de cada juego (solo la tarjeta resumida), así que acá NO se puede
    detectar switch2 -- eso solo sale en dekudeals_check/dekudeals_item,
    que sí traen la página entera.
    """

    def fetch():
        try:
            r = _deku_session().get(
                "https://www.dekudeals.com/search",
                params={"q": query},
                timeout=10,
            )
        except requests.RequestException:
            return []
        if r.status_code != 200:
            return []

        results = []
        for card in _CARD_SPLIT_RE.split(r.text)[1:]:
            title_m = _CARD_TITLE_RE.search(card)
            if not title_m:
                continue

            slug, name_raw = title_m.group(1), title_m.group(2)
            image_m = _CARD_IMAGE_RE.search(card)
            price_m = _CARD_PRICE_RE.search(card)

            entry = {
                "slug": slug,
                "name": unescape(name_raw).strip(),
                "image": image_m.group(1) if image_m else None,
                "currency": "ARS",
            }
            if price_m:
                try:
                    entry["available"] = True
                    entry["price"] = _parse_ar_price(price_m.group(1))
                except ValueError:
                    entry["available"] = False
            else:
                entry["available"] = False

            results.append(entry)
            if len(results) >= limit:
                break
        return results


    return cached(f"dekudeals_search:{query}:{limit}", fetch)


def _parse_eshop_ar_from_html(html):
    """
    Extrae el bloque de analytics que DekuDeals expone en cualquier
    página de item (ya sea /app/<steam_appid> o /items/<slug>):
    outAnalytics['eshop_ar:<NSUID>'] = {"currency":"ARS","value":<centavos>,
      "items":[{...,"discount":<monto en pesos, NO porcentaje>,...}]}

    Ojo con "discount": es un parámetro de e-commerce estilo Google
    Analytics -- ahí "discount" es el MONTO descontado en la moneda del
    item, no un porcentaje (nos confundió antes: al mostrarlo directo
    con "%" salían valores como "-3624950%"). Para el % real hay que
    compararlo contra el precio de lista (MSRP), que también viene en
    la misma página.
    """
    m = re.search(
        r"outAnalytics\['eshop_ar:(\d+)'\]\s*=\s*(\{.*?\})\s*(?:;|</script>)",
        html,
    )
    if not m:
        return {"on_switch": False}

    nsuid = m.group(1)
    try:
        data = json.loads(m.group(2))
    except ValueError:
        return {"on_switch": False}

    value = data.get("value")
    final_price = value / 100 if value is not None else None

    discount_percent = 0
    msrp_m = _MSRP_RE.search(html)
    if msrp_m and final_price is not None:
        try:
            msrp = _parse_ar_price(msrp_m.group(1))
            if msrp > 0 and final_price < msrp:
                discount_percent = round((msrp - final_price) / msrp * 100)
        except ValueError:
            pass

    return {
        "on_switch": True,
        "nsuid": nsuid,
        "currency": data.get("currency", "ARS"),
        "price": final_price,
        "discount_percent": discount_percent,
        "switch2": bool(_SWITCH2_RE.search(html)),
    }


def dekudeals_check(steam_appid):
    """
    Consulta si un juego de Steam existe en la eShop de Nintendo Switch
    Argentina, y si es así devuelve su precio en ARS y su NSUID, todo en
    una sola request. No requiere ninguna búsqueda ni catálogo manual.
    """

    def fetch():
        try:
            r = _deku_session().get(
                DEKUDEALS_APP_URL.format(appid=steam_appid), timeout=10
            )
        except requests.RequestException as e:
            return {"on_switch": False, "error": str(e)}
        if r.status_code != 200:
            return {"on_switch": False}
        return _parse_eshop_ar_from_html(r.text)

    return cached(f"dekudeals:{steam_appid}", fetch)


def dekudeals_item(slug):
    """
    Igual que dekudeals_check, pero para juegos que no están en Steam
    -- se consulta la página de DekuDeals por su slug
    (/items/<slug>, el mismo que devuelve dekudeals_search) en vez de
    por steam_appid. Es lo que permite seguir precios de exclusivos de
    Nintendo en la watchlist.
    """

    def fetch():
        try:
            r = _deku_session().get(
                f"https://www.dekudeals.com/items/{slug}", timeout=10
            )
        except requests.RequestException as e:
            return {"on_switch": False, "error": str(e)}
        if r.status_code != 200:
            return {"on_switch": False}
        return _parse_eshop_ar_from_html(r.text)

    return cached(f"dekudeals_item:{slug}", fetch)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@app.get("/api/steam/search")
def route_steam_search():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "falta ?q="}), 400
    return jsonify(steam_search(q))


@app.get("/api/steam/price/<int:appid>")
def route_steam_price(appid):
    return jsonify(steam_price(appid))


@app.get("/api/steam/deals")
def route_steam_deals():
    return jsonify(steam_deals())


@app.get("/api/pc/search")
def route_pc_search():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "falta ?q="}), 400
    return jsonify(pc_store_search(q))


@app.get("/api/dolar")
def route_dolar():
    return jsonify(dolar_rates())


@app.get("/api/debug/steam-chart")
def route_debug_steam_chart():
    """
    Diagnóstico para chequear, SIN tener que leer logs del server, si el
    scraping del ranking real (steam_global_top_sellers_ranked, el que
    arma sort="relevancia") está funcionando -- si "ranked_count" da 0,
    el pedido a Steam está fallando (bloqueado, timeout, o cambió el
    HTML y el regex ya no encuentra nada) y por eso la home vuelve a
    quedar en el orden "de siempre" sin el top real aplicado encima.
    "top5" y "top5_names" dejan ver A OJO si lo que trajo es realmente
    el top 100 de ahora (comparar contra
    store.steampowered.com/charts/topselling/global en el navegador).
    """
    chart = _steam_global_chart()
    ranked = chart["ranked"]
    top5 = ranked[:5]
    top5_names = []
    for appid in top5:
        price = steam_price(appid)
        top5_names.append(price.get("name") or f"appid {appid}")
    deals_cache_age_sec = (
        round(time.time() - _steam_global_deals_cache["ts"])
        if _steam_global_deals_cache["ts"]
        else None
    )
    return jsonify(
        {
            "ranked_count": len(ranked),
            "top5": top5,
            "top5_names": top5_names,
            "global_deals_cached_count": len(_steam_global_deals_cache["data"]),
            "global_deals_cache_age_sec": deals_cache_age_sec,
            "global_deals_refreshing_now": _steam_global_deals_refreshing,
        }
    )


@app.get("/api/debug/xbox-deals")
def route_debug_xbox_deals():
    """
    Mismo propósito que /api/debug/steam-chart pero para Xbox: permite
    chequear desde afuera (sin leer logs del server) si el barrido
    paginado a emerald.xboxservices.com está funcionando, cuántos juegos
    trajo en total y hace cuánto fue el último refresco -- sin esto, un
    0 en "count" podría ser "todavía no corrió" o "se rompió el scraping"
    y no habría forma de distinguirlos desde afuera.
    """
    entry = _SCHEDULED.get("xbox_deals", {"data": None, "ts": 0.0, "refreshing": False})
    deals = entry["data"] or []
    age_sec = round(time.time() - entry["ts"]) if entry["ts"] else None
    return jsonify(
        {
            "count": len(deals),
            "sample_names": [d["name"] for d in deals[:5]],
            "cache_age_sec": age_sec,
            "refreshing_now": entry["refreshing"],
            # Por qué el ÚLTIMO barrido cortó donde cortó -- null en
            # "error" con count en 0 significa que no cortó por error (el
            # canal vino vacío de entrada, por ejemplo); con algo acá, es
            # la causa real (pedido rechazado, status code, JSON raro).
            "last_error": _xbox_last_error,
            # Cuántos de esos `count` juegos terminaron pegados a un
            # candidato real (Steam/Epic/GOG) la ÚLTIMA vez que se llamó a
            # home_candidates() (ver _merge_xbox_into) -- se recalcula en
            # CADA pedido a /api/home, no solo 1 vez por hora, así que este
            # número se actualiza solo apenas termina el primer barrido de
            # Xbox, sin esperar ni forzar nada. "matched": null significa
            # que todavía no se pidió /api/home en este proceso -- pedilo
            # una vez (o esperá el warm-up) y volvé a chequear acá.
            "matched_into_pool": _xbox_last_matched["matched"],
            "matched_pool_total_xbox": _xbox_last_matched["total"],
            "matched_age_sec": (
                round(time.time() - _xbox_last_matched["ts"])
                if _xbox_last_matched["ts"]
                else None
            ),
        }
    )


@app.get("/api/debug/playstation-deals")
def route_debug_playstation_deals():
    """Mismo propósito que /api/debug/xbox-deals pero para PlayStation."""
    entry = _SCHEDULED.get("playstation_deals", {"data": None, "ts": 0.0, "refreshing": False})
    deals = entry["data"] or []
    age_sec = round(time.time() - entry["ts"]) if entry["ts"] else None
    return jsonify(
        {
            "count": len(deals),
            "sample_names": [d["name"] for d in deals[:5]],
            "cache_age_sec": age_sec,
            "refreshing_now": entry["refreshing"],
            "last_error": _ps_last_error,
            "matched_into_pool": _ps_last_matched["matched"],
            "matched_pool_total_playstation": _ps_last_matched["total"],
            "matched_age_sec": (
                round(time.time() - _ps_last_matched["ts"])
                if _ps_last_matched["ts"]
                else None
            ),
        }
    )


@app.get("/api/debug/epic-gog-steam")
def route_debug_epic_gog_steam():
    """
    Mismo propósito que /api/debug/xbox-deals pero para
    epic_gog_steam_prices(): cuántos juegos de Epic/GOG se resolvieron
    con precio de Steam, hace cuánto fue el último barrido, y si está
    corriendo uno ahora -- para confirmar si un force-refresh realmente
    mejoró el número, en vez de adivinar mirando la home.
    """
    entry = _SCHEDULED.get("epic_gog_steam_prices", {"data": None, "ts": 0.0, "refreshing": False})
    prices = entry["data"] or {}
    age_sec = round(time.time() - entry["ts"]) if entry["ts"] else None
    return jsonify(
        {
            "resolved_count": len(prices),
            "cache_age_sec": age_sec,
            "refreshing_now": entry["refreshing"],
        }
    )


# Nombres válidos para /api/debug/force-refresh=<job> -- cada uno
# corresponde a una clave real de _SCHEDULED (o, para steam_global, a su
# propio diccionario de cache aparte).
_FORCE_REFRESH_JOBS = {
    "home_relevancia": lambda: ("home_candidates:relevancia:0", lambda: home_candidates("relevancia", only_discounted=False)),
    "home_destacados": lambda: ("home_candidates:mas_vendidas:1", lambda: home_candidates("mas_vendidas", only_discounted=True)),
    "xbox": lambda: ("xbox_deals", xbox_deals),
    "epic_gog_steam": lambda: ("epic_gog_steam_prices", epic_gog_steam_prices),
}


@app.get("/api/debug/force-refresh")
def route_debug_force_refresh():
    """
    Fuerza que uno de los jobs de fondo (los que corren con
    scheduled_cached, más steam_global aparte) vuelva a calcularse YA,
    sin esperar a que venza su intervalo normal (1 hora para la home, 24
    horas para Xbox y para la resolución de precios Epic/GOG->Steam) ni
    reiniciar el server entero.

    Por qué hace falta esto: un candidato que falló en resolver su precio
    de Steam durante el único pase diario de epic_gog_steam_prices (por
    ejemplo, justo mientras Steam estaba bloqueando) se queda mostrando
    "no disponible" hasta el PRÓXIMO pase, 24hs después -- no hay
    reintento automático más seguido (a propósito: resolver esto más
    seguido es justo el patrón de ráfaga que ya bloqueó la IP una vez).
    Esto da una forma manual de pedir "probá de nuevo ahora", para usar
    cuando Nico ya sabe que el bloqueo pasó y no quiere esperar.

    ?job=home_relevancia | home_destacados | xbox | epic_gog_steam | all
    Dispara el recálculo en un hilo de fondo (como scheduled_cached
    siempre hace) y devuelve al toque -- no espera a que termine. Mirar
    /api/debug/xbox-deals o volver a pedir /api/home en unos segundos
    para ver el resultado.
    """
    valid_names = list(_FORCE_REFRESH_JOBS.keys()) + ["steam_global"]
    job = request.args.get("job", "all")
    jobs = valid_names if job == "all" else [job]
    invalid = [j for j in jobs if j not in valid_names]
    if invalid:
        return jsonify({"error": f"job inválido: {invalid}", "jobs_validos": valid_names + ["all"]}), 400

    triggered = []
    for j in jobs:
        if j == "steam_global":
            # steam_global_top_sellers_deals() no usa scheduled_cached (es
            # un patrón más viejo, con su propio dict aparte) -- se fuerza
            # distinto, pero entra en la misma lista.
            _steam_global_deals_cache["ts"] = 0.0
            steam_global_top_sellers_deals()
        else:
            key, call = _FORCE_REFRESH_JOBS[j]()
            entry = _SCHEDULED.get(key)
            if entry:
                entry["ts"] = 0.0  # fuerza "stale" -- el call de abajo dispara el recálculo
            call()  # scheduled_cached ve que está stale y arranca el hilo de fondo
        triggered.append(j)

    return jsonify({"triggered": triggered})



@app.get("/api/nintendo/check/<int:steam_appid>")
def route_nintendo_check(steam_appid):
    return jsonify(dekudeals_check(steam_appid))


def compare_steam_results(steam_results, with_prices=True):
    """
    Toma una lista de resultados tipo Steam (appid, name, tiny_image, y
    opcionalmente price ya resuelto) y arma matched/steam_only contra
    Nintendo, más la búsqueda en Epic/GOG por cada nombre. La reutilizan
    tanto /api/compare (a partir de una búsqueda) como /api/home (a
    partir de las ofertas de Steam).

    Los pedidos a Nintendo (DekuDeals) y Epic/GOG (CheapShark) de cada
    juego son independientes entre sí, así que se disparan en paralelo
    (hilos, ya que son requests de red, no cómputo) en vez de uno por
    uno -- si no, el tiempo de carga crece directo con la cantidad de
    juegos.
    """

    def process_one(s):
        sp = s.get("price") if not with_prices else steam_price(s["appid"])
        deku = dekudeals_check(s["appid"])
        # pc_store_exact_matches, no pc_store_search directo -- mismo
        # motivo que en route_home (ver el comentario grande ahí): sin
        # filtrar, CheapShark también trae DLCs/bundles con título
        # parecido como si fueran el juego mismo.
        pc = (
            pc_store_exact_matches(s["name"], limit=10)
            + xbox_exact_matches(s["name"])
            + ps_exact_matches(s["name"])
        )
        return s, sp, deku, pc

    matched = []
    not_on_switch = []
    pc_results = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        for s, sp, deku, pc in pool.map(process_one, steam_results):
            if deku.get("on_switch"):
                matched.append(
                    {"name": s["name"], "steam": {**s, "price": sp}, "nintendo": deku}
                )
            else:
                not_on_switch.append({**s, "price": sp})
            pc_results.extend(pc)

    return matched, not_on_switch, pc_results


@app.get("/api/compare")
def route_compare():
    q = request.args.get("q", "")
    if not q:
        return jsonify({"error": "falta ?q="}), 400

    steam_results = steam_search(q)
    # BUG REAL encontrado (reportado por Nico: buscaba "black flag
    # resynced", Steam SÍ encontraba "Assassin's Creed Black Flag
    # Resynced", pero Xbox nunca aparecía aunque xbox_deals() lo tuviera) --
    # compare_steam_results ya arma, por cada resultado de Steam, sus
    # matches exactos de Epic/GOG/Xbox usando el NOMBRE REAL del juego
    # ("Assassin's Creed Black Flag Resynced") -- pero acá se tiraba ese
    # resultado (el "_" de abajo) y se volvía a armar pc_stores buscando
    # contra lo que el usuario ESCRIBIÓ ("black flag resynced"), no contra
    # el título completo. pc_store_search(q) es fuzzy (CheapShark busca
    # por substring), así que igual encontraba algo ahí -- pero
    # xbox_exact_matches(q) exige coincidencia EXACTA (Xbox no tiene
    # buscador propio), y nadie escribe el título completo tal cual en el
    # buscador, así que esa parte nunca pegaba.
    matched, not_on_switch, pc_from_steam_names = compare_steam_results(steam_results)

    # Búsqueda directa en Nintendo, independiente de si el juego está en
    # Steam. Evita duplicar lo que ya salió matcheado desde Steam.
    already_matched_names = {m["name"].strip().lower() for m in matched}
    nintendo_direct = [
        d
        for d in dekudeals_search(q)
        if d["name"].strip().lower() not in already_matched_names
    ]

    # pc_store_search(q) + xbox_exact_matches(q) + ps_exact_matches(q)
    # siguen siendo útiles ADEMÁS (cubren un juego de Epic/GOG que ni
    # apareció en la búsqueda de Steam, o el caso borde de que alguien sí
    # escriba el título completo) -- se combinan con lo de arriba, sin
    # duplicar la misma oferta dos veces (puede pisarse si el nombre
    # coincide en ambos).
    pc_stores = pc_store_search(q) + xbox_exact_matches(q) + ps_exact_matches(q)
    seen_pc = {(p.get("store"), p.get("game_id")) for p in pc_stores}
    for p in pc_from_steam_names:
        key = (p.get("store"), p.get("game_id"))
        if key not in seen_pc:
            seen_pc.add(key)
            pc_stores.append(p)

    return jsonify(
        {
            "query": q,
            "matched": matched,
            "steam_only": not_on_switch,
            "pc_stores": pc_stores,
            "nintendo_direct": nintendo_direct,
        }
    )


HOME_SORTS = ("descuento", "precio", "relevancia", "mas_vendidas")


def home_candidates(sort="descuento", only_discounted=False):
    """
    Pool combinado de candidatos para el feed de inicio, juntando TRES
    fuentes independientes (no solo Steam): Steam, Epic y GOG (estas dos
    últimas navegadas directo por descuento en CheapShark, sin buscar
    nada puntual). Se deduplica por nombre -- si el mismo juego aparece
    en más de una tienda, se completa en un solo candidato con los
    precios que se van encontrando.

    Ya se probaron un par de criterios propios "por detrás" (primero
    descuento, después precio de lista) como DEFAULT silencioso de la
    pantalla principal, y en los dos casos terminó siendo confuso -- el
    usuario no elegía cómo se ordenaba, simplemente cambiaba. Ahora el
    orden es una eleccion EXPLÍCITA (ver ?sort= más abajo), y la pantalla
    de inicio además separa un par de secciones curadas (Destacados / Más
    vendidos) de la lista general, en vez de mezclar todo en una sola
    grilla gigante.

    sort="descuento": mayor % de descuento primero (entre las tres
    tiendas). La fuente de Steam son sus ofertas actuales ("specials").

    sort="precio": precio de lista (sin descuento) de mayor a menor, y a
    igual precio, mayor descuento -- proxy de qué tan grande/conocido es
    el juego (ver list_price_usd). Es el criterio que arma la sección
    "Destacados" (con only_discounted=True: solo candidatos que además
    tengan algún descuento activo, si no saldrían ahí juegos caros sin
    ninguna oferta real).

    sort="relevancia": primero los juegos que están en el ranking de más
    vendidos de Steam ahora mismo (en ese orden real de Valve), después
    el resto tal cual lo va entregando cada fuente -- Steam (categoría
    "specials"), más ofertas de Steam que suma CheapShark, y por último
    lo que suma Epic/GOG. OJO: Steam no siempre devuelve el mismo orden
    para el mismo pedido, así que la parte "resto" de este modo puede
    "bailar" entre cargas (el bloque de más vendidos al principio no,
    ese es estable mientras no cambie el ranking real).

    sort="mas_vendidas": la fuente de Steam son sus MÁS VENDIDOS ahora
    mismo (categoría "top_sellers" -- el ranking real de Valve), SIN
    reordenar (ese ranking ya es el dato interesante en sí). La mayoría
    de estos no están en oferta (discount_percent en 0); only_discounted
    se encarga de filtrar eso y completar con ofertas reales de Steam si
    hace falta (ver más abajo) -- se probó traer esta sección desde otro
    endpoint (la portada real de la tienda) pero no se pudo confirmar que
    funcionara, así que se volvió a este, que es estable.

    only_discounted=True descarta, al final, cualquier candidato sin
    descuento activo en ninguna tienda (lo usa la sección "Destacados"
    para no mostrar un juego caro que no está en oferta).

    Solo se conservan candidatos con steam_appid conocido: es el ID que
    usamos para todo (consultar Nintendo, armar la watchlist), así que
    un juego sin Steam ni relación con Steam no se puede seguir en esta
    app tal como está armada. Sigue sumando bastante: CheapShark expone
    el steamAppID de un montón de ofertas de Epic/GOG aunque la oferta
    en sí sea de esa tienda.
    """
    if sort not in HOME_SORTS:
        sort = "descuento"

    def fetch():
        # Pedido en PRIMER lugar, antes que cualquier otra fuente (Steam
        # specials, Epic, GOG, Xbox) -- a pedido explícito de Nico: quiere
        # que el ranking de más vendidos de Steam sea lo primero que se
        # trae en cada ciclo, para que en cuanto esté disponible, el resto
        # del pool ya se termine de armar organizado en base a él (en vez
        # de, por ejemplo, quedar a mitad de un barrido largo de CheapShark
        # y recién al final acordarse de pedirlo). En la práctica el
        # resultado ya era el mismo de cualquier forma -- esto solo se usa
        # para ORDENAR, y el ordenamiento siempre corrió sobre el pool ya
        # completo, antes de cachear nada, así que nunca se veía a medio
        # ordenar -- pero no cuesta nada pedirlo primero y es más claro así.
        # steam_global_top_sellers_ranked() usa cached() (no
        # scheduled_cached): la primera vez que se pide bloquea ESTE hilo
        # de fondo (nunca un pedido de un visitante real) hasta traer la
        # página; después queda 5 minutos en caché, así que pedirlo acá
        # para TODOS los sorts (no solo "relevancia", que es el único que
        # lo usa para ordenar) no agrega pedidos de red extra reales en la
        # práctica -- como mucho, una vez cada 5 minutos.
        top_sellers_rank = {
            appid: i for i, appid in enumerate(steam_global_top_sellers_ranked())
        }

        candidates = {}

        def upsert(name, tiny_image, steam_appid, **store_price):
            key = name.strip().lower()
            c = candidates.setdefault(
                key, {"name": name, "tiny_image": None, "steam_appid": None}
            )
            if tiny_image and not c["tiny_image"]:
                c["tiny_image"] = tiny_image
            if steam_appid and not c["steam_appid"]:
                c["steam_appid"] = steam_appid
            c.update(store_price)
            return key

        steam_source = steam_top_sellers() if sort == "mas_vendidas" else steam_deals_all()

        for d in steam_source:
            upsert(
                d["name"],
                d["tiny_image"],
                d["appid"],
                steam={
                    "available": True,
                    "is_free": False,
                    "currency": d["currency"],
                    "initial_price": d["initial_price"],
                    "final_price": d["final_price"],
                    "discount_percent": d["discount_percent"],
                },
            )

        # "mas_vendidas" NO suma Epic/GOG ni este extra de Steam: es
        # específicamente el ranking de más vendidos de STEAM
        # (top_sellers), no "lo que esté más barato en cualquier tienda".
        # Antes esto sí los sumaba, y como los más vendidos reales casi
        # nunca tienen descuento activo al mismo tiempo, only_discounted
        # terminaba descartando casi todo Steam y rellenando "Destacados"
        # con lo que trajera CheapShark (que ordena por % de descuento, no
        # por fama -- así aparecían indies con 95% off como si fueran
        # "destacados").
        if sort != "mas_vendidas":
            # limit más alto que antes (era 60, el tope de UNA sola página
            # de CheapShark) -- ahora cheapshark_browse_deals pagina sola
            # por dentro, así que esto realmente trae más candidatos en
            # vez de cortarse siempre en la misma cantidad.
            for d in cheapshark_browse_deals("Epic Games Store", limit=240):
                upsert(d["name"], d["thumb"], d.get("steam_appid"), epic=d)

            for d in cheapshark_browse_deals("GOG", limit=240):
                upsert(d["name"], d["thumb"], d.get("steam_appid"), gog=d)

            # steam_deals_all() (el endpoint "featuredcategories" de Steam)
            # es un snapshot curado y CHICO (lo que Steam mete en su propia
            # portada de "Specials") -- no tiene forma de pedir "la página
            # que sigue", es una lista fija. Por eso cuando alguien filtra
            # por Steam en el sidebar, el pool entero quedaba acotado a esa
            # lista corta y el scroll infinito se quedaba sin nada más para
            # pedir enseguida (bien detectado: "aparecen 9 que son
            # claramente esos 3 y los destacados"). CheapShark también
            # rastrea ofertas de Steam (es una tienda más para ellos, con
            # el mismo storeID real que Epic/GOG) y SÍ se puede paginar --
            # mismo mecanismo que ya usamos arriba para Epic/GOG, nada
            # nuevo que adivinar. Esto no reemplaza steam_deals_all() (esa
            # sigue siendo la fuente para "destacados"/mas_vendidas, que
            # quiere justo lo que Steam cura como su propio escaparate),
            # solo AMPLÍA el pool general con más ofertas reales de Steam
            # que la lista corta de Valve no incluye.
            for d in cheapshark_browse_deals("Steam", limit=240):
                upsert(
                    d["name"],
                    d.get("thumb"),
                    d.get("steam_appid"),
                    steam={
                        "available": True,
                        "is_free": False,
                        "currency": d["currency"],
                        "initial_price": d["initial_price"],
                        "final_price": d["final_price"],
                        "discount_percent": d["discount_percent"],
                    },
                )

            # Tercera fuente de Steam: el precio real de los juegos del
            # ranking GLOBAL de más vendidos (steam_global_top_sellers_deals,
            # ver el comentario grande junto a su definición) -- cubre
            # juegos grandes en oferta que ni "specials" ni CheapShark
            # tenían (caso real: Warhammer 40,000: Space Marine 2, Forza
            # Horizon 6, que estaban en oferta en Steam pero no aparecían
            # en el pool). Esta vez SIN el problema de la primera versión
            # (ráfaga de pedidos paralelos que terminó bloqueando la IP de
            # Nico): acá se pide secuencial con pausas y se cachea 24hs, así
            # que a esta altura ya está resuelto por un hilo de fondo, no
            # por este pedido -- ver steam_global_top_sellers_deals().
            for d in steam_global_top_sellers_deals():
                extra = {}
                for p in d.get("pc_stores", []):
                    if p["store"] == "Epic Games Store":
                        extra["epic"] = p
                    elif p["store"] == "GOG":
                        extra["gog"] = p
                upsert(
                    d["name"],
                    d.get("tiny_image"),
                    d["appid"],
                    steam={
                        "available": True,
                        "is_free": False,
                        "currency": d["currency"],
                        "initial_price": d["initial_price"],
                        "final_price": d["final_price"],
                        "discount_percent": d["discount_percent"],
                    },
                    **extra,
                )

        # BUG REAL encontrado (reportado por Nico: "un monton de juegos
        # que me aparecen con no disponible en steam cuando si estan
        # disponibles", después de tener la app abierta un rato) --
        # candidatos que llegaron SOLO por Epic/GOG (los dos loops de
        # cheapshark_browse_deals de arriba) traen un steam_appid gracias
        # a que CheapShark lo expone, pero NUNCA se les pegó la clave
        # "steam" -- a esos, route_home los resolvía EN VIVO, por página,
        # con un ThreadPoolExecutor de hasta 10 pedidos en paralelo a
        # Steam. EXACTAMENTE el mismo patrón que ya había baneado la IP de
        # Nico en Steam/Akamai una vez. Se arregla con
        # epic_gog_steam_prices() (ver más abajo, es su PROPIO job --
        # NO una resolución acá adentro: la primera versión de este fix
        # SÍ la hizo acá, secuencial con pausas por cada candidato sin
        # "steam", y resultó ser OTRO bug: con cientos de candidatos de
        # Epic/GOG, esto tardaba varios minutos CADA VEZ que corría este
        # fetch() -- "el backend me esta haciendo los pedidos [pero] la
        # pagina no me muestra nada" era justo eso, home_candidates
        # todavía no había terminado de calcular nada). Con un job propio
        # y cacheado aparte, acá solo queda una lectura en memoria, sin
        # red y sin bloquear nada.
        resolved_prices = epic_gog_steam_prices()
        for c in candidates.values():
            if c["steam_appid"] and "steam" not in c:
                price = resolved_prices.get(c["steam_appid"])
                if price:
                    c["steam"] = price

        # Xbox NO se pega acá adentro -- ver el comentario grande junto a
        # _merge_xbox_into, más abajo (fuera de este fetch()). Dos
        # versiones anteriores lo intentaron acá: la primera corría antes
        # de que el pool estuviera completo (cruzaba contra un puñado de
        # candidatos y daba match 0 siempre), la segunda ya cruzaba contra
        # el pool completo pero quedaba CONGELADA adentro del resultado
        # cacheado 1 hora de scheduled_cached -- confirmado con datos
        # reales (Nico: matched_into_pool volvió a dar 0 con
        # matched_pool_total_xbox en 0 varios minutos después de que
        # /api/debug/xbox-deals ya mostraba count 663) -- el fetch() que
        # ganó la carrera de arranque había corrido ANTES de que
        # xbox_deals() terminara su primer barrido (que tarda. ~1-2 min
        # en paginar todo), capturó xbox_deals() vacío, y como fetch()
        # completo (con ESE 0) queda cacheado una hora entera, no había
        # forma de que se autocorrija sin esperar o reiniciar nunca
        # (reiniciar reinicia TAMBIÉN xbox_deals(), repitiendo la carrera).

        with_appid = [c for c in candidates.values() if c["steam_appid"]]

        def best_discount(c):
            # .get(..., 0) y no c[k]["discount_percent"] directo -- BUG
            # REAL encontrado (Nico: 500 en /api/home, KeyError
            # 'discount_percent'): steam_price() devuelve, para un juego
            # GRATIS en Steam, {"available": True, "is_free": True,
            # "name": ...} SIN discount_percent (ni ningún otro campo de
            # precio) -- y ese dict llega tal cual a c["steam"] vía
            # epic_gog_steam_prices() (resolved_prices, ver más abajo) para
            # un candidato que llegó solo por Epic/GOG. Puede pasar con
            # cualquier tienda en teoría, no solo Steam, así que el fix va
            # acá en general.
            return max(
                (c[k].get("discount_percent", 0) for k in ("steam", "epic", "gog", "xbox", "playstation") if k in c),
                default=0,
            )

        if sort == "descuento":
            with_appid.sort(key=best_discount, reverse=True)
        elif sort == "precio":
            rate = _usd_ars_rate()

            def list_price_usd(c):
                if "steam" in c and c["steam"].get("initial_price"):
                    return c["steam"]["initial_price"] / rate if rate else 0
                for k in ("epic", "gog"):
                    if k in c and c[k].get("initial_price"):
                        return c[k]["initial_price"]
                return 0

            with_appid.sort(key=lambda c: (-list_price_usd(c), -best_discount(c)))
        elif sort == "relevancia":
            # El usuario pidió que los más vendidos aparezcan primero en
            # la grilla general -- no hay un ranking de ventas para
            # Epic/GOG ni para las ofertas que suma CheapShark (esas APIs
            # no lo exponen), así que lo único "real" con lo que se puede
            # priorizar es el ranking GLOBAL de Steam
            # (steam_global_top_sellers_ranked(), ya pedido al principio de
            # este fetch() -- ver el comentario grande ahí -- OJO, NO es
            # steam_top_sellers(), esa es una lista chica de la portada
            # vieja que resultó ser un ranking distinto). Se compara por
            # steam_appid (no por nombre: más preciso, sin depender de que
            # el nombre venga escrito igual en las dos fuentes). Los
            # candidatos que están en ese ranking van primero, EN ESE
            # ORDEN; todo lo demás queda después, en el mismo orden
            # relativo en que ya venía (sort() de Python es estable, así
            # que los que empatan en "no está en el ranking" no se
            # reordenan entre sí) -- sigue sin haber un criterio propio por
            # encima para ESE resto, solo se adelanta lo que sí tiene un
            # dato real de popularidad.
            not_top_seller = len(top_sellers_rank)
            with_appid.sort(
                key=lambda c: top_sellers_rank.get(c["steam_appid"], not_top_seller)
            )
        # "mas_vendidas": sin reordenar, queda el orden de inserción
        # (candidates.values() de un dict de Python 3.7+ ya lo preserva) --
        # ESE orden de inserción YA ES el ranking real de Valve (viene de
        # steam_top_sellers(), ver arriba), así que no hay nada para
        # adelantar acá.

        if only_discounted:
            if sort == "mas_vendidas":
                # "Destacados" no puede mostrar algo que NO sea una oferta
                # de verdad (antes, al no descartar nada, se colaba por
                # ejemplo un visor de VR a precio de lista completo, sin
                # ningún descuento -- confuso bajo un cartel que dice
                # "Destacado" junto al resto de ofertas). Así que acá SÍ se
                # descarta todo lo que no tenga descuento activo, igual que
                # el resto de los sorts.
                discounted = [c for c in with_appid if best_discount(c) > 0]

                # Pero los más vendidos "de verdad" casi nunca están en
                # oferta al mismo tiempo, así que esto solo puede dejar 0,
                # 1 o 2 resultados. Red de seguridad: si hace falta, se
                # completa con las mejores ofertas REALES de Steam ahora
                # mismo (la categoría "specials", mismo origen que
                # sort="descuento") que todavía no estén en la lista --
                # siguen siendo descuentos genuinos (no al pedo), aunque
                # no figuren en el ranking de más vendidos.
                #
                # 6 (no 4): tiene que ser múltiplo del tamaño de página
                # del carrusel (2, ver FeaturedCarousel.tsx) -- con un
                # número que no es múltiplo exacto, la última página
                # quedaba con una sola tarjeta sola y un montón de hueco
                # al lado, como si "se hubiera quedado sin más".
                MIN_DESTACADOS = 6
                if len(discounted) < MIN_DESTACADOS:
                    seen = {c["name"].strip().lower() for c in discounted}
                    padding = []
                    for d in steam_deals_all():
                        key = d["name"].strip().lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        padding.append(
                            {
                                "name": d["name"],
                                "tiny_image": d["tiny_image"],
                                "steam_appid": d["appid"],
                                "steam": {
                                    "available": True,
                                    "is_free": False,
                                    "currency": d["currency"],
                                    "initial_price": d["initial_price"],
                                    "final_price": d["final_price"],
                                    "discount_percent": d["discount_percent"],
                                },
                            }
                        )
                    padding.sort(key=best_discount, reverse=True)
                    discounted += padding[: MIN_DESTACADOS - len(discounted)]

                with_appid = discounted
            else:
                with_appid = [c for c in with_appid if best_discount(c) > 0]

        return with_appid

    # scheduled_cached, NO cached() -- esto es lo que arma el barrido
    # completo a CheapShark (Epic+GOG+Steam, ver arriba), la parte cara.
    # Con cached() (TTL normal), CUALQUIER visita después de que venciera
    # el TTL disparaba el recálculo -- con tráfico normal del sitio (o
    # con Nico mismo yendo y viniendo mientras prueba cosas) alcanzaba
    # para varios recálculos por hora, cada uno con su propia tanda de
    # pedidos a CheapShark. Con scheduled_cached, el recálculo pasa SOLO
    # cada HOME_CANDIDATES_REFRESH_SECONDS, en un hilo de fondo, pase lo
    # que pase con el tráfico -- diez visitas o cero en el medio dan
    # exactamente la misma cantidad de pedidos salientes. Las ofertas no
    # cambian minuto a minuto, así que esto no le hace perder nada de
    # "frescura" real a la home.
    candidates = scheduled_cached(
        f"home_candidates:{sort}:{int(only_discounted)}",
        fetch,
        interval_seconds=HOME_CANDIDATES_REFRESH_SECONDS,
    )
    # None solo puede pasar en el primerísimo pedido tras un restart del
    # server, mientras el primer cálculo en segundo plano todavía no
    # terminó -- unos pocos segundos. _warm_home_candidates_cache (ver
    # más abajo) dispara ese primer cálculo apenas arranca el proceso,
    # en vez de esperar a que llegue el primer visitante, así esa
    # ventana es lo más chica posible.
    if candidates is None:
        return []

    # Xbox y PlayStation se pegan ACÁ, fuera del caché de 1 hora de arriba
    # -- ver el comentario grande en _merge_xbox_into sobre por qué (la
    # versión anterior, adentro de fetch(), quedaba pegada al snapshot del
    # momento exacto del cálculo, que en el primer arranque del proceso
    # casi siempre está vacío).
    _merge_xbox_into(candidates)
    _merge_ps_into(candidates)
    return candidates


def _warm_home_candidates_cache():
    """
    Dispara, apenas arranca el proceso, el primer cálculo en segundo
    plano de los DOS pools que realmente usa el frontend (el principal,
    sort=relevancia, y el de "Destacados", sort=mas_vendidas con
    only_discounted) -- así la primera visita real después de un
    restart no se encuentra con una home vacía durante los varios
    segundos que tarda el primer barrido a CheapShark/Steam. Sin esto,
    scheduled_cached funciona igual, pero recién arranca a calcular
    cuando llega el primer pedido que lo necesita.
    """
    home_candidates("relevancia", only_discounted=False)
    home_candidates("mas_vendidas", only_discounted=True)
    # Dispara también el primer barrido de Xbox y de precios de Steam para
    # Epic/GOG apenas arranca el proceso, en vez de esperar a que
    # home_candidates los pida de rebote -- mismo motivo que los dos
    # calls de arriba. Son jobs propios (scheduled_cached), así que esto
    # solo DISPARA sus hilos de fondo, no bloquea nada acá.
    xbox_deals()
    playstation_deals()
    epic_gog_steam_prices()


_warm_home_candidates_cache()


def _candidate_best_discount(c):
    # Mismo fix que el best_discount de adentro de fetch() (ver el
    # comentario grande ahí): .get(..., 0), nunca ["discount_percent"]
    # directo -- un juego gratis en Steam resuelto vía
    # epic_gog_steam_prices() llega sin esa clave.
    return max(
        (c[k].get("discount_percent", 0) for k in ("steam", "epic", "gog", "xbox", "playstation") if k in c),
        default=0,
    )


def _candidate_price_ars(c, rate):
    """
    Precio en ARS de un candidato, para el filtro de rango de precio.

    Esto asumía que el precio de Steam "ya viene en ARS" porque se pide
    con cc=ar -- resultó ser falso: Steam dejó de tener precios en pesos
    para Argentina y devuelve USD igual (se nota en toda la app: el
    precio de Steam siempre se muestra como "US$", nunca como "$" sin
    convertir). Con esa asunción, el filtro de precio comparaba el rango
    en ARS que tipea el usuario (ej. 0-500) contra un precio en USD (ej.
    14.99) -- como CASI cualquier precio en dólares cae dentro de ese
    rango, el filtro no filtraba casi nada. Ahora se mira el campo
    "currency" de cada oferta en vez de asumirlo por la tienda: si ya
    dice "ARS" se usa tal cual, si no, se convierte con la cotización.
    None si no se puede saber (no hay ninguna oferta con precio Y no hay
    cotización para convertir las que están en USD) -- esos candidatos no
    se descartan por el filtro de precio, para no esconder ofertas solo
    porque dolarapi.com esté caído.
    """

    def in_ars(price):
        if price is None or price.get("final_price") is None:
            return None
        if price.get("currency") == "ARS":
            return price["final_price"]
        if rate:
            return price["final_price"] * rate
        return None

    for k in ("steam", "epic", "gog", "xbox", "playstation"):
        if k in c:
            value = in_ars(c[k])
            if value is not None:
                return value
    return None


def _filter_candidates(candidates, stores=None, min_discount=0, price_min=None, price_max=None, rate=None):
    """
    Filtra el pool de candidatos por el sidebar de filtros. "stores" solo
    puede ser un subconjunto de {"steam","epic","gog","xbox"} -- Switch
    queda afuera a propósito: recién se sabe si un candidato está en la
    eShop DESPUÉS de consultarlo uno por uno contra DekuDeals (ver
    enrich() en route_home), que es justo lo que la paginación evita
    hacer para TODO el pool de una. Filtrar/contar por Switch acá
    implicaría consultar DekuDeals para cientos de candidatos en cada
    pedido, perdiendo la gracia de pedir solo la página que se está
    mostrando. Xbox sí entra acá como las otras tres: se conoce de
    antemano para TODO el pool (ver xbox_deals), sin costo extra por
    filtrar.
    """
    out = candidates
    if stores:
        wanted = set(stores) & {"steam", "epic", "gog", "xbox", "playstation"}
        if wanted:
            out = [c for c in out if wanted & set(c.keys())]
    if min_discount:
        out = [c for c in out if _candidate_best_discount(c) >= min_discount]
    if price_min is not None or price_max is not None:
        def in_range(c):
            price = _candidate_price_ars(c, rate)
            if price is None:
                return True
            if price_min is not None and price < price_min:
                return False
            if price_max is not None and price > price_max:
                return False
            return True

        out = [c for c in out if in_range(c)]
    return out


def home_facets(candidates, stores=None, min_discount=0, price_min=None, price_max=None):
    """
    Conteos para el sidebar de filtros ("Steam 782", "70% o más 205",
    etc.), calculados sobre el pool COMPLETO (antes de paginar). Cada
    conteo se calcula habiendo aplicado ya los OTROS filtros activos pero
    NO el propio -- así "Epic Games 216" responde "si además marcás Epic,
    te quedan 216", no un número fijo que ignora el resto de tus filtros.
    """
    rate = _usd_ars_rate()

    base_for_stores = _filter_candidates(
        candidates, stores=None, min_discount=min_discount,
        price_min=price_min, price_max=price_max, rate=rate,
    )
    store_counts = {
        store: sum(1 for c in base_for_stores if store in c)
        for store in ("steam", "epic", "gog", "xbox", "playstation")
    }

    base_for_discount = _filter_candidates(
        candidates, stores=stores, min_discount=0,
        price_min=price_min, price_max=price_max, rate=rate,
    )
    discount_counts = {
        str(tier): sum(1 for c in base_for_discount if _candidate_best_discount(c) >= tier)
        for tier in (50, 70, 90)
    }

    return {"stores": store_counts, "discount": discount_counts}


@app.get("/api/home")
def route_home():
    """
    Arma una comparación de entrada, sin que el usuario busque nada,
    combinando ofertas de Steam + Epic + GOG (ver home_candidates) y
    cruzándolas contra Nintendo AR.

    Paginado: ?page=1&page_size=12 (defaults). Cada página solo dispara
    requests a DekuDeals para los juegos de ESA página -- por eso pedir
    más es rápido aunque el pool total sea grande.

    ?sort=descuento (default) | precio | relevancia | mas_vendidas -- ver
    home_candidates. ?only_discounted=1 descarta los candidatos sin
    descuento activo (lo usa la sección "Destacados" del frontend).

    Filtros del sidebar (todos opcionales, se aplican ANTES de paginar):
    ?stores=steam,epic,gog -- solo candidatos presentes en alguna de esas
      tiendas (Switch no se puede filtrar acá, ver _filter_candidates).
    ?min_discount=50|70|90 -- descarta candidatos con menos descuento que
      eso en TODAS las tiendas donde aparecen.
    ?price_min=<ars>&price_max=<ars> -- precio final en pesos (Epic/GOG
      se convierten con la cotización del dólar tarjeta).
    ?switch_only=1 -- NO es un filtro real por Switch (seguimos sin poder
      hacer eso acá, ver arriba): descarta de entrada los candidatos sin
      steam_appid (los que llegaron solos por Xbox/PlayStation, ver
      _merge_xbox_into/_merge_ps_into), que NUNCA pueden estar en la eShop
      -- sin un steam_appid no hay con qué consultar DekuDeals. BUG REAL
      reportado por Nico (filtro de Switch se quedaba pegado en
      "cargando"): esos candidatos se agregan al FINAL del pool completo,
      así que al filtrar por Switch el scroll infinito tenía que paginar
      a través de varios cientos de ellos (Xbox solo ya son ~660) para
      encontrar el próximo juego que sí pudiera estar en Switch -- cada
      uno de esos tramos se veía, para el usuario, como si el filtro se
      hubiera trabado para siempre, aunque en teoría iba a terminar.
    """
    page = request.args.get("page", default=1, type=int)
    # Default más chico que antes: en Vercel (plan Hobby) cada función
    # tiene un tope de 10s, y cada juego acá dispara 2 requests externos
    # en paralelo (Nintendo + Epic/GOG) -- con menos juegos por página
    # hay más margen para no pasarse del límite.
    page_size = request.args.get("page_size", default=8, type=int)
    page = max(1, page)
    page_size = max(1, min(page_size, 50))
    sort = request.args.get("sort", default="descuento")
    if sort not in HOME_SORTS:
        sort = "descuento"
    only_discounted = request.args.get("only_discounted") == "1"

    stores_param = request.args.get("stores", default="")
    stores = [s for s in stores_param.split(",") if s] or None
    min_discount = request.args.get("min_discount", default=0, type=int) or 0
    price_min = request.args.get("price_min", type=float)
    price_max = request.args.get("price_max", type=float)
    switch_only = request.args.get("switch_only") == "1"

    full_candidates = home_candidates(sort, only_discounted=only_discounted)
    all_candidates = _filter_candidates(
        full_candidates,
        stores=stores,
        min_discount=min_discount,
        price_min=price_min,
        price_max=price_max,
        rate=_usd_ars_rate(),
    )
    if switch_only:
        # Ver el docstring de arriba (?switch_only=1) -- descarta acá,
        # ANTES de paginar, lo que ya se sabe que nunca va a poder estar
        # en Switch (sin steam_appid no hay con qué consultar DekuDeals).
        all_candidates = [c for c in all_candidates if c["steam_appid"]]
    facets = home_facets(
        full_candidates,
        stores=stores,
        min_discount=min_discount,
        price_min=price_min,
        price_max=price_max,
    )
    start = (page - 1) * page_size
    page_candidates = all_candidates[start : start + page_size]

    # OJO -- se probó acá (y en /api/compare) hacer una búsqueda en vivo
    # contra CheapShark por cada candidato de cada página (pc_store_exact_
    # matches, el mismo mecanismo que usa la búsqueda manual) para que un
    # juego en Epic/GOG a precio NORMAL (sin descuento propio ahí) también
    # saliera en la grilla principal, no solo buscándolo a mano. SE SACÓ:
    # con 8 candidatos por página y hasta 10 en paralelo (ThreadPoolExecutor
    # más abajo), cada scroll/carga de la home disparaba una ráfaga de
    # pedidos a CheapShark -- multiplicado por cualquier visitante mirando
    # la home, eso terminó baneando a Nico de CheapShark ("me estan
    # baneando todo el rato"), mismo tipo de problema que ya había pasado
    # con Steam/Akamai. En vez de pedirlo por página (automático, sin
    # límite real de frecuencia), ese chequeo de Epic/GOG ahora se hace
    # SOLO para los juegos del top 100 global de Steam, una vez por día,
    # como parte del mismo refresco secuencial-con-pausas que ya existe
    # para sus precios (ver _refresh_steam_global_deals) -- de ahí sale
    # directo en las claves "epic"/"gog" del candidato, igual que antes.
    #
    # Mismo criterio para Steam: antes acá se llamaba a steam_price(appid)
    # EN VIVO para cualquier candidato sin "steam" ya mergeado (los que
    # vienen solo de Epic/GOG) -- con hasta 10 en paralelo por página, eso
    # terminó baneando a Nico de Steam/Akamai otra vez (reportado como
    # "un monton de juegos... con no disponible en steam cuando si estan
    # disponibles" después de tener la app abierta un rato). Ahora eso se
    # resuelve DENTRO de home_candidates, secuencial-con-pausas, una vez
    # por hora (ver el comentario grande ahí, junto a with_appid) -- acá
    # ya no se le pega a Steam para nada, solo se lee lo que haya quedado.
    def enrich(c):
        appid = c["steam_appid"]
        sp = c.get("steam") or {"available": False}
        # Candidatos agregados SOLO por Xbox (ver _merge_xbox_into) no
        # tienen steam_appid -- no hay forma de saber si están en la
        # eShop de Switch sin ese ID, así que ni se le pega a DekuDeals
        # (pedido desperdiciado, 404 seguro). Tampoco tiene sentido armar
        # un "steam_result" para ellos: no hay appid con el que enlazar a
        # la tienda de Steam ni con el que armar el identificador de
        # "seguir" de tipo steam -- esos juegos llegan al frontend
        # enteros por "pc_results" más abajo (mismo mecanismo que ya usan
        # Epic/GOG), con su propio "seguir" por game_id de Xbox.
        deku = dekudeals_check(appid) if appid else {"on_switch": False}
        return c, sp, deku

    matched = []
    not_on_switch = []
    pc_results = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        for i, (c, sp, deku) in enumerate(pool.map(enrich, page_candidates)):
            if c["steam_appid"]:
                steam_result = {
                    "appid": c["steam_appid"],
                    "name": c["name"],
                    "tiny_image": c["tiny_image"],
                    "price": sp,
                    # Posición real de este candidato en la lista COMPLETA (no
                    # solo en esta página) -- ver el comentario largo en
                    # buildGameGroups del frontend (CompareTable.tsx): "matched"
                    # y "steam_only" son dos arrays separados, y como el scroll
                    # infinito los va acumulando por separado página a página,
                    # el frontend no tenía forma de saber el orden real entre
                    # ambos -- terminaba mostrando TODO "matched" antes que TODO
                    # "steam_only" sin importar de qué página vino cada uno (un
                    # juego con versión de Switch cargado en la página 3 se
                    # colaba arriba de uno sin Switch que ya se había mostrado
                    # desde la página 1). Con este índice, el front puede
                    # fusionar los dos arrays respetando el orden real.
                    "order": start + i,
                }
                if deku.get("on_switch"):
                    matched.append(
                        {"name": c["name"], "steam": steam_result, "nintendo": deku}
                    )
                else:
                    not_on_switch.append(steam_result)

            if "epic" in c:
                pc_results.append({**c["epic"], "name": c["name"]})
            if "gog" in c:
                pc_results.append({**c["gog"], "name": c["name"]})
            if "xbox" in c:
                pc_results.append({**c["xbox"], "name": c["name"]})
            if "playstation" in c:
                pc_results.append({**c["playstation"], "name": c["name"]})

    return jsonify(
        {
            "query": " ",
            "sort": sort,
            "matched": matched,
            "steam_only": not_on_switch,
            "pc_stores": pc_results,
            "page": page,
            "page_size": page_size,
            "total": len(all_candidates),
            "has_more": start + page_size < len(all_candidates),
            "facets": facets,
        }
    )



@app.post("/api/watchlist/deals")
def route_watchlist_deals():
    """
    Chequea una lista de juegos -- de Steam contra Steam+Nintendo+Epic/GOG,
    de Nintendo-directo contra Nintendo+Epic/GOG (no tiene Steam) -- y
    devuelve el precio actual de CADA uno (esté o no en oferta). Antes
    esto filtraba y solo devolvía los que tenían descuento activo; ahora
    devuelve todos, para que la pestaña de seguidos pueda mostrar las
    mismas tarjetas que la pantalla principal (con precio normal
    incluido), no solo un listado aparte de ofertas.

    También se suma la comparación contra las OTRAS tiendas para cada
    item, sea cual sea la tienda por la que lo seguiste: un seguido por
    Steam o Nintendo-directo se cruza contra Epic/GOG buscando por
    nombre vía CheapShark; un seguido DIRECTO en Epic/GOG (kind "pc") se
    consulta por su gameID (no por nombre) y, como CheapShark además
    sabe su steamAppID cuando existe, también se cruza con Steam/Switch
    -- así la watchlist compara siempre entre TODAS las tiendas, no solo
    la que usaste para seguir ese juego.

    Sin estado en el server: la watchlist vive en el navegador de cada
    visitante (localStorage), así que acá no se guarda ni se lee nada
    de disco -- el cliente manda la lista entera en el body cada vez.

    Body JSON: {"items": [{"id": "...", "kind": "steam"|"nintendo"|"pc",
                            "appid"?: number, "slug"?: string,
                            "store"?: "epic"|"gog"|"xbox"|"playstation", "gameId"?: string,
                            "name": string}, ...]}
    """
    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return jsonify({"error": "'items' debe ser una lista"}), 400

    def process_one(item):
        kind = item.get("kind")

        if kind == "steam":
            appid = item.get("appid")
            if not appid:
                return None
            sp = steam_price(appid)
            deku = dekudeals_check(appid)
            pc_stores = pc_store_search(item.get("name", ""), limit=3)
            return {
                "id": item.get("id"),
                "name": item.get("name"),
                "steam": sp,
                "nintendo": deku,
                "pc_stores": pc_stores,
            }

        if kind == "nintendo":
            slug = item.get("slug")
            if not slug:
                return None
            deku = dekudeals_item(slug)
            pc_stores = pc_store_search(item.get("name", ""), limit=3)

            # Un exclusivo de Nintendo seguido directo por su slug de
            # DekuDeals puede, en realidad, SÍ estar en Steam (DekuDeals
            # solo nos dice "existe en Switch", no si es exclusivo de
            # verdad) -- buscamos por nombre en Steam para poder
            # mostrarlo también ahí, igual que con Epic/GOG. Nos
            # quedamos con el primer resultado (ya viene ordenado por
            # relevancia por la propia búsqueda de Steam).
            steam_matches = steam_search(item.get("name", ""))
            steam_appid = steam_matches[0]["appid"] if steam_matches else None
            sp = steam_price(steam_appid) if steam_appid else None

            return {
                "id": item.get("id"),
                "name": item.get("name"),
                "steam": sp,
                "nintendo": deku,
                "pc_stores": pc_stores,
                "steam_appid": steam_appid,
            }

        if kind == "pc" and item.get("store") == "xbox":
            # Seguido DIRECTO desde una fila de Xbox -- distinto del resto
            # de "pc" (Epic/GOG), que se identifican por su gameID de
            # CheapShark. Xbox no pasa por CheapShark: game_id acá es el
            # productId propio de xbox_deals(), así que se busca ahí
            # mismo (listado completo ya en memoria, sin pedido nuevo) en
            # vez de cheapshark_game_deal. El cruce contra las demás
            # tiendas (Steam/Switch/Epic/GOG) es por NOMBRE, igual que en
            # todos los demás cruces con Xbox de la app (no hay forma de
            # hacerlo por ID, Xbox no comparte identificador con nadie más).
            game_id = item.get("gameId")
            xbox_deal = next(
                (d for d in xbox_deals() if d["game_id"] == game_id), None
            )
            if not game_id or not xbox_deal:
                return None

            name = item.get("name", "")
            steam_matches = steam_search(name)
            steam_appid = steam_matches[0]["appid"] if steam_matches else None
            sp = steam_price(steam_appid) if steam_appid else None
            deku = dekudeals_check(steam_appid) if steam_appid else {"on_switch": False}
            other_stores = pc_store_exact_matches(name, limit=5)

            return {
                "id": item.get("id"),
                "name": item.get("name"),
                "steam": sp,
                "nintendo": deku,
                "pc": xbox_deal,
                "pc_stores": other_stores,
                "steam_appid": steam_appid,
            }

        if kind == "pc" and item.get("store") == "playstation":
            # Mismo mecanismo que la rama de Xbox de arriba -- PlayStation
            # tampoco pasa por CheapShark, game_id acá es el id propio de
            # playstation_deals().
            game_id = item.get("gameId")
            ps_deal = next(
                (d for d in playstation_deals() if d["game_id"] == game_id), None
            )
            if not game_id or not ps_deal:
                return None

            name = item.get("name", "")
            steam_matches = steam_search(name)
            steam_appid = steam_matches[0]["appid"] if steam_matches else None
            sp = steam_price(steam_appid) if steam_appid else None
            deku = dekudeals_check(steam_appid) if steam_appid else {"on_switch": False}
            other_stores = pc_store_exact_matches(name, limit=5)

            return {
                "id": item.get("id"),
                "name": item.get("name"),
                "steam": sp,
                "nintendo": deku,
                "pc": ps_deal,
                "pc_stores": other_stores,
                "steam_appid": steam_appid,
            }

        if kind == "pc":
            game_id = item.get("gameId")
            store = item.get("store")
            store_name = {"epic": "Epic Games Store", "gog": "GOG"}.get(store)
            if not game_id or not store_name:
                return None

            pc_deal = cheapshark_game_deal(game_id, store_name)
            # CheapShark ya sabe (por el mismo gameID) si este juego
            # también está en la OTRA tienda PC y cuál es su steamAppID
            # -- con eso se arma la comparación contra Steam/Switch y la
            # otra tienda, igual que pasa cuando seguís algo por Steam.
            other_stores, steam_appid = cheapshark_game_pc_stores(game_id)
            other_stores = [p for p in other_stores if p["store"] != store_name]

            sp = steam_price(steam_appid) if steam_appid else None
            deku = dekudeals_check(steam_appid) if steam_appid else {"on_switch": False}

            return {
                "id": item.get("id"),
                "name": item.get("name"),
                "steam": sp,
                "nintendo": deku,
                "pc": pc_deal,
                "pc_stores": other_stores,
                "steam_appid": steam_appid,
            }

        return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = [r for r in pool.map(process_one, items) if r is not None]

    return jsonify(results)


if __name__ == "__main__":
    app.run(debug=True, port=5000)

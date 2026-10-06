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

Xbox queda afuera por ahora: como Nintendo, no tiene API pública, así
que agregarlo requeriría el mismo tipo de trabajo manual/frágil que
hicimos para Nintendo.

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
"""

import json
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

    def cached_page(page_number):
        return cached(
            f"cheapshark_browse:{store_name}:{page_number}",
            lambda: fetch_page(page_number),
        )

    # Las páginas se piden todas en paralelo (son independientes entre sí)
    # en vez de una por una -- con 4+ páginas por tienda, pedirlas en
    # serie sumaba varios segundos a la primera carga (la que no tiene
    # nada en cache todavía). pool.map devuelve los resultados en el
    # mismo orden que se pidieron, así que arriba abajo sigue siendo el
    # orden real de páginas aunque las respuestas lleguen desordenadas.
    with ThreadPoolExecutor(max_workers=min(pages_needed, 10)) as pool:
        pages = list(pool.map(cached_page, range(pages_needed)))

    all_deals = []
    for page_deals in pages:
        if not page_deals:
            break  # CheapShark ya no tenía más páginas para esta tienda
        all_deals.extend(page_deals)
        if len(page_deals) < CHEAPSHARK_PAGE_SIZE:
            break  # página incompleta: era la última

    return all_deals[:limit]


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
        pc = pc_store_exact_matches(s["name"], limit=10)
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
    matched, not_on_switch, _ = compare_steam_results(steam_results)

    # Búsqueda directa en Nintendo, independiente de si el juego está en
    # Steam. Evita duplicar lo que ya salió matcheado desde Steam.
    already_matched_names = {m["name"].strip().lower() for m in matched}
    nintendo_direct = [
        d
        for d in dekudeals_search(q)
        if d["name"].strip().lower() not in already_matched_names
    ]

    return jsonify(
        {
            "query": q,
            "matched": matched,
            "steam_only": not_on_switch,
            "pc_stores": pc_store_search(q),
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

        with_appid = [c for c in candidates.values() if c["steam_appid"]]

        def best_discount(c):
            return max(
                (c[k]["discount_percent"] for k in ("steam", "epic", "gog") if k in c),
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
            # (steam_global_top_sellers_ranked(), ver más arriba -- OJO,
            # NO es steam_top_sellers(), esa es una lista chica de la
            # portada vieja que resultó ser un ranking distinto). Se
            # compara por steam_appid (no por nombre: más preciso, sin
            # depender de que el nombre venga escrito igual en las dos
            # fuentes). Los candidatos que están en ese ranking van
            # primero, EN ESE ORDEN; todo lo demás queda después, en el
            # mismo orden relativo en que ya venía (sort() de Python es
            # estable, así que los que empatan en "no está en el ranking"
            # no se reordenan entre sí) -- sigue sin haber un criterio
            # propio por encima para ESE resto, solo se adelanta lo que sí
            # tiene un dato real de popularidad.
            top_sellers_rank = {
                appid: i for i, appid in enumerate(steam_global_top_sellers_ranked())
            }
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

    return cached(f"home_candidates:{sort}:{int(only_discounted)}", fetch)


def _candidate_best_discount(c):
    return max(
        (c[k]["discount_percent"] for k in ("steam", "epic", "gog") if k in c),
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

    for k in ("steam", "epic", "gog"):
        if k in c:
            value = in_ars(c[k])
            if value is not None:
                return value
    return None


def _filter_candidates(candidates, stores=None, min_discount=0, price_min=None, price_max=None, rate=None):
    """
    Filtra el pool de candidatos por el sidebar de filtros. "stores" solo
    puede ser un subconjunto de {"steam","epic","gog"} -- Switch queda
    afuera a propósito: recién se sabe si un candidato está en la eShop
    DESPUÉS de consultarlo uno por uno contra DekuDeals (ver enrich() en
    route_home), que es justo lo que la paginación evita hacer para TODO
    el pool de una. Filtrar/contar por Switch acá implicaría consultar
    DekuDeals para cientos de candidatos en cada pedido, perdiendo la
    gracia de pedir solo la página que se está mostrando.
    """
    out = candidates
    if stores:
        wanted = set(stores) & {"steam", "epic", "gog"}
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
        for store in ("steam", "epic", "gog")
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

    full_candidates = home_candidates(sort, only_discounted=only_discounted)
    all_candidates = _filter_candidates(
        full_candidates,
        stores=stores,
        min_discount=min_discount,
        price_min=price_min,
        price_max=price_max,
        rate=_usd_ars_rate(),
    )
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
    def enrich(c):
        appid = c["steam_appid"]
        sp = c.get("steam") or steam_price(appid)
        deku = dekudeals_check(appid)
        return c, sp, deku

    matched = []
    not_on_switch = []
    pc_results = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        for i, (c, sp, deku) in enumerate(pool.map(enrich, page_candidates)):
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
                            "store"?: "epic"|"gog", "gameId"?: string,
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

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
import re
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


def cached(key, fetch_fn):
    now = time.time()
    if key in _CACHE:
        ts, data = _CACHE[key]
        if now - ts < CACHE_TTL_SECONDS:
            return data
    data = fetch_fn()
    _CACHE[key] = (now, data)
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


def _steam_items_from_category(category):
    items = _steam_featured_categories().get(category, {}).get("items", [])
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
    ]


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


def cheapshark_browse_deals(store_name, limit=40):
    """
    Trae las mejores ofertas ACTUALES de una tienda (sin buscar nada
    puntual), ordenadas por descuento. CheapShark además devuelve
    'steamAppID' en cada oferta cuando ese juego también existe en
    Steam -- lo usamos para poder cruzarlo con Nintendo (que solo se
    puede consultar por ese ID) aunque la oferta en sí sea de Epic/GOG.
    """
    store_ids = cheapshark_store_ids()
    if store_name not in store_ids:
        return []
    store_id = store_ids[store_name]

    def fetch():
        try:
            r = requests.get(
                CHEAPSHARK_DEALS_URL,
                params={
                    "storeID": store_id,
                    "sortBy": "Savings",
                    "pageSize": limit,
                    "onSale": 1,
                },
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            deals = r.json()
        except (requests.RequestException, ValueError):
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
        ]

    return cached(f"cheapshark_browse:{store_name}:{limit}", fetch)


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
        pc = pc_store_search(s["name"], limit=3)
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


HOME_SORTS = ("destacadas", "mas_vendidas")


def home_candidates(sort="destacadas"):
    """
    Pool combinado de candidatos para el feed de inicio, juntando TRES
    fuentes independientes (no solo Steam): Steam, Epic y GOG (estas dos
    últimas navegadas directo por descuento en CheapShark, sin buscar
    nada puntual). Se deduplica por nombre -- si el mismo juego aparece
    en más de una tienda, se completa en un solo candidato con los
    precios que se van encontrando.

    SIN reordenar nada acá (se probaron dos criterios propios -- primero
    por descuento, después por precio de lista -- y en los dos casos el
    usuario prefirió sacarlos): el orden final es el orden en que cada
    fuente va entregando sus resultados. Primero Steam (specials o
    top_sellers según "sort", tal cual los devuelve su API, sin tocar);
    los candidatos de Epic/GOG que NO están también en la lista de Steam
    se agregan al final, en el orden en que los va dando CheapShark.

    sort="destacadas" (default): la fuente de Steam son sus OFERTAS
    actuales (categoría "specials").

    sort="mas_vendidas": la fuente de Steam son sus MÁS VENDIDOS ahora
    mismo (categoría "top_sellers" -- el ranking real de Valve).

    Solo se conservan candidatos con steam_appid conocido: es el ID que
    usamos para todo (consultar Nintendo, armar la watchlist), así que
    un juego sin Steam ni relación con Steam no se puede seguir en esta
    app tal como está armada. Sigue sumando bastante: CheapShark expone
    el steamAppID de un montón de ofertas de Epic/GOG aunque la oferta
    en sí sea de esa tienda.
    """
    if sort not in HOME_SORTS:
        sort = "destacadas"

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

        for d in cheapshark_browse_deals("Epic Games Store", limit=60):
            upsert(d["name"], d["thumb"], d.get("steam_appid"), epic=d)

        for d in cheapshark_browse_deals("GOG", limit=60):
            upsert(d["name"], d["thumb"], d.get("steam_appid"), gog=d)

        # candidates.values() ya viene en orden de inserción (dict de
        # Python 3.7+): primero todo lo de Steam, en el orden en que la
        # API lo entregó, y después lo que sumó cada vuelta de Epic/GOG
        # -- no se reordena nada acá.
        return [c for c in candidates.values() if c["steam_appid"]]

    return cached(f"home_candidates:{sort}", fetch)


@app.get("/api/home")
def route_home():
    """
    Arma una comparación de entrada, sin que el usuario busque nada,
    combinando ofertas de Steam + Epic + GOG (ver home_candidates) y
    cruzándolas contra Nintendo AR.

    Paginado: ?page=1&page_size=12 (defaults). Cada página solo dispara
    requests a DekuDeals para los juegos de ESA página -- por eso pedir
    más es rápido aunque el pool total sea grande.

    ?sort=destacadas (default) o ?sort=mas_vendidas -- ver home_candidates.
    """
    page = request.args.get("page", default=1, type=int)
    # Default más chico que antes: en Vercel (plan Hobby) cada función
    # tiene un tope de 10s, y cada juego acá dispara 2 requests externos
    # en paralelo (Nintendo + Epic/GOG) -- con menos juegos por página
    # hay más margen para no pasarse del límite.
    page_size = request.args.get("page_size", default=8, type=int)
    page = max(1, page)
    page_size = max(1, min(page_size, 50))
    sort = request.args.get("sort", default="destacadas")
    if sort not in HOME_SORTS:
        sort = "destacadas"

    all_candidates = home_candidates(sort)
    start = (page - 1) * page_size
    page_candidates = all_candidates[start : start + page_size]

    def enrich(c):
        appid = c["steam_appid"]
        sp = c.get("steam") or steam_price(appid)
        deku = dekudeals_check(appid)
        return c, sp, deku

    matched = []
    not_on_switch = []
    pc_results = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        for c, sp, deku in pool.map(enrich, page_candidates):
            steam_result = {
                "appid": c["steam_appid"],
                "name": c["name"],
                "tiny_image": c["tiny_image"],
                "price": sp,
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

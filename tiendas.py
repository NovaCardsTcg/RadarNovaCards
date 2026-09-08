# -*- coding: utf-8 -*-
"""Adaptadores de tienda del radar Pokemon.

Cada adaptador devuelve una lista de Producto. La regla es que ninguno puede
tumbar la ejecucion: si una tienda cambia el HTML o bloquea la peticion, se
lanza TiendaCaida, el orquestador sigue con las demas y lo reporta por Telegram.

Hay dos familias de fuente, y la diferencia importa:

  - BUSCADOR (amazon, eci, mediamarkt): devuelve precio y stock, asi que puede
    disparar los tres avisos (nuevo, vuelve el stock, baja el precio).
  - SITEMAP (game, carrefour, pokemoncenter): el buscador de esas webs esta
    detras de Cloudflare/Akamai o se pinta por JavaScript, pero el sitemap es
    publico y se sirve sin pelea. Solo da la URL, asi que solo puede disparar
    "producto nuevo". El titulo y el precio se sacan despues de la ficha, y
    solo para los productos nuevos, que son pocos.
"""

from __future__ import annotations

import json
import random
import re
import time
import unicodedata
from dataclasses import dataclass
from html import unescape
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

# curl_cffi imita la huella TLS/HTTP2 de un Chrome real. requests, por muy bien
# que le pongas las cabeceras, tiene un apreton de manos SSL reconocible, y los
# cortafuegos de tipo Akamai o Cloudflare lo puntuan como bot. Es una senal
# distinta de la IP y suma con ella: desde casa daba igual porque la IP
# residencial ya aprobaba, pero desde un centro de datos cada punto cuenta.
#
# Va con reserva a proposito: si la libreria no esta instalada, el radar
# funciona igual con requests en vez de romperse.
try:
    from curl_cffi import requests as navegador
    from curl_cffi.requests.exceptions import RequestException as _ErrorCffi
    IMPERSONA = "chrome131"
    ERRORES_RED = (requests.RequestException, _ErrorCffi)
except ImportError:
    navegador = requests
    IMPERSONA = None
    ERRORES_RED = (requests.RequestException,)

TIMEOUT = 25
REINTENTOS = 3

# Se rota entre versiones creibles de Chrome. No es evasion sofisticada: es
# evitar que la cabecera por defecto de requests marque la peticion como bot.
AGENTES = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# El Corte Ingles devuelve 403 sin el juego completo de Sec-Fetch-* y sec-ch-ua.
# Comprobado: con cabeceras minimas responde 403; con estas, 200.
CABECERAS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-mobile": "?0",
}


class TiendaCaida(Exception):
    """La tienda no responde o ha cambiado de forma irreconocible."""


@dataclass
class Producto:
    tienda: str
    pid: str
    titulo: str
    url: str
    precio: float | None = None
    disponible: bool | None = None
    imagen: str | None = None
    # Se marca cuando no se ha podido contrastar el precio con la ficha.
    sin_confirmar: bool = False

    @property
    def clave(self) -> str:
        return f"{self.tienda}:{self.pid}"


# Pistas de que la respuesta es un muro antibot y no la pagina pedida. Sirven
# para que el aviso de tienda caida diga QUE pasa y no solo que fallo.
MARCADORES = {
    "cloudflare": ("cf-browser-verification", "cf-chl", "attention required",
                   "checking your browser"),
    "akamai": ("akamaighost", "reference #", "access denied"),
    "amazon-bot": ("api-services-support@amazon.com", "introduce los caracteres",
                   "continuar comprando", "continue shopping"),
    "generico": ("unusual traffic", "bot detected", "are you a robot"),
    # bm-verify solo se ve si el interstitial no se ha podido atravesar.
    "amazon-verify": ("bm-verify",),
}


def _marcador(texto: str) -> str:
    t = texto[:20000].lower()
    for nombre, claves in MARCADORES.items():
        if any(k in t for k in claves):
            return nombre
    return ""


def _sesion(portada: str | None = None) -> requests.Session:
    """Sesion con cabeceras de navegador y, si se pide, cookies de la portada.

    Lo de la portada no es adorno. Amazon, El Corte Ingles, MediaMarkt y
    Carrefour rechazan con 403 la peticion que entra directa a una URL de
    busqueda sin haber pasado por la web: no hay cookie de sesion y el
    Sec-Fetch-Site dice que vienes de ningun sitio. Pidiendo antes la portada
    se recogen esas cookies y la busqueda ya va como navegacion interna.

    Ayuda, pero no hace milagros: si el bloqueo es por reputacion de IP (que es
    lo que le pasa a los runners de GitHub, que salen por rangos de centro de
    datos), esto no lo arregla.
    """
    if IMPERSONA:
        # Imitando a Chrome, las cabeceras de identidad (User-Agent, sec-ch-ua)
        # las pone la libreria, coherentes con la huella TLS que manda. Ponerlas
        # a mano seria contraproducente: un User-Agent de Windows con una huella
        # TLS de Mac es justo la incoherencia que buscan los cortafuegos.
        s = navegador.Session(impersonate=IMPERSONA)
        s.headers.update({k: v for k, v in CABECERAS.items()
                          if not k.lower().startswith(("sec-ch-ua", "user-agent"))})
    else:
        s = requests.Session()
        s.headers.update(CABECERAS)
        s.headers["User-Agent"] = random.choice(AGENTES)

    if portada:
        try:
            s.get(portada, timeout=TIMEOUT)
            s.headers["Referer"] = portada
            s.headers["Sec-Fetch-Site"] = "same-origin"
            time.sleep(random.uniform(0.8, 1.8))
        except ERRORES_RED:
            pass  # sin cookies se intenta igual
    return s


def _sigue_interstitial(s, r, **kw):
    """Atraviesa la pagina de verificacion de Amazon.

    Cuando Amazon duda de ti no responde 403: devuelve un 200 de 2 KB con un
    <meta http-equiv="refresh"> que apunta a la misma URL con un token
    bm-verify pegado, y a los 5 segundos el navegador se recarga solo. Un
    script que no lo siga se cree que la busqueda no ha dado resultados.

    Basta con hacer lo que haria el navegador: esperar y pedir el destino.
    """
    if len(r.text) > 20000:
        return r
    m = re.search(r'http-equiv="refresh"[^>]*content="\s*(\d+)\s*;\s*URL=\'([^\']+)\'',
                  r.text, re.I)
    if not m:
        return r
    espera = min(int(m.group(1)), 10)
    destino = unescape(m.group(2))
    if destino.startswith("/"):
        destino = "https://" + urlparse(r.url).netloc + destino
    time.sleep(espera)
    try:
        return s.get(destino, timeout=TIMEOUT, **kw)
    except ERRORES_RED:
        return r


def _get(s: requests.Session, url: str, **kw) -> requests.Response:
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = s.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return _sigue_interstitial(s, r, **kw)
            marca = _marcador(r.text)
            ultimo = "HTTP %s%s" % (r.status_code, " [%s]" % marca if marca else "")
            # 403/429/503 suele ser antibot: cambiar de agente y esperar mas.
            if r.status_code in (403, 429, 503):
                if not IMPERSONA:
                    s.headers["User-Agent"] = random.choice(AGENTES)
                time.sleep(2 + intento * 3)
                continue
        except ERRORES_RED as e:
            ultimo = type(e).__name__
        time.sleep(1 + intento * 2)
    raise TiendaCaida("%s -> %s" % (url[:70], ultimo))


def _precio(texto: str) -> float | None:
    """'1.234,56 EUR' -> 1234.56. None si no hay nada parseable.

    Admite apostrofo ademas de coma porque GAME parte el precio en dos spans y
    el texto sale como "69 '99 €". Sin esto se leia 69 en vez de 69,99, que en
    un radar de bajadas de precio no es un detalle menor.
    """
    if not texto:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+)\s*[,'](\d{2})\b", texto)
    if m:
        return float(m.group(1).replace(".", "") + "." + m.group(2))
    m = re.search(r"(\d+(?:\.\d+)?)", texto.replace(".", ""))
    return float(m.group(1)) if m else None


def normaliza(t: str) -> str:
    """Minusculas y sin acentos, para comparar sin sorpresas con los acentos."""
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# --------------------------------------------------------------------------
# Buscadores: dan precio y stock
#
# Los tres siguen la misma regla para decidir si la tienda esta caida: que una
# palabra clave concreta no devuelva nada NO es un fallo (MediaMarkt, por
# ejemplo, no tiene ni un resultado para "pokemon jcc"). Solo se considera
# caida si no sale nada con ninguna de las palabras: eso ya si huele a bloqueo
# o a cambio de maquetacion.
# --------------------------------------------------------------------------

# Identificadores de vendedor de Amazon, sacados de las facetas del panel
# lateral del propio buscador (los enlaces "p_6:..." de la columna Vendedor).
VENDEDORES_AMAZON = {
    "amazon.es": "A1AT7YVPFBWXBL",
    "amazon.uk": "A2EL6K6KDM9FO1",
    "amazon.us": "A8ZZTUQ8GZK8C",
}


def amazon(palabras: list[str], paginas: int = 2,
           vendedores: list[str] | None = None) -> list[Producto]:
    """Busqueda en Amazon.es, opcionalmente restringida a ciertos vendedores.

    Filtrar por vendedor se hace con la faceta del propio buscador de Amazon
    (rh=p_6:<id>), no adivinando el vendedor desde la ficha: el bloque de la
    caja de compra cambia de maquetacion segun el producto y no es de fiar.
    Buscando "pokemon cartas", el filtro de Amazon.es deja 18 resultados de 48.
    """
    s = _sesion("https://www.amazon.es/")
    out: dict[str, Producto] = {}
    pista = ""
    filtro = ""
    if vendedores:
        ids = [VENDEDORES_AMAZON.get(v, v) for v in vendedores]
        filtro = "&rh=" + quote_plus("p_6:" + "|".join(ids))
    for palabra in palabras:
        for pag in range(1, paginas + 1):
            # s=date-desc-rank es el orden "novedades": lo recien listado sale arriba.
            url = ("https://www.amazon.es/s?k=%s&s=date-desc-rank&page=%d%s"
                   % (quote_plus(palabra), pag, filtro))
            r = _get(s, url)
            # Amazon casi nunca responde 403: cuando no le gustas devuelve un 200
            # con una pagina de interstitial. Por eso hay que mirar el contenido.
            marca = _marcador(r.text)
            if marca:
                raise TiendaCaida("muro antibot de Amazon [%s]" % marca)
            pista = "200 pero sin tarjetas, %d KB" % (len(r.text) // 1024)
            sopa = BeautifulSoup(r.text, "lxml")
            tarjetas = sopa.select('div[data-component-type="s-search-result"]')
            for c in tarjetas:
                asin = c.get("data-asin")
                if not asin or len(asin) != 10:
                    continue
                h2 = c.select_one("h2")
                titulo = h2.get_text(" ", strip=True) if h2 else ""
                if not titulo:
                    continue
                off = c.select_one("span.a-price span.a-offscreen")
                precio = _precio(off.get_text(strip=True)) if off else None
                agotado = "no disponible" in c.get_text(" ", strip=True).lower()
                img = c.select_one("img.s-image")
                out[asin] = Producto(
                    tienda="amazon", pid=asin, titulo=titulo,
                    url="https://www.amazon.es/dp/" + asin,
                    precio=precio,
                    disponible=(precio is not None and not agotado),
                    imagen=img.get("src") if img else None,
                )
            time.sleep(random.uniform(1.0, 2.5))
    if not out:
        raise TiendaCaida("Amazon sin tarjetas para ninguna palabra (%s)" % pista)
    return list(out.values())


def eci(palabras: list[str], paginas: int = 2) -> list[Producto]:
    """El Corte Ingles.

    El precio fiable no esta en el HTML visible sino en el datalayer que la web
    embebe en window.__MOONSHINE_STATE__. La URL de la ficha si esta en el HTML
    (atributo data-url). Se cruzan los dos por la referencia (A200971037).
    """
    s = _sesion("https://www.elcorteingles.es/")
    out: dict[str, Producto] = {}
    for palabra in palabras:
        for pag in range(1, paginas + 1):
            url = "https://www.elcorteingles.es/search/?s=" + quote_plus(palabra)
            if pag > 1:
                url += "&page=%d" % pag
            r = _get(s, url, allow_redirects=True)
            sopa = BeautifulSoup(r.text, "lxml")

            datos = {}
            for prod in _datalayer_eci(r.text):
                if prod.get("code_a"):
                    datos[prod["code_a"]] = prod

            arts = sopa.select('article[id^="product-A"]')
            for a in arts:
                ref = a.get("id", "").replace("product-", "")
                if not ref:
                    continue
                enlace = a.select_one("[data-url]")
                ruta = enlace.get("data-url") if enlace else ""
                d = datos.get(ref, {})
                titulo = d.get("name") or a.get("aria-label") or ref
                precio = None
                if isinstance(d.get("price"), dict):
                    precio = d["price"].get("f_price")
                badges = d.get("badges") or {}
                img = a.select_one("img[src]")
                out[ref] = Producto(
                    tienda="eci", pid=ref, titulo=titulo,
                    url=("https://www.elcorteingles.es" + ruta) if ruta
                        else ("https://www.elcorteingles.es/search/?s=" + ref),
                    precio=precio,
                    # coming_soon = anunciado pero todavia no comprable.
                    disponible=(precio is not None and not badges.get("coming_soon")),
                    imagen=img.get("src") if img else None,
                )
            time.sleep(random.uniform(1.0, 2.5))
    if not out:
        raise TiendaCaida("ECI no devuelve articulos para ninguna palabra clave")
    return list(out.values())


def _datalayer_eci(html: str) -> list[dict]:
    """Recorta el blob JSON de ECI por balance de llaves y saca los productos."""
    i = html.find("window.__MOONSHINE_STATE__")
    if i < 0:
        return []
    ini = html.find("{", i)
    if ini < 0:
        return []
    prof, fin = 0, None
    for p in range(ini, len(html)):
        if html[p] == "{":
            prof += 1
        elif html[p] == "}":
            prof -= 1
            if prof == 0:
                fin = p + 1
                break
    if not fin:
        return []
    try:
        estado = json.loads(html[ini:fin])
    except json.JSONDecodeError:
        return []

    encontrados: list[dict] = []

    def recorre(o):
        if isinstance(o, dict):
            if o.get("code_a") and o.get("name"):
                encontrados.append(o)
            for v in o.values():
                recorre(v)
        elif isinstance(o, list):
            for v in o:
                recorre(v)

    recorre(estado)
    return encontrados


def mediamarkt(palabras: list[str], paginas: int = 2) -> list[Producto]:
    s = _sesion("https://www.mediamarkt.es/")
    out: dict[str, Producto] = {}
    for palabra in palabras:
        for pag in range(1, paginas + 1):
            url = ("https://www.mediamarkt.es/es/search.html?query=%s&page=%d"
                   % (quote_plus(palabra), pag))
            r = _get(s, url)
            sopa = BeautifulSoup(r.text, "lxml")
            tarjetas = sopa.select('[data-test="mms-product-card"]')
            for c in tarjetas:
                a = c.select_one('a[href*="/product/"]')
                if not a:
                    continue
                href = a.get("href", "")
                m = re.search(r"-(\d+)\.html", href)
                if not m:
                    continue
                pid = m.group(1)
                t = c.select_one('[data-test="product-title"]')
                titulo = t.get_text(" ", strip=True) if t else ""
                if not titulo:
                    continue
                # El bloque trae precio tachado y precio final; el ultimo es el vigente.
                pe = c.select_one('[data-test="mms-price"]')
                precio = None
                if pe:
                    importes = re.findall(r"\d[\d.]*,\d{2}", pe.get_text(" ", strip=True))
                    if importes:
                        precio = _precio(importes[-1])
                # data-test="mms-cofr-delivery_AVAILABLE" marca el estado de envio.
                disp = bool(c.select_one('[data-test*="delivery_AVAILABLE"]'))
                img = c.select_one('[data-test="product-image"] img') or c.select_one("img")
                out[pid] = Producto(
                    tienda="mediamarkt", pid=pid, titulo=titulo,
                    url=("https://www.mediamarkt.es" + href) if href.startswith("/") else href,
                    precio=precio, disponible=disp,
                    imagen=img.get("src") if img else None,
                )
            time.sleep(random.uniform(1.0, 2.5))
    if not out:
        raise TiendaCaida("MediaMarkt no devuelve tarjetas para ninguna palabra clave")
    return list(out.values())


# --------------------------------------------------------------------------
# Sitemaps: solo detectan altas nuevas
# --------------------------------------------------------------------------

def _urls_sitemap(s: requests.Session, url: str) -> list[str]:
    r = _get(s, url, headers={"Accept": "application/xml,text/xml,*/*"})
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)


def _titulo_desde_slug(url: str) -> str:
    trozo = [t for t in url.rstrip("/").split("/") if t][-1]
    trozo = re.sub(r"-\d+$", "", trozo)
    return trozo.replace("-", " ").strip().capitalize()


def _encaja(url: str, palabras: list[str]) -> bool:
    """En un sitemap solo tenemos la URL, asi que se filtra por el slug.

    Se exigen TODAS las palabras del termino, no solo la primera. Mirando solo
    la primera, "cartas pokemon" dejaba entrar cualquier cosa con "cartas" en la
    URL: sobres de Magic, de Hello Kitty y de Yu-Gi-Oh se colaban en el radar de
    Pokemon. En GAME eran 742 productos en vez de 409.
    """
    u = normaliza(url)
    return any(all(t in u for t in normaliza(p).split()) for p in palabras)


def game(palabras: list[str]) -> list[Producto]:
    """GAME pinta el buscador por JS y lo protege con reCAPTCHA; el sitemap no."""
    s = _sesion()
    urls = _urls_sitemap(s, "https://www.game.es/sitemap/sitemap_products.xml")
    if len(urls) < 1000:
        raise TiendaCaida("sitemap de GAME sospechosamente corto (%d)" % len(urls))
    out = []
    for u in urls:
        if not _encaja(u, palabras):
            continue
        m = re.search(r"-(\d+)$", u.rstrip("/"))
        if not m:
            continue
        out.append(Producto(tienda="game", pid=m.group(1),
                            titulo=_titulo_desde_slug(u), url=u))
    return out


def pokemoncenter(palabras: list[str]) -> list[Producto]:
    """La tienda oficial: /search da 403 (Akamai), el sitemap de productos no.

    Aqui no se filtra por palabra clave: en Pokemon Center todo es Pokemon.
    """
    s = _sesion()
    urls = _urls_sitemap(s, "https://www.pokemoncenter.com/sitemaps/products.xml")
    if len(urls) < 100:
        raise TiendaCaida("sitemap de Pokemon Center corto (%d)" % len(urls))
    out, vistos = [], set()
    for u in urls:
        m = re.search(r"/product/([\w-]+)/", u)
        if not m or m.group(1) in vistos:
            continue
        vistos.add(m.group(1))
        out.append(Producto(tienda="pokemoncenter", pid=m.group(1),
                            titulo=_titulo_desde_slug(u), url=u))
    return out


def carrefour(palabras: list[str], trozos: int = 40) -> list[Producto]:
    """Carrefour bloquea buscador y API con Cloudflare, pero sirve los sitemaps.

    Son 40 ficheros de ~1,5 MB. Por eso esta tienda corre con cadencia lenta
    (ver cada_min en config.json): barrerlos cada 10 minutos seria absurdo.
    """
    s = _sesion("https://www.carrefour.es/")
    out, vistos = [], set()
    for n in range(trozos):
        url = ("https://www.carrefour.es/sitemap/non-food/products/"
               "productSitemap-%05d-of-%05d.xml" % (n, trozos))
        try:
            urls = _urls_sitemap(s, url)
        except TiendaCaida:
            if n == 0:
                raise
            break  # se acabaron los trozos: Carrefour ha cambiado el reparto
        for u in urls:
            if not _encaja(u, palabras):
                continue
            # Formato: /<slug-del-producto>/<ean>/p  ->  el nombre esta en el
            # penultimo tramo, no en el ultimo, que es el codigo de barras.
            tramos = [t for t in u.split("/") if t]
            m = re.search(r"/(\d{8,14})/p", u)
            pid = m.group(1) if m else tramos[-2]
            if pid in vistos:
                continue
            vistos.add(pid)
            slug = tramos[-3] if len(tramos) >= 3 else tramos[-1]
            out.append(Producto(tienda="carrefour", pid=pid,
                                titulo=slug.replace("-", " ").strip().capitalize(),
                                url=u))
        time.sleep(0.4)
    return out


# --------------------------------------------------------------------------
# Ficha: solo se pide para los productos nuevos de las fuentes tipo sitemap
# --------------------------------------------------------------------------

# Selectores del precio vigente en la ficha de producto. El de la tarjeta del
# buscador y el de la ficha pueden no coincidir (ofertas de otros vendedores,
# variantes del producto, promociones que caducan), y el que vale es el de la
# ficha: es el que ve la persona cuando pincha el aviso.
PRECIO_EN_FICHA = {
    "amazon": ("#corePrice_feature_div span.a-offscreen",
               ".priceToPay span.a-offscreen",
               "#corePriceDisplay_desktop_feature_div span.a-offscreen",
               "span.a-price span.a-offscreen"),
    "eci": ('[data-synth="PRICE"]', ".price-sale", ".product_detail-price"),
    "mediamarkt": ('[data-test="mms-product-price"]', '[data-test="branded-price-value"]'),
    # GAME parte el precio en spans (int / decimal) y usa apostrofo de separador.
    # El primer .buy--price de la pagina es el del producto; los siguientes son
    # los de los productos relacionados.
    "game": (".buy--price",),
}


# Como se sabe si un producto esta comprable, mirando su ficha. Cada tienda lo
# dice a su manera y ninguna con la misma etiqueta.
STOCK_EN_FICHA = {
    # GAME no declara availability en su ld+json: el boton de comprar esta o no
    # esta, y esa es toda la senal. Comprobado sobre 8 fichas al azar: 4 con
    # boton y stock, 4 sin boton y agotadas.
    "game": ("button.buy--btn",),
    "eci": ('[data-synth="ADD_TO_CART"]', ".product_detail-buy button"),
    "mediamarkt": ('[data-test="cofr-add-to-basket-button"]',),
}


_SESIONES: dict[str, object] = {}


def _sesion_de(tienda: str):
    """Una sola sesion por tienda, reutilizada mientras dure la pasada.

    Abrir sesion nueva para cada ficha significaba dos peticiones por producto
    (portada + ficha) y tirar las cookies recien conseguidas. Al repasar el
    stock de 25 productos eso son 50 peticiones en vez de 26, y Amazon empezaba
    a responder 503 a la tercera o cuarta. Reutilizando la sesion se pide la
    portada una vez y las cookies duran toda la pasada, que es exactamente lo
    que hace un navegador.
    """
    s = _SESIONES.get(tienda)
    if s is None:
        s = _sesion(_PORTADAS.get(tienda))
        _SESIONES[tienda] = s
    return s


def _lee_ficha(p: Producto) -> dict:
    """Abre la ficha UNA vez y saca de ella todo lo que se pueda.

    Existe para que no haya dos sitios distintos sabiendo leer el precio de una
    tienda. Los habia, y GAME lo pagaba: el aviso de producto nuevo iba por un
    camino que solo miraba las metaetiquetas Open Graph, que GAME no rellena, y
    salia siempre sin precio, mientras el repaso de stock leia 6,99 sin
    problema por el otro. Ahora los dos caminos entran por aqui.

    Devuelve {} si la ficha no se ha podido leer, que no es lo mismo que "sin
    precio" o "agotado": quien llama decide que hacer con la duda.
    """
    try:
        s = _sesion_de(p.tienda)
        r = _get(s, p.url)
        sopa = BeautifulSoup(r.text, "lxml")

        precio = disponible = None

        for sel in PRECIO_EN_FICHA.get(p.tienda, ()):
            el = sopa.select_one(sel)
            if el:
                precio = _precio(el.get_text(" ", strip=True))
                if precio:
                    break

        for sel in STOCK_EN_FICHA.get(p.tienda, ()):
            if sopa.select_one(sel):
                disponible = True
                break
        else:
            if p.tienda in STOCK_EN_FICHA:
                disponible = False

        # Schema.org: lo rellena Carrefour y sirve de reserva para el resto.
        if precio is None or disponible is None:
            for ld in sopa.find_all("script", type="application/ld+json"):
                try:
                    d = json.loads(ld.string or "{}")
                except (json.JSONDecodeError, AttributeError):
                    continue
                d = d[0] if isinstance(d, list) and d else d
                if not isinstance(d, dict):
                    continue
                ofertas = d.get("offers") or {}
                ofertas = ofertas[0] if isinstance(ofertas, list) and ofertas else ofertas
                if not isinstance(ofertas, dict):
                    continue
                if precio is None and ofertas.get("price"):
                    try:
                        precio = float(str(ofertas["price"]).replace(",", "."))
                    except ValueError:
                        pass
                if disponible is None and ofertas.get("availability"):
                    disponible = "instock" in str(ofertas["availability"]).lower()

        def meta(*props):
            for prop in props:
                el = (sopa.find("meta", property=prop)
                      or sopa.find("meta", attrs={"name": prop}))
                if el and el.get("content"):
                    return el["content"].strip()
            return None

        titulo = meta("og:title", "twitter:title")
        if titulo:
            titulo = re.sub(r"\s*[|-]\s*(GAME|Carrefour|Pok.mon Center).*$", "",
                            titulo).strip()
        return {"precio": precio, "disponible": disponible,
                "titulo": titulo, "imagen": meta("og:image")}
    except (TiendaCaida,) + ERRORES_RED:
        return {}


def ficha(p: Producto) -> tuple[float | None, bool | None]:
    """Precio y disponibilidad de la ficha. (None, None) si no se ha podido leer."""
    d = _lee_ficha(p)
    return d.get("precio"), d.get("disponible")


def precio_ficha(p: Producto) -> float | None:
    """Solo el precio. Se usa para confirmar las bajadas antes de avisar."""
    return ficha(p)[0]


# Como sacar la referencia del producto de una URL pegada a mano en Telegram.
DE_URL = {
    "amazon.es": r"/(?:dp|gp/product)/([A-Z0-9]{10})",
    "elcorteingles.es": r"/(A\d{6,})",
    "mediamarkt.es": r"-(\d+)\.html",
    "game.es": r"-(\d+)/?$",
    "carrefour.es": r"/(\d{8,14})/p",
    "pokemoncenter.com": r"/product/([\w-]+)/",
}


def desde_url(url: str) -> Producto | None:
    """Convierte una URL de tienda en un Producto identificable.

    Se usa para el comando /vigilar: la persona pega el enlace del producto que
    quiere y hay que saber de que tienda es y con que referencia guardarlo, para
    que case con lo que ya hay en memoria.
    """
    url = url.strip()
    if not url.startswith("http"):
        return None
    dominio = urlparse(url).netloc.lower()
    for host, patron in DE_URL.items():
        if not dominio.endswith(host):
            continue
        m = re.search(patron, url)
        if not m:
            return None
        return Producto(tienda=_TIENDA_POR_HOST[host], pid=m.group(1),
                        titulo=_titulo_desde_slug(url), url=url)
    return None


_TIENDA_POR_HOST = {
    "amazon.es": "amazon",
    "elcorteingles.es": "eci",
    "mediamarkt.es": "mediamarkt",
    "game.es": "game",
    "carrefour.es": "carrefour",
    "pokemoncenter.com": "pokemoncenter",
}


_PORTADAS = {
    "amazon": "https://www.amazon.es/",
    "eci": "https://www.elcorteingles.es/",
    "mediamarkt": "https://www.mediamarkt.es/",
    "carrefour": "https://www.carrefour.es/",
}



def detalle(p: Producto) -> Producto:
    """Completa titulo, precio, stock e imagen abriendo la ficha del producto.

    Se llama solo para las altas nuevas de las fuentes de sitemap, que traen la
    URL pero nada mas. Si la ficha no se puede leer, el producto se queda como
    estaba: un aviso con el titulo sacado del slug es infinitamente mejor que
    ningun aviso.
    """
    d = _lee_ficha(p)
    if d.get("titulo"):
        p.titulo = d["titulo"]
    if d.get("precio") is not None:
        p.precio = d["precio"]
    if d.get("disponible") is not None:
        p.disponible = d["disponible"]
    if d.get("imagen"):
        p.imagen = d["imagen"]
    return p


# clave -> (funcion, da_precio_y_stock)
ADAPTADORES = {
    "amazon": (amazon, True),
    "eci": (eci, True),
    "mediamarkt": (mediamarkt, True),
    "game": (game, False),
    "pokemoncenter": (pokemoncenter, False),
    "carrefour": (carrefour, False),
}

NOMBRES = {
    "amazon": "Amazon.es",
    "eci": "El Corte Ingles",
    "mediamarkt": "MediaMarkt",
    "game": "GAME",
    "pokemoncenter": "Pokemon Center",
    "carrefour": "Carrefour",
}

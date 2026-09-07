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
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

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

    @property
    def clave(self) -> str:
        return f"{self.tienda}:{self.pid}"


def _sesion() -> requests.Session:
    s = requests.Session()
    s.headers.update(CABECERAS)
    s.headers["User-Agent"] = random.choice(AGENTES)
    return s


def _get(s: requests.Session, url: str, **kw) -> requests.Response:
    ultimo = None
    for intento in range(REINTENTOS):
        try:
            r = s.get(url, timeout=TIMEOUT, **kw)
            if r.status_code == 200:
                return r
            ultimo = "HTTP %s" % r.status_code
            # 403/429/503 suele ser antibot: cambiar de agente y esperar mas.
            if r.status_code in (403, 429, 503):
                s.headers["User-Agent"] = random.choice(AGENTES)
                time.sleep(2 + intento * 3)
                continue
        except requests.RequestException as e:
            ultimo = type(e).__name__
        time.sleep(1 + intento * 2)
    raise TiendaCaida("%s -> %s" % (url[:70], ultimo))


def _precio(texto: str) -> float | None:
    """'1.234,56 EUR' -> 1234.56. None si no hay nada parseable."""
    if not texto:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})", texto)
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

def amazon(palabras: list[str], paginas: int = 2) -> list[Producto]:
    s = _sesion()
    out: dict[str, Producto] = {}
    for palabra in palabras:
        for pag in range(1, paginas + 1):
            # s=date-desc-rank es el orden "novedades": lo recien listado sale arriba.
            url = ("https://www.amazon.es/s?k=%s&s=date-desc-rank&page=%d"
                   % (quote_plus(palabra), pag))
            r = _get(s, url)
            if "api-services-support@amazon.com" in r.text:
                raise TiendaCaida("captcha de Amazon")
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
        raise TiendaCaida("Amazon no devuelve tarjetas para ninguna palabra clave")
    return list(out.values())


def eci(palabras: list[str], paginas: int = 2) -> list[Producto]:
    """El Corte Ingles.

    El precio fiable no esta en el HTML visible sino en el datalayer que la web
    embebe en window.__MOONSHINE_STATE__. La URL de la ficha si esta en el HTML
    (atributo data-url). Se cruzan los dos por la referencia (A200971037).
    """
    s = _sesion()
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
    s = _sesion()
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
    """En un sitemap solo tenemos la URL, asi que se filtra por el slug."""
    u = normaliza(url)
    return any(normaliza(p.split()[0]) in u for p in palabras)


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
    s = _sesion()
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

def detalle(p: Producto) -> Producto:
    """Completa titulo/precio/imagen leyendo las metaetiquetas Open Graph.

    Se llama solo para altas nuevas, que son pocas. Si falla, se deja el
    producto como estaba: un aviso con el titulo sacado del slug es
    infinitamente mejor que ningun aviso.
    """
    try:
        s = _sesion()
        r = _get(s, p.url)
        sopa = BeautifulSoup(r.text, "lxml")

        def meta(*props):
            for prop in props:
                el = (sopa.find("meta", property=prop)
                      or sopa.find("meta", attrs={"name": prop}))
                if el and el.get("content"):
                    return el["content"].strip()
            return None

        t = meta("og:title", "twitter:title")
        if t:
            p.titulo = re.sub(r"\s*[|-]\s*(GAME|Carrefour|Pok.mon Center).*$", "", t).strip()
        pr = meta("product:price:amount", "og:price:amount")
        if pr:
            p.precio = _precio(pr.replace(".", ","))
        else:
            ld = sopa.find("script", type="application/ld+json")
            if ld:
                try:
                    d = json.loads(ld.string or "{}")
                    d = d[0] if isinstance(d, list) and d else d
                    ofertas = d.get("offers") or {}
                    ofertas = ofertas[0] if isinstance(ofertas, list) and ofertas else ofertas
                    if ofertas.get("price"):
                        p.precio = float(str(ofertas["price"]).replace(",", "."))
                    if ofertas.get("availability"):
                        p.disponible = "InStock" in str(ofertas["availability"])
                except (json.JSONDecodeError, ValueError, AttributeError, TypeError):
                    pass
        im = meta("og:image")
        if im:
            p.imagen = im
    except (TiendaCaida, requests.RequestException):
        pass
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

# -*- coding: utf-8 -*-
"""Recoge titulares de TCG de feeds publicos para la seccion de noticias de Cromel.

Es un agregador, no un copiador: de cada noticia se guarda el titular, un
extracto corto (el que la propia fuente publica en su feed, recortado), la imagen
de cabecera si el feed la da, el nombre de la fuente y el ENLACE a la noticia
original. El texto completo se lee en la fuente. Las noticias propias de Cromel
(origen 'manual') se escriben a mano y nunca se tocan desde aqui.

Solo se usan feeds que la propia web publica para que se lean con un programa
(RSS/Atom). Se piden con una identificacion honesta y como mucho cada 6 horas.
"""

from __future__ import annotations

import html
import os
import re
import time
from urllib.parse import quote
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

UA = "CromelBot/1.0 (+https://cromeltcg.com; agregador de titulares TCG)"
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

# Palabras que delatan el juego de una noticia (en minusculas, sin tildes).
JUEGOS = [
    # Funko primero: "Funko Pop One Piece" es una noticia de Funko, no de cartas.
    ("funko", ("funko",)),
    ("one_piece", ("one piece", "op-0", "op-1", "op0", "op1", "eb-0", "prb-")),
    ("pokemon", ("pokemon", "pokémon", "pikachu", "charizard", "tcg pocket")),
    ("lorcana", ("lorcana",)),
    ("magic", ("magic: the gathering", "magic the gathering", "mtg", "commander", "universes beyond")),
    ("dragon_ball", ("dragon ball",)),
    ("riftbound", ("riftbound",)),
    ("mercado", ("psa ", "cgc ", "beckett", "grading", "graded", "gradeo", "price guide", "trading card", "card market", "tcgplayer")),
]
# Para que una noticia de un feed generico (ICv2...) entre, tiene que ir de TCG.
SOLO_TCG = ("tcg", "trading card", "card game", "booster", "pokemon", "pokémon", "one piece", "magic: the gathering",
            "lorcana", "dragon ball", "riftbound", "yu-gi-oh", "psa ", "cgc ", "grading", "elite trainer", "funko")
RUIDO = ("casino", "bet", "slots", "poker online", "crypto", "betting")


def _sin_tildes(t: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def juego_de(texto: str, defecto: str | None) -> str | None:
    t = _sin_tildes(texto)
    for juego, palabras in JUEGOS:
        if any(_sin_tildes(p) in t for p in palabras):
            return juego
    return defecto


def _texto(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _limpia(t: str, largo: int = 230) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(t or ""))
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= largo:
        return t
    corte = t[:largo].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return corte + "…"


def _fecha(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _imagen(item, cuerpo: str) -> str | None:
    for tag in ("media:content", "media:thumbnail"):
        for e in item.findall(tag, NS):
            u = e.get("url")
            if u and u.startswith("https://") and (e.get("medium") in (None, "image") or tag == "media:thumbnail"):
                return u
    for e in item.findall("enclosure"):
        if (e.get("type") or "").startswith("image/") and (e.get("url") or "").startswith("https://"):
            return e.get("url")
    m = re.search(r'<img[^>]+src=["\'](https://[^"\']+)', cuerpo or "", re.I)
    return html.unescape(m.group(1)) if m else None


def lee_feed(feed: dict, dias: int = 10) -> list[dict]:
    """Devuelve noticias normalizadas de un feed. No lanza: un feed roto da []."""
    try:
        r = requests.get(feed["url"], timeout=25, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.5"})
        r.raise_for_status()
        raiz = ET.fromstring(r.content)
    except Exception as e:
        print("[noticias] %s: no se pudo leer (%s)" % (feed.get("nombre"), str(e)[:80]))
        return []

    limite = datetime.now(timezone.utc) - timedelta(days=dias)
    items = raiz.findall(".//item") or raiz.findall(".//atom:entry", NS)
    out = []
    for it in items:
        titulo = _limpia(_texto(it.find("title")) or _texto(it.find("atom:title", NS)), 200)
        enlace = _texto(it.find("link"))
        if not enlace:
            a = it.find("atom:link", NS)
            enlace = a.get("href", "") if a is not None else ""
        cuerpo = _texto(it.find("content:encoded", NS)) or _texto(it.find("description")) or _texto(it.find("atom:summary", NS)) or _texto(it.find("atom:content", NS))
        fecha = _fecha(_texto(it.find("pubDate")) or _texto(it.find("dc:date", NS)) or _texto(it.find("atom:published", NS)) or _texto(it.find("atom:updated", NS)))
        if not titulo or not enlace.startswith("https://") or not fecha or fecha < limite or fecha > datetime.now(timezone.utc) + timedelta(hours=2):
            continue
        todo = _sin_tildes(titulo + " " + _limpia(cuerpo, 600))
        if any(p in todo.split() or (" %s " % p) in (" %s " % todo) for p in RUIDO):
            continue
        # Feeds de tema general: solo lo que habla de cartas. Los especializados, todo.
        if feed.get("filtrar") and not any(_sin_tildes(p) in todo for p in SOLO_TCG):
            continue
        juego = juego_de(titulo + " " + _limpia(cuerpo, 400), feed.get("juego"))
        if not juego:
            continue
        extracto = _limpia(cuerpo)
        if len(extracto) < 40:
            continue
        out.append({
            "titulo": titulo, "resumen": extracto, "imagen_url": _imagen(it, cuerpo),
            "fuente_nombre": feed["nombre"], "fuente_url": enlace, "juego": juego,
            "publicada_en": fecha.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    out.sort(key=lambda x: x["publicada_en"], reverse=True)
    return out[: int(feed.get("maximo", 6))]


def imagen_de_pagina(url: str) -> str | None:
    """La imagen de cabecera (og:image / twitter:image) de la propia noticia.

    Muchos feeds no traen foto; la pagina del articulo casi siempre si, porque
    es la que usan las redes al compartirlo. Es la imagen que la propia fuente
    eligio para ese articulo, asi que se relaciona con lo que cuenta.
    """
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
        if r.status_code != 200:
            return None
        cab = r.text[:200_000]
    except requests.RequestException:
        return None
    for patron in (
        r"""<meta[^>]+(?:property|name)=["'](?:og:image(?::secure_url)?|twitter:image)["'][^>]+content=["']([^"']+)""",
        r"""<meta[^>]+content=["']([^"']+)["'][^>]+(?:property|name)=["'](?:og:image|twitter:image)["']""",
    ):
        m = re.search(patron, cab, re.I)
        if m:
            u = html.unescape(m.group(1)).strip()
            if u.startswith("//"):
                u = "https:" + u
            if u.startswith("https://") and not re.search(r"logo|favicon|default|placeholder", u, re.I):
                return u
    return None


# ---------------------------------------------------------------- traduccion
# La web es en español: los titulares y extractos que llegan en ingles se
# traducen antes de guardarlos. Con GEMINI_API_KEY se usa Gemini (mejor
# calidad); si no, MyMemory (gratis, sin clave, con limite diario). Si los dos
# fallan, se deja el texto original antes que perder la noticia.
_EN = re.compile(r"\b(the|and|of|to|with|will|from|this|is|are|for|new|its|their|reveals?|released?|gets|after)\b", re.I)
_CACHE_TRAD: dict[str, str] = {}


def es_ingles(t: str) -> bool:
    return len(_EN.findall(t or "")) >= 2


# Google retira modelos de Gemini cada cierto tiempo (gemini-2.0-flash ya da 404).
# Se prueban en orden y se recuerda el primero que responda; GEMINI_MODELO
# permite fijar otro sin tocar el codigo.
GEMINI_MODELOS = ["gemini-3.8-flash", "gemini-flash-latest"]
_GEMINI_BUENO: str | None = None


def gemini_post(cuerpo: dict, timeout: int = 120):
    """POST a generateContent con el primer modelo que exista. Devuelve la respuesta."""
    global _GEMINI_BUENO
    r = None
    vistos: list[str] = []
    for m in [os.environ.get("GEMINI_MODELO"), _GEMINI_BUENO, *GEMINI_MODELOS]:
        if not m or m in vistos:
            continue
        vistos.append(m)
        r = requests.post("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % m,
                          timeout=timeout, json=cuerpo,
                          headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "content-type": "application/json"})
        if r.status_code == 404:
            continue
        _GEMINI_BUENO = m
        return r
    return r


def _traduce_gemini(t: str) -> str | None:
    if not os.environ.get("GEMINI_API_KEY"):
        return None
    try:
        r = gemini_post(timeout=60, cuerpo={"contents": [{"parts": [{"text":
                  "Traduce al español de España este texto de una noticia de cartas coleccionables o "
                  "figuras Funko. Natural y fiel, sin añadir nada. No traduzcas nombres propios de sets, "
                  "cartas, productos ni personajes. Devuelve solo la traducción.\n\n" + t}]}],
                  "generationConfig": {"temperature": 0.2, "maxOutputTokens": 600}})
        if r.status_code != 200:
            return None
        partes = r.json()["candidates"][0]["content"]["parts"]
        out = "".join(p.get("text", "") for p in partes).strip()
        return out or None
    except Exception:
        return None


def _traduce_mymemory(t: str) -> str | None:
    try:
        r = requests.get("https://api.mymemory.translated.net/get?q=%s&langpair=en|es" % quote(t[:480]),
                         timeout=25, headers={"User-Agent": UA})
        d = r.json()
        out = html.unescape((d.get("responseData") or {}).get("translatedText") or "").strip()
        # Cuando se acaba el cupo diario, MyMemory devuelve un aviso en vez de la traduccion.
        if not out or "MYMEMORY WARNING" in out.upper() or d.get("responseStatus") not in (200, "200"):
            return None
        return out
    except Exception:
        return None


def traduce(t: str) -> str:
    if not t or not es_ingles(t):
        return t
    if t not in _CACHE_TRAD:
        _CACHE_TRAD[t] = _traduce_gemini(t) or _traduce_mymemory(t) or t
        time.sleep(0.4)
    return _CACHE_TRAD[t]


def recoge(feeds: list[dict]) -> list[dict]:
    todo, vistos = [], set()
    for f in feeds:
        n = lee_feed(f)
        print("[noticias] %-26s %2d titulares" % (f.get("nombre", "?")[:26], len(n)))
        for x in n:
            if x["fuente_url"] not in vistos:
                vistos.add(x["fuente_url"])
                if not x.get("imagen_url"):
                    x["imagen_url"] = imagen_de_pagina(x["fuente_url"])
                    time.sleep(0.5)
                # La web es en español.
                x["titulo"] = _limpia(traduce(x["titulo"]), 200)
                x["resumen"] = _limpia(traduce(x["resumen"]))
                todo.append(x)
        time.sleep(1.0)
    return todo

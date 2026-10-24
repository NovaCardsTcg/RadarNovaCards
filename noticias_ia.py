"""Una o dos noticias al dia para Cromel, redactadas con la API de Claude.

Flujo de cada ejecucion (la lanza .github/workflows/noticias.yml cada pocas horas):

  1. Mira cuantas noticias de este tipo hay ya hoy y hace cuanto salio la ultima.
     Si ya se llego al cupo, termina sin gastar nada.
  2. Reune titulares recientes de fuentes publicas (PokeBeach, la web oficial de One
     Piece Card Game y los feeds de config.json -> noticias_feeds).
  3. Descarta lo ya publicado, los articulos de estrategia y los rumores, y ordena
     dando prioridad al juego que menos noticias lleva esta semana.
  4. Lee el articulo original y pide a Claude una noticia NUEVA en español, con sus
     palabras y solo con los datos de la fuente. Claude puede negarse (rumor, no es
     TCG, poca informacion).
  5. Valida lo que devuelve (longitudes, que cada cifra exista en la fuente, que no
     copie frases, que no lleve enlaces) y lo guarda en Supabase con la fuente, el
     enlace y la foto de cabecera de la fuente, con su credito.

Variables de entorno: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY.
`--seco` hace todo menos guardar: imprime la noticia que saldria.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import noticias

UA = "Mozilla/5.0 (compatible; CromelBot/1.0; +https://cromeltcg.com)"
MODELO = os.environ.get("CROMEL_IA_MODELO", "claude-sonnet-5-5")
POR_DIA = int(os.environ.get("CROMEL_IA_POR_DIA", "2"))
HORAS_ENTRE = float(os.environ.get("CROMEL_IA_HORAS_ENTRE", "5"))
MAX_INTENTOS = 6                 # llamadas a la API por ejecucion, como mucho
MAX_EDAD_DIAS = 4                # no se cuentan noticias mas viejas
MARCA_IA = "*Texto redactado con ayuda de inteligencia artificial."
JUEGOS = ("pokemon", "one_piece", "lorcana", "magic", "dragon_ball", "riftbound", "mercado")

# Fuentes que no tienen feed: se lee su portada y se sacan los enlaces a articulos.
PORTADAS = [
    {"nombre": "PokeBeach", "url": "https://www.pokebeach.com/", "juego": "pokemon",
     "patron": r"https://www\.pokebeach\.com/20\d\d/\d\d/[a-z0-9-]+"},
    {"nombre": "One Piece Card Game", "url": "https://en.onepiece-cardgame.com/news/", "juego": "one_piece",
     "patron": r"(?:https://en\.onepiece-cardgame\.com)?/onepiececg/bccard/en/news/20\d\d/\d\d/\d\d/[A-Za-z0-9]+/?"},
]

# Titulares que no son noticia: estrategia, listas de mazos, rumores.
NO_NOTICIA = re.compile(
    r"\b(deck ?list|decklist|metagame|tier list|how to|guide to (?:play|build)|best deck|"
    r"my favorite|favorite deck|tournament report|top 8|top 16|rumou?r|leak(?:ed|s)?\b|"
    r"speculat|prediction|opinion|review:|unboxing|giveaway)\b", re.I)
INTERES = re.compile(
    r"\b(release|releas|reveal|announce|set|expansion|booster|box|prerelease|promo|ban|restrict|"
    r"errata|tournament|championship|worlds|regional|event|price|sale|restock|grading|psa|cgc|"
    r"collab|partnership|store|retail|launch|preorder|pre-order|starter deck|"
    r"lanzamiento|expansi|torneo|prohib)", re.I)


def log(*a):
    print("[noticias_ia]", *a, flush=True)


# ------------------------------------------------------------------ Supabase
def _sb(ruta: str, **kw):
    base = os.environ["SUPABASE_URL"].rstrip("/")
    h = {"apikey": os.environ["SUPABASE_SERVICE_KEY"], "Authorization": "Bearer " + os.environ["SUPABASE_SERVICE_KEY"]}
    h.update(kw.pop("headers", {}))
    return requests.request(kw.pop("method", "GET"), f"{base}/rest/v1/{ruta}", headers=h, timeout=30, **kw)


def ya_publicadas() -> tuple[set[str], set[str], list[dict]]:
    """(urls de fuente ya guardadas, slugs, noticias de IA de los ultimos dias)."""
    r = _sb("noticias?select=slug,fuente_url,juego,publicada_en,cuerpo&order=publicada_en.desc&limit=400")
    r.raise_for_status()
    filas = r.json()
    urls = {f["fuente_url"] for f in filas}
    slugs = {f["slug"] for f in filas}
    ia = [f for f in filas if MARCA_IA in (f.get("cuerpo") or "")]
    return urls, slugs, ia


def cupo_libre(ia: list[dict], ahora: datetime) -> str | None:
    """None si se puede publicar; si no, el motivo."""
    fechas = sorted((datetime.fromisoformat(f["publicada_en"].replace("Z", "+00:00")) for f in ia), reverse=True)
    hoy = [d for d in fechas if d.date() == ahora.date()]
    if len(hoy) >= POR_DIA:
        return f"ya hay {len(hoy)} noticias de hoy"
    if fechas and ahora - fechas[0] < timedelta(hours=HORAS_ENTRE):
        return f"la ultima salio hace menos de {HORAS_ENTRE:g} h"
    return None


# ------------------------------------------------------------------ candidatas
def _get(url: str) -> str | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en,es;q=0.8"}, timeout=25)
        return r.text if r.status_code == 200 else None
    except requests.RequestException:
        return None


def _meta(sopa, *props):
    for p in props:
        el = sopa.find("meta", property=p) or sopa.find("meta", attrs={"name": p})
        if el and el.get("content"):
            return html.unescape(el["content"]).strip()
    return None


def _fecha(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


MALA_IMAGEN = re.compile(r"logo|favicon|avatar|default|placeholder|sprite|icon", re.I)


def lee_articulo(url: str) -> dict | None:
    """Titulo, foto, fecha y texto limpio de un articulo."""
    txt = _get(url)
    if not txt:
        return None
    sopa = BeautifulSoup(txt, "lxml")
    titulo = _meta(sopa, "og:title", "twitter:title") or (sopa.title.get_text(strip=True) if sopa.title else "")
    titulo = re.sub(r"\s*[-|–]\s*(PokeBeach|ONE PIECE CARD GAME|One Piece Card Game).*$", "", titulo).strip()
    for malo in sopa.select("script, style, nav, footer, aside, form, header, noscript, .comments, #comments"):
        malo.decompose()
    cont = (sopa.select_one(".entry-content") or sopa.select_one("[itemprop=articleBody]")
            or sopa.select_one("article") or sopa.select_one("main") or sopa.body)
    if cont is None:
        return None
    partes = []
    for el in cont.find_all(["p", "li", "h2", "h3", "td"]):
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if len(t) >= 25 or (el.name in ("h2", "h3") and len(t) >= 4):
            partes.append(t)
    texto = "\n".join(dict.fromkeys(partes))[:9000]
    # Foto: la de cabecera de la propia fuente; si es un logo, la primera foto grande del articulo.
    img = _meta(sopa, "og:image", "twitter:image")
    if img:
        img = urljoin(url, img)
    if not img or MALA_IMAGEN.search(img):
        img = None
        for im in cont.find_all("img"):
            src = urljoin(url, im.get("data-src") or im.get("src") or "")
            if src.startswith("https://") and re.search(r"\.(jpe?g|png|webp)(\?|$)", src, re.I) and not MALA_IMAGEN.search(src):
                img = src
                break
    return {"titulo": html.unescape(titulo), "imagen": img if img and img.startswith("https://") else None,
            "fecha": _fecha(_meta(sopa, "article:published_time", "og:updated_time")), "texto": texto}


def candidatas(config: dict, ahora: datetime) -> list[dict]:
    out: list[dict] = []
    for p in PORTADAS:
        txt = _get(p["url"])
        if not txt:
            log("portada sin respuesta:", p["nombre"])
            continue
        vistos = []
        for m in re.finditer(p["patron"], txt):
            u = urljoin(p["url"], m.group(0)).rstrip("/")
            if u not in vistos:
                vistos.append(u)
        log("%-22s %2d enlaces" % (p["nombre"], len(vistos)))
        for u in vistos[:12]:
            out.append({"fuente": p["nombre"], "url": u, "juego": p["juego"], "titulo": u.rsplit("/", 1)[-1].replace("-", " ")})
    for f in config.get("noticias_feeds", []):
        try:
            for x in noticias.lee_feed(f, dias=MAX_EDAD_DIAS):
                out.append({"fuente": f["nombre"], "url": x["fuente_url"], "juego": x.get("juego") or f.get("juego"),
                            "titulo": x["titulo"], "fecha": _fecha(x.get("publicada_en"))})
        except Exception as e:  # una fuente caida no para las demas
            log("feed con error:", f.get("nombre"), str(e)[:80])
    return out


def puntua(c: dict, por_juego: dict[str, int]) -> float:
    s = 0.0
    t = c["titulo"]
    if NO_NOTICIA.search(t):
        return -1
    if INTERES.search(t):
        s += 2
    j = c.get("juego") or noticias.juego_de(t, None) or "tcg"
    s += 3 - min(por_juego.get(j, 0), 3)          # prioridad al juego con menos noticias esta semana
    if c.get("fecha"):
        s += max(0, 2 - (datetime.now(timezone.utc) - c["fecha"]).total_seconds() / 86400)
    return s


# ------------------------------------------------------------------ Claude
SISTEMA = """Eres redactor de Cromel, una web española de reposiciones y ofertas de cartas coleccionables (TCG). Escribes noticias breves, claras y exactas para coleccionistas y jugadores de España.

Recibirás el texto de UN artículo de otra web (en inglés u otro idioma). Escribe una noticia NUEVA en español de España, con tus propias palabras.

Reglas estrictas:
- Usa SOLO datos que aparezcan en el texto de la fuente: fechas, cifras, nombres de sets y productos. No inventes ni deduzcas nada. Si el texto no da un dato, no lo pongas.
- No copies frases de la fuente: reformula. No traduzcas literalmente párrafos enteros.
- Mantén los nombres oficiales de sets, cartas y productos tal y como aparecen (no los traduzcas si no los traduce la fuente).
- Si es un rumor, una filtración, una opinión, una guía de estrategia, una lista de mazos o no trata de juegos de cartas coleccionables, o el texto no tiene información suficiente para unas 150 palabras, responde publicar=false.
- Todo el texto en español de España, incluido el título y el resumen. No dejes frases en inglés (salvo nombres oficiales de sets, cartas y productos).
- NO menciones de dónde viene la información: ni el nombre de la web de la fuente, ni "según...", ni "de acuerdo con...", ni enlaces. Escribe como si lo contara Cromel.
- Reescribe de verdad: cambia el orden de las ideas, el enfoque y las expresiones, de modo que no parezca una versión de la fuente. Empieza por lo que más le importe a un coleccionista español (fechas, qué sale, cuándo y dónde).
- Tono informativo y neutro. Sin primera persona, sin sensacionalismo, sin emojis, sin enlaces, sin llamadas a comprar.
- titulo: 40-100 caracteres, concreto, sin comillas de clic fácil. resumen: 110-230 caracteres, una o dos frases. cuerpo: 180-380 palabras en 3-5 párrafos cortos separados por línea en blanco; puedes usar un subtítulo con "## " y una lista con "- " solo si ayuda. Formato permitido: párrafos, "## ", "- " y **negrita**.
- juego: uno de pokemon, one_piece, lorcana, magic, dragon_ball, riftbound, mercado (mercado = precios, gradeo, ventas, tiendas).

Responde SOLO con un JSON, sin texto alrededor:
{"publicar": true|false, "motivo": "una frase", "titulo": "...", "resumen": "...", "cuerpo": "...", "juego": "..."}"""


def pide_a_claude(c: dict, art: dict) -> dict | None:
    if os.environ.get("CROMEL_IA_FALSO"):           # para probar el flujo sin gastar
        return json.loads(os.environ["CROMEL_IA_FALSO"])
    cuerpo = {
        "model": MODELO, "max_tokens": 2200, "system": SISTEMA,
        "messages": [{"role": "user", "content":
                      f"Fuente: {c['fuente']}\nTítulo original: {art['titulo']}\nFecha: {art.get('fecha') or 'sin fecha'}\n\nTexto del artículo:\n{art['texto']}"}],
    }
    for intento in range(3):
        try:
            r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, json=cuerpo, headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
        except requests.RequestException as e:
            log("red:", str(e)[:80])
            time.sleep(5)
            continue
        if r.status_code in (429, 500, 502, 503, 529):
            time.sleep(10 * (intento + 1))
            continue
        if r.status_code != 200:
            log("API", r.status_code, r.text[:200])
            return None
        txt = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        if not m:
            log("respuesta sin JSON")
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            log("JSON invalido")
            return None
    return None


# ------------------------------------------------------------------ validacion
def _norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t.lower()) if unicodedata.category(c) != "Mn")


def _numeros(t: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in re.findall(r"\d[\d.,]*\d|\d", t)}


def valida(a: dict, fuente: str, fuente_nombre: str = "") -> str | None:
    """None si vale; si no, el motivo de rechazo."""
    for k in ("titulo", "resumen", "cuerpo", "juego"):
        if not isinstance(a.get(k), str) or not a[k].strip():
            return f"falta {k}"
    if a["juego"] not in JUEGOS:
        return "juego no valido"
    if not 25 <= len(a["titulo"]) <= 130:
        return "titulo fuera de largo"
    if not 60 <= len(a["resumen"]) <= 300:
        return "resumen fuera de largo"
    palabras = len(a["cuerpo"].split())
    if not 140 <= palabras <= 520:
        return f"cuerpo de {palabras} palabras"
    todo = " ".join((a["titulo"], a["resumen"], a["cuerpo"]))
    if re.search(r"https?://|www\.", todo, re.I):
        return "lleva enlaces"
    if fuente_nombre and _norm(fuente_nombre) in _norm(todo):
        return "nombra la fuente"
    if re.search(r"\b(segun|de acuerdo con|according to|publicado por)\b", _norm(a["cuerpo"])):
        return "cita una fuente"
    if re.search(r"[\U0001F300-\U0001FAFF☀-➿]", todo):
        return "lleva emojis"
    if len(re.findall(r"\b(el|la|de|que|en|los|las|un|una|con|por)\b", _norm(todo))) < 12:
        return "no parece español"
    if len(re.findall(r"\b(the|and|of|to|with|will|from|this|their)\b", _norm(todo))) > 6:
        return "demasiado inglés"
    # Cada cifra debe salir de la fuente (los numeros de 1 cifra, "dos", etc. se perdonan).
    fuente_n = _numeros(fuente)
    for n in _numeros(todo):
        if len(n) >= 2 and n not in fuente_n:
            return f"cifra que no esta en la fuente: {n}"
    # Nada de copiar: ninguna secuencia de 8 palabras de la fuente.
    pf = _norm(re.sub(r"[^\w\s]", " ", fuente)).split()
    pa = _norm(re.sub(r"[^\w\s]", " ", a["cuerpo"])).split()
    sh = {" ".join(pf[i:i + 8]) for i in range(max(0, len(pf) - 7))}
    for i in range(max(0, len(pa) - 7)):
        if " ".join(pa[i:i + 8]) in sh:
            return "copia una frase de la fuente"
    return None


def slug_de(titulo: str, existentes: set[str]) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", _norm(titulo)).strip("-")[:80].strip("-") or "noticia"
    base, n = s, 2
    while s in existentes:
        s = f"{base}-{n}"
        n += 1
    return s


# ------------------------------------------------------------------ principal
def main(seco: bool = False) -> int:
    ahora = datetime.now(timezone.utc)
    config = json.load(open(os.path.join(os.path.dirname(__file__), "config.json"), encoding="utf-8"))
    urls, slugs, ia = ya_publicadas()
    motivo = None if seco else cupo_libre(ia, ahora)
    if motivo:
        log("nada que hacer:", motivo)
        return 0

    por_juego: dict[str, int] = {}
    for f in ia:
        if ahora - datetime.fromisoformat(f["publicada_en"].replace("Z", "+00:00")) < timedelta(days=7):
            por_juego[f["juego"]] = por_juego.get(f["juego"], 0) + 1

    cands = [c for c in candidatas(config, ahora) if c["url"] not in urls]
    pun = [(puntua(c, por_juego), c) for c in cands]
    pun = [x for x in pun if x[0] >= 0]
    pun.sort(key=lambda x: x[0], reverse=True)
    log(f"{len(cands)} candidatas nuevas, {len(pun)} validas")

    llamadas = 0
    for _, c in pun:
        if llamadas >= MAX_INTENTOS:
            break
        art = lee_articulo(c["url"])
        if not art or len(art["texto"]) < 500:
            continue
        if art["fecha"] and ahora - art["fecha"] > timedelta(days=MAX_EDAD_DIAS):
            continue
        if NO_NOTICIA.search(art["titulo"]):
            continue
        llamadas += 1
        log("probando:", art["titulo"][:90])
        r = pide_a_claude(c, art)
        if not r or not r.get("publicar"):
            log("  descartada:", (r or {}).get("motivo", "sin respuesta"))
            continue
        malo = valida(r, art["texto"] + " " + art["titulo"], c["fuente"])
        if malo:
            log("  rechazada:", malo)
            continue
        cuerpo = r["cuerpo"].strip() + f"\n\n{MARCA_IA}"
        fila = {
            "slug": slug_de(r["titulo"], slugs), "titulo": r["titulo"].strip(), "resumen": r["resumen"].strip(), "cuerpo": cuerpo,
            "imagen_url": art["imagen"], "imagen_credito": c["fuente"], "fuente_nombre": "Cromel", "fuente_url": c["url"],
            "juego": r["juego"], "origen": "manual", "destacada": False, "publicada": True,
            "publicada_en": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if seco:
            print(json.dumps(fila, ensure_ascii=False, indent=1))
            return 0
        resp = _sb("noticias", method="POST", json=fila, headers={"Prefer": "return=minimal"})
        if resp.status_code in (200, 201, 204):
            log("PUBLICADA:", fila["slug"])
            return 0
        log("  Supabase", resp.status_code, resp.text[:160])
    log("ninguna candidata valida en esta ejecucion")
    return 0


if __name__ == "__main__":
    sys.exit(main(seco="--seco" in sys.argv))

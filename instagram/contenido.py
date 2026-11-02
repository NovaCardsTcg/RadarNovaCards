# -*- coding: utf-8 -*-
"""Que se publica cada dia: elige los datos y escribe el texto del post.

Cada generador devuelve un dict {tipo, titulo, diapositivas, caption, productos}
o None si hoy no hay material suficiente (entonces se prueba el siguiente de
RESERVA). Las diapositivas son datos; las dibuja plantilla.html.

Calendario (hora de Madrid, ver ig_ajustes.horas):
  primer hueco        -> chollos del dia (sellado)
  huecos intermedios  -> publicidad de la app (app_promo.py)
  ultimo hueco        -> segun el dia de la semana (TARDE)

Textos segun las guias de instagram-marketing: gancho en los primeros 125
caracteres, una sola peticion y 5 hashtags que rotan (30 se leen como spam).
"""

from __future__ import annotations

import datetime as dt
import random
import re

import requests

from . import app_promo, render, supa

WEB = "https://cromeltcg.com"
MADRID = dt.timezone(dt.timedelta(hours=1))  # se corrige con zoneinfo si esta

try:
    from zoneinfo import ZoneInfo
    MADRID = ZoneInfo("Europe/Madrid")
except Exception:  # pragma: no cover
    pass

# lunes=0 ... domingo=6
TARDE = {0: "suben", 1: "reposiciones", 2: "valiosas", 3: "lanzamientos", 4: "funko",
         5: "otros_juegos", 6: "resumen"}
RESERVA = ["valiosas", "chollos", "suben", "app"]

NOMBRE_JUEGO = {"pokemon": "Pokémon", "one_piece": "One Piece", "lorcana": "Lorcana", "magic": "Magic",
                "dragon_ball": "Dragon Ball", "riftbound": "Riftbound"}
# (de nicho, intermedios, generales): se cogen 2 + 1 + 1 al azar en cada post.
HASHTAGS = {
    "pokemon": (["#cartaspokemonespaña", "#pokemontcgespaña", "#coleccionistapokemon", "#cazacartas", "#sobrespokemon"],
                ["#cartaspokemon", "#pokemonespaña"], ["#pokemontcg", "#pokemoncards"]),
    "one_piece": (["#onepiececardgameespaña", "#onepiecetcgespaña", "#cartasonepiece"],
                  ["#onepiececardgame", "#onepiecetcg"], ["#onepiece"]),
    "lorcana": (["#lorcanaespaña", "#cartaslorcana", "#tcgespaña"], ["#disneylorcana"], ["#lorcana"]),
    "magic": (["#magicespaña", "#mtgespaña", "#tcgespaña"], ["#magicthegathering"], ["#mtg"]),
    "dragon_ball": (["#dragonballtcg", "#fusionworldespaña", "#tcgespaña"], ["#dragonballsuper"], ["#dragonball"]),
    "funko": (["#funkoespaña", "#funkopopespaña", "#coleccionfunko"], ["#funkopopcollector"], ["#funkopop"]),
    "psa": (["#gradeocartas", "#cartasgradeadas", "#psaespaña"], ["#psagrading", "#psa10"], ["#pokemontcg"]),
    "varios": (["#tcgespaña", "#cartascoleccionables", "#lanzamientostcg"], ["#pokemonespaña", "#onepiececardgame"], ["#tcg"]),
}
PIE = "\n\n👉 Todas las ofertas en cromeltcg.com (enlace en la bio)"


def eur(c: int | None) -> str:
    if not c:
        return ""
    s = "%.2f" % (c / 100)
    return s.replace(".", ",") + " €"


def _hashtags(tema: str = "varios") -> str:
    """#cromeltcg y 4 mas del tema: 2 de nicho, 1 intermedio y 1 general."""
    g = HASHTAGS.get(tema, HASHTAGS["varios"])
    tags = random.sample(g[0], 2) + random.sample(g[1], 1) + random.sample(g[2], 1)
    return " ".join(["#cromeltcg"] + tags)


def _limpia_titulo(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"\s*[\(\[][^)\]]*(envio|envío|stock|oferta)[^)\]]*[\)\]]", "", t, flags=re.I)
    return t[:110].rstrip()


def _img_carta(base: str | None, grande: bool = True) -> str | None:
    if not base:
        return None
    if base.endswith(".png"):
        return base.replace(".png", "_hires.png") if grande and "_hires" not in base else base
    return base + ("/high.webp" if grande else "/low.webp")


def _logo_coleccion(base: str | None) -> str | None:
    if not base:
        return None
    return base if base.endswith(".png") else base + ".png"


def _usados(dias: int = 7) -> set[str]:
    """Productos y cartas que ya salieron en Instagram hace poco: no se repiten."""
    desde = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dias)).isoformat()
    try:
        filas = supa.lee("ig_publicaciones", select="datos", creado="gte." + desde)
    except requests.HTTPError:
        return set()  # sin la migracion 13 (o en una prueba local)
    out: set[str] = set()
    for f in filas:
        out.update((f.get("datos") or {}).get("usados") or [])
    return out


def _chollos(filtro: dict, n: int, dias: float = 1.5, por_tienda: int = 2, orden: str | None = None) -> list[dict]:
    desde = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=dias)).isoformat()
    params = {
        "select": "id,producto_id,titulo,tienda,tienda_id,tienda_logo,aviso,precio_cents,precio_anterior_cents,"
                  "descuento_pct,imagen_url,juego,categoria,publicado_en,coleccion,franquicia",
        "estado": "eq.activo", "en_stock": "is.true", "imagen_url": "not.is.null",
        "precio_cents": "not.is.null", "publicado_en": "gte." + desde,
        "order": orden or "descuento_pct.desc.nullslast,publicado_en.desc", "limit": "200",
    }
    params.update(filtro)
    filas = supa.lee("v_chollos", **params)
    usados = _usados()
    out, cuenta, vistos = [], {}, set()
    for f in filas:
        clave = re.sub(r"\W+", "", (f["titulo"] or "").lower())[:40]
        if f["producto_id"] in usados or clave in vistos:
            continue
        # Sin foto no hay ficha, y asi las cifras de la portada y del texto cuadran.
        if not render._data_uri(f["imagen_url"]):
            continue
        if cuenta.get(f["tienda_id"], 0) >= por_tienda:
            continue
        cuenta[f["tienda_id"]] = cuenta.get(f["tienda_id"], 0) + 1
        vistos.add(clave)
        out.append(f)
        if len(out) >= n:
            break
    return out


def _diapo_producto(f: dict) -> dict:
    logo = f.get("tienda_logo") or ""
    if logo.startswith("/"):
        logo = WEB + logo
    return {"tipo": "producto", "imagen": f["imagen_url"], "titulo": _limpia_titulo(f["titulo"]),
            "tienda": f["tienda"], "tienda_logo": logo or None, "precio": eur(f["precio_cents"]),
            "antes": eur(f.get("precio_anterior_cents")) if f.get("descuento_pct") else "",
            "descuento": f.get("descuento_pct") or 0, "aviso": f["aviso"],
            "juego": NOMBRE_JUEGO.get(f.get("juego") or "", "")}


def _linea_producto(f: dict) -> str:
    extra = " (-%d%%)" % f["descuento_pct"] if f.get("descuento_pct") else ""
    return "• %s: %s%s en %s" % (_limpia_titulo(f["titulo"])[:70].rstrip(), eur(f["precio_cents"]), extra, f["tienda"])


def _post_productos(tipo: str, filas: list[dict], etiqueta: str, titulo_html: str, sub: str,
                    intro: list[str], tags: tuple[str, ...]) -> dict:
    hoy = dt.datetime.now(MADRID)
    portada = {"tipo": "portada", "etiqueta": etiqueta, "titulo": titulo_html, "sub": sub,
               "imagenes": [f["imagen_url"] for f in filas[:3]]}
    diapos = [portada] + [_diapo_producto(f) for f in filas] + [{"tipo": "cta"}]
    # Las etiquetas, del juego que mas sale en el post.
    juegos = [f.get("juego") for f in filas if f.get("juego") in HASHTAGS]
    tema = tags[0] if tags else (max(set(juegos), key=juegos.count) if juegos else "varios")
    caption = (random.choice(intro) + "\n\n" + "\n".join(_linea_producto(f) for f in filas)
               + "\n\nPrecios vistos el %s; pueden cambiar en cualquier momento." % hoy.strftime("%d/%m a las %H:%M")
               + PIE + "\n\n" + _hashtags(tema))
    return {"tipo": tipo, "diapositivas": diapos, "caption": caption,
            "usados": [f["producto_id"] for f in filas]}


# ------------------------------------------------------------------ generadores

def chollos() -> dict | None:
    filas = _chollos({"categoria": "eq.sellado"}, 6)
    if len(filas) < 4:
        filas += _chollos({"categoria": "in.(accesorio,gradeada)"}, 6 - len(filas))
    if len(filas) < 3:
        return None
    return _post_productos(
        "chollos", filas, "🔥 Chollos de hoy", "%d chollos que ha cazado <em>hoy el radar</em>" % len(filas),
        "Ofertas y reposiciones de las tiendas de España. <b>Desliza →</b>",
        ["🔥 Los chollos TCG de hoy, cazados por el radar en las tiendas de España:",
         "🃏 Esto es lo mejor que ha salido hoy en las tiendas de cartas:",
         "👀 Si estabas esperando precio, hoy es el día. Lo mejor del radar:"], ())


def reposiciones() -> dict | None:
    filas = _chollos({"aviso": "eq.reposicion", "categoria": "neq.funko"}, 6, dias=4, orden="publicado_en.desc")
    if len(filas) < 3:
        return None
    return _post_productos(
        "reposiciones", filas, "🔄 Reposiciones", "%d productos que <em>vuelven a tener stock</em>" % len(filas),
        "Lo que han repuesto las tiendas estos días. <b>Vuela rápido.</b>",
        ["🔄 Vuelve el stock: esto es lo que han repuesto las tiendas estos días.",
         "⚡ Reposiciones recientes en tiendas de España (vuelan, ojo):"], ())


def funko() -> dict | None:
    filas = _chollos({"categoria": "eq.funko", "descuento_pct": "gte.15"}, 6, dias=5)
    if len(filas) < 3:
        return None
    return _post_productos(
        "funko", filas, "🧸 Funko en oferta", "%d Funko Pop <em>rebajados</em> esta semana" % len(filas),
        "Solo con un 15 % de descuento o más. <b>Desliza →</b>",
        ["🧸 Viernes de Funko: los Pop con mejor descuento que ha visto el radar esta semana.",
         "🧸 Funko Pop rebajados (15 % o más) en tiendas de España:"], ("funko",))


def otros_juegos() -> dict | None:
    filas = _chollos({"juego": "in.(one_piece,lorcana,magic,dragon_ball,riftbound)", "categoria": "neq.funko"}, 6, dias=4)
    if len(filas) < 3:
        return None
    return _post_productos(
        "otros_juegos", filas, "🏴‍☠️ Más allá de Pokémon", "One Piece, Lorcana <em>y más</em>",
        "Lo mejor de los otros juegos esta semana.",
        ["🏴‍☠️ No todo es Pokémon: lo mejor de One Piece, Lorcana y compañía esta semana.",
         "🃏 Chollos de otros TCG que ha cazado el radar:"], ())


def _diapo_carta(c: dict, extra: str = "") -> dict:
    return {"tipo": "carta", "imagen": _img_carta(c.get("imagen")), "nombre": c["nombre"],
            "coleccion": c.get("coleccion") or "", "precio": eur(c["precio_cents"]), "extra": extra}


def suben() -> dict | None:
    try:
        filas = supa.rpc("ig_cartas_que_suben", {"p_dias": 7, "p_min_cents": 300}) or []
    except requests.HTTPError:
        return None
    usados = _usados()
    filas = [f for f in filas if f["id"] not in usados][:6]
    if len(filas) < 3:
        return None
    portada = {"tipo": "portada", "etiqueta": "📈 Precios de cartas", "titulo": "%d cartas que han subido <em>esta semana</em>" % len(filas),
               "sub": "Precio de mercado ahora y hace una semana.", "imagenes": [_img_carta(f["imagen"]) for f in filas[:3]],
               "cartas": True}
    diapos = [portada] + [_diapo_carta(f, "▲ +%d%% · antes %s" % (f["sube_pct"], eur(f["antes_cents"]))) for f in filas]
    caption = (random.choice(["📈 Las cartas Pokémon que más han subido esta semana:",
                              "📈 ¿Tienes alguna? Estas son las cartas que más se han revalorizado en 7 días:"])
               + "\n\n" + "\n".join("• %s (%s): %s → %s (+%d%%)" % (f["nombre"], f["coleccion"], eur(f["antes_cents"]),
                                                                     eur(f["precio_cents"]), f["sube_pct"]) for f in filas)
               + "\n\nPrecio de tendencia de Cardmarket. Mira el valor de tu colección en la app."
               + PIE + "\n\n" + _hashtags("pokemon"))
    return {"tipo": "suben", "diapositivas": diapos + [{"tipo": "cta"}], "caption": caption, "usados": [f["id"] for f in filas]}


def valiosas() -> dict | None:
    hoy = dt.date.today().isoformat()
    cols = supa.lee("colecciones", select="id,nombre,logo,fecha,n_con_precio,padre_id",
                    fecha="lte." + hoy, n_con_precio="gte.40", order="fecha.desc", limit="12")
    cols = [c for c in cols if not c.get("padre_id")]
    usados = _usados(28)
    for col in cols:
        if "col:" + col["id"] in usados:
            continue
        cartas = supa.lee("cartas", select="id,nombre,imagen,precio_cents", coleccion_id="eq." + col["id"],
                          imagen="not.is.null", precio_cents="not.is.null", order="precio_cents.desc", limit="6")
        if len(cartas) < 4:
            continue
        for c in cartas:
            c["coleccion"] = col["nombre"]
        portada = {"tipo": "portada", "etiqueta": "💎 ¿Cuánto vale?", "titulo": "Las cartas más caras de <em>%s</em>" % col["nombre"],
                   "sub": "Top %d por precio de mercado." % len(cartas), "imagenes": [_img_carta(c["imagen"]) for c in cartas[:3]],
                   "logo": _logo_coleccion(col.get("logo")), "cartas": True}
        diapos = [portada] + [_diapo_carta(c, "#%d de la colección" % (i + 1)) for i, c in enumerate(cartas)]
        caption = ("💎 ¿Te ha tocado alguna? Las cartas más caras de %s ahora mismo:\n\n" % col["nombre"]
                   + "\n".join("%d. %s: %s" % (i + 1, c["nombre"], eur(c["precio_cents"])) for i, c in enumerate(cartas))
                   + "\n\nLa colección entera, con el precio de cada carta, en la app."
                   + PIE + "\n\n" + _hashtags("pokemon"))
        return {"tipo": "valiosas", "diapositivas": diapos + [{"tipo": "cta"}], "caption": caption,
                "usados": ["col:" + col["id"]] + [c["id"] for c in cartas]}
    return None


def lanzamientos() -> dict | None:
    try:
        lista = requests.get(WEB + "/api/lanzamientos", timeout=30).json()
    except Exception:
        return None
    lista = lista[:5]
    if len(lista) < 2:
        return None
    portada = {"tipo": "portada", "etiqueta": "📅 Lanzamientos", "titulo": "Lo que <em>sale</em> pronto",
               "sub": "Fechas en España y aviso el día que haya stock.", "imagenes": []}
    diapos = [portada] + [{"tipo": "lanzamiento", "nombre": l["nombre"], "codigo": l.get("codigo") or "",
                           "juego": l["juego"], "fecha": l["fecha_texto"], "cuenta": l["cuenta_atras"],
                           "destacadas": (l.get("destacadas") or [])[:3]} for l in lista]
    caption = ("📅 Apunta las fechas: próximos lanzamientos TCG en España\n\n"
               + "\n".join("• %s%s (%s): %s" % (l["nombre"], " " + l["codigo"] if l.get("codigo") else "", l["juego"],
                                                 l["fecha_texto"]) for l in lista)
               + "\n\nEn la app puedes activar el aviso para cuando haya stock."
               + PIE + "\n\n" + _hashtags("varios"))
    return {"tipo": "lanzamientos", "diapositivas": diapos + [{"tipo": "cta"}], "caption": caption,
            "usados": ["lanz:" + l["id"] for l in lista]}


def resumen() -> dict | None:
    desde = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).isoformat()
    cuenta = {}
    for aviso in ("nuevo", "reposicion", "bajada"):
        r = requests.get("%s/rest/v1/v_chollos" % supa.URL, timeout=60,
                         params={"select": "id", "publicado_en": "gte." + desde, "aviso": "eq." + aviso},
                         headers={"apikey": supa.CLAVE, "Authorization": "Bearer " + supa.CLAVE,
                                  "Prefer": "count=exact", "Range": "0-0"})
        cuenta[aviso] = int((r.headers.get("content-range") or "*/0").split("/")[-1] or 0)
    total = sum(cuenta.values())
    top = _chollos({}, 4, dias=7)
    if total < 10 or len(top) < 3:
        return None
    cifras = {"tipo": "cifras", "etiqueta": "📊 La semana del radar", "titulo": "<em>%d</em> avisos esta semana" % total,
              "filas": [["🆕", "Productos nuevos", cuenta["nuevo"]], ["🔄", "Reposiciones", cuenta["reposicion"]],
                        ["📉", "Bajadas de precio", cuenta["bajada"]]]}
    post = _post_productos("resumen", top, "📊 Resumen semanal", "Lo mejor de <em>la semana</em>",
                           "Los descuentos más gordos de los últimos 7 días.",
                           ["📊 Resumen de la semana: %d avisos (%d nuevos, %d reposiciones y %d bajadas). Lo mejor:"
                            % (total, cuenta["nuevo"], cuenta["reposicion"], cuenta["bajada"])], ())
    post["diapositivas"].insert(1, cifras)
    return post


def app() -> dict:
    """Publicidad de la app: la siguiente serie y gancho que no hayan salido en 60 dias."""
    serie, g = app_promo.siguiente(_usados(60))
    gancho = serie["ganchos"][g]
    portada = {"tipo": "app_portada", "etiqueta": serie["etiqueta"], "titulo": gancho, "sub": serie["sub"],
               "capturas": serie["portada"]}
    pasos = [{"tipo": "paso", "captura": c, "titulo": t, "texto": x} for c, t, x in serie["pasos"]]
    cierre = {"tipo": "cta", "titulo": serie["cierre"], "pide": serie["pide"]}
    caption = (re.sub(r"</?em>", "", gancho) + "\n\n" + serie["texto"] + "\n\n" + serie["pide"] + "."
               + "\n\n👉 Gratis en cromeltcg.com (enlace en la bio)\n\n" + _hashtags(serie["tags"]))
    return {"tipo": "app", "diapositivas": [portada] + pasos + [cierre], "caption": caption,
            "usados": ["app:%s:%d" % (serie["id"], g)]}


GENERADORES = {"app": app, "chollos": chollos, "reposiciones": reposiciones, "funko": funko, "otros_juegos": otros_juegos,
               "suben": suben, "valiosas": valiosas, "lanzamientos": lanzamientos, "resumen": resumen}


def tipo_del_hueco(indice: int, fecha: dt.date, total: int = 2) -> str:
    if indice == 0:
        return "chollos"
    return TARDE[fecha.weekday()] if indice == total - 1 else "app"


def genera(tipo: str) -> dict | None:
    """El post de ese tipo o, si hoy no hay material, el primero de RESERVA que lo tenga."""
    for t in [tipo] + [r for r in RESERVA if r != tipo]:
        post = GENERADORES[t]()
        if post:
            return post
        print("[ig] sin material para '%s'" % t)
    return None

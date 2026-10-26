# -*- coding: utf-8 -*-
"""Puente del radar con la app Cromel Chollos (Supabase).

Dos cosas, las dos opcionales: si faltan SUPABASE_URL y SUPABASE_SERVICE_KEY
no hace nada y el radar sigue como siempre (canal de Telegram incluido).

1. sincroniza(): en cada pasada manda el catalogo de la tienda, clasificado en
   sellado / accesorio / gradeada / suelta, a radar_sincroniza(). La base
   compara con lo que ya tenia y decide nuevo, reposicion, bajada o agotado.
   El canal sigue con su propia logica: aqui no se decide nada.
2. envia_pendientes(): reparte los avisos de las alertas de la app (y las
   respuestas a comentarios) por el bot y por notificacion push.

Un fallo aqui nunca tumba la pasada del radar: se anota y se sigue.
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

import avisos
import formato
from tiendas import normaliza

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
CLAVE = os.environ.get("SUPABASE_SERVICE_KEY", "")
# Direccion publica de la app, para el boton de los avisos ("Ver en Cromel").
APP_URL = os.environ.get("CROMEL_APP_URL", "").rstrip("/")
VAPID_PRIVADA = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_CONTACTO = os.environ.get("VAPID_SUBJECT", "mailto:suscripciones@linkener.com")


def activo() -> bool:
    return bool(URL and CLAVE)


def _rpc(funcion: str, datos: dict, timeout: int = 60):
    r = requests.post(
        "%s/rest/v1/rpc/%s" % (URL, funcion), json=datos, timeout=timeout,
        headers={"apikey": CLAVE, "Authorization": "Bearer " + CLAVE, "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json() if r.content else None


# ---------------------------------------------------------------- clasificar

JUEGOS = [  # (terminos en el titulo normalizado, juego)
    (("one piece",), "one_piece"),
    (("lorcana",), "lorcana"),
    (("magic the gathering", "magic: the gathering", "mtg"), "magic"),
    (("dragon ball",), "dragon_ball"),
    (("pokemon",), "pokemon"),
]
# Un producto es Funko si lo dice el titulo. Vale para cualquier tienda.
ES_FUNKO = re.compile(r"\bfunko\b", re.I)

# Franquicia de un Funko a partir del titulo (orden: lo mas concreto primero).
# Cada entrada es (franquicia, patron). La primera que encaja gana.
FRANQUICIAS_FUNKO = [
    ("One Piece", r"one piece"),
    ("Dragon Ball", r"dragon ?ball"),
    ("Naruto", r"naruto|boruto"),
    ("Jujutsu Kaisen", r"jujutsu"),
    ("Solo Leveling", r"solo leveling"),
    ("Pokémon", r"pokemon|pikachu|charizard|eevee"),
    ("Marvel", r"marvel|spider[- ]?man|avengers|x-?men|deadpool|wolverine|venom|hulk|iron ?man|\bthor\b|loki|capitan america|captain america|black panther"),
    ("Star Wars", r"star wars|mandalorian|grogu|baby yoda|darth|jedi|sith"),
    ("Harry Potter", r"harry potter|hogwarts|dumbledore|voldemort"),
    ("DC", r"\bdc\b|batman|superman|\bjoker\b|harley quinn|wonder woman|aquaman|flash"),
    ("Disney", r"disney|mickey|minnie|stitch|pixar|toy story|frozen|\bpooh\b"),
    ("Anime", r"anime|demon slayer|kimetsu|chainsaw|hunter x hunter|bleach|evangelion|sailor moon|my hero|dragon quest"),
]
_FRANQ_RE = [(nombre, re.compile(pat, re.I)) for nombre, pat in FRANQUICIAS_FUNKO]


def franquicia_de(titulo: str) -> str | None:
    for nombre, rx in _FRANQ_RE:
        if rx.search(titulo):
            return nombre
    return None


GRADEO = re.compile(r"\b(PSA|PCA|CGC|BGS|Beckett|SGC|TAG|ACE)\s*[-:]?\s*(\d{1,2}(?:[.,]5)?)\b", re.I)
ACCESORIOS = [  # el orden importa: "PET Protector ... (10 Pack)" es protector, no funda
    (("protector", "acrylic", "acrilico", "display case", "vitrina", "magnetic", "one touch", "case for"), "Protectores"),
    (("toploader", "top loader", "semi rigid"), "Toploaders"),
    (("album", "binder", "archivador", "carpeta", "portfolio"), "Álbumes"),
    (("deck box", "deckbox", "caja de mazo", "caja para mazo", "deck case", "deck holder"), "Cajas de mazo"),
    (("playmat", "tapete"), "Tapetes"),
    (("funda", "fundas", "sleeve", "sleeves"), "Fundas"),
    (("dado", "dados", "dice", "contador", "contadores"), "Dados y contadores"),
]
# Un accesorio cuenta si es de un juego, de una marca de accesorios de cartas o
# dice que es para cartas: asi no entran protectores de coches de juguete.
DE_CARTAS = ("card", "cartas", "carta", "tcg", "booster", "etb", "elite trainer", "dragon shield", "ultra pro",
             "ultimate guard", "gamegenic", "kmc", "mintkeeper", "trading")
SUELTA = re.compile(r"\b[A-Z]{0,4}\d{1,3}\s*/\s*[A-Z]{0,4}\d{2,3}\b|\b(OP|ST|EB|PRB)\d{2}-\d{3}\b|carta suelta|single card", re.I)
RAREZAS = [(("promo",), "Promo"), (("alt art", "alternate art", "arte alternativo", "parallel", "manga"), "Alt Art"),
           (("full art",), "Full Art"), (("secret", "secreta", "hyper", "gold"), "Secreta"),
           (("reverse",), "Reverse"), (("holo",), "Holo")]
# La app ve mas que el canal: Lorcana, Magic y Dragon Ball tambien.
PALABRAS_APP = ["lorcana", "magic the gathering", "mtg", "dragon ball"]


def _contiene(t: str, terminos) -> bool:
    return any(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(x), t) for x in terminos)


def _primera(t: str, tabla):
    for terminos, nombre in tabla:
        if _contiene(t, terminos):
            return nombre
    return None


def juego_de(titulo: str, etiquetas: str = "") -> str | None:
    # Las etiquetas de la tienda ("pokemon", vendor "Pokémon") cuentan igual que
    # el titulo: una gradeada japonesa antigua no suele llevar "Pokémon" en el nombre.
    t = " %s " % normaliza(titulo + " " + etiquetas)
    for terminos, j in JUEGOS:
        if any(x in t for x in terminos):
            return j
    return None


def config_app(config: dict) -> dict:
    c = dict(config)
    c["palabras"] = list(config["palabras"]) + PALABRAS_APP
    c["excluir"] = list(config["excluir"]) + ["caja de mazo", "ultimate guard", "deck holder", "playmat", "tapete"]
    return c


def categoria_de(p, config_app_: dict, relevante) -> tuple[str, str | None, str | None] | None:
    """(categoria, subtipo, gradeo), o None si no es de cartas coleccionables."""
    # Funko: o lo marca la tienda (Passarella) o lo dice el titulo ("Funko POP...").
    # No pasa por el filtro de TCG. La franquicia va en su propio campo (ver para_app).
    if getattr(p, "categoria_forzada", None) == "funko" or ES_FUNKO.search(p.titulo):
        return "funko", None, None
    t = normaliza(p.titulo)
    etiquetas = normaliza(getattr(p, "etiquetas", "") or "")
    juego = juego_de(p.titulo, etiquetas)
    g = GRADEO.search(p.titulo)
    # Lo que la propia tienda clasifica (CardZone: "Carta Gradeada", "Carta
    # Suelta") manda sobre lo que se deduce del titulo.
    if g and (juego or _contiene(t, DE_CARTAS) or "gradeada" in etiquetas):
        empresa = g.group(1).upper().replace("BECKETT", "BGS")
        return "gradeada", empresa, "%s %s" % (empresa, g.group(2).replace(",", "."))
    if "carta gradeada" in etiquetas or "graded" in etiquetas.split(" | "):
        return "gradeada", None, None
    acc = _primera(t, ACCESORIOS)
    if acc and (juego or _contiene(t, DE_CARTAS)):
        return "accesorio", acc, None
    # Una carta suelta que la tienda etiqueta como tal cuenta aunque el titulo no
    # diga el juego ("Feebas (2024) SV8 JP"): la tienda es de cartas.
    if "carta suelta" in etiquetas or "single" in etiquetas.split(" | ") or (juego and SUELTA.search(p.titulo)):
        return "suelta", _primera(t, RAREZAS), None
    if juego and relevante(p, config_app_, True):
        return "sellado", None, None
    return None


def para_app(productos: list, config: dict, relevante, destacado) -> list[dict]:
    capp = config_app(config)
    out = []
    for p in productos:
        cat = categoria_de(p, capp, relevante)
        if not cat:
            continue
        categoria, subtipo, gradeo = cat
        funko = categoria == "funko"
        # Funko: solo entra lo que tenga descuento (por defecto >= 15 %), para que
        # la seccion no se llene de figuras a precio normal (Amazon, ECI, Juguetilandia
        # tienen miles). El descuento sale del precio tachado de la propia tienda.
        if funko:
            minimo = config.get("funko_descuento_min", 15) / 100
            ant = getattr(p, "precio_anterior", None)
            if not (p.precio and ant and ant > 0 and (1 - p.precio / ant) >= minimo):
                continue
        c = formato.clasifica(p.titulo)
        juego = juego_de(p.titulo, getattr(p, "etiquetas", "") or "") or "otro"
        sellado = categoria == "sellado"
        out.append({
            "pid": p.pid, "titulo": p.titulo, "url": p.url, "imagen": p.imagen,
            "categoria": categoria, "subtipo": subtipo, "gradeo": gradeo, "juego": juego,
            # La franquicia (Marvel, One Piece...) solo la llevan los Funko: la da
            # la tienda (Passarella) o se deduce del titulo.
            "franquicia": (getattr(p, "franquicia", None) or franquicia_de(p.titulo)) if funko else None,
            "coleccion": c["coleccion"] if sellado and juego in ("pokemon", "one_piece") else None,
            "tipo": c["tipo"] if sellado else None, "idioma": c["idioma"],
            "destacado": bool(destacado(p, config)),
            "precio_cents": round(p.precio * 100) if p.precio else None,
            "precio_anterior_cents": round(p.precio_anterior * 100) if getattr(p, "precio_anterior", None) else None,
            "en_stock": p.disponible,
        })
    return out


# ---------------------------------------------------------------- 1. sincronizar

def sincroniza(tienda: str, productos: list, config: dict, relevante, destacado) -> dict | None:
    if not activo() or not productos:
        return None
    try:
        lote = para_app(productos, config, relevante, destacado)
        if not lote:
            return None
        res = _rpc("radar_sincroniza", {"p_tienda": tienda, "p_productos": lote})
        cambios = {k: v for k, v in (res or {}).items() if v and k != "leidos"}
        print("[app] %s: %d productos%s" % (tienda, len(lote), (" " + json.dumps(cambios)) if cambios else ""))
        return res
    except Exception as e:  # la app nunca tumba el radar
        print("[app] %s: no se pudo sincronizar (%s)" % (tienda, str(e)[:160]))
        return None


# ---------------------------------------------------------------- 2. avisar

ICONO = {"nuevo": "\U0001F195 NUEVO", "reposicion": "\U0001F7E2 REPOSICIÓN", "bajada": "\U0001F4C9 BAJADA"}


def _eur(c: int | None) -> str:
    return ("%.2f €" % (c / 100)).replace(".", ",") if c else ""


def _enlace(n: dict) -> str:
    if not APP_URL:
        return ""
    if n.get("url"):                      # la base ya dice que abrir (migracion 9)
        return APP_URL + n["url"]
    return "%s/chollo/%s" % (APP_URL, n["chollo_id"]) if n.get("chollo_id") else ""


def _texto(n: dict) -> tuple[str, str]:
    esc = avisos.esc
    if n["tipo"] == "respuesta":
        titulo = "%s te ha respondido" % (n["autor"] or "Alguien")
        cuerpo = (n["texto"] or "")[:200]
        tg = "\U0001F4AC <b>%s</b> en «%s»:\n%s" % (esc(titulo), esc(n["titulo"] or ""), esc(cuerpo))
        return titulo, tg
    if n["tipo"] == "carta":              # alerta de precio de una carta (Cardmarket)
        titulo = "Bajada · %s" % n["titulo"]
        tg = "\U0001F4C9 Tu alerta de precio\n<b>%s</b>\nAhora %s en Cardmarket (tu tope: %s)" % (
            esc(n["titulo"] or ""), esc(_eur(n["precio_cents"]) or "ver precio"), esc(n["palabra"] or ""))
        return titulo, tg
    linea = "%s — %s" % (n["tienda"], _eur(n["precio_cents"]) or "ver precio")
    titulo = "%s · %s" % (ICONO.get(n["aviso"], "").split(" ", 1)[-1].capitalize(), n["titulo"])
    tg = "\U0001F514 Tu alerta «%s»\n<b>%s</b>\n%s\n%s" % (
        esc(n["palabra"] or ""), ICONO.get(n["aviso"], ""), esc(linea), esc(n["titulo"] or ""))
    return titulo, tg


def _push(sub: dict, datos: dict) -> bool:
    """True si hay que borrar la suscripcion (el movil ya no existe)."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return False
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(datos), vapid_private_key=VAPID_PRIVADA, vapid_claims={"sub": VAPID_CONTACTO},
                ttl=6 * 3600, timeout=10)
    except WebPushException as e:
        codigo = getattr(e.response, "status_code", 0)
        return codigo in (404, 410)
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- 3. moderar

AYUDA_MODERACION = """
<b>App Cromel</b> (solo si hay Supabase)
/moderar - reportes pendientes de la app, numerados
/ocultar 2 - ocultar el chollo del reporte 2 (no vuelve aunque se reponga)
/mostrar 2 - volver a enseñarlo
/resolver 2 - cerrar el reporte sin tocar nada
/bloquear 2 - ocultar ese comentario y bloquear a su autor 7 dias"""

COMANDOS = ("moderar", "ocultar", "mostrar", "resolver", "bloquear")


def _reportes(estado: dict) -> list[dict]:
    """Lista numerada; los numeros se guardan para que /ocultar 2 sepa a que se refiere."""
    lista = _rpc("reportes_pendientes", {"p_lim": 20}) or []
    estado["moderacion"] = {str(i + 1): {"chollo": r["chollo_id"], "comentario": r["comentario_id"]}
                            for i, r in enumerate(lista)}
    return lista


def comando(cmd: str, arg: str, estado: dict) -> str:
    esc = avisos.esc
    if not activo():
        return "La app no esta conectada (faltan SUPABASE_URL y SUPABASE_SERVICE_KEY)."
    try:
        if cmd == "moderar":
            lista = _reportes(estado)
            if not lista:
                return "Sin reportes pendientes en la app."
            lineas = []
            for i, r in enumerate(lista, 1):
                que = ("\U0001F4AC %s: “%s”" % (esc(r.get("autor") or "?"), esc((r.get("texto") or "")[:80]))
                       if r.get("comentario_id") else "\U0001F4E6 %s" % esc((r.get("titulo") or "")[:70]))
                lineas.append("<b>%d</b>. [%s x%s] %s" % (i, esc(r["motivo"]), r.get("n_reportes", 1), que))
            return "\U0001F6A9 <b>Reportes pendientes</b>\n" + "\n".join(lineas) + "\n\n/ocultar N · /mostrar N · /resolver N · /bloquear N"
        ref = (estado.get("moderacion") or {}).get(arg.strip())
        if not ref:
            return "Primero /moderar y luego el numero: /%s 2" % cmd
        if cmd == "ocultar":
            # Un reporte de comentario oculta solo el comentario, nunca el chollo.
            if ref["comentario"]:
                res = _rpc("admin_modera", {"p_accion": "ocultar_comentario", "p_comentario": ref["comentario"]})
            else:
                res = _rpc("admin_modera", {"p_accion": "ocultar_chollo", "p_chollo": ref["chollo"]})
        elif cmd == "mostrar":
            res = _rpc("admin_modera", {"p_accion": "mostrar_chollo", "p_chollo": ref["chollo"]})
        elif cmd == "resolver":
            res = _rpc("admin_modera", {"p_accion": "resolver", "p_chollo": ref["chollo"] if not ref["comentario"] else None,
                                        "p_comentario": ref["comentario"]})
        else:  # bloquear
            if not ref["comentario"]:
                return "Ese reporte es de un chollo, no de un comentario: usa /ocultar o /resolver."
            res = _rpc("admin_modera", {"p_accion": "bloquear_usuario", "p_comentario": ref["comentario"], "p_dias": 7})
        return "✅ %s" % esc(str(res or "hecho"))
    except Exception as e:
        return "No he podido hablar con la app: %s" % esc(str(e)[:120])


def avisa_reportes(estado: dict) -> str | None:
    """Mensaje al admin cuando entran reportes nuevos (como mucho uno por pasada)."""
    if not activo():
        return None
    try:
        lista = _rpc("reportes_pendientes", {"p_lim": 20}) or []
    except Exception:
        return None
    vistos = set(estado.get("reportes_vistos") or [])
    nuevos = [r for r in lista if r["id"] not in vistos]
    estado["reportes_vistos"] = [r["id"] for r in lista][:200]
    if not nuevos:
        return None
    return ("\U0001F6A9 <b>%d reporte%s nuevo%s en la app</b> (%d pendientes). Mira /moderar."
            % (len(nuevos), "s" if len(nuevos) > 1 else "", "s" if len(nuevos) > 1 else "", len(lista)))


# ---------------------------------------------------------------- 4. noticias

def recoge_noticias(config: dict, estado: dict) -> int:
    """Titulares de TCG de los feeds de config.json -> seccion de noticias de la app.

    Como mucho cada `noticias_cada_h` horas. Las noticias que ya existen (misma
    fuente) no se tocan, y las que se ocultan a mano no vuelven.
    """
    feeds = config.get("noticias_feeds") or []
    if not activo() or not feeds:
        return 0
    cada = float(config.get("noticias_cada_h", 6)) * 3600
    if time.time() - estado.get("noticias_ultima", 0) < cada:
        return 0
    estado["noticias_ultima"] = time.time()  # aunque falle: no se insiste cada pasada
    try:
        import noticias
        items = noticias.recoge(feeds)
        if not items:
            return 0
        try:
            n = _rpc("radar_noticias", {"p_items": items}) or 0
        except requests.HTTPError:
            # Base sin la migracion 6 (juego 'funko' no admitido): las de Funko
            # entran como 'tcg' en vez de perder todo el lote.
            for x in items:
                if x.get("juego") == "funko":
                    x["juego"] = "tcg"
            n = _rpc("radar_noticias", {"p_items": items}) or 0
        print("[app] noticias: %d titulares leidos, %s nuevos" % (len(items), n))
        return int(n)
    except Exception as e:
        print("[app] noticias: no se pudieron recoger (%s)" % str(e)[:160])
        return 0


def traduce_noticias_guardadas(estado: dict, maximo: int = 25) -> int:
    """Pasa al español los titulares recogidos que se guardaron en ingles.

    Las nuevas ya llegan traducidas (noticias.recoge); esto arregla las que
    entraron antes. Como mucho una vez por hora y `maximo` por vez.
    """
    if not activo() or time.time() - estado.get("traduccion_ultima", 0) < 3600:
        return 0
    estado["traduccion_ultima"] = time.time()
    try:
        import noticias
        h = {"apikey": CLAVE, "Authorization": "Bearer " + CLAVE, "Content-Type": "application/json"}
        r = requests.get("%s/rest/v1/noticias?select=id,titulo,resumen&origen=eq.auto&order=publicada_en.desc&limit=200"
                         % URL, headers=h, timeout=30)
        r.raise_for_status()
        n = 0
        for x in r.json():
            if n >= maximo:
                break
            if not (noticias.es_ingles(x["titulo"]) or noticias.es_ingles(x["resumen"])):
                continue
            titulo = noticias._limpia(noticias.traduce(x["titulo"]), 200)
            resumen = noticias._limpia(noticias.traduce(x["resumen"]))
            if (titulo, resumen) == (x["titulo"], x["resumen"]) or len(titulo) < 5 or len(resumen) < 10:
                continue
            p = requests.patch("%s/rest/v1/noticias?id=eq.%s" % (URL, x["id"]), headers=h, timeout=30,
                               json={"titulo": titulo, "resumen": resumen})
            if p.status_code in (200, 204):
                n += 1
        if n:
            print("[app] noticias: %d titulares traducidos al español" % n)
        return n
    except Exception as e:
        print("[app] noticias: no se pudieron traducir (%s)" % str(e)[:160])
        return 0


def envia_pendientes() -> int:
    if not activo():
        return 0
    try:
        pendientes = _rpc("pendientes_envio", {"p_lim": 300}) or []
    except Exception as e:
        print("[app] no se pudo leer la cola de avisos (%s)" % str(e)[:160])
        return 0
    hechos = []
    for n in pendientes:
        titulo, texto_tg = _texto(n)
        enlace = _enlace(n)
        if n.get("telegram_id"):
            boton = ("Ver en Cromel", enlace) if enlace else None
            avisos.enviar(texto_tg, boton=boton, destino=str(n["telegram_id"]))
            time.sleep(0.05)  # ~20/s, por debajo del limite global de Telegram (30/s)
        if VAPID_PRIVADA:
            datos = {"title": titulo[:80], "body": n["tienda"] and ("%s · %s" % (n["tienda"], _eur(n["precio_cents"]) or "ver precio")) or "",
                     "url": enlace or "/", "icon": "/icono.svg", "image": n.get("imagen_url")}
            for sub in n.get("push") or []:
                if _push(sub, datos):
                    try:
                        _rpc("borra_push", {"p_endpoint": sub["endpoint"]})
                    except Exception:
                        pass
        hechos.append(n["id"])
    if hechos:
        try:
            _rpc("marca_enviadas", {"p_ids": hechos})
        except Exception as e:
            print("[app] avisos enviados pero sin marcar (%s): se repetiran" % str(e)[:120])
        print("[app] %d avisos de alertas enviados" % len(hechos))
    return len(hechos)

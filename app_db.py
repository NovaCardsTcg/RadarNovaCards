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


def juego_de(titulo: str) -> str | None:
    t = " %s " % normaliza(titulo)
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
    t = normaliza(p.titulo)
    juego = juego_de(p.titulo)
    g = GRADEO.search(p.titulo)
    if g and (juego or _contiene(t, DE_CARTAS)):
        empresa = g.group(1).upper().replace("BECKETT", "BGS")
        return "gradeada", empresa, "%s %s" % (empresa, g.group(2).replace(",", "."))
    acc = _primera(t, ACCESORIOS)
    if acc and (juego or _contiene(t, DE_CARTAS)):
        return "accesorio", acc, None
    if juego and SUELTA.search(p.titulo):
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
        c = formato.clasifica(p.titulo)
        juego = juego_de(p.titulo) or "otro"
        sellado = categoria == "sellado"
        out.append({
            "pid": p.pid, "titulo": p.titulo, "url": p.url, "imagen": p.imagen,
            "categoria": categoria, "subtipo": subtipo, "gradeo": gradeo, "juego": juego,
            "coleccion": c["coleccion"] if sellado and juego in ("pokemon", "one_piece") else None,
            "tipo": c["tipo"] if sellado else None, "idioma": c["idioma"],
            "destacado": bool(destacado(p, config)),
            "precio_cents": round(p.precio * 100) if p.precio else None,
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
    return "%s/chollo/%s" % (APP_URL, n["chollo_id"]) if APP_URL else ""


def _texto(n: dict) -> tuple[str, str]:
    esc = avisos.esc
    if n["tipo"] == "respuesta":
        titulo = "%s te ha respondido" % (n["autor"] or "Alguien")
        cuerpo = (n["texto"] or "")[:200]
        tg = "\U0001F4AC <b>%s</b> en «%s»:\n%s" % (esc(titulo), esc(n["titulo"] or ""), esc(cuerpo))
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

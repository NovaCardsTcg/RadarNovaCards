# -*- coding: utf-8 -*-
"""API de Instagram (la de "Instagram Login", sin pagina de Facebook).

Publicar siempre va en dos pasos: se crea un contenedor apuntando a una URL
publica, Meta la descarga y la procesa, y solo entonces se publica. Las fotos
tienen que ser JPEG.

El token dura 60 dias. El primero lo pone Miquel como secreto IG_TOKEN en
GitHub; a partir de ahi se guarda en la tabla ig_token y se renueva solo cada
semana. Si Miquel cambia el secreto, manda el nuevo (se compara la huella).

Tambien vale la via de Facebook Login (el Instagram conectado a una pagina), para
cuentas a las que Meta no deja usar la otra: token que no empieza por "IG", host
graph.facebook.com. El token de pagina que sale de uno de usuario de larga
duracion no caduca, asi que esa via no renueva nada.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import time

import requests

from . import supa

API = "https://graph.instagram.com"
API_FACEBOOK = "https://graph.facebook.com"
VERSION = "v23.0"
MAX_CAPTION = 2200


class ErrorIG(Exception):
    pass


_SESION: dict = {}


def _ahora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _es_de_facebook(token: str) -> bool:
    return not token.startswith("IG")


def _pide(metodo: str, ruta: str, token: str, **datos) -> dict:
    url = "%s/%s/%s" % (API_FACEBOOK if _es_de_facebook(token) else API, VERSION, ruta.lstrip("/"))
    datos["access_token"] = token
    if metodo == "GET":
        r = requests.get(url, params=datos, timeout=120)
    else:
        r = requests.post(url, data=datos, timeout=120)
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code >= 400 or "error" in j:
        e = j.get("error") or {}
        raise ErrorIG("Instagram %s: %s (code=%s, subcode=%s)" % (
            r.status_code, e.get("message") or r.text[:200], e.get("code"), e.get("error_subcode")))
    return j


def sesion() -> tuple[str, str]:
    """(token, ig_user_id). Renueva el token si toca."""
    if _SESION:
        return _SESION["token"], _SESION["id"]
    secreto = os.environ.get("IG_TOKEN", "").strip()
    huella = hashlib.sha256(secreto.encode()).hexdigest() if secreto else None
    filas = supa.lee("ig_token", select="*", id="eq.1")
    fila = filas[0] if filas else None

    if secreto and (not fila or fila.get("huella_secreto") != huella):
        token = secreto
        renovado = _ahora()
        fila = None
    elif fila:
        token = fila["token"]
        renovado = dt.datetime.fromisoformat(fila["renovado"].replace("Z", "+00:00"))
    else:
        raise ErrorIG("falta el token: pon el secreto IG_TOKEN en GitHub (ver instagram/README.md)")

    if _es_de_facebook(token):
        return _sesion_facebook(token, fila, huella)

    caduca = fila.get("caduca") if fila else None
    # Renovar exige que el token tenga mas de 24 h; se hace cada 7 dias.
    if fila is None or _ahora() - renovado > dt.timedelta(days=7):
        try:
            j = _pide("GET", "/refresh_access_token", token, grant_type="ig_refresh_token")
            token = j["access_token"]
            caduca = (_ahora() + dt.timedelta(seconds=int(j.get("expires_in", 5184000)))).isoformat()
            renovado = _ahora()
        except ErrorIG as e:
            # Un token recien creado (menos de 24 h) no se puede renovar todavia.
            print("[ig] no se ha renovado el token (%s); se usa tal cual" % str(e)[:120])

    yo = _pide("GET", "/me", token, fields="user_id,username")
    ig_id = os.environ.get("IG_USER_ID", "").strip() or str(yo.get("user_id") or yo.get("id"))
    supa.guarda("ig_token", {"id": 1, "token": token, "ig_user_id": ig_id, "usuario": yo.get("username"),
                             "huella_secreto": huella or (fila or {}).get("huella_secreto"),
                             "renovado": renovado.isoformat(), "caduca": caduca})
    _SESION.update(token=token, id=ig_id, usuario=yo.get("username"))
    return token, ig_id


def _sesion_facebook(token: str, fila: dict | None, huella: str | None) -> tuple[str, str]:
    """Busca el Instagram conectado. Con un token de usuario, se queda con el de la pagina."""
    campos = "instagram_business_account{id,username}"
    try:
        paginas = _pide("GET", "/me/accounts", token, fields="access_token," + campos).get("data") or []
    except ErrorIG:
        # Ya es un token de pagina: /me es la propia pagina y no tiene /accounts.
        paginas = [dict(_pide("GET", "/me", token, fields=campos), access_token=token)]
    conectada = next((p for p in paginas if p.get("instagram_business_account")), None)
    if not conectada:
        raise ErrorIG("ninguna pagina de Facebook de este token tiene un Instagram conectado")
    cuenta = conectada["instagram_business_account"]
    token = conectada.get("access_token") or token
    ig_id = os.environ.get("IG_USER_ID", "").strip() or str(cuenta["id"])
    supa.guarda("ig_token", {"id": 1, "token": token, "ig_user_id": ig_id, "usuario": cuenta.get("username"),
                             "huella_secreto": huella or (fila or {}).get("huella_secreto"),
                             "renovado": _ahora().isoformat(), "caduca": None})
    _SESION.update(token=token, id=ig_id, usuario=cuenta.get("username"))
    return token, ig_id


def usuario() -> str:
    sesion()
    return _SESION.get("usuario") or ""


def cuota_restante() -> int:
    token, yo = sesion()
    j = _pide("GET", "%s/content_publishing_limit" % yo, token, fields="quota_usage,config")
    fila = (j.get("data") or [{}])[0]
    return int((fila.get("config") or {}).get("quota_total", 50)) - int(fila.get("quota_usage", 0))


def _espera(contenedor: str, limite_s: int = 600) -> None:
    token, _ = sesion()
    fin = time.time() + limite_s
    pausa = 4.0
    while time.time() < fin:
        j = _pide("GET", contenedor, token, fields="status_code,status")
        estado = j.get("status_code")
        if estado == "FINISHED":
            return
        if estado in ("ERROR", "EXPIRED"):
            raise ErrorIG("Meta no ha podido procesar el contenido (%s): %s" % (estado, j.get("status")))
        time.sleep(pausa)
        pausa = min(pausa * 1.5, 20)
    raise ErrorIG("Meta sigue procesando tras %d s" % limite_s)


def _publica(contenedor: str) -> dict:
    token, yo = sesion()
    _espera(contenedor)
    j = _pide("POST", "%s/media_publish" % yo, token, creation_id=contenedor)
    media = j["id"]
    enlace = ""
    try:
        enlace = _pide("GET", media, token, fields="permalink").get("permalink", "")
    except ErrorIG:
        pass
    return {"media_id": media, "permalink": enlace}


def publica_fotos(urls: list[str], caption: str) -> dict:
    """Una foto o un carrusel (2-10), segun cuantas haya."""
    token, yo = sesion()
    caption = (caption or "")[:MAX_CAPTION]
    if len(urls) == 1:
        c = _pide("POST", "%s/media" % yo, token, image_url=urls[0], caption=caption)
        return _publica(c["id"])
    if not 2 <= len(urls) <= 10:
        raise ErrorIG("un carrusel lleva de 2 a 10 fotos (hay %d)" % len(urls))
    hijos = [_pide("POST", "%s/media" % yo, token, image_url=u, is_carousel_item="true")["id"] for u in urls]
    for h in hijos:
        _espera(h)
    c = _pide("POST", "%s/media" % yo, token, media_type="CAROUSEL", children=",".join(hijos), caption=caption)
    return _publica(c["id"])


def publica_reel(video_url: str, caption: str) -> dict:
    token, yo = sesion()
    c = _pide("POST", "%s/media" % yo, token, media_type="REELS", video_url=video_url,
              caption=(caption or "")[:MAX_CAPTION], share_to_feed="true")
    return _publica(c["id"])

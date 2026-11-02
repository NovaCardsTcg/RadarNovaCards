# -*- coding: utf-8 -*-
"""Lo minimo de Supabase que necesita el publicador: tablas, rpc y almacen.

Usa la clave de servicio (secreto SUPABASE_SERVICE_KEY), la misma del radar.
Las tablas ig_* no tienen politicas de lectura: solo se ven con esta clave.
"""

from __future__ import annotations

import os

import requests

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
CLAVE = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = "instagram"


def activo() -> bool:
    return bool(URL and CLAVE)


def _cab(extra: dict | None = None) -> dict:
    c = {"apikey": CLAVE, "Authorization": "Bearer " + CLAVE}
    c.update(extra or {})
    return c


def lee(tabla: str, **params) -> list[dict]:
    r = requests.get("%s/rest/v1/%s" % (URL, tabla), params=params, headers=_cab(), timeout=60)
    r.raise_for_status()
    return r.json()


def inserta(tabla: str, filas, conflicto: str | None = None) -> list[dict]:
    pref = "return=representation"
    params = {}
    if conflicto:
        pref += ",resolution=ignore-duplicates"
        params["on_conflict"] = conflicto
    r = requests.post("%s/rest/v1/%s" % (URL, tabla), json=filas, params=params, timeout=60,
                      headers=_cab({"Content-Type": "application/json", "Prefer": pref}))
    r.raise_for_status()
    return r.json() if r.content else []


def cambia(tabla: str, filtro: dict, datos: dict) -> list[dict]:
    r = requests.patch("%s/rest/v1/%s" % (URL, tabla), params=filtro, json=datos, timeout=60,
                       headers=_cab({"Content-Type": "application/json", "Prefer": "return=representation"}))
    r.raise_for_status()
    return r.json() if r.content else []


def guarda(tabla: str, fila: dict) -> None:
    """Inserta o sustituye por clave primaria."""
    r = requests.post("%s/rest/v1/%s" % (URL, tabla), json=fila, timeout=60,
                      headers=_cab({"Content-Type": "application/json",
                                    "Prefer": "resolution=merge-duplicates"}))
    r.raise_for_status()


def rpc(funcion: str, datos: dict | None = None):
    r = requests.post("%s/rest/v1/rpc/%s" % (URL, funcion), json=datos or {}, timeout=60,
                      headers=_cab({"Content-Type": "application/json"}))
    r.raise_for_status()
    return r.json() if r.content else None


def sube(ruta: str, contenido: bytes, tipo: str) -> str:
    """Sube un fichero al bucket publico y devuelve su URL publica."""
    r = requests.post("%s/storage/v1/object/%s/%s" % (URL, BUCKET, ruta), data=contenido, timeout=300,
                      headers=_cab({"Content-Type": tipo, "x-upsert": "true", "cache-control": "3600"}))
    r.raise_for_status()
    return "%s/storage/v1/object/public/%s/%s" % (URL, BUCKET, ruta)


def borra(rutas: list[str]) -> None:
    if not rutas:
        return
    r = requests.delete("%s/storage/v1/object/%s" % (URL, BUCKET), json={"prefixes": rutas}, timeout=60,
                        headers=_cab({"Content-Type": "application/json"}))
    r.raise_for_status()

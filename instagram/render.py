# -*- coding: utf-8 -*-
"""Dibuja las diapositivas de un post con Chromium (Playwright) y las da en JPEG.

Las fotos se bajan antes desde Python y entran como data: URI. Muchas tiendas
bloquean sus imagenes si se piden desde otra web, y asi se les pide como un
navegador normal (y, si aun asi fallan, por el intermediario /img de la app).
Una diapositiva de producto sin foto no se publica.
"""

from __future__ import annotations

import base64
import datetime as dt
import os
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

AQUI = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
MAX_BYTES = 6 * 1024 * 1024
_CACHE: dict[str, str | None] = {}


def _data_uri(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("data:"):
        return url
    if url in _CACHE:
        return _CACHE[url]
    host = urlparse(url).netloc
    intentos = [(url, {"User-Agent": UA, "Referer": "https://%s/" % host, "Accept": "image/avif,image/webp,image/*,*/*"}),
                ("https://cromeltcg.com/img?u=" + quote(url, safe=""), {"User-Agent": UA})]
    out = None
    for u, cab in intentos:
        try:
            r = requests.get(u, headers=cab, timeout=30)
            tipo = r.headers.get("content-type", "").split(";")[0]
            if r.ok and tipo.startswith("image/") and len(r.content) < MAX_BYTES:
                out = "data:%s;base64,%s" % (tipo, base64.b64encode(r.content).decode())
                break
        except requests.RequestException:
            continue
    _CACHE[url] = out
    return out


_VIVAS: dict[str, bytes] = {}


def _capturas_vivas() -> None:
    """Calendario de la web real, con su cuenta atras al dia. Si falla, se usa la guardada."""
    from playwright.sync_api import sync_playwright

    canal = os.environ.get("IG_NAVEGADOR") or None
    try:
        with sync_playwright() as p:
            nav = p.chromium.launch(channel=canal) if canal else p.chromium.launch()
            ctx = nav.new_context(viewport={"width": 390, "height": 794}, device_scale_factor=2,
                                  color_scheme="dark", is_mobile=True, locale="es-ES")
            ctx.add_init_script("try { localStorage.setItem('sobreVisto', '1') } catch (e) {}")
            pag = ctx.new_page()
            pag.goto("https://cromeltcg.com/lanzamientos", wait_until="networkidle", timeout=45000)
            _VIVAS["lanzamientos"] = pag.screenshot(type="jpeg", quality=84)
            pag.locator('main a[href^="/lanzamientos/"]').first.click()
            pag.wait_for_load_state("networkidle")
            pag.wait_for_timeout(800)
            _VIVAS["lanzamiento"] = pag.screenshot(type="jpeg", quality=84)
            nav.close()
    except Exception as e:
        print("[ig] sin capturas en directo, uso las guardadas: %s" % str(e)[:150])


def _captura(nombre: str) -> str:
    if nombre in ("lanzamientos", "lanzamiento") and nombre not in _VIVAS:
        _capturas_vivas()
    datos = _VIVAS.get(nombre) or (AQUI / "capturas" / (nombre + ".jpg")).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(datos).decode()


def _prepara(diapos: list[dict]) -> list[dict]:
    listas = []
    for d in diapos:
        d = dict(d)
        if d["tipo"] in ("producto", "carta"):
            d["imagen"] = _data_uri(d.get("imagen"))
            if not d["imagen"]:
                print("[ig] diapositiva sin foto, fuera: %s" % (d.get("titulo") or d.get("nombre")))
                continue
            if d.get("tienda_logo"):
                d["tienda_logo"] = _data_uri(d["tienda_logo"])
        if d["tipo"] == "paso":
            d["captura"] = _captura(d["captura"])
        if d["tipo"] == "app_portada":
            d["capturas"] = [_captura(c) for c in d["capturas"]]
        if d["tipo"] == "portada":
            d["imagenes"] = [x for x in (_data_uri(u) for u in d.get("imagenes") or []) if x]
            if d.get("logo"):
                d["logo"] = _data_uri(d["logo"])
        listas.append(d)
    return listas


def dibuja(diapos: list[dict], carpeta: Path) -> list[Path]:
    """Devuelve las rutas de los JPEG (1080x1350), en orden."""
    from playwright.sync_api import sync_playwright

    diapos = _prepara(diapos)[:10]  # un carrusel admite 10 como mucho
    carpeta.mkdir(parents=True, exist_ok=True)
    try:
        from zoneinfo import ZoneInfo
        ahora = dt.datetime.now(ZoneInfo("Europe/Madrid"))
    except Exception:
        ahora = dt.datetime.now()
    fecha = ahora.strftime("%d/%m/%Y")
    salida = []
    with sync_playwright() as p:
        # En el PC se puede usar Edge (IG_NAVEGADOR=msedge) sin bajar Chromium.
        canal = os.environ.get("IG_NAVEGADOR") or None
        nav = p.chromium.launch(channel=canal) if canal else p.chromium.launch()
        pag = nav.new_page(viewport={"width": 1080, "height": 1350}, device_scale_factor=1)
        pag.goto((AQUI / "plantilla.html").as_uri())
        pag.wait_for_load_state("networkidle")
        for i, d in enumerate(diapos, 1):
            fallos = pag.evaluate("([d, n, t, f]) => pinta(d, n, t, f)", [d, i, len(diapos), fecha])
            if fallos:
                print("[ig] imagenes que no cargan en la diapositiva %d: %s" % (i, fallos))
            ruta = carpeta / ("%02d.jpg" % i)
            pag.screenshot(path=str(ruta), type="jpeg", quality=90)
            salida.append(ruta)
        nav.close()
    return salida

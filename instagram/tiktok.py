# -*- coding: utf-8 -*-
"""TikTok -> Instagram: ver que hay nuevo en el perfil y bajar un post sin marca de agua.

Igual que en tiktok-mirror: TikTok da cada video en dos variantes, con y sin
marca; yt-dlp las expone como formatos distintos y aqui se descarta la marcada.
Los posts de fotos (carrusel de TikTok) pasan como carrusel de Instagram.

Leer el perfil entero es lo fragil (TikTok cambia la web cada pocas semanas y
rompe el extractor). Si falla, el publicador avisa y se pueden mandar los
enlaces a mano con /ig <url> en Telegram, que eso va siempre por video suelto.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"}


def _ydl(extra: dict | None = None):
    import yt_dlp
    o = {"quiet": True, "no_warnings": True, "noprogress": True}
    o.update(extra or {})
    return yt_dlp.YoutubeDL(o)


def id_de(url: str) -> str | None:
    m = re.search(r"/(?:video|photo)/(\d+)", url)
    return m.group(1) if m else None


def recientes(usuario: str, n: int = 8) -> list[dict]:
    """Ultimos posts del perfil: [{id, url, descripcion}], el mas nuevo primero."""
    with _ydl({"extract_flat": True, "playlistend": n}) as y:
        info = y.extract_info("https://www.tiktok.com/@%s" % usuario.lstrip("@"), download=False)
    out = []
    for e in (info.get("entries") or [])[:n]:
        vid = str(e.get("id") or "")
        if not vid.isdigit():
            continue
        out.append({"id": vid, "url": e.get("url") or "https://www.tiktok.com/@%s/video/%s" % (usuario, vid),
                    "descripcion": e.get("description") or e.get("title") or ""})
    return out


def _sin_marca(f: dict) -> bool:
    return "watermark" not in " ".join(str(f.get(k) or "") for k in ("format_id", "format_note", "format")).lower()


def baja(url: str, carpeta: Path) -> dict:
    """{tipo: video|fotos, archivos: [Path], descripcion, id}."""
    carpeta.mkdir(parents=True, exist_ok=True)
    with _ydl() as y:
        info = y.extract_info(url, download=False)
    desc = info.get("description") or info.get("title") or ""
    vid = str(info.get("id") or id_de(url) or "tiktok")
    formatos = [f for f in (info.get("formats") or [])
                if f.get("url") and f.get("vcodec") not in (None, "none") and _sin_marca(f)]
    if formatos:
        # H.264 primero (lo que mejor traga Instagram), luego la mayor calidad.
        formatos.sort(key=lambda f: (str(f.get("vcodec") or "").startswith(("h264", "avc")),
                                     f.get("height") or 0, f.get("tbr") or 0))
        elegido = formatos[-1]
        with _ydl({"format": elegido["format_id"], "outtmpl": str(carpeta / (vid + ".%(ext)s")), "overwrites": True}) as y:
            y.extract_info(url, download=True)
        archivos = sorted(carpeta.glob(vid + ".*"))
        if not archivos:
            raise RuntimeError("yt-dlp no ha dejado el video")
        return {"tipo": "video", "archivos": archivos[:1], "descripcion": desc, "id": vid}

    # Post de fotos: cada entrada trae la imagen como formato sin video ni audio.
    from PIL import Image
    urls = []
    for e in info.get("entries") or [info]:
        for f in e.get("formats") or []:
            if f.get("vcodec") == "none" and f.get("acodec") == "none" and f.get("url"):
                urls.append(f["url"])
                break
    archivos = []
    for i, u in enumerate(urls[:10], 1):
        crudo = carpeta / ("%s-%02d.bin" % (vid, i))
        r = requests.get(u, headers=UA, timeout=60)
        r.raise_for_status()
        crudo.write_bytes(r.content)
        jpg = carpeta / ("%s-%02d.jpg" % (vid, i))
        Image.open(crudo).convert("RGB").save(jpg, "JPEG", quality=92)  # Instagram solo quiere JPEG
        archivos.append(jpg)
    if not archivos:
        raise RuntimeError("no hay formato sin marca de agua ni fotos en ese post")
    return {"tipo": "fotos", "archivos": archivos, "descripcion": desc, "id": vid}


# Etiquetas que solo tienen sentido en TikTok.
_SOLO_TIKTOK = re.compile(r"#(fyp|fy|foryou|foryoupage|parati|xyzbca|viral|tiktok\w*)\b", re.I)


def caption(descripcion: str) -> str:
    t = _SOLO_TIKTOK.sub("", descripcion or "")
    t = re.sub(r"[ \t]{2,}", " ", t).strip()
    pie = "\n\n👉 Ofertas y reposiciones TCG al momento en cromeltcg.com (enlace en la bio)"
    if "#cromeltcg" not in t.lower():
        pie += "\n\n#cromeltcg #pokemontcg #tcgespaña"
    return (t + pie).strip()

# -*- coding: utf-8 -*-
"""Publicador de Instagram de Cromel. Lo lanza .github/workflows/instagram.yml cada 20 min.

En cada pasada:
  1. Mira si hay TikToks nuevos en el perfil (ig_ajustes.tiktok_usuario) y los
     mete en la cola segun el modo: 'todo' (pasan solos tras un margen para
     pararlos), 'elegir' (esperan a /ig si N) o 'nada'.
  2. Si ha llegado una de las horas de ig_ajustes.horas, encola el post
     automatico de ese hueco (ver contenido.py).
  3. Publica lo que toca de la cola (como mucho 3 por pasada) y avisa por Telegram.
  4. Borra del almacen las imagenes de hace mas de 30 dias.

Uso a mano:
  python -m instagram.publicar                  pasada normal
  python -m instagram.publicar --prueba [tipo]  dibuja los posts en instagram/prueba/ sin publicar
  python -m instagram.publicar --ya tipo        publica ya un post de ese tipo
"""

from __future__ import annotations

import datetime as dt
import html
import os
import sys
import tempfile
from pathlib import Path

import requests

from . import contenido, ig, render, supa, tiktok

AQUI = Path(__file__).resolve().parent
MADRID = contenido.MADRID
MAX_POR_PASADA = 3
INTENTOS = 3
VENTANA_HUECO = dt.timedelta(hours=3)  # el cron de GitHub llega tarde a menudo


class SinMaterial(Exception):
    pass


def _ahora() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def telegram(texto: str) -> None:
    token, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        print("[telegram] " + texto)
        return
    try:
        requests.post("https://api.telegram.org/bot%s/sendMessage" % token, timeout=20,
                      json={"chat_id": chat, "text": texto, "parse_mode": "HTML", "disable_web_page_preview": True})
    except requests.RequestException as e:
        print("[telegram] no enviado: %s" % e)


def aviso(texto: str, nivel: str = "warning") -> None:
    """Linea que GitHub ensena como anotacion, visible sin iniciar sesion: asi se
    puede diagnosticar desde fuera. Nunca lleva el token (Meta no lo repite en sus errores)."""
    print("::%s title=instagram::%s" % (nivel, " ".join(str(texto).split())[:900]))


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def ajustes() -> dict:
    filas = supa.lee("ig_ajustes", select="*", id="eq.1")
    return filas[0] if filas else {"activo": True, "tiktok_modo": "nada", "horas": ["13:30", "20:30"],
                                   "tiktok_margen_min": 60}


# ------------------------------------------------------------------ 1. TikTok

def mira_tiktok(aj: dict) -> None:
    usuario = (aj.get("tiktok_usuario") or "").lstrip("@")
    if aj.get("tiktok_modo") == "nada" or not usuario:
        return
    try:
        lista = tiktok.recientes(usuario)
    except Exception as e:
        print("[tiktok] no se puede leer el perfil: %s" % e)
        hoy = dt.date.today().isoformat()
        nota = supa.inserta("ig_publicaciones", [{"tipo": "nota", "origen_id": "nota:tiktok:" + hoy,
                                                  "estado": "descartado", "error": str(e)[:300]}], "origen_id")
        if nota:  # una vez al dia, no en cada pasada
            telegram("⚠️ Hoy no puedo leer tu perfil de TikTok (@%s). Lo que quieras pasar a Instagram, "
                     "mándamelo con <code>/ig enlace-del-tiktok</code>." % esc(usuario))
        return
    if not lista:
        return
    ya = supa.lee("ig_publicaciones", select="origen_id", tipo="eq.tiktok")
    conocidos = {f["origen_id"] for f in ya}
    if not conocidos:
        # Primera vez: lo que ya estaba publicado no se sube de golpe.
        supa.inserta("ig_publicaciones", [{"tipo": "tiktok", "origen_id": v["id"], "estado": "descartado",
                                           "datos": {"url": v["url"], "base": True}} for v in lista], "origen_id")
        telegram("📸 Vigilo tu TikTok <b>@%s</b>. A partir de ahora, lo que publiques %s." % (
            esc(usuario), "pasará a Instagram" if aj["tiktok_modo"] == "todo" else "te lo pregunto antes de pasarlo a Instagram"))
        return
    for v in reversed(lista):  # el mas antiguo primero
        if v["id"] in conocidos:
            continue
        encola_tiktok(v["url"], aj, v["id"], v.get("descripcion") or "")


def encola_tiktok(url: str, aj: dict, vid: str | None = None, descripcion: str = "", a_mano: bool = False) -> dict | None:
    vid = vid or tiktok.id_de(url) or url
    elegir = aj.get("tiktok_modo") == "elegir" and not a_mano
    margen = 0 if a_mano else int(aj.get("tiktok_margen_min") or 0)
    cuando = _ahora() + dt.timedelta(minutes=margen)
    filas = supa.inserta("ig_publicaciones", [{
        "tipo": "tiktok", "origen_id": vid, "estado": "esperando" if elegir else "pendiente",
        "publicar_desde": cuando.isoformat(), "datos": {"url": url, "descripcion": descripcion[:500]}}], "origen_id")
    if not filas or a_mano:
        return filas[0] if filas else None
    n = filas[0]["id"]
    resumen = esc((descripcion or url)[:90])
    if elegir:
        telegram("🎬 TikTok nuevo: “%s”\n¿Lo paso a Instagram? <code>/ig si %d</code> · <code>/ig no %d</code>" % (resumen, n, n))
    else:
        telegram("🎬 TikTok nuevo: “%s”\nPasa a Instagram a las %s. Para que no pase: <code>/ig no %d</code>"
                 % (resumen, cuando.astimezone(MADRID).strftime("%H:%M"), n))
    return filas[0]


# ------------------------------------------------------------------ 2. huecos automaticos

def programa_huecos(aj: dict) -> None:
    ahora = _ahora().astimezone(MADRID)
    for i, h in enumerate(aj.get("horas") or []):
        try:
            hh, mm = (int(x) for x in h.split(":"))
        except ValueError:
            continue
        hueco = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not hueco <= ahora < hueco + VENTANA_HUECO:
            continue
        tipo = contenido.tipo_del_hueco(i, ahora.date(), len(aj["horas"]))
        supa.inserta("ig_publicaciones", [{"tipo": tipo, "origen_id": "auto:%s:%s" % (ahora.date().isoformat(), h),
                                           "estado": "pendiente", "datos": {"generar": tipo}}], "origen_id")


# ------------------------------------------------------------------ 3. publicar

def _sube_fotos(rutas: list[Path], carpeta: str) -> tuple[list[str], list[str]]:
    urls, claves = [], []
    for r in rutas:
        clave = "%s/%s" % (carpeta, r.name)
        urls.append(supa.sube(clave, r.read_bytes(), "image/jpeg"))
        claves.append(clave)
    return urls, claves


def publica_auto(fila: dict, tmp: Path) -> dict:
    tipo = (fila.get("datos") or {}).get("generar") or fila["tipo"]
    post = contenido.genera(tipo)
    if not post:
        raise SinMaterial("hoy no hay material para ningun post")
    datos = {"usados": post["usados"], "generar": tipo}
    if post.get("reel"):
        video = render.video(post["diapositivas"], tmp)
        clave = "reels/%d.mp4" % fila["id"]
        url = supa.sube(clave, video.read_bytes(), "video/mp4")
        try:
            res = ig.publica_reel(url, post["caption"])
        finally:
            supa.borra([clave])
        datos["archivos"] = []
        return {"res": res, "caption": post["caption"], "tipo": post["tipo"], "datos": datos}
    rutas = render.dibuja(post["diapositivas"], tmp)
    if not rutas:
        raise SinMaterial("no ha salido ninguna diapositiva")
    urls, claves = _sube_fotos(rutas, "posts/%d" % fila["id"])
    res = ig.publica_fotos(urls, post["caption"])
    # Que lo vean tambien los seguidores: una historia que falle no estropea el post.
    try:
        ig.publica_historia(urls[0])
    except Exception as e:
        print("[ig] sin historia: %s" % str(e)[:150])
    datos["archivos"] = claves
    return {"res": res, "caption": post["caption"], "tipo": post["tipo"], "datos": datos}


def publica_tiktok(fila: dict, tmp: Path) -> dict:
    datos = fila.get("datos") or {}
    post = tiktok.baja(datos["url"], tmp)
    texto = tiktok.caption(post["descripcion"])
    if post["tipo"] == "video":
        video = post["archivos"][0]
        if video.stat().st_size > 50 * 1024 * 1024:
            raise RuntimeError("el video pesa mas de 50 MB (limite del almacen gratis)")
        clave = "reels/%d%s" % (fila["id"], video.suffix)
        url = supa.sube(clave, video.read_bytes(), "video/mp4")
        try:
            res = ig.publica_reel(url, texto)
        finally:
            supa.borra([clave])
        claves = []
    else:
        urls, claves = _sube_fotos(post["archivos"], "tiktok/%d" % fila["id"])
        res = ig.publica_fotos(urls, texto)
    datos.update(archivos=claves, descripcion=post["descripcion"][:500])
    return {"res": res, "caption": texto, "tipo": "tiktok", "datos": datos}


NOMBRES = {"reel": "Reel de la app", "saber": "Guía útil", "frase": "Frase", "app": "Publicidad de la app", "chollos": "Chollos del día", "reposiciones": "Reposiciones", "funko": "Funko en oferta",
           "otros_juegos": "Otros juegos", "suben": "Cartas que suben", "valiosas": "Cartas más caras",
           "lanzamientos": "Lanzamientos", "resumen": "Resumen semanal", "tiktok": "TikTok → Reel"}


def caduca_viejos() -> int:
    """Un post automatico que no salio a su hora ya no vale: sus chollos y su
    cuenta atras son de otro dia. Sin esto, tras unos dias sin token saldria
    toda la cola de golpe. Los TikToks si esperan: son tuyos y no caducan."""
    limite = (_ahora() - VENTANA_HUECO).isoformat()
    viejos = supa.cambia("ig_publicaciones", {"estado": "eq.pendiente", "tipo": "neq.tiktok",
                                              "publicar_desde": "lt." + limite},
                         {"estado": "descartado", "error": "caducado: no salio a su hora"})
    return len(viejos)


def procesa_cola() -> int:
    n = caduca_viejos()
    if n:
        print("[ig] %d posts automaticos caducados (no salieron a su hora)" % n)
    filas = supa.lee("ig_publicaciones", select="*", estado="eq.pendiente",
                     publicar_desde="lte." + _ahora().isoformat(), order="publicar_desde", limit=str(MAX_POR_PASADA))
    if not filas:
        return 0
    try:
        cuota = ig.cuota_restante()
    except ig.ErrorIG as e:
        # La cola sigue intacta: sale en cuanto el token funcione. Se avisa una vez al dia.
        aviso("no puedo conectar con Instagram: %s" % e)
        nota = supa.inserta("ig_publicaciones", [{"tipo": "nota", "origen_id": "nota:token:" + dt.date.today().isoformat(),
                                                  "estado": "descartado", "error": str(e)[:300]}], "origen_id")
        if nota:
            telegram("⚠️ No puedo conectar con Instagram:\n<code>%s</code>" % esc(str(e)[:400]))
        return 0
    if cuota < len(filas):
        print("[ig] sin cuota de publicacion hoy")
        return 0
    hechas = 0
    for f in filas:
        # Se reserva la fila: si dos pasadas coinciden, solo una la publica.
        if not supa.cambia("ig_publicaciones", {"id": "eq.%d" % f["id"], "estado": "eq.pendiente"}, {"estado": "publicando"}):
            continue
        with tempfile.TemporaryDirectory() as d:
            try:
                r = publica_tiktok(f, Path(d)) if f["tipo"] == "tiktok" else publica_auto(f, Path(d))
            except SinMaterial as e:
                supa.cambia("ig_publicaciones", {"id": "eq.%d" % f["id"]}, {"estado": "descartado", "error": str(e)})
                print("[ig] %d descartado: %s" % (f["id"], e))
                continue
            except Exception as e:
                n = int(f.get("intentos") or 0) + 1
                final = n >= INTENTOS
                supa.cambia("ig_publicaciones", {"id": "eq.%d" % f["id"]}, {
                    "estado": "error" if final else "pendiente", "intentos": n, "error": str(e)[:500],
                    "publicar_desde": (_ahora() + dt.timedelta(minutes=30)).isoformat()})
                aviso("%d fallo (%d/%d): %s" % (f["id"], n, INTENTOS, e))
                if final:
                    telegram("❌ No he podido publicar en Instagram (%s, n.º %d):\n<code>%s</code>"
                             % (NOMBRES.get(f["tipo"], f["tipo"]), f["id"], esc(str(e)[:300])))
                continue
        supa.cambia("ig_publicaciones", {"id": "eq.%d" % f["id"]}, {
            "estado": "publicado", "tipo": r["tipo"], "caption": r["caption"], "datos": r["datos"], "error": None,
            "ig_media_id": r["res"]["media_id"], "permalink": r["res"]["permalink"], "publicado_en": _ahora().isoformat()})
        telegram("📸 Publicado en Instagram: <b>%s</b>\n%s" % (NOMBRES.get(r["tipo"], r["tipo"]), r["res"]["permalink"]))
        hechas += 1
    return hechas


# ------------------------------------------------------------------ 4. limpieza

def limpia() -> None:
    hace = (_ahora() - dt.timedelta(days=30)).isoformat()
    filas = supa.lee("ig_publicaciones", select="id,datos", estado="eq.publicado", publicado_en="lt." + hace,
                     **{"datos->>limpio": "is.null"}, limit="20")
    for f in filas:
        d = f.get("datos") or {}
        try:
            supa.borra(d.get("archivos") or [])
        except requests.HTTPError as e:
            print("[ig] no se han podido borrar las imagenes de %d: %s" % (f["id"], e))
            continue
        d["limpio"] = True
        supa.cambia("ig_publicaciones", {"id": "eq.%d" % f["id"]}, {"datos": d})


# ------------------------------------------------------------------ main

def prueba(tipos: list[str]) -> None:
    salida = AQUI / "prueba"
    for t in tipos or list(contenido.GENERADORES):
        post = contenido.GENERADORES[t]()
        if not post:
            print("%-13s sin material" % t)
            continue
        if post.get("reel"):
            print("%-13s %s" % (t, render.video(post["diapositivas"], salida / t)))
            continue
        rutas = render.dibuja(post["diapositivas"], salida / t)
        (salida / t / "caption.txt").write_text(post["caption"], encoding="utf-8")
        print("%-13s %d diapositivas -> %s" % (t, len(rutas), salida / t))


def main(argv: list[str]) -> int:
    if not supa.activo():
        print("faltan SUPABASE_URL y SUPABASE_SERVICE_KEY")
        return 1
    if argv[:1] == ["--prueba"]:
        prueba(argv[1:])
        return 0
    if argv[:1] == ["--ya"] and len(argv) > 1:
        supa.inserta("ig_publicaciones", [{"tipo": argv[1], "origen_id": "ya:%s:%s" % (argv[1], _ahora().isoformat()),
                                           "datos": {"generar": argv[1]}}])
    try:
        aj = ajustes()
        token = os.environ.get("IG_TOKEN") or supa.lee("ig_token", select="id", id="eq.1")
    except requests.HTTPError as e:
        aviso("falta la migracion 13 en Supabase (%s): no hago nada" % e)
        return 0
    if not token:
        aviso("falta el secreto IG_TOKEN: no hago nada (ver instagram/README.md)")
        return 0
    if not aj.get("activo", True):
        aviso("publicador en pausa (/ig on para reanudar)")
        return 0
    mira_tiktok(aj)
    programa_huecos(aj)
    n = procesa_cola()
    limpia()
    try:
        filas = supa.lee("ig_publicaciones", select="estado,tipo,error,creado", order="id.desc", limit="200")
        cuenta = {}
        for f in filas:
            cuenta[f["estado"]] = cuenta.get(f["estado"], 0) + 1
        ultimo = next((f for f in filas if f.get("error") and f["tipo"] != "nota"), None)
        aviso("pasada: %d publicadas | cola %s | horas %s | ultimo error: %s" % (
            n, cuenta, aj.get("horas"), (ultimo or {}).get("error", "ninguno")[:300]), "notice")
    except requests.HTTPError:
        print("pasada terminada: %d publicadas" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

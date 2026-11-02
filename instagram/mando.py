# -*- coding: utf-8 -*-
"""Comando /ig del bot de Telegram: manejar el Instagram desde el movil.

Lo atiende el radar (avisos.py -> app_db.comando), que es quien lee los
mensajes del bot; aqui solo se cambian las tablas ig_* y el publicador lo
recoge en su siguiente pasada (cada 20 min). Solo usa requests: el radar no
tiene ni Playwright ni yt-dlp.
"""

from __future__ import annotations

import datetime as dt
import html
import re

from . import supa

AYUDA = """<b>Instagram</b>
/ig - estado, cola y ultimos posts
/ig on · /ig off - encender o pausar el publicador
/ig &lt;enlace de TikTok&gt; - pasar ese TikTok a Instagram ya
/ig si 12 · /ig no 12 - aprobar o parar el TikTok n.º 12
/ig tiktok todo|elegir|nada - que hacer con tus TikToks nuevos
/ig usuario cromeltcg - tu usuario de TikTok
/ig margen 60 - minutos de margen antes de pasar un TikTok (modo todo)
/ig horas 13:30 18:00 20:30 - horas de los posts automaticos
/ig ya chollos - publicar ya un post (reel, saber, frase, app, chollos, reposiciones, funko, otros_juegos, suben, valiosas, lanzamientos, resumen)"""

TIPOS = ("reel", "saber", "frase", "app", "chollos", "reposiciones", "funko", "otros_juegos", "suben", "valiosas", "lanzamientos", "resumen")
ICONO = {"pendiente": "⏳", "esperando": "❓", "publicando": "🔄", "publicado": "✅", "error": "❌", "descartado": "🚫"}


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def _cambia_ajustes(datos: dict) -> None:
    datos["actualizado"] = dt.datetime.now(dt.timezone.utc).isoformat()
    supa.cambia("ig_ajustes", {"id": "eq.1"}, datos)


def _estado() -> str:
    aj = (supa.lee("ig_ajustes", select="*", id="eq.1") or [{}])[0]
    tok = (supa.lee("ig_token", select="usuario,caduca,renovado", id="eq.1") or [{}])[0]
    cola = supa.lee("ig_publicaciones", select="id,tipo,estado,publicar_desde,datos",
                    estado="in.(pendiente,esperando,publicando)", order="publicar_desde", limit="8")
    ult = supa.lee("ig_publicaciones", select="id,tipo,estado,permalink,error,publicado_en",
                   estado="in.(publicado,error)", order="id.desc", limit="5")
    l = ["📸 <b>Instagram</b> %s" % ("encendido" if aj.get("activo") else "en PAUSA (/ig on)")]
    l.append("Cuenta: %s" % ("@" + esc(tok["usuario"]) if tok.get("usuario") else "sin conectar (falta el secreto IG_TOKEN)"))
    if tok.get("caduca"):
        l.append("Token válido hasta el %s (se renueva solo)" % tok["caduca"][:10])
    l.append("Posts automáticos a las %s" % ", ".join(aj.get("horas") or []))
    l.append("TikTok: %s · modo <b>%s</b>" % ("@" + esc(aj["tiktok_usuario"]) if aj.get("tiktok_usuario") else "sin usuario (/ig usuario)",
                                             esc(aj.get("tiktok_modo"))))
    if cola:
        l.append("\n<b>En cola</b>")
        for f in cola:
            l.append("%s %d · %s %s" % (ICONO.get(f["estado"], ""), f["id"], esc(f["tipo"]),
                                        esc(((f.get("datos") or {}).get("descripcion") or "")[:40])))
    if ult:
        l.append("\n<b>Últimos</b>")
        for f in ult:
            l.append("%s %d · %s %s" % (ICONO.get(f["estado"], ""), f["id"], esc(f["tipo"]),
                                        esc(f.get("permalink") or (f.get("error") or "")[:60])))
    return "\n".join(l)


def comando(arg: str) -> str:
    if not supa.activo():
        return "Falta conectar Supabase (SUPABASE_URL y SUPABASE_SERVICE_KEY)."
    partes = arg.split()
    sub = partes[0].lower() if partes else ""
    resto = partes[1:]
    try:
        if not sub:
            return _estado()
        if sub in ("ayuda", "help"):
            return AYUDA
        if sub in ("on", "off"):
            _cambia_ajustes({"activo": sub == "on"})
            return "Instagram %s." % ("encendido" if sub == "on" else "en pausa: no publico nada hasta /ig on")
        if "tiktok.com" in sub:
            from .tiktok import id_de
            url = partes[0]
            filas = supa.inserta("ig_publicaciones", [{
                "tipo": "tiktok", "origen_id": id_de(url) or url, "estado": "pendiente",
                "datos": {"url": url, "a_mano": True}}], "origen_id")
            if not filas:
                supa.cambia("ig_publicaciones", {"origen_id": "eq." + (id_de(url) or url), "estado": "in.(esperando,descartado,error)"},
                            {"estado": "pendiente", "intentos": 0, "publicar_desde": dt.datetime.now(dt.timezone.utc).isoformat()})
            return "🎬 Apuntado. Sale en Instagram en la próxima pasada (unos 20 min)."
        if sub in ("si", "sí", "no") and resto and resto[0].isdigit():
            n = int(resto[0])
            if sub == "no":
                hecho = supa.cambia("ig_publicaciones", {"id": "eq.%d" % n, "estado": "in.(pendiente,esperando)"}, {"estado": "descartado"})
                return "🚫 El n.º %d no se publica." % n if hecho else "El n.º %d ya no está en cola." % n
            hecho = supa.cambia("ig_publicaciones", {"id": "eq.%d" % n, "estado": "in.(esperando,descartado,error)"},
                                {"estado": "pendiente", "intentos": 0, "publicar_desde": dt.datetime.now(dt.timezone.utc).isoformat()})
            return "✅ El n.º %d sale en la próxima pasada." % n if hecho else "No encuentro el n.º %d (o ya está publicado)." % n
        if sub == "tiktok" and resto and resto[0] in ("todo", "elegir", "nada"):
            _cambia_ajustes({"tiktok_modo": resto[0]})
            return {"todo": "Tus TikToks nuevos pasarán a Instagram solos (con margen para pararlos).",
                    "elegir": "Te preguntaré por cada TikTok nuevo antes de pasarlo.",
                    "nada": "Ya no miro tu TikTok. Puedes seguir mandándome enlaces con /ig enlace."}[resto[0]]
        if sub == "usuario" and resto:
            u = resto[0].lstrip("@").strip()
            _cambia_ajustes({"tiktok_usuario": u})
            return "Vigilo el TikTok @%s." % esc(u)
        if sub == "margen" and resto and resto[0].isdigit():
            _cambia_ajustes({"tiktok_margen_min": min(int(resto[0]), 1440)})
            return "Margen de %s min antes de pasar cada TikTok." % resto[0]
        if sub == "horas" and resto:
            horas = [h for h in resto if re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", h)]
            if not horas:
                return "Uso: /ig horas 13:30 18:00 20:30"
            _cambia_ajustes({"horas": horas[:4]})
            return "Posts automáticos a las %s (hora de España)." % ", ".join(horas[:4])
        if sub == "ya" and resto and resto[0] in TIPOS:
            ahora = dt.datetime.now(dt.timezone.utc).isoformat()
            supa.inserta("ig_publicaciones", [{"tipo": resto[0], "origen_id": "ya:%s:%s" % (resto[0], ahora),
                                               "datos": {"generar": resto[0]}}])
            return "Apuntado: post de <b>%s</b> en la próxima pasada (unos 20 min)." % resto[0]
        return AYUDA
    except Exception as e:
        return "No he podido hablar con la base de datos: %s" % esc(str(e)[:150])

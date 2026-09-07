# -*- coding: utf-8 -*-
"""Capa de Telegram: enviar avisos y atender los comandos del movil.

No se usa python-telegram-bot a proposito. Ese paquete esta pensado para un
proceso vivo escuchando siempre, y aqui el bot no vive: despierta cada X
minutos en GitHub Actions, hace su trabajo y muere. Con la API HTTP a pelo son
cuatro llamadas y cero dependencias que mantener.

Los comandos se leen con getUpdates al principio de cada ejecucion, asi que
tardan como mucho un ciclo en aplicarse. Para cambiar palabras clave o apagar
una tienda desde el sofa, es de sobra.
"""

from __future__ import annotations

import html
import os
import time

import requests

API = "https://api.telegram.org/bot%s/%s"
TIMEOUT = 20


class SinCredenciales(Exception):
    pass


def _token() -> str:
    t = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not t:
        raise SinCredenciales("falta TELEGRAM_TOKEN")
    return t


def _chat() -> str:
    c = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not c:
        raise SinCredenciales("falta TELEGRAM_CHAT_ID")
    return c


def _llama(metodo: str, **datos):
    """Una llamada a la API. Devuelve None si falla: un aviso perdido no puede
    tumbar la ejecucion ni impedir que se guarde el estado."""
    try:
        r = requests.post(API % (_token(), metodo), json=datos, timeout=TIMEOUT)
        j = r.json()
        if not j.get("ok"):
            print("[telegram] %s fallo: %s" % (metodo, str(j.get("description"))[:120]))
            return None
        return j.get("result")
    except (requests.RequestException, ValueError) as e:
        print("[telegram] %s error: %s" % (metodo, type(e).__name__))
        return None


def esc(t: str) -> str:
    return html.escape(str(t or ""), quote=False)


def hay_credenciales() -> bool:
    return bool(os.environ.get("TELEGRAM_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def enviar(texto: str, imagen: str | None = None, boton: tuple[str, str] | None = None,
           silencioso: bool = False):
    """Manda un mensaje. Con imagen usa sendPhoto, que en el movil entra como
    tarjeta con foto y se lee de un vistazo sin abrir nada.

    Sin credenciales no revienta: escribe por pantalla. Asi se puede probar en
    local sin token, y una pasada en Actions con el secreto mal puesto sigue
    guardando estado en vez de morirse a la mitad.
    """
    if not hay_credenciales():
        print("[sin telegram] " + texto.replace("\n", " | ")[:160])
        return None

    base = {"chat_id": _chat(), "parse_mode": "HTML",
            "disable_notification": silencioso}
    if boton:
        base["reply_markup"] = {"inline_keyboard": [[{"text": boton[0], "url": boton[1]}]]}

    if imagen:
        r = _llama("sendPhoto", photo=imagen, caption=texto[:1024], **base)
        if r:
            return r
        # Imagen caducada o bloqueada por Telegram: se manda igual sin foto.
    return _llama("sendMessage", text=texto[:4096],
                  link_preview_options={"is_disabled": True}, **base)


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------

AYUDA = """<b>Radar Pokemon</b>

/estado - que vigila, cuanto lleva visto y salud de cada tienda
/palabras - ver las palabras clave
/add pokemon 151 - anadir palabra clave
/quitar pokemon 151 - quitar palabra clave
/tiendas - ver tiendas y si estan encendidas
/tienda amazon off - encender o apagar una tienda
/precio 5 - avisar solo si el precio baja un 5% o mas
/max 15 - maximo de avisos por ciclo
/pausa - dejar de avisar (sigue tomando nota)
/reanudar - volver a avisar
/prueba - manda un aviso de prueba
/ayuda - esto"""


def leer_comandos(estado: dict, config: dict) -> list[str]:
    """Aplica los comandos pendientes sobre config. Devuelve las respuestas.

    Solo obedece al chat autorizado. Sin esto, cualquiera que diera con el bot
    podria apagarle las tiendas o vaciarle las palabras clave.
    """
    respuestas: list[str] = []
    if not hay_credenciales():
        return respuestas
    offset = estado.get("telegram_offset", 0)
    updates = _llama("getUpdates", offset=offset, timeout=0, limit=50) or []

    mio = str(_chat())
    for u in updates:
        estado["telegram_offset"] = u["update_id"] + 1
        msg = u.get("message") or u.get("channel_post") or {}
        texto = (msg.get("text") or "").strip()
        quien = str((msg.get("chat") or {}).get("id", ""))
        if not texto.startswith("/"):
            continue
        if quien != mio:
            print("[telegram] ignorado comando de chat ajeno %s" % quien)
            continue
        respuestas.append(_ejecuta(texto, config, estado))
    return [r for r in respuestas if r]


def _ejecuta(texto: str, config: dict, estado: dict) -> str:
    partes = texto.split(maxsplit=1)
    cmd = partes[0].lower().lstrip("/").split("@")[0]
    arg = partes[1].strip() if len(partes) > 1 else ""

    if cmd in ("ayuda", "start", "help"):
        return AYUDA

    if cmd == "palabras":
        return "<b>Palabras clave</b>\n" + "\n".join("- " + esc(p) for p in config["palabras"])

    if cmd == "add":
        if not arg:
            return "Uso: /add pokemon 151"
        if arg.lower() in [p.lower() for p in config["palabras"]]:
            return "Ya estaba: " + esc(arg)
        config["palabras"].append(arg)
        return "Anadida: <b>%s</b>\nAhora vigilo %d palabras." % (esc(arg), len(config["palabras"]))

    if cmd == "quitar":
        antes = len(config["palabras"])
        config["palabras"] = [p for p in config["palabras"] if p.lower() != arg.lower()]
        if len(config["palabras"]) == antes:
            return "No tenia esa palabra: " + esc(arg)
        return "Quitada: " + esc(arg)

    if cmd == "tiendas":
        filas = []
        for k, act in config["tiendas"].items():
            salud = estado.get("salud", {}).get(k, {})
            fallos = salud.get("fallos", 0)
            n = len([1 for c in estado.get("productos", {}) if c.startswith(k + ":")])
            marca = "ON " if act else "OFF"
            aviso = " (%d fallos seguidos)" % fallos if fallos else ""
            filas.append("%s <b>%s</b> - %d vistos%s" % (marca, k, n, aviso))
        return "<b>Tiendas</b>\n" + "\n".join(filas) + "\n\nCambiar: /tienda amazon off"

    if cmd == "tienda":
        try:
            nombre, valor = arg.split()
        except ValueError:
            return "Uso: /tienda amazon off"
        if nombre not in config["tiendas"]:
            return "No conozco esa tienda. Mira /tiendas"
        config["tiendas"][nombre] = valor.lower() in ("on", "si", "1", "true")
        return "%s queda <b>%s</b>" % (esc(nombre), "encendida" if config["tiendas"][nombre] else "apagada")

    if cmd == "precio":
        try:
            config["bajada_min_pct"] = max(0.0, float(arg.replace(",", ".").rstrip("%")))
        except ValueError:
            return "Uso: /precio 5   (avisa si baja un 5% o mas)"
        return "Aviso de bajada a partir del <b>%g%%</b>" % config["bajada_min_pct"]

    if cmd == "max":
        try:
            config["max_avisos"] = max(1, int(arg))
        except ValueError:
            return "Uso: /max 15"
        return "Maximo <b>%d</b> avisos por ciclo" % config["max_avisos"]

    if cmd == "pausa":
        config["pausado"] = True
        return "Pausado. Sigo tomando nota de todo, pero no te aviso. /reanudar para volver."

    if cmd == "reanudar":
        config["pausado"] = False
        return "Reanudado."

    if cmd == "prueba":
        enviar("Prueba del radar. Si ves esto, los avisos llegan bien.",
               boton=("Abrir Amazon", "https://www.amazon.es/s?k=pokemon"))
        return ""

    if cmd == "estado":
        return resumen(estado, config)

    return "No conozco /%s. Mira /ayuda" % esc(cmd)


def resumen(estado: dict, config: dict) -> str:
    total = len(estado.get("productos", {}))
    filas = []
    for k, act in config["tiendas"].items():
        if not act:
            continue
        n = len([1 for c in estado.get("productos", {}) if c.startswith(k + ":")])
        salud = estado.get("salud", {}).get(k, {})
        marca = "!" if salud.get("fallos") else "-"
        filas.append("%s %s: %d" % (marca, k, n))
    return (
        "<b>Radar Pokemon</b>\n"
        "%s\n"
        "Vigilando <b>%d</b> productos en total.\n\n"
        "%s\n\n"
        "Palabras: %d | Bajada minima: %g%% | Max avisos: %d\n"
        "Ultima pasada: %s"
        % ("PAUSADO" if config.get("pausado") else "Activo",
           total, "\n".join(filas) or "(ninguna tienda encendida)",
           len(config["palabras"]), config.get("bajada_min_pct", 5),
           config.get("max_avisos", 15),
           estado.get("ultima_pasada", "nunca"))
    )


def responde(textos: list[str]):
    for t in textos:
        enviar(t)
        time.sleep(0.4)

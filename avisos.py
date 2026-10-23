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
from urllib.parse import quote_plus

import requests

import tiendas

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


# Ultimo resultado de la API, para poder dejarlo escrito en estado.json. Sin
# esto no hay forma de distinguir "los secrets estan mal" de "todo bien pero no
# habia nada que contar", que desde fuera se ven exactamente igual.
ULTIMO = {"intentos": 0, "ok": 0, "error": ""}


def _llama(metodo: str, **datos):
    """Una llamada a la API. Devuelve None si falla: un aviso perdido no puede
    tumbar la ejecucion ni impedir que se guarde el estado."""
    ULTIMO["intentos"] += 1
    try:
        for intento in range(2):
            r = requests.post(API % (_token(), metodo), json=datos, timeout=TIMEOUT)
            j = r.json()
            if j.get("ok"):
                ULTIMO["ok"] += 1
                return j.get("result")
            # Telegram limita a ~1 mensaje por segundo y chat y ~20 por minuto
            # en un canal. Cuando se pasa, responde 429 con los segundos que
            # hay que esperar; ignorarlo es perder el mensaje. La primera
            # pasada tras un parón solto 46 envios seguidos y perdio 15 asi.
            espera = (j.get("parameters") or {}).get("retry_after")
            if j.get("error_code") == 429 and espera and intento == 0:
                print("[telegram] 429, espero %ss" % espera)
                time.sleep(min(int(espera), 60) + 1)
                continue
            desc = str(j.get("description"))[:120]
            ULTIMO["error"] = "%s: %s" % (metodo, desc)
            print("[telegram] %s fallo: %s" % (metodo, desc))
            return None
    except (requests.RequestException, ValueError) as e:
        ULTIMO["error"] = "%s: %s" % (metodo, type(e).__name__)
        print("[telegram] %s error: %s" % (metodo, type(e).__name__))
        return None


def esc(t: str) -> str:
    return html.escape(str(t or ""), quote=False)


def hay_credenciales() -> bool:
    return bool(os.environ.get("TELEGRAM_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


# Accesos directos del menu fijado en el canal. Cada uno lleva a la busqueda de
# Amazon con la etiqueta de afiliado pegada.
#
# Se enlaza a la BUSQUEDA y no a productos concretos a proposito: un producto se
# agota y el enlace queda muerto, mientras que una busqueda sigue viva siempre y
# ademas no muestra precios, que es lo que el acuerdo de Afiliados no deja
# publicar sin su API.
CATEGORIAS = [
    ("Fundas", "fundas protectores cartas pokemon"),
    ("Carpetas", "carpeta archivador cartas pokemon"),
    ("Cajas y deck box", "caja almacenaje cartas pokemon deck box"),
    ("Tapetes", "tapete playmat pokemon"),
    ("Dados y contadores", "dados contadores pokemon jcc"),
    ("Sobres", "sobres pokemon jcc"),
    ("Cajas de entrenador", "caja entrenador elite pokemon"),
    ("Latas", "lata pokemon jcc"),
    ("One Piece: sobres y cajas", "one piece card game booster"),
    ("One Piece: mazos", "one piece card game starter deck"),
]


def menu_canal(config: dict) -> tuple[str, list]:
    """El mensaje de menu que se fija arriba del canal.

    Es el "buscador dentro del canal" que se puede tener de verdad: Telegram no
    deja poner una caja de texto en un canal, pero si botones permanentes. Cada
    uno abre la busqueda de Amazon ya filtrada y con la etiqueta.
    """
    etiqueta = config.get("amazon_afiliado", "")
    filas, fila = [], []
    for nombre, consulta in config.get("categorias") or CATEGORIAS:
        url = "https://www.amazon.es/s?k=" + quote_plus(consulta)
        if etiqueta:
            url += "&tag=" + etiqueta
        fila.append({"text": nombre, "url": url})
        if len(fila) == 2:
            filas.append(fila)
            fila = []
    if fila:
        filas.append(fila)

    texto = ("<b>Busca lo que necesites</b>\n\n"
             "Accesorios y producto sellado de Pokemon y One Piece. Toca una categoria y te "
             "lleva directo.\n\n"
             "<i>En el canal se avisa de producto nuevo, reposiciones y bajadas "
             "de precio en Amazon, El Corte Ingles, GAME y CardZone.</i>")
    return texto, filas


def enviar(texto: str, imagen: str | None = None, boton: tuple[str, str] | None = None,
           silencioso: bool = False, destino: str | None = None,
           teclado: list | None = None):
    """Manda un mensaje. Con imagen usa sendPhoto, que en el movil entra como
    tarjeta con foto y se lee de un vistazo sin abrir nada.

    Sin credenciales no revienta: escribe por pantalla. Asi se puede probar en
    local sin token, y una pasada en Actions con el secreto mal puesto sigue
    guardando estado en vez de morirse a la mitad.

    `destino` manda a otro chat que no sea el del dueno: se usa para el canal
    publico. El bot tiene que ser administrador del canal para poder publicar.
    """
    if not hay_credenciales():
        print("[sin telegram] " + texto.replace("\n", " | ")[:160])
        return None

    base = {"chat_id": destino or _chat(), "parse_mode": "HTML",
            "disable_notification": silencioso}
    if teclado:
        base["reply_markup"] = {"inline_keyboard": teclado}
    elif boton:
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
/vendedor amazon - en Amazon, solo lo que vende Amazon
/canal @micanal - publicar tambien en un canal publico
/menu - publicar y fijar el menu de busqueda en el canal
/borrar ultimos 3 - quitar del canal los ultimos avisos (o /borrar 17 18)
/afiliado tuetiqueta-21 - tu codigo de Amazon Afiliados
/reporte - resumen de lo que ha pasado
/reportes 24 - mandarmelo solo cada 24 h (off para quitarlo)
/vigilar &lt;url&gt; - vigilar el stock de un producto concreto
/dejar &lt;url&gt; - dejar de vigilarlo
/pausa - dejar de avisar (sigue tomando nota)
/reanudar - volver a avisar
/prueba - manda un aviso de prueba
/ayuda - esto

<b>App Cromel</b> (solo con Supabase conectado)
/moderar - reportes pendientes de la app, numerados
/ocultar 2 - ocultar el chollo o comentario del reporte 2
/mostrar 2 - volver a ensenar ese chollo
/resolver 2 - cerrar el reporte sin tocar nada
/bloquear 2 - ocultar ese comentario y bloquear a su autor 7 dias"""


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
        canal = config.get("canal_publico") or ""
        if not canal:
            return "Prueba enviada. No hay canal publico configurado (/canal)."
        # Se publica de verdad en el canal: es la unica forma de saber si el
        # bot tiene permiso, porque Telegram solo da el error al intentarlo.
        antes = ULTIMO["ok"]
        enviar("Prueba del radar. Si ves esto, el canal esta bien configurado.",
               destino=canal, silencioso=True)
        if ULTIMO["ok"] > antes:
            return "Prueba enviada aqui y publicada en <b>%s</b>." % esc(canal)
        return ("Aqui ha llegado, pero en <b>%s</b> ha fallado:\n<code>%s</code>\n\n"
                "Suele ser que el bot no es administrador del canal, o que no "
                "tiene marcado 'Publicar mensajes'."
                % (esc(canal), esc(ULTIMO["error"][:120])))

    if cmd == "vendedor":
        opciones = {"amazon": ["amazon.es"], "todos": [],
                    "global": ["amazon.es", "amazon.uk", "amazon.us"]}
        if arg.lower() not in opciones:
            actual = config.get("amazon_vendedores") or []
            return ("En Amazon ahora mismo: <b>%s</b>\n\n"
                    "/vendedor amazon - solo lo vendido por Amazon.es\n"
                    "/vendedor global - Amazon.es, UK y US\n"
                    "/vendedor todos - tambien vendedores externos"
                    % (", ".join(actual) if actual else "todos los vendedores"))
        config["amazon_vendedores"] = opciones[arg.lower()]
        return "En Amazon vigilo: <b>%s</b>" % (
            ", ".join(config["amazon_vendedores"]) or "todos los vendedores")

    if cmd in ("vigilar", "dejar"):
        producto = tiendas.desde_url(arg)
        if not producto:
            return ("Pasame el enlace del producto tal cual sale en el navegador.\n"
                    "Ejemplo: /vigilar https://www.game.es/sobre-de-cartas-...-251944")
        vig = estado.setdefault("vigilando", {})
        if cmd == "dejar":
            if vig.pop(producto.clave, None):
                return "Ya no vigilo: " + esc(producto.titulo)
            return "No lo tenia en la lista."
        # disp a None hace que entre en la cola de repaso en la proxima pasada.
        vig[producto.clave] = {"url": producto.url, "titulo": producto.titulo,
                               "disp": None, "visto": 0}
        return ("Vigilando <b>%s</b>\nTe aviso cuando pase de agotado a comprable.\n"
                "En la lista hay %d productos." % (esc(producto.titulo), len(vig)))

    if cmd == "borrar":
        canal = config.get("canal_publico") or ""
        if not canal:
            return "No hay canal configurado. Ponlo con /canal @tucanal"
        pub = estado.get("publicados", [])
        trozos = arg.replace(",", " ").split()
        if trozos and trozos[0].lower() in ("ultimos", "ultimo"):
            try:
                n = int(trozos[1]) if len(trozos) > 1 else 1
            except ValueError:
                n = 1
            ids = [x["id"] for x in pub[-n:]]
        else:
            ids = [int(x) for x in trozos if x.isdigit()]
        if not ids:
            return ("Uso: /borrar ultimos 3, o /borrar 17 18 19 con los numeros del "
                    "mensaje en el canal (se ven en t.me/%s/NUMERO).\n"
                    "Llevo anotados %d mensajes publicados." % (esc(canal.lstrip("@")), len(pub)))
        borrados = 0
        for mid in ids:
            # Un bot administrador puede borrar cualquier mensaje del canal si
            # tiene el permiso de eliminar; los suyos propios, siempre.
            if _llama("deleteMessage", chat_id=canal, message_id=mid):
                borrados += 1
                estado["publicados"] = [x for x in estado.get("publicados", []) if x["id"] != mid]
        if borrados < len(ids):
            return ("Borrados %d de %d. Ultimo error: <code>%s</code>\n\n"
                    "Si es por permisos, al bot le falta <b>Eliminar mensajes</b> en los "
                    "administradores del canal." % (borrados, len(ids), esc(ULTIMO["error"][:100])))
        return "Borrados %d mensajes del canal." % borrados

    if cmd == "menu":
        canal = config.get("canal_publico") or ""
        if not canal:
            return "No hay canal configurado. Ponlo con /canal @tucanal"
        texto_menu, teclado = menu_canal(config)
        res = enviar(texto_menu, destino=canal, teclado=teclado, silencioso=True)
        if not res:
            return ("No he podido publicar en %s:\n<code>%s</code>"
                    % (esc(canal), esc(ULTIMO["error"][:120])))
        # Fijarlo es lo que lo convierte en menu: queda arriba del canal para
        # todo el que entre, en vez de perderse entre los avisos.
        fijado = _llama("pinChatMessage", chat_id=canal,
                        message_id=res.get("message_id"),
                        disable_notification=True)
        aviso = "" if fijado else ("\n\nPublicado, pero no he podido fijarlo: "
                                   "al bot le falta el permiso de fijar mensajes.")
        return "Menu publicado en <b>%s</b>.%s" % (esc(canal), aviso)

    if cmd == "canal":
        if arg.lower() in ("off", "no", "quitar"):
            config["canal_publico"] = ""
            return "Canal publico desconectado. Los avisos solo vienen a este chat."
        if arg:
            config["canal_publico"] = arg
            return ("Publicare tambien en <b>%s</b>.\n\nRecuerda: el bot tiene que ser "
                    "administrador del canal, con permiso para publicar." % esc(arg))
        actual = config.get("canal_publico") or ""
        return ("Canal publico: <b>%s</b>\n\n"
                "/canal @micanal - publicar ahi tambien\n"
                "/canal off - dejar de publicar"
                % (esc(actual) if actual else "ninguno"))

    if cmd == "afiliado":
        if arg:
            config["amazon_afiliado"] = arg.strip()
            return "Etiqueta de afiliado de Amazon: <b>%s</b>" % esc(arg.strip())
        return ("Etiqueta de afiliado: <b>%s</b>\n\n/afiliado tuetiqueta-21"
                % (esc(config.get("amazon_afiliado")) or "ninguna"))

    if cmd == "reporte":
        return reporte(estado, config)

    if cmd == "reportes":
        if arg.lower() in ("off", "no", "0"):
            config["reporte_cada_h"] = 0
            return "Reportes automaticos apagados. Siempre te queda /reporte."
        try:
            config["reporte_cada_h"] = max(1, int(arg))
        except ValueError:
            actual = config.get("reporte_cada_h", 0)
            return ("Reportes automaticos: <b>%s</b>\n\n"
                    "/reportes 24 - uno al dia\n"
                    "/reportes 12 - manana y noche\n"
                    "/reportes off - ninguno"
                    % ("cada %d h" % actual if actual else "apagados"))
        return "Te mando un reporte cada <b>%d h</b>." % config["reporte_cada_h"]

    if cmd == "estado":
        return resumen(estado, config)

    # Moderacion de la app Cromel Chollos. Import local: app_db importa este
    # modulo para mandar mensajes, y asi no hay ciclo al cargar.
    import app_db
    if cmd in app_db.COMANDOS:
        return app_db.comando(cmd, arg, estado)

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
    vig = estado.get("vigilando", {})
    agotados = sum(1 for v in vig.values() if v.get("disp") is False)
    sin_ver = sum(1 for v in vig.values() if v.get("disp") is None)
    return (
        "<b>Radar Pokemon</b>\n"
        "%s\n"
        "Vigilando <b>%d</b> productos en total.\n\n"
        "%s\n\n"
        "<b>Stock</b>: %d productos en la lista, %d agotados ahora mismo"
        "%s\n\n"
        "Palabras: %d | Bajada minima: %g%% | Max avisos: %d\n"
        "Ultima pasada: %s"
        % ("PAUSADO" if config.get("pausado") else "Activo",
           total, "\n".join(filas) or "(ninguna tienda encendida)",
           len(vig), agotados,
           (", %d sin repasar todavia" % sin_ver) if sin_ver else "",
           len(config["palabras"]), config.get("bajada_min_pct", 5),
           config.get("max_avisos", 15),
           estado.get("ultima_pasada", "nunca"))
    )


def reporte(estado: dict, config: dict) -> str:
    """Resumen de lo que ha pasado desde el reporte anterior.

    El radar normal solo habla cuando hay novedades, asi que su silencio es
    ambiguo: no se distingue "no ha pasado nada" de "lleva media noche roto".
    El reporte es lo que resuelve eso, y por eso lleva siempre la salud de las
    tiendas aunque no haya nada que contar.
    """
    c = estado.get("contadores", {})
    desde = estado.get("reporte_desde", "")
    vig = estado.get("vigilando", {})
    agotados = sum(1 for v in vig.values() if v.get("disp") is False)

    lineas = ["<b>Reporte del radar</b>"]
    if desde:
        lineas.append("<i>desde %s</i>" % esc(desde))
    lineas.append("")

    total = c.get("nuevo", 0) + c.get("stock", 0) + c.get("precio", 0)
    if total:
        lineas.append("<b>%d avisos</b> en este periodo:" % total)
        for clave, etiqueta in (("nuevo", "productos nuevos"),
                                ("stock", "vuelven a estar disponibles"),
                                ("precio", "bajadas de precio")):
            if c.get(clave):
                lineas.append("  %d %s" % (c[clave], etiqueta))
    else:
        lineas.append("Sin novedades en este periodo.")

    destacados = c.get("destacados") or []
    if destacados:
        lineas.append("")
        lineas.append("<b>Lo mas interesante</b>")
        for d in destacados[:5]:
            lineas.append("  " + esc(d))

    lineas.append("")
    lineas.append("<b>Vigilando</b>")
    # El recuento de la ultima pasada, no el tamano de la memoria: ahi quedan
    # restos de cuando los filtros eran mas anchos.
    por_tienda = estado.get("recuento", {})
    for t, act in config["tiendas"].items():
        if not act:
            continue
        salud = estado.get("salud", {}).get(t, {})
        marca = " (no responde)" if salud.get("fallos") else ""
        lineas.append("  %s: %d%s" % (t, por_tienda.get(t, 0), marca))
    lineas.append("  stock vigilado: %d, de ellos %d agotados" % (len(vig), agotados))

    pend = len(estado.get("candidatos", {}))
    if pend:
        lineas.append("  bajadas en observacion: %d" % pend)
    return "\n".join(lineas)


def responde(textos: list[str]):
    for t in textos:
        enviar(t)
        time.sleep(0.4)

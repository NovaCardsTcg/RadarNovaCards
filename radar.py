# -*- coding: utf-8 -*-
"""Radar Pokemon: una pasada por las tiendas y avisa por Telegram.

Se ejecuta suelto, sin proceso de fondo: GitHub Actions lo llama cada pocos
minutos, lee el estado de la pasada anterior, compara y se muere. Todo lo que
tiene que sobrevivir entre ejecuciones esta en estado.json.

Uso:
    python radar.py            una pasada normal
    python radar.py --resembrar  vuelve a sembrar (no avisa, solo toma nota)
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import avisos
import tiendas
from tiendas import TiendaCaida, normaliza

AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(AQUI, "config.json")
ESTADO = os.path.join(AQUI, "estado.json")

# Cuantas fichas se abren por ciclo para poner titulo y precio a las altas
# nuevas de las fuentes tipo sitemap. Es un tope de cortesia con la tienda y
# de tiempo de ejecucion, no un limite de avisos.
MAX_FICHAS = 10


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def carga(ruta: str, defecto: dict) -> dict:
    try:
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        for k, v in defecto.items():
            d.setdefault(k, v)
        return d
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(defecto)


def guarda(ruta: str, datos: dict, legible: bool = False):
    """Escritura atomica. El estado va comprimido (son decenas de miles de
    claves y se reescribe en cada pasada); la config va indentada, que es un
    fichero que se abre a mano."""
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if legible:
            json.dump(datos, f, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            json.dump(datos, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, ruta)


CONFIG_DEFECTO = {
    "palabras": ["pokemon"],
    "excluir": [],
    "exigir_palabra": True,
    "tiendas": {k: True for k in tiendas.ADAPTADORES},
    "cada_min": {"amazon": 0, "eci": 0, "mediamarkt": 0,
                 "game": 30, "pokemoncenter": 30, "carrefour": 360},
    "bajada_min_pct": 5.0,
    "bajada_min_eur": 1.0,
    "max_avisos": 15,
    "rodaje_pasadas": 12,
    "pausado": False,
}

ESTADO_DEFECTO = {
    "productos": {},      # clave -> [precio, disponible]
    "salud": {},          # tienda -> {fallos, ultimo_error, ultima_ok}
    "sembrado": {},       # tienda -> true cuando ya tiene una foto inicial
    "ultima_tienda": {},  # tienda -> timestamp epoch de la ultima consulta
    "pasadas": 0,         # para saber cuando termina el rodaje
    "aviso_rodaje": False,
    "telegram_offset": 0,
    "ultima_pasada": "",
}


def toca(tienda: str, config: dict, estado: dict) -> bool:
    """Respeta la cadencia propia de cada tienda.

    Carrefour son 40 sitemaps de 1,5 MB: barrerlo cada 10 minutos seria tirar
    ancho de banda por tirarlo, porque las altas nuevas no van tan rapido.
    """
    cada = config.get("cada_min", {}).get(tienda, 0)
    if not cada:
        return True
    ultima = estado.get("ultima_tienda", {}).get(tienda, 0)
    return (time.time() - ultima) >= cada * 60


def relevante(p, config: dict, exigir_palabra: bool) -> bool:
    """Filtra el ruido.

    Las exclusiones se aplican siempre. La exigencia de que el titulo contenga
    una palabra clave solo se aplica a los buscadores, porque es ahi donde
    aparece la morralla: Amazon, buscando "pokemon", devuelve fundas de movil y
    calcetines.

    En las fuentes de sitemap seria contraproducente: el titulo se saca del
    slug de la URL, y en Pokemon Center un slug como
    "poke-ball-classic-clog-by-crocs" no lleva la palabra pokemon aunque el
    producto lo sea. Esas fuentes ya vienen filtradas por la URL (o, en el caso
    de la tienda oficial, no necesitan filtro ninguno).
    """
    t = normaliza(p.titulo)
    for mala in config.get("excluir", []):
        if normaliza(mala) in t:
            return False
    if not exigir_palabra or not config.get("exigir_palabra", True):
        return True
    return any(normaliza(pal.split()[0]) in t for pal in config["palabras"])


def formatea(tipo: str, p, anterior=None, config=None) -> tuple[str, tuple[str, str]]:
    iconos = {"nuevo": "NUEVO", "stock": "VUELVE EL STOCK", "precio": "BAJA DE PRECIO"}
    cab = "<b>%s</b> - %s" % (iconos[tipo], tiendas.NOMBRES.get(p.tienda, p.tienda))
    lineas = [cab, avisos.esc(p.titulo)]

    if tipo == "precio" and anterior and anterior[0]:
        pct = (anterior[0] - p.precio) / anterior[0] * 100
        lineas.append("<s>%.2f EUR</s>  <b>%.2f EUR</b>  (-%.0f%%)"
                      % (anterior[0], p.precio, pct))
    elif p.precio is not None:
        lineas.append("<b>%.2f EUR</b>" % p.precio)
    else:
        lineas.append("(precio sin confirmar, mira la ficha)")

    return "\n".join(lineas), ("Ver producto", p.url)


def compara(tienda: str, productos: list, config: dict, estado: dict,
            en_rodaje: bool) -> list[tuple]:
    """Devuelve los sucesos (tipo, producto, anterior) frente al estado guardado.

    Sobre el rodaje: el buscador de Amazon no devuelve siempre el mismo
    conjunto. Con la misma consulta y a un minuto de distancia aparecen y
    desaparecen articulos de la cola larga, asi que las primeras pasadas
    marcarian como "nuevo" cosas que llevan meses en el catalogo. Medido:
    20 falsos en la segunda pasada, 8 en la siguiente, 2 en la siguiente y 0
    despues; ECI y MediaMarkt, cero desde el principio.

    Por eso durante el rodaje se anotan los productos de los buscadores sin
    avisar. Las bajadas de precio y las vueltas de stock si avisan desde el
    minuto uno: esas se comparan contra un producto ya conocido y no las afecta
    la rotacion.
    """
    memoria = estado["productos"]
    sucesos = []
    _, da_precio = tiendas.ADAPTADORES[tienda]
    # Los sitemaps son el catalogo entero, no una muestra: ahi un alta nueva
    # es un alta nueva de verdad y no necesita rodaje.
    callar_nuevos = en_rodaje and da_precio

    for p in productos:
        anterior = memoria.get(p.clave)

        if anterior is None:
            if not callar_nuevos:
                sucesos.append(("nuevo", p, None))
        elif da_precio:
            pre_precio, pre_disp = (anterior + [None, None])[:2]
            if pre_disp is False and p.disponible is True:
                sucesos.append(("stock", p, anterior))
            elif (p.precio is not None and pre_precio and p.precio < pre_precio):
                baja_eur = pre_precio - p.precio
                baja_pct = baja_eur / pre_precio * 100
                if (baja_pct >= config["bajada_min_pct"]
                        and baja_eur >= config["bajada_min_eur"]):
                    sucesos.append(("precio", p, anterior))

        memoria[p.clave] = [p.precio, p.disponible]

    return sucesos


def salud_tienda(tienda: str, estado: dict, ok: bool, error: str = "") -> str | None:
    """Lleva la cuenta de fallos y decide si merece un aviso al movil.

    No se avisa al primer fallo: Amazon corta la peticion de vez en cuando y se
    recupera sola en la pasada siguiente, y eso no es noticia. Se avisa al
    tercer fallo seguido (media hora sin responder) y luego cada 6, para que
    una tienda caida de verdad no se convierta en spam.
    """
    s = estado["salud"].setdefault(tienda, {"fallos": 0, "ultimo_error": "", "ultima_ok": ""})
    if ok:
        caidos = s["fallos"]
        s["fallos"] = 0
        s["ultimo_error"] = ""
        s["ultima_ok"] = ahora()
        if caidos >= 3:
            return "<b>%s</b> vuelve a responder." % tiendas.NOMBRES.get(tienda, tienda)
        return None

    s["fallos"] += 1
    s["ultimo_error"] = error[:200]
    if s["fallos"] == 3 or s["fallos"] % 6 == 0:
        return ("<b>%s</b> no responde (%d intentos seguidos).\n<code>%s</code>\n\n"
                "Si sigue asi, seguramente han cambiado la web o han empezado a "
                "bloquear. El resto de tiendas siguen funcionando."
                % (tiendas.NOMBRES.get(tienda, tienda), s["fallos"], avisos.esc(error[:150])))
    return None


def main() -> int:
    resembrar = "--resembrar" in sys.argv
    config = carga(CONFIG, CONFIG_DEFECTO)
    estado = carga(ESTADO, ESTADO_DEFECTO)

    if not avisos.hay_credenciales():
        print("AVISO: sin TELEGRAM_TOKEN / TELEGRAM_CHAT_ID. La pasada se hace "
              "igual y el estado se guarda, pero los avisos salen por pantalla.")

    # Los comandos se atienden siempre, incluso en pausa: si no, /reanudar
    # no podria llegar nunca.
    respuestas = avisos.leer_comandos(estado, config)
    if respuestas:
        avisos.responde(respuestas)

    estado["pasadas"] = estado.get("pasadas", 0) + 1
    rodaje = config.get("rodaje_pasadas", 12)
    en_rodaje = estado["pasadas"] <= rodaje and not resembrar

    pendientes: list[tuple] = []
    avisos_sistema: list[str] = []
    resumen_pasada = []

    for tienda, activa in config["tiendas"].items():
        if not activa or tienda not in tiendas.ADAPTADORES:
            continue
        if not toca(tienda, config, estado):
            continue

        fn, da_precio = tiendas.ADAPTADORES[tienda]
        try:
            t0 = time.time()
            productos = fn(config["palabras"])
            productos = [p for p in productos if relevante(p, config, da_precio)]
            estado.setdefault("ultima_tienda", {})[tienda] = time.time()
            msg = salud_tienda(tienda, estado, ok=True)
            if msg:
                avisos_sistema.append(msg)
        except TiendaCaida as e:
            print("[%s] CAIDA: %s" % (tienda, e))
            msg = salud_tienda(tienda, estado, ok=False, error=str(e))
            if msg:
                avisos_sistema.append(msg)
            continue
        except Exception as e:  # noqa: BLE001 - una tienda rota no tumba el radar
            print("[%s] ERROR inesperado:\n%s" % (tienda, traceback.format_exc()))
            msg = salud_tienda(tienda, estado, ok=False,
                               error="%s: %s" % (type(e).__name__, e))
            if msg:
                avisos_sistema.append(msg)
            continue

        primera = not estado["sembrado"].get(tienda) or resembrar
        sucesos = compara(tienda, productos, config, estado, en_rodaje)
        resumen_pasada.append("%s:%d" % (tienda, len(productos)))
        print("[%s] %d productos, %d sucesos, %.1fs"
              % (tienda, len(productos), len(sucesos), time.time() - t0))

        if primera:
            # Primera foto de la tienda: se guarda todo y no se avisa de nada.
            # Sin esto, el estreno serian miles de mensajes de golpe.
            estado["sembrado"][tienda] = True
            avisos_sistema.append(
                "<b>%s</b> sembrada: %d productos anotados. A partir de ahora "
                "solo te aviso de los cambios."
                % (tiendas.NOMBRES.get(tienda, tienda), len(productos)))
            continue

        pendientes.extend(sucesos)

    # Las fuentes de sitemap solo traen la URL: se abre la ficha de las altas
    # nuevas para poder mandar titulo y precio de verdad.
    fichas = 0
    for tipo, p, ant in pendientes:
        if tipo == "nuevo" and not tiendas.ADAPTADORES[p.tienda][1] and fichas < MAX_FICHAS:
            tiendas.detalle(p)
            fichas += 1

    if config.get("pausado"):
        print("pausado: %d sucesos anotados sin avisar" % len(pendientes))
        pendientes = []

    # Primero lo nuevo, luego el stock, luego las bajadas: si hay recorte por
    # el tope de avisos, que caiga lo menos urgente.
    orden = {"nuevo": 0, "stock": 1, "precio": 2}
    pendientes.sort(key=lambda x: orden[x[0]])

    tope = config.get("max_avisos", 15)
    for tipo, p, ant in pendientes[:tope]:
        texto, boton = formatea(tipo, p, ant, config)
        avisos.enviar(texto, imagen=p.imagen, boton=boton)
        time.sleep(0.5)

    if len(pendientes) > tope:
        resto = len(pendientes) - tope
        avisos.enviar("Y <b>%d</b> cambios mas que no te mando para no llenarte el "
                      "movil. Sube el tope con /max si quieres verlos todos." % resto)

    if not en_rodaje and not estado.get("aviso_rodaje"):
        estado["aviso_rodaje"] = True
        avisos_sistema.append(
            "Rodaje terminado: ya conozco %d productos. A partir de ahora un "
            "aviso de NUEVO es un alta de verdad, no ruido del buscador."
            % len(estado["productos"]))

    for msg in avisos_sistema:
        avisos.enviar(msg, silencioso=True)
        time.sleep(0.4)

    estado["ultima_pasada"] = ahora()
    guarda(ESTADO, estado)
    guarda(CONFIG, config, legible=True)
    print("pasada %s | %s | %d avisos"
          % (estado["ultima_pasada"], " ".join(resumen_pasada), len(pendientes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

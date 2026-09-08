# -*- coding: utf-8 -*-
"""Radar Pokemon: una pasada por las tiendas y avisa por Telegram.

Se ejecuta suelto, sin proceso de fondo: GitHub Actions lo llama cada pocos
minutos, lee el estado de la pasada anterior, compara y se muere. Todo lo que
tiene que sobrevivir entre ejecuciones esta en estado.json.

Uso:
    python radar.py                      una pasada normal (la que corre en GitHub)
    python radar.py --resembrar          vuelve a sembrar, sin avisar de nada
    python radar.py --solo eci,carrefour solo esas tiendas
    python radar.py --estado otro.json   usa otro fichero de memoria

Las dos ultimas son las que permiten la pasada manual desde casa: El Corte
Ingles, MediaMarkt y Carrefour bloquean las IPs de centro de datos por las que
sale GitHub, pero desde una conexion domestica responden sin problema. Con
--solo y --estado se lanzan aparte y con su propia memoria, sin pisar la que
mantiene GitHub para las otras tres tiendas.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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

# Tope de fichas que se abren para confirmar bajadas de precio. Las bajadas de
# verdad son pocas; si un dia salen cien es que algo va mal, y mas vale mandar
# los avisos sin confirmar que tener la pasada media hora abriendo paginas.
MAX_COMPROBACIONES = 12


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def carga_env():
    """Lee un .env de al lado, si existe, para las ejecuciones desde casa.

    En GitHub las credenciales llegan como variables de entorno del workflow y
    esto no hace nada. En local evita tener que exportarlas a mano cada vez, y
    mantiene el token fuera del repo: .env esta en .gitignore.
    """
    ruta = os.path.join(AQUI, ".env")
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            # Lo que ya venga del entorno manda: en Actions no se pisa nada.
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


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
    "tematica": [],
    "destacar": [],
    "excluir_patron": [],
    "exigir_palabra": True,
    "tiendas": {k: True for k in tiendas.ADAPTADORES},
    "cada_min": {"amazon": 0, "eci": 0, "mediamarkt": 0,
                 "game": 30, "pokemoncenter": 30, "carrefour": 360},
    "bajada_min_pct": 5.0,
    "bajada_min_eur": 1.0,
    "max_avisos": 15,
    "rodaje_pasadas": 12,
    "amazon_vendedores": ["amazon.es"],
    "fichas_por_pasada": 25,
    "horas_silencio": 12,
    "pasadas_confirmar": 2,
    "horas_candidato": 24,
    "reporte_cada_h": 24,
    "pausado": False,
}

ESTADO_DEFECTO = {
    "productos": {},      # clave -> [precio, disponible]
    "salud": {},          # tienda -> {fallos, ultimo_error, ultima_ok}
    "sembrado": {},       # tienda -> true cuando ya tiene una foto inicial
    "ultima_tienda": {},  # tienda -> timestamp epoch de la ultima consulta
    "vigilando": {},      # clave -> {url, titulo, disp, visto} para el stock
    "candidatos": {},     # clave -> {base, precio, pasadas} de bajadas a confirmar
    "avisado": {},        # "clave|tipo" -> epoch del ultimo aviso, para el silencio
    "contadores": {},     # avisos acumulados desde el ultimo reporte
    "recuento": {},       # tienda -> productos que pasaron el filtro en la ultima pasada
    "reporte_desde": "",  # cuando empezo el periodo que cubre el proximo reporte
    "ultimo_reporte": 0,
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


def contiene(texto: str, termino: str) -> bool:
    """Busca el termino como palabra completa, no como trozo de otra palabra.

    Buscando subcadenas, "lata" casaba con "escarLATA" (los juegos de Switch) y
    con "pLATAforma" (los auriculares), y "sobre" casaba con la preposicion:
    "despertador pokemon bulbasaur SOBRE pokeball". Media juguetería entraba en
    el radar por accidente.

    Sirve tambien para terminos de varias palabras ("entrenador elite"), que se
    buscan enteros y seguidos.
    """
    return re.search(r"\b%s\b" % re.escape(normaliza(termino)), texto) is not None


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
        if contiene(t, mala):
            return False

    # Patrones para lo que una lista de palabras no sabe describir. El caso real
    # es la carta suelta: lo que la delata no es una palabra sino el numero de
    # coleccion en el titulo ("071/072", "SV041/SV122"). Con el filtro de
    # vendedor puesto seguian entrando 44 de 89 productos de Amazon, todas
    # cartas sueltas de revendedores.
    for patron in config.get("excluir_patron", []):
        if re.search(patron, p.titulo, re.I):
            return False

    # Filtro tematico. Sin el, "pokemon" en Carrefour saca 981 productos que son
    # mochilas, funkos, peluches, tazas y sabanas: solo 27 llevaban "cartas" en
    # el nombre. Con el se queda en unos 60, que ademas es lo que hace viable
    # comprobarles el stock uno por uno.
    tematica = config.get("tematica") or []
    if tematica and not any(contiene(t, x) for x in tematica):
        return False

    if not exigir_palabra or not config.get("exigir_palabra", True):
        return True
    # Todas las palabras del termino, no solo la primera. Mirando la primera,
    # "cartas pokemon" dejaba pasar cualquier cosa con "cartas" en el titulo:
    # entraban barajas de Disney y juegos de cartas de Stitch.
    return any(all(x in t for x in normaliza(pal).split())
               for pal in config["palabras"])


def destacado(p, config: dict) -> bool:
    """El producto encaja con algo de la lista `destacar` de la config."""
    t = normaliza(p.titulo)
    return any(contiene(t, x) for x in (config.get("destacar") or []))


def formatea(tipo: str, p, anterior=None, config=None) -> tuple[str, tuple[str, str]]:
    iconos = {"nuevo": "NUEVO", "stock": "VUELVE EL STOCK", "precio": "BAJA DE PRECIO"}
    cab = "<b>%s</b> - %s" % (iconos[tipo], tiendas.NOMBRES.get(p.tienda, p.tienda))
    if config and destacado(p, config):
        cab = "⭐ " + cab
    lineas = [cab, avisos.esc(p.titulo)]

    if tipo == "precio" and anterior and anterior[0]:
        pct = (anterior[0] - p.precio) / anterior[0] * 100
        lineas.append("<s>%.2f EUR</s>  <b>%.2f EUR</b>  (-%.0f%%)"
                      % (anterior[0], p.precio, pct))
        if getattr(p, "sin_confirmar", False):
            lineas.append("<i>No he podido abrir la ficha para confirmarlo.</i>")
    elif p.precio is not None:
        lineas.append("<b>%.2f EUR</b>" % p.precio)
    else:
        lineas.append("(precio sin confirmar, mira la ficha)")

    return "\n".join(lineas), ("Ver producto", p.url)


def baja_bastante(antes, ahora, config: dict) -> bool:
    """La bajada supera los dos umbrales? Se piden los dos a la vez.

    Solo el porcentaje deja pasar los centimos de un producto caro; solo los
    euros deja pasar bajadas irrelevantes en un producto barato.
    """
    if ahora is None or not antes or ahora >= antes:
        return False
    baja_eur = antes - ahora
    return (baja_eur / antes * 100 >= config["bajada_min_pct"]
            and baja_eur >= config["bajada_min_eur"])


def confirma_bajadas(pendientes: list[tuple], config: dict, estado: dict) -> list[tuple]:
    """Abre la ficha de cada bajada y comprueba que el precio es el de verdad.

    El precio de la tarjeta del buscador y el de la ficha no siempre coinciden:
    ofertas de otros vendedores que ganan y pierden la caja de compra,
    variantes del producto, promociones que caducan. El resultado era avisar de
    una bajada y que al pinchar apareciera el precio de siempre.

    Aqui manda la ficha, que es lo que ve la persona al pinchar:

      - Si la ficha confirma la bajada, el aviso lleva el precio de la ficha,
        no el del buscador, para que el mensaje y la pagina digan lo mismo.
      - Si la ficha NO la confirma, no se avisa y se corrige la memoria con el
        precio de la ficha.
      - Si la ficha no se puede leer, el aviso sale igual y lo dice: a estas
        alturas la bajada ya viene avalada por haber aguantado varias pasadas,
        que es una senal mas fuerte que una sola lectura del listado.

    Ese ultimo caso es el que provocaba el bucle de avisos repetidos. Antes se
    avisaba "con reserva" y se dejaba en memoria el precio sin verificar, asi
    que un articulo cuyo listado alterna entre dos precios (una ETB que unas
    veces sale a 52,99 y otras a 43,99) disparaba el mismo -17% una pasada si y
    otra no, indefinidamente. Al conservar la referencia anterior la oscilacion
    deja de morder, y sin confirmacion no sale ningun aviso: un aviso de precio
    que no se puede comprobar no vale nada, porque el precio es justo lo unico
    que estas afirmando.
    """
    salida = []
    comprobadas = 0
    for tipo, p, ant in pendientes:
        if tipo != "precio":
            salida.append((tipo, p, ant))
            continue

        if comprobadas >= MAX_COMPROBACIONES:
            # Se acabo el presupuesto de fichas, pero la bajada ya viene
            # confirmada por persistencia: se manda sin el contraste extra.
            # Antes se aplazaba reescribiendo la memoria con [base, None], que
            # ademas de tirar la confirmacion ganada borraba el stock conocido
            # y dejaba muerto el aviso de "vuelve el stock" de ese producto.
            p.sin_confirmar = True
            salida.append((tipo, p, ant))
            continue

        comprobadas += 1
        real = tiendas.precio_ficha(p)

        if real is None:
            # No se ha podido leer, pero la bajada ya venia avalada por haber
            # aguantado varias pasadas: se avisa diciendo que no se ha podido
            # contrastar contra la ficha.
            p.sin_confirmar = True
            salida.append((tipo, p, ant))
            continue

        estado["productos"][p.clave] = [real, p.disponible]
        if not baja_bastante(ant[0], real, config):
            print("[%s] bajada descartada: la tarjeta decia %.2f pero la ficha "
                  "dice %.2f (antes %.2f)" % (p.tienda, p.precio, real, ant[0]))
            continue

        p.precio = real
        salida.append((tipo, p, ant))
    return salida


def revisa_bajada(p, base, config: dict, estado: dict):
    """Exige que una bajada aguante dos pasadas antes de darla por buena.

    Es la defensa contra el bucle de avisos falsos. El listado de Amazon
    alterna a veces entre dos precios para el mismo articulo (una ETB que unas
    veces sale a 52,99 y otras a 43,99), y con la comparacion simple eso
    disparaba el mismo -17% una pasada si y otra no, para siempre.

    La clave esta en que, mientras una bajada esta a medio confirmar, la
    referencia NO se mueve. Asi la oscilacion no puede morder: si el precio
    vuelve a subir, se descarta el candidato y no ha pasado nada; si sigue
    abajo en la pasada siguiente, es que la bajada es de verdad.

    Se eligio esto en vez de fiarlo todo a abrir la ficha porque la ficha de
    Amazon no siempre se deja leer (llega a fallar 8 de 8 cuando la IP esta
    limitada), y un radar que depende de eso se queda mudo sin avisar de que
    esta mudo. Aqui no hace falta ni una peticion extra.
    """
    cands = estado.setdefault("candidatos", {})
    cand = cands.get(p.clave)

    hacen_falta = max(1, config.get("pasadas_confirmar", 2))

    # Un candidato caducado se tira. Con el listado de Amazon rotando, un
    # articulo puede desaparecer de los resultados y volver dias despues; sin
    # caducidad, confirmaria una bajada contra una referencia rancia.
    horas_cand = config.get("horas_candidato", 24)
    if cand and time.time() - cand.get("visto", 0) > horas_cand * 3600:
        print("[%s] candidato caducado tras %d h, se reinicia: %s"
              % (p.tienda, horas_cand, p.titulo[:44]))
        del cands[p.clave]
        cand = None

    def confirma(base_original):
        estado["productos"][p.clave] = [p.precio, p.disponible]
        return ("precio", p, [base_original, p.disponible])

    if cand:
        # Sigue igual de bajo, o mas: la bajada se sostiene.
        if p.precio <= cand["precio"] * 1.005:
            cand["pasadas"] += 1
            cand["precio"] = min(cand["precio"], p.precio)
            cand["visto"] = time.time()
            if cand["pasadas"] >= hacen_falta:
                base_original = cand.pop("base")
                del cands[p.clave]
                return confirma(base_original)
            print("[%s] bajada en observacion (%d/%d): %s"
                  % (p.tienda, cand["pasadas"], hacen_falta, p.titulo[:44]))
            return None

        # Ha subido respecto al minimo observado, pero puede seguir por debajo
        # de la referencia original. Sin volver a mirarlo contra la base, una
        # bajada de 52,99 a 46,99 que pasara antes por 43,99 se perdia para
        # siempre: el candidato se descartaba y 46,99 quedaba de referencia.
        base_original = cand["base"]
        del cands[p.clave]
        if baja_bastante(base_original, p.precio, config):
            print("[%s] sube a %.2f pero sigue por debajo de %.2f, se reinicia "
                  "la observacion: %s"
                  % (p.tienda, p.precio, base_original, p.titulo[:44]))
            base = base_original
        else:
            print("[%s] bajada descartada, el precio ha vuelto a %.2f: %s"
                  % (p.tienda, p.precio, p.titulo[:44]))
            return None

    if baja_bastante(base, p.precio, config):
        # Con pasadas_confirmar=1 se avisa ya, sin esperar a la siguiente.
        if hacen_falta <= 1:
            return confirma(base)
        cands[p.clave] = {"base": base, "precio": p.precio, "pasadas": 1,
                          "visto": time.time()}
        print("[%s] posible bajada %.2f -> %.2f, a confirmar en la siguiente "
              "pasada: %s" % (p.tienda, base, p.precio, p.titulo[:44]))
    return None


def quita_repetidos(pendientes: list[tuple], config: dict, estado: dict) -> list[tuple]:
    """Silencia el mismo aviso para el mismo producto durante unas horas.

    Es el cinturon ademas de los tirantes. Aunque una bajada sea real y este
    confirmada, si el precio va y viene no hace falta contarlo cada diez
    minutos: con saberlo una vez al dia sobra para decidir si comprar.
    """
    horas = config.get("horas_silencio", 12)
    if not horas:
        return pendientes
    registro = estado.setdefault("avisado", {})
    ahora_ts = time.time()
    salida = []
    for tipo, p, ant in pendientes:
        marca = "%s|%s" % (p.clave, tipo)
        ultimo = registro.get(marca, 0)
        if ahora_ts - ultimo < horas * 3600:
            print("[%s] %s silenciado (avisado hace %.1f h): %s"
                  % (p.tienda, tipo, (ahora_ts - ultimo) / 3600, p.titulo[:44]))
            continue
        salida.append((tipo, p, ant))

    # El registro no puede crecer para siempre: fuera lo caducado.
    limite = ahora_ts - horas * 3600
    estado["avisado"] = {k: v for k, v in registro.items() if v >= limite}
    return salida


def apunta_para_reporte(p, tipo: str, config: dict, estado: dict):
    """Suma el aviso al recuento del proximo reporte."""
    c = estado.setdefault("contadores", {})
    c[tipo] = c.get(tipo, 0) + 1
    # Lo destacado se guarda con nombre: en el reporte es lo unico que se lee
    # de verdad, el resto son numeros.
    if destacado(p, config):
        muestras = c.setdefault("destacados", [])
        if len(muestras) < 10:
            etiqueta = {"nuevo": "nuevo", "stock": "vuelve", "precio": "baja"}[tipo]
            muestras.append("%s - %s" % (etiqueta, p.titulo[:52]))


def sella_enviado(p, tipo: str, estado: dict):
    """Anota que este aviso SI ha salido, para que el silencio cuente desde ahi.

    Se sella al enviar y no al decidir: si el aviso se cae por el tope de
    max_avisos o porque Telegram falla, no se ha enterado nadie y no puede
    contar como avisado.
    """
    estado.setdefault("avisado", {})["%s|%s" % (p.clave, tipo)] = time.time()


def primera_vez_sembrando(tienda: str, estado: dict, resembrar: bool) -> bool:
    return not estado["sembrado"].get(tienda) or resembrar


def registra_para_vigilar(tienda: str, productos: list, estado: dict):
    """Apunta los productos de las fuentes de sitemap para poder mirarles el stock.

    En estas tiendas el sitemap dice que existe un producto, pero no si se puede
    comprar. Para eso hay que abrir la ficha, y son cientos: no caben en una
    pasada. Asi que se guarda la lista y se van repasando poco a poco.
    """
    vig = estado.setdefault("vigilando", {})
    for p in productos:
        entrada = vig.get(p.clave)
        if entrada is None:
            vig[p.clave] = {"url": p.url, "titulo": p.titulo, "disp": None, "visto": 0}
        else:
            entrada["url"] = p.url
            entrada["titulo"] = p.titulo


def repasa_stock(config: dict, estado: dict) -> list[tuple]:
    """Abre fichas del catalogo vigilado y detecta los que vuelven a estar a la venta.

    El orden de prioridad es lo que hace que esto funcione con un presupuesto
    pequeno de peticiones por pasada:

      1. Los que se sabe agotados. Son los unicos que pueden dar la noticia, asi
         que se miran siempre y primero.
      2. Los que no se han mirado nunca. Es el barrido inicial: descubre cuales
         estan agotados hoy. Con el filtro tematico son ~155 productos, o sea
         unas ocho pasadas.
      3. Los que constan disponibles, por si se agotan. Se repasan por turnos y
         sin prisa, que ahi no hay nada urgente.
    """
    vig = estado.get("vigilando") or {}
    if not vig:
        return []

    agotados = [k for k, v in vig.items() if v.get("disp") is False]
    sin_ver = [k for k, v in vig.items() if v.get("disp") is None]
    con_stock = sorted((k for k, v in vig.items() if v.get("disp") is True),
                       key=lambda k: vig[k].get("visto", 0))

    presupuesto = config.get("fichas_por_pasada", 25)
    cola = (agotados + sin_ver + con_stock)[:presupuesto]

    sucesos = []
    for clave in cola:
        v = vig[clave]
        tienda, pid = clave.split(":", 1)
        p = tiendas.Producto(tienda=tienda, pid=pid, titulo=v["titulo"], url=v["url"])
        precio, disponible = tiendas.ficha(p)
        v["visto"] = int(time.time())
        if disponible is None:
            continue  # no se ha podido leer: se deja como estaba
        antes = v.get("disp")
        v["disp"] = disponible
        if precio is not None:
            v["precio"] = precio
        if antes is False and disponible:
            p.precio = precio
            p.disponible = True
            sucesos.append(("stock", p, None))
    return sucesos


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
            elif p.precio is not None:
                suceso = revisa_bajada(p, pre_precio, config, estado)
                if suceso:
                    sucesos.append(suceso)
                # La bajada a medio confirmar no toca la referencia: de eso se
                # encarga revisa_bajada. Saltamos la actualizacion de abajo.
                if p.clave in estado.get("candidatos", {}):
                    continue

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
    p = argparse.ArgumentParser(description="Radar Pokemon: una pasada por las tiendas")
    p.add_argument("--resembrar", action="store_true",
                   help="vuelve a tomar la foto inicial sin avisar de nada")
    p.add_argument("--solo", default="",
                   help="lista separada por comas: solo estas tiendas")
    p.add_argument("--estado", default=ESTADO,
                   help="fichero de memoria a usar (por defecto estado.json)")
    args = p.parse_args()

    carga_env()
    resembrar = args.resembrar
    fichero_estado = args.estado if os.path.isabs(args.estado) \
        else os.path.join(AQUI, args.estado)

    config = carga(CONFIG, CONFIG_DEFECTO)
    estado = carga(fichero_estado, ESTADO_DEFECTO)

    if args.solo:
        pedidas = [t.strip() for t in args.solo.split(",") if t.strip()]
        desconocidas = [t for t in pedidas if t not in tiendas.ADAPTADORES]
        if desconocidas:
            print("tiendas desconocidas: %s" % ", ".join(desconocidas))
            return 2
        config["tiendas"] = {t: (t in pedidas) for t in tiendas.ADAPTADORES}
        # La cadencia lenta es para no machacar los sitemaps desde el cron. Una
        # pasada manual la pide una persona: si la pide, se hace y punto.
        config["cada_min"] = {t: 0 for t in tiendas.ADAPTADORES}
        print("pasada manual: %s" % ", ".join(pedidas))

    if not avisos.hay_credenciales():
        print("AVISO: sin TELEGRAM_TOKEN / TELEGRAM_CHAT_ID. La pasada se hace "
              "igual y el estado se guarda, pero los avisos salen por pantalla.")

    # Los comandos se atienden siempre, incluso en pausa: si no, /reanudar
    # no podria llegar nunca. En las pasadas manuales no, para que no le roben
    # los comandos a la que corre en GitHub, que es la que manda la config.
    if not args.solo:
        respuestas = avisos.leer_comandos(estado, config)
        if respuestas:
            avisos.responde(respuestas)

    # Al apagar una tienda, lo suyo queda en memoria ocupando sitio para nada:
    # Pokemon Center eran 8.399 productos. Se limpia, pero solo en la pasada
    # completa: en una manual con --solo el resto de tiendas estan apagadas de
    # mentira y borrarlas seria cargarse la memoria buena.
    if not args.solo:
        apagadas = {t for t, on in config["tiendas"].items() if not on}
        if apagadas:
            for deposito in ("productos", "vigilando"):
                antes = len(estado.get(deposito, {}))
                estado[deposito] = {k: v for k, v in estado.get(deposito, {}).items()
                                    if k.split(":", 1)[0] not in apagadas}
                fuera = antes - len(estado[deposito])
                if fuera:
                    print("[limpieza] %s: %d entradas de tiendas apagadas"
                          % (deposito, fuera))
            estado["salud"] = {k: v for k, v in estado.get("salud", {}).items()
                               if k not in apagadas}

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
            # Amazon admite filtrar por vendedor con la faceta de su buscador.
            extra = {}
            if tienda == "amazon" and config.get("amazon_vendedores"):
                extra["vendedores"] = config["amazon_vendedores"]
            productos = fn(config["palabras"], **extra)
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

        # Las tiendas de sitemap alimentan la lista de vigilancia de stock.
        if not da_precio and tienda in ("game", "carrefour"):
            registra_para_vigilar(tienda, productos, estado)

        # En las fuentes de sitemap lo que llega es el catalogo entero, asi que
        # lo que no esta es que ya no existe o ya no pasa el filtro: se puede
        # tirar sin miedo. En los buscadores NO se hace, porque ahi cada pasada
        # devuelve una muestra rotatoria y borrar lo que no ha salido hoy seria
        # olvidar medio catalogo y volver a avisarlo como nuevo manana.
        if not da_precio and not primera_vez_sembrando(tienda, estado, resembrar):
            vivos = {p.clave for p in productos}
            for deposito in ("productos", "vigilando"):
                estado[deposito] = {
                    k: v for k, v in estado.get(deposito, {}).items()
                    if not k.startswith(tienda + ":") or k in vivos}

        primera = not estado["sembrado"].get(tienda) or resembrar
        sucesos = compara(tienda, productos, config, estado, en_rodaje)
        resumen_pasada.append("%s:%d" % (tienda, len(productos)))
        # Lo que de verdad se vigila hoy. En "productos" quedan restos de
        # cuando los filtros eran mas anchos (Amazon acumulo 506 entradas que
        # ya no pasan el filtro), y contar esos en el reporte seria mentir.
        estado.setdefault("recuento", {})[tienda] = len(productos)
        print("[%s] %d productos, %d sucesos, %.1fs"
              % (tienda, len(productos), len(sucesos), time.time() - t0))

        if primera:
            # Primera foto de la tienda: se guarda todo y no se avisa de nada.
            # Sin esto, el estreno serian miles de mensajes de golpe.
            # Los candidatos que haya creado compara() en esta pasada tambien
            # sobran: si no, --resembrar prometeria no avisar y en la pasada
            # siguiente soltaria de golpe todas las bajadas que anoto.
            estado["candidatos"] = {k: v for k, v in estado.get("candidatos", {}).items()
                                    if not k.startswith(tienda + ":")}
            estado["sembrado"][tienda] = True
            avisos_sistema.append(
                "<b>%s</b> sembrada: %d productos anotados. A partir de ahora "
                "solo te aviso de los cambios."
                % (tiendas.NOMBRES.get(tienda, tienda), len(productos)))
            continue

        pendientes.extend(sucesos)

    # El repaso de stock va aparte del barrido de catalogos: la lista de
    # vigilancia ya esta en el estado, asi que se puede mirar en cada pasada
    # aunque el sitemap de Carrefour solo se descargue cada seis horas.
    pendientes.extend(repasa_stock(config, estado))

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

    pendientes = confirma_bajadas(pendientes, config, estado)
    pendientes = quita_repetidos(pendientes, config, estado)

    # Primero lo destacado, luego lo nuevo, el stock y las bajadas: si hay
    # recorte por el tope de avisos, que caiga lo menos urgente y nunca lo que
    # el usuario ha marcado como que le importa.
    orden = {"nuevo": 0, "stock": 1, "precio": 2}
    pendientes.sort(key=lambda x: (not destacado(x[1], config), orden[x[0]]))

    tope = config.get("max_avisos", 15)
    for tipo, p, ant in pendientes[:tope]:
        texto, boton = formatea(tipo, p, ant, config)
        avisos.enviar(texto, imagen=p.imagen, boton=boton)
        sella_enviado(p, tipo, estado)
        apunta_para_reporte(p, tipo, config, estado)
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

    # Queda anotado si Telegram esta bien configurado y si los envios cuelan.
    # Se publica con el estado, asi que se puede comprobar sin entrar a los
    # logs de Actions ni tener acceso a los secrets del repo.
    estado["telegram"] = {
        "credenciales": avisos.hay_credenciales(),
        "intentos": avisos.ULTIMO["intentos"],
        "enviados_ok": avisos.ULTIMO["ok"],
        "ultimo_error": avisos.ULTIMO["error"],
    }
    cada_h = config.get("reporte_cada_h", 0)
    if cada_h and time.time() - estado.get("ultimo_reporte", 0) >= cada_h * 3600:
        if estado.get("ultimo_reporte"):        # el primero no se manda: no cubre nada
            avisos.enviar(avisos.reporte(estado, config), silencioso=True)
        estado["ultimo_reporte"] = time.time()
        estado["reporte_desde"] = ahora()
        estado["contadores"] = {}

    estado["ultima_pasada"] = ahora()
    guarda(fichero_estado, estado)
    # En una pasada manual la config viene retocada en memoria (--solo cambia
    # tiendas y cadencias): guardarla dejaria apagadas las demas tiendas para
    # siempre. Solo la escribe la pasada completa.
    if not args.solo:
        guarda(CONFIG, config, legible=True)
    print("pasada %s | %s | %d avisos"
          % (estado["ultima_pasada"], " ".join(resumen_pasada), len(pendientes)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

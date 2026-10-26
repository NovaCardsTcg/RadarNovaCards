"""Colecciones y cartas de Pokemon TCG (en español) -> Supabase, para cromeltcg.com.

Fuente: TCGdex (https://tcgdex.dev), API publica y gratuita con los nombres
oficiales en español, las imagenes de cada carta y los precios de referencia
de Cardmarket en euros (media, minimo, tendencia, 7 y 30 dias).

Por que se sincroniza en vez de leer la API en vivo: el precio solo viene carta
a carta (una peticion por carta), y una coleccion tiene 100-250. Con los datos
en nuestra base, la pagina de una coleccion es una sola consulta, no depende
de que TCGdex este levantado y sirve para Google.

Cada ejecucion (workflow cartas.yml, a diario):
  1. Lista de colecciones y ficha de cada una (fecha, serie, cartas).
  2. Todas las cartas (id, numero, nombre, imagen): se actualizan siempre.
  3. Precios: a diario para las colecciones de los ultimos 3 años; las
     antiguas por turnos (10 por dia), asi todo se refresca cada ~2 semanas.
  4. Valor estimado de cada coleccion (suma de sus cartas con precio).

Uso:
  python cartas_tcgdex.py                 sincroniza (necesita SUPABASE_URL y SUPABASE_SERVICE_KEY)
  python cartas_tcgdex.py --seco          lee TCGdex y resume, sin escribir nada
  python cartas_tcgdex.py --todo          precios de TODAS las colecciones (primera carga)
  python cartas_tcgdex.py --sets me05,me04   solo esas colecciones (con precios)
  python cartas_tcgdex.py --demo ruta.json --sets me05,me04,30th
                                          volcado para la version de prueba de la app
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

import requests

import formato  # clasifica(): el nombre de coleccion que usa el radar en sus ofertas

API = "https://api.tcgdex.net/v2/es"
UA = "CromelBot/1.0 (+https://cromeltcg.com; colecciones Pokemon TCG)"
ANOS_RECIENTES = 3          # colecciones con precio diario
ANTIGUAS_POR_DIA = 10       # colecciones antiguas que se refrescan cada dia (por turnos)
HILOS = 8

S = requests.Session()
S.headers["User-Agent"] = UA


def log(*a):
    print("[cartas]", *a, flush=True)


def get(ruta: str, intentos: int = 3):
    for i in range(intentos):
        try:
            r = S.get(API + ruta, timeout=25)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5 * (i + 1))
    return None


def cents(v) -> int | None:
    try:
        return None if v is None else int(round(float(v) * 100))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ TCGdex -> filas
def fila_coleccion(s: dict, ahora: str) -> dict:
    nombre = s.get("name") or s["id"]
    cc = s.get("cardCount") or {}
    serie = s.get("serie") or {}
    return {
        "id": s["id"], "nombre": nombre,
        "serie_id": serie.get("id"), "serie": serie.get("name"),
        "logo": s.get("logo"), "simbolo": s.get("symbol"),
        "fecha": s.get("releaseDate") or None,
        "n_oficial": cc.get("official"), "n_total": cc.get("total"),
        "abreviatura": (s.get("abbreviation") or {}).get("official"),
        # Mismo nombre que pone el radar en sus ofertas ("Oscuridad Absoluta"):
        # asi la pagina de la coleccion puede ensenar los sobres a la venta.
        "coleccion_radar": formato.clasifica(nombre)["coleccion"],
        "actualizado": ahora,
    }


def fila_basica(set_id: str, c: dict, ahora: str) -> dict:
    return {"id": c["id"], "coleccion_id": set_id, "local_id": str(c.get("localId") or ""),
            "nombre": c.get("name") or c["id"], "imagen": c.get("image"), "actualizado": ahora}


def fila_con_precio(set_id: str, c: dict, ahora: str) -> dict:
    p = (c.get("pricing") or {}).get("cardmarket") or {}
    trend, avg = p.get("trend"), p.get("avg")
    base = trend if trend is not None else avg
    f = fila_basica(set_id, c, ahora)
    f.update({
        "categoria": c.get("category"), "rareza": c.get("rarity"), "ilustrador": c.get("illustrator"),
        "precio_cents": cents(base), "precio_bajo_cents": cents(p.get("low")),
        "precio_7d_cents": cents(p.get("avg7")), "precio_30d_cents": cents(p.get("avg30")),
        "precio_holo_cents": cents(p.get("trend-holo") if p.get("trend-holo") is not None else p.get("avg-holo")),
        "precio_actualizado": p.get("updated"),
    })
    return f


def lee_colecciones() -> list[dict]:
    """Ficha completa de cada coleccion (la lista no trae fecha ni serie)."""
    lista = get("/sets") or []
    log("%d colecciones en la lista" % len(lista))
    out: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for s in ex.map(lambda x: get("/sets/%s" % x["id"]), lista):
            if s and s.get("id"):
                out.append(s)
    out.sort(key=lambda s: (s.get("releaseDate") or "0000"), reverse=True)
    return out


def lee_cartas_con_precio(ids: list[str]) -> list[dict]:
    with cf.ThreadPoolExecutor(max_workers=HILOS) as ex:
        return [c for c in ex.map(lambda i: get("/cards/%s" % i), ids) if c]


def que_colecciones_con_precio(sets: list[dict], todo: bool, solo: set[str] | None) -> list[str]:
    if solo:
        return [s["id"] for s in sets if s["id"] in solo]
    if todo:
        return [s["id"] for s in sets]
    limite = (date.today() - timedelta(days=365 * ANOS_RECIENTES)).isoformat()
    recientes = [s["id"] for s in sets if (s.get("releaseDate") or "9999") >= limite]
    antiguas = [s["id"] for s in sets if (s.get("releaseDate") or "9999") < limite]
    # Turno del dia entre las antiguas: todas se refrescan cada ~2 semanas.
    turno = []
    if antiguas:
        bloques = max(1, -(-len(antiguas) // ANTIGUAS_POR_DIA))
        i = date.today().toordinal() % bloques
        turno = antiguas[i * ANTIGUAS_POR_DIA:(i + 1) * ANTIGUAS_POR_DIA]
    return recientes + turno


# ------------------------------------------------------------------ Supabase
URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
CLAVE = os.environ.get("SUPABASE_SERVICE_KEY") or ""


def sb(metodo: str, ruta: str, **kw):
    h = {"apikey": CLAVE, "Authorization": "Bearer " + CLAVE, "Content-Type": "application/json"}
    h.update(kw.pop("headers", {}))
    r = requests.request(metodo, "%s/rest/v1/%s" % (URL, ruta), headers=h, timeout=180, **kw)
    if r.status_code >= 400:
        raise RuntimeError("Supabase %s %s: %s" % (r.status_code, ruta, r.text[:200]))
    return r


def upsert(tabla: str, filas: list[dict], lote: int = 500) -> int:
    """Inserta o actualiza por id. Solo toca las columnas que van en las filas."""
    n = 0
    for i in range(0, len(filas), lote):
        sb("POST", tabla + "?on_conflict=id", json=filas[i:i + lote],
           headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        n += len(filas[i:i + lote])
    return n


# ------------------------------------------------------------------ principal
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true")
    ap.add_argument("--todo", action="store_true")
    ap.add_argument("--sets", default="")
    ap.add_argument("--demo", default="")
    a = ap.parse_args()
    solo = {x.strip() for x in a.sets.split(",") if x.strip()} or None
    escribir = not a.seco and not a.demo
    if escribir and not (URL and CLAVE):
        log("faltan SUPABASE_URL / SUPABASE_SERVICE_KEY"); return 1
    ahora = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t0 = time.time()

    sets = lee_colecciones()
    if not sets:
        log("TCGdex no responde: no se toca nada"); return 1
    colecciones = [fila_coleccion(s, ahora) for s in sets]
    basicas = [fila_basica(s["id"], c, ahora) for s in sets for c in (s.get("cards") or []) if c.get("id")]
    log("%d colecciones, %d cartas; con radar: %d" % (
        len(colecciones), len(basicas), sum(1 for c in colecciones if c["coleccion_radar"])))

    con_precio_ids = que_colecciones_con_precio(sets, a.todo, solo)
    por_set = {s["id"]: [c["id"] for c in (s.get("cards") or []) if c.get("id")] for s in sets}
    ids_cartas = [cid for sid in con_precio_ids for cid in por_set.get(sid, [])]
    log("precios: %d colecciones, %d cartas a leer" % (len(con_precio_ids), len(ids_cartas)))
    completas = lee_cartas_con_precio(ids_cartas)
    con_precio = [fila_con_precio(c.get("set", {}).get("id") or c["id"].rsplit("-", 1)[0], c, ahora) for c in completas]
    n_precio = sum(1 for f in con_precio if f["precio_cents"] is not None)
    log("leidas %d cartas, %d con precio (%.0fs)" % (len(con_precio), n_precio, time.time() - t0))

    if a.demo:
        quiero = solo or set(con_precio_ids)
        datos = {"generado": ahora,
                 "colecciones": colecciones,
                 "cartas": [f for f in con_precio if f["coleccion_id"] in quiero]}
        with open(a.demo, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, ensure_ascii=False, indent=1)
        log("demo: %s (%d colecciones, %d cartas)" % (a.demo, len(datos["colecciones"]), len(datos["cartas"])))
        return 0
    if a.seco:
        top = sorted((f for f in con_precio if f["precio_cents"]), key=lambda f: -f["precio_cents"])[:5]
        for f in top:
            log("  top: %s %s %s -> %.2f EUR" % (f["coleccion_id"], f["local_id"], f["nombre"], f["precio_cents"] / 100))
        return 0

    upsert("colecciones", colecciones)
    upsert("cartas", basicas)
    upsert("cartas", con_precio)
    n_val = sb("POST", "rpc/actualiza_valor_colecciones", json={}).json()
    log("guardado: %d colecciones, %d cartas, %d con precio; %s colecciones con valor (%.0fs)" % (
        len(colecciones), len(basicas), n_precio, n_val, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())

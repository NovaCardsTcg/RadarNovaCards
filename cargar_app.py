# -*- coding: utf-8 -*-
"""Carga de golpe el catalogo de las tiendas en la app Cromel (Supabase).

Es lo mismo que hace el radar en cada pasada, pero a mano y una sola vez, para
ver la app llena sin esperar a GitHub. Solo las tiendas que dan precio y stock
y no bloquean a un ordenador normal (Amazon, MediaMarkt y Carrefour quedan
para el radar de GitHub).

La clave service_role NO va en ningun fichero: se escribe en la terminal.

    $env:SUPABASE_URL = "https://<proyecto>.supabase.co"
    $env:SUPABASE_SERVICE_KEY = "<service_role>"
    python cargar_app.py            # todas
    python cargar_app.py cardzone   # solo una
"""

from __future__ import annotations

import json
import os
import sys
import time

import app_db
import radar
import tiendas as T

TIENDAS = ["cardzone", "laescotilla", "toyplanet", "toysrus", "flashstore", "frikimonkey"]


def main(elegidas: list[str]) -> int:
    if not app_db.activo():
        print("Faltan SUPABASE_URL y SUPABASE_SERVICE_KEY en esta terminal (ver el principio de este fichero).")
        return 2
    config = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"), encoding="utf-8"))
    T.TIMEOUT, T.REINTENTOS = 25, 1
    fallos = 0
    for clave in elegidas or TIENDAS:
        if clave not in T.ADAPTADORES:
            print("%-12s tienda desconocida" % clave)
            fallos += 1
            continue
        t0 = time.time()
        try:
            productos = T.ADAPTADORES[clave][0](config["palabras"])
        except Exception as e:
            print("%-12s no responde (%s)" % (clave, str(e)[:80]))
            fallos += 1
            continue
        res = app_db.sincroniza(clave, productos, config, radar.relevante, radar.destacado)
        if res is None:
            print("%-12s %d leidos, nada que subir o la app no ha respondido" % (clave, len(productos)))
            fallos += 1
        else:
            print("%-12s %d leidos, %d subidos, %.0f s" % (clave, len(productos), res.get("leidos", 0), time.time() - t0))
    print("Hecho." if not fallos else "Hecho con %d tienda(s) con problemas." % fallos)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

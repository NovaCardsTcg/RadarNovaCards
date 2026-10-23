# -*- coding: utf-8 -*-
"""Clasifica un producto TCG por su titulo: coleccion, tipo e idioma.

Es el formato de los canales de alertas que funcionan (PokeTracker, por
ejemplo): "RESTOCK · Caos Creciente · Blister [ES]". De un vistazo se sabe si
interesa sin abrir el enlace, que en un canal con muchos avisos es lo que
decide si la gente se queda.

Todo sale del titulo, porque es lo unico comun a todas las tiendas. Si no se
reconoce algo, se omite: mejor una etiqueta de menos que una equivocada.
"""

from __future__ import annotations

import re

from tiendas import normaliza

# (terminos que lo delatan, nombre que se muestra). El orden importa: lo mas
# especifico primero, porque se queda con la primera coincidencia.
COLECCIONES = [
    (("30 aniversario", "30th celebration", "30th anniversary", "celebracion 30",
      "celebracion"), "30 Aniversario"),
    (("caos creciente", "chaos rising"), "Caos Creciente"),
    (("oscuridad absoluta", "pitch black"), "Oscuridad Absoluta"),
    (("equilibrio perfecto", "perfect order"), "Equilibrio Perfecto"),
    (("heroes ascendentes", "ascended heroes"), "Héroes Ascendentes"),
    (("fuegos fantasmales", "phantasmal flames"), "Fuegos Fantasmales"),
    (("ciudad luminalia", "lumiose"), "Ciudad Luminalia"),
    (("megapotencia", "mega potencia"), "Megapotencia"),
    (("megaluz lunar",), "Megaluz Lunar"),
    (("rivales predestinados", "destined rivals"), "Rivales Predestinados"),
    (("juntos de aventuras", "journey together"), "Juntos de Aventuras"),
    (("evoluciones prismaticas", "prismatic evolutions"), "Evoluciones Prismáticas"),
    (("chispas fulgurantes", "surging sparks"), "Chispas Fulgurantes"),
    (("fabula sombria", "shrouded fable"), "Fábula Sombría"),
    (("corona astral", "stellar crown"), "Corona Astral"),
    (("fuerzas temporales", "temporal forces"), "Fuerzas Temporales"),
    (("destinos de paldea", "paldean fates"), "Destinos de Paldea"),
    (("151",), "151"),
    (("blanco y negro", "fulgor negro", "black bolt", "white flare"), "Fulgor Negro / Llama Blanca"),
    (("mega brave",), "Mega Brave (JP)"),
    (("mega symphonia",), "Mega Symphonia (JP)"),
    (("inferno x",), "Inferno X (JP)"),
    (("abyss eye",), "Abyss Eye (JP)"),
    (("nihil zero",), "Nihil Zero (JP)"),
    (("megaevolucion", "mega evolution", "mega evolucion"), "Megaevolución"),
    (("escarlata y purpura", "scarlet & violet", "scarlet and violet"), "Escarlata y Púrpura"),
]

# One Piece se identifica por el codigo de expansion (OP09, ST-21, EB02...).
CODIGO_OP = re.compile(r"\b(OP|ST|EB|PRB)\s*-?\s*(\d{1,2})\b", re.I)

TIPOS = [
    (("ultra premium", "ultraprémium", "ultrapremium", "upc"), "Ultra Premium"),
    (("entrenador elite", "elite trainer", "etb"), "ETB"),
    (("booster box", "display", "expositor", "caja de sobres", "36 sobres"), "Booster Box"),
    (("booster bundle", "lote de sobres", "pack de sobres", "pack 5 sobres",
      "pack de 3 sobres", "pack 3 sobres"), "Booster Bundle"),
    (("blister",), "Blister"),
    (("minilata", "mini lata", "mini tin"), "Mini Lata"),
    (("lata", "tin"), "Lata"),
    (("coleccion premium", "premium collection", "coleccion prémium"), "Colección Premium"),
    (("illustration box", "special box", "pin box"), "Caja especial"),
    (("starter deck", "baraja de combate", "baraja combate", "battle deck", "mazo"), "Mazo"),
    (("coleccion", "collection", "caja"), "Colección"),
    (("sobre", "booster", "pack"), "Sobre"),
]

IDIOMAS = [
    (("japones", "japanese", "jpn", "(jp)", " jp "), "JA"),
    (("ingles", "english", "(en)"), "EN"),
    (("frances", "french"), "FR"),
    (("aleman", "german"), "DE"),
    (("italiano", "italian"), "IT"),
    (("español", "espanol", "castellano", "(es)", "jcc"), "ES"),
]


def _busca(texto: str, tabla) -> str | None:
    for terminos, nombre in tabla:
        for t in terminos:
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(normaliza(t)), texto):
                return nombre
    return None


def clasifica(titulo: str) -> dict:
    t = " %s " % normaliza(titulo)
    juego = "One Piece" if "one piece" in t else "Pokémon"
    coleccion = None
    if juego == "One Piece":
        m = CODIGO_OP.search(titulo)
        if m:
            coleccion = "%s%02d" % (m.group(1).upper(), int(m.group(2)))
    if not coleccion:
        coleccion = _busca(t, COLECCIONES)
    return {
        "juego": juego,
        "coleccion": coleccion,
        "tipo": _busca(t, TIPOS),
        "idioma": _busca(t, IDIOMAS),
    }


def etiqueta(titulo: str) -> str:
    """'Caos Creciente · Blister [ES]', o lo que se haya podido deducir."""
    c = clasifica(titulo)
    partes = [x for x in (c["coleccion"], c["tipo"]) if x]
    if c["juego"] == "One Piece":
        partes.insert(0, "One Piece")
    texto = " · ".join(partes)
    if c["idioma"]:
        texto = (texto + " " if texto else "") + "[%s]" % c["idioma"]
    return texto

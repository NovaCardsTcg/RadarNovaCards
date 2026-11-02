# -*- coding: utf-8 -*-
"""Publicidad de la app: carruseles que ensenan como es Cromel y para que sirve.

Seis series con tres ganchos cada una: 18 portadas distintas antes de repetir
ninguna. Estructura de las guias de instagram-marketing: portada con una promesa
(nunca un titulo pelado), una idea por diapositiva que se lea en 2 segundos y una
sola peticion al final. Aqui no hay cifras inventadas: solo lo que la app hace.

Las capturas salen de la version de prueba (marketing-capturas.mjs), asi que sus
precios son de ejemplo y cada diapositiva lo dice. Las de lanzamientos caducan
("en 3 dias"), asi que esas se hacen en directo al publicar (render.py).
"""

from __future__ import annotations

SERIES = [
    {"id": "vista", "etiqueta": "🃏 Qué es Cromel", "tags": "pokemon",
     "ganchos": ["Deja de mirar <em>10 tiendas</em> cada mañana",
                 "Todas las tiendas de cartas en <em>una sola app</em>",
                 "Lo que hace Cromel <em>en 5 pantallas</em>"],
     "sub": "Gratis y sin descargas. Desliza para verla.",
     "portada": ["mi-coleccion-cartas", "inicio", "pregradeo"],
     "pasos": [("inicio", "Ofertas y reposiciones juntas", "Un radar revisa las tiendas de España cada pocos minutos."),
               ("alerta", "Te avisa de lo que buscas", "Por Telegram o por notificación, cuando sale o lo reponen."),
               ("carta-grafico", "El precio de cada carta", "Y cómo ha cambiado en el último mes."),
               ("mi-coleccion", "Tu colección, con su valor", "Marca lo que tienes y mira cuánto vale."),
               ("pregradeo", "Pregradeo con IA", "Una estimación de la nota antes de pagar el gradeo.")],
     "cierre": "Toda la caza de cartas, <em>en un sitio</em>", "pide": "Envíaselo a quien aún mira tienda por tienda",
     "texto": "Cromel junta ofertas, reposiciones y bajadas de precio de las tiendas TCG de España, "
              "y te avisa de lo que buscas. También tiene el precio de cada carta Pokémon, tu colección y pregradeo con IA."},
    {"id": "alertas", "etiqueta": "🔔 Alertas", "tags": "pokemon",
     "ganchos": ["Cómo enterarte de una reposición <em>antes de que vuele</em>",
                 "Se agotó otra vez. <em>Que no te vuelva a pasar</em>",
                 "Pon un aviso y <em>olvídate de recargar</em>"],
     "sub": "Tres pasos y el radar vigila por ti.",
     "portada": ["alerta", "buscar", "ficha"],
     "pasos": [("buscar", "1. Busca lo que quieres", "Una ETB, una colección o un sobre concreto."),
               ("alerta", "2. Crea la alerta", "Eliges Telegram o notificación en el móvil."),
               ("ficha", "3. Te avisa y vas a la tienda", "Con el precio y el enlace directo al producto.")],
     "cierre": "Que el radar <em>recargue por ti</em>", "pide": "Guárdalo para la próxima reposición",
     "texto": "El radar revisa las tiendas de España cada pocos minutos. Dile qué buscas y te avisa cuando salga, "
              "lo repongan o baje de precio."},
    {"id": "coleccion", "etiqueta": "🗂️ Mi colección", "tags": "pokemon",
     "ganchos": ["¿Cuánto vale <em>tu colección</em>? Míralo en 3 pasos",
                 "Deja de apuntar tus cartas <em>en un Excel</em>",
                 "Tu álbum Pokémon, <em>con precios de mercado</em>"],
     "sub": "Marca las que tienes y la app hace la cuenta.",
     "portada": ["coleccion-progreso", "mi-coleccion", "album"],
     "pasos": [("colecciones", "1. Elige la colección", "Están las colecciones Pokémon, carta a carta."),
               ("coleccion-progreso", "2. Marca las que tienes", "Y ves cuánto te falta para completarla."),
               ("mi-coleccion", "3. Mira cuánto vale", "Con un gráfico de cómo cambia el valor."),
               ("mi-coleccion-cartas", "Tus cartas, a la vista", "Ordénalas por valor, colección o nombre.")],
     "cierre": "Tu colección, <em>siempre al día</em>", "pide": "Envíaselo a tu compañero de sobres",
     "texto": "Marca las cartas que tienes y las que buscas. Cromel calcula el valor de tu colección "
              "con precios de mercado y te enseña cómo cambia."},
    {"id": "pregradeo", "etiqueta": "✨ Pregradeo con IA", "tags": "psa",
     "ganchos": ["Antes de pagar un gradeo, <em>haz esto</em>",
                 "¿Qué nota sacaría <em>tu carta</em>?",
                 "¿Para gradear <em>o para la carpeta</em>?"],
     "sub": "Hazle una foto y sal de dudas antes de enviarla.",
     "portada": ["pregradeo-foto", "pregradeo", "carta"],
     "pasos": [("pregradeo-foto", "1. Hazle una foto", "Por delante; el dorso es opcional."),
               ("pregradeo", "2. Mira la nota estimada", "Con centrado, esquinas, bordes y superficie por separado."),
               ("album", "Siempre a mano", "Lo tienes en Mi álbum, junto a tu colección.")],
     "cierre": "Una estimación, <em>no una nota oficial</em>", "pide": "Guárdalo para antes de tu próximo envío",
     "texto": "Sube una foto de tu carta y la IA estima la nota que podría sacar, revisando centrado, esquinas, "
              "bordes y superficie. Es orientativo: la nota oficial solo la da la empresa de gradeo."},
    {"id": "precios", "etiqueta": "📈 Precios de cartas", "tags": "pokemon",
     "ganchos": ["¿Tu carta sube <em>o baja</em>?",
                 "Mira el precio de tu carta <em>antes de venderla</em>",
                 "El precio de cada carta Pokémon, <em>en tu móvil</em>"],
     "sub": "Búscala y lo ves en segundos.",
     "portada": ["carta", "carta-grafico", "coleccion"],
     "pasos": [("buscar", "1. Busca la carta", "Por su nombre, como Charizard o Pikachu."),
               ("carta", "2. Mira su precio", "El precio de tendencia de Cardmarket."),
               ("carta-grafico", "3. Mira cómo ha cambiado", "Con el gráfico del último mes.")],
     "cierre": "Que no te la <em>malvendan</em>", "pide": "Guárdalo para cuando vayas a vender",
     "texto": "En Cromel están las colecciones Pokémon con el precio de cada carta y su evolución en el último mes. "
              "También puedes pedir un aviso si baja del precio que digas."},
    {"id": "lanzamientos", "etiqueta": "📅 Lanzamientos", "tags": "varios",
     "ganchos": ["¿Cuándo sale <em>la próxima colección</em>?",
                 "Que no te pille <em>sin reservar</em>",
                 "Las fechas TCG, <em>en un calendario</em>"],
     "sub": "Pokémon, One Piece, Lorcana, Magic y Riftbound.",
     "portada": ["lanzamiento", "lanzamientos", "colecciones"],
     "pasos": [("lanzamientos", "Todas las fechas en España", "Con la cuenta atrás de cada colección."),
               ("lanzamiento", "Cada colección, a fondo", "Fecha, cartas estrella, productos y aviso de stock."),
               ("alerta", "Aviso cuando haya stock", "Activa la alerta y no tienes que estar pendiente.")],
     "cierre": "Apunta las fechas <em>y a cazar</em>", "pide": "Envíaselo a quien te acompaña a las reservas",
     "texto": "El calendario de Cromel reúne los próximos lanzamientos TCG en España, con productos y precios orientativos. "
              "Y puedes activar un aviso para cuando haya stock."},
]


def siguiente(usados: set[str]) -> tuple[dict, int]:
    """La primera combinacion serie + gancho que no ha salido; si han salido todas, vuelta a empezar."""
    for g in range(3):
        for s in SERIES:
            if "app:%s:%d" % (s["id"], g) not in usados:
                return s, g
    return SERIES[0], 0

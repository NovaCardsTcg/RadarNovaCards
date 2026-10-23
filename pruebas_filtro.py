# -*- coding: utf-8 -*-
"""Pruebas del filtro con titulos REALES vistos en produccion.

Cada vez que algo que no es TCG llega al canal, su titulo acaba aqui como caso
que debe FALLAR; cada vez que un filtro nuevo amenaza con tirar producto bueno,
el titulo acaba aqui como caso que debe PASAR. Ejecutar antes de cada push:

    python pruebas_filtro.py
"""
import sys

import radar
import tiendas as T

cfg = radar.carga(radar.CONFIG, radar.CONFIG_DEFECTO)

# (tienda, titulo, debe_pasar)
CASOS = [
    # Frikimonkey (Shopify): sellado dentro, accesorios y gradeadas fuera
    ("frikimonkey", "Pokemon Pack Scarlet & Violet", True),
    ("frikimonkey", "Shining Fates Elite Trainer Box (English) | Pokémon TCG", True),
    ("frikimonkey", "PET Protector for Pokémon Elite Trainer Box (10 Pack) | Friki Monkey", False),
    ("frikimonkey", "Charizard – Pokémon GO 010/078 (PCA 9 or Higher Mint, 2022)", False),
    # ToysRUs y Flash Store: oficiales que el filtro tiraba
    ("toysrus", "Elite Trainer Box Rivales Predestinados", True),
    ("toysrus", "Pokémon - Caja Entrenador de Élite Héroes Ascendentes (esp)", True),
    ("toysrus", "Pokémon - Summer Tin Box (Esp) (Varios modelos)", True),
    ("toysrus", "Taza cerámica Elite 380 ml en caja - Pokémon Metal Meltdown", False),
    ("toysrus", "Ravensburger - Pokemon - Puzzle 3D Storage Box Pokémon Ravensburger", False),
    ("flashstore", "Pokémon 151 Sobre Japón", True),
    ("flashstore", "One Piece OP-14 Warlords of the Sea Sobre", True),
    ("flashstore", "One Piece ST-29 EggHead Starter Deck", True),
    # Toy Planet (Shopify): oficiales que el filtro tiraba
    ("toyplanet", "Pokémon TCG 30º Aniversario Poster Collection con 3 Sobres y 3 Cartas Promocionales +6 años", True),
    ("toyplanet", "Sobre Pokémon Fuegos Fantasmales surtidos", True),
    ("toyplanet", "One piece  póster maxi 91,5x61  gear 5th goofy", False),
    ("toyplanet", "Taza Pokémon Gengar y Mimikyu 320 ml", False),
    # Amazon: producto oficial sellado SIN "JCC" en el titulo (no se pueden perder)
    ("amazon", "Pokémon: Caja de Entrenador Élite de Megaevolución-Caos Creciente (1 Carta de promoción)", True),
    ("amazon", "Pokémon: Baraja Combate de Liga de Mewtwo ex del Team Rocket (1 baraja de 60 Cartas)", True),
    ("amazon", "Pokémon: Expositor de Sobres de Mejora de Megaevolución-Oscuridad Absoluta (18)", True),
    ("amazon", "Pokémon: Minilata Megaevolución-Héroes Ascendentes: Riolu y Darumaka (1 Pegatina)", True),
    ("amazon", "Pokémon: Colección prémium Mega-Greninja ex (1 Carta holográfica de promoción)", True),
    ("amazon", "Pokémon: Blíster de 3 Sobres de Mejora de Megaevolución-Oscuridad Absoluta", True),
    ("amazon", "Pokémon Colección Ursaluna Luna Carmesí (4 Cartas holográficas y 4 Paquetes de mejora)", True),
    ("amazon", "Pokemon TCG: Lycanroc V/Corviknight V Battle Deck - One At Random", True),
    # Amazon: producto oficial con JCC
    ("amazon", "Pokémon: Lata Megapotencia: Mega-Zeraora ex de JCC Pokémon (4 Sobres de Mejora)", True),
    ("amazon", "Pokémon: Caja de Entrenador Élite de Celebración 30.º Aniversario de JCC Pokémon", True),
    ("amazon", "Pokémon: Álbum para Colecciones de Celebración 30.º Aniversario de JCC Pokémon", True),
    ("amazon", "Pokémon Academia de Combate del Juego de Cartas Coleccionables Pokémon de JCC", True),
    # Amazon: basura vista en el canal o en el embudo (debe fallar)
    ("amazon", "Name IT Nmmmihat Pokemon SS Nreg Top Box Sky para Niños", False),
    ("amazon", "Name IT Pokémon LS Top Box (pack de 1)", False),
    ("amazon", "One Piece Box Set 5 (volúmenes 91-111)", False),
    ("amazon", "Asmodee 5 Minutos: One Piece", False),
    ("amazon", "TOPI GAMES One Piece Raid en Onigashima Juego de cartas", False),
    ("amazon", "Vivre Card One Piece Booster Pack 02 - Encyclopédie", False),
    ("amazon", "Dobble One Piece Juego de cartas", False),
    ("amazon", "Ravensburger Nakama Juego cooperativo de One Piece", False),
    ("amazon", "Archivador Cartas, 24 Hojas 432 Bolsillos, Álbum Cartas con Carga Lateral", False),
    ("amazon", "EVORETRO Acrylic Display Case Protector for Pokemon Booster Box - 1-Pack", False),
    ("amazon", "Kellogg's Pokemon Cereal de Trigo y Maíz Sabor a Cacao, 350g (Paquete de 2)", False),
    ("amazon", "Pop! Games: Pokemon- Lycanroc (Midday)", False),
    ("amazon", "Pokémon PKW4534-5cm Battle Figure Pack de 2 Torkoal y Bulbasaur", False),
    ("amazon", "Enciclopedia Pokémon (Guía Pokémon): Edición revisada y ampliada especial 30 aniversario", False),
    ("amazon", "POKÉMON ESCARLATA Y PURPURA 01: 1", False),
    ("amazon", "Sheroz (Rose) 071/072 Entrenadore Full Art - Myboost X Epée et Bouclier 4.5", False),
    ("amazon", "Toxizap (Toxel) SV041/SV122 Variocolor Secreta - Myboost X Epée et Bouclier", False),
    ("amazon", "Winning Moves Goddit! Stitch - Juego de Cartas rápido y Divertido", False),
    # CardZone (sin regla de prefijo: ahi el titulo va al reves)
    ("cardzone", "Caja IB-08 Illustration Box One Piece - Ingles", True),
    ("cardzone", "Pack 5 Sobres One Piece The World's Strongest Warriors OP17 - Japones", True),
    ("cardzone", "Mazo Starter Deck ST-36 Yellow Eustass Kid One Piece - Ingles", True),
    ("cardzone", "Blister Pokemon Oscuridad Absoluta - Español", True),
    ("cardzone", "Mega Froslass (2025) M2a JP - PSA 10", False),
    # GAME (sitemap: titulo sacado del slug)
    ("game", "Sobre de cartas pokemon abyss eye pack booster japones merchandising", True),
    ("game", "Caja de entrenador elite pokemon megaevolucion oscuridad absoluta merchandising", True),
    ("game", "Bento box pokemon pikachu merchandising", False),
    ("game", "Sobre de cromos one piece netflix serie merchandising", False),
    ("game", "Pokemon escarlata nintendo switch", False),
    ("game", "Reacondicionado Pokémon TCG Colección Ultraprémium Mega-Charizard", False),
    ("game", "Mazo de cartas One Piece Starter deck ST-06 en Japonés", True),
    ("game", "Radio despertador luminoso pokemon bulbasaur sobre pokeball 18cm", False),
]

mal = 0
for tienda, titulo, esperado in CASOS:
    p = T.Producto(tienda=tienda, pid="x", titulo=titulo, url="u", precio=9.99)
    exigir = T.ADAPTADORES[tienda][1]   # los buscadores exigen la palabra clave
    r = radar.relevante(p, cfg, exigir)
    if r != esperado:
        mal += 1
        print("MAL  %-9s pasa=%-5s esperado=%-5s %s" % (tienda, r, esperado, titulo[:70]))
print("%d casos, %d mal" % (len(CASOS), mal))
sys.exit(1 if mal else 0)

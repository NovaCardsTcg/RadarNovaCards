# Radar Pokémon

Vigila las novedades de producto Pokémon en seis tiendas y te lo dice por Telegram.
Corre en GitHub Actions, así que no necesita el PC encendido.

Avisa de tres cosas:

- **NUEVO** — una referencia que no había visto nunca en esa tienda.
- **VUELVE EL STOCK** — un producto conocido pasa de agotado a comprable.
- **BAJA DE PRECIO** — cae respecto a la última vez (por defecto, un 5% y 1 € como mínimo).
  Antes de avisarte se abre la ficha del producto para confirmar que el precio es
  ese de verdad; ver la sección 6.

---

## 1. Puesta en marcha

### Crear el bot (2 minutos)

1. En Telegram, habla con **@BotFather** y manda `/newbot`. Te pide un nombre y un
   usuario. Al final te da un token tipo `8123456789:AAH...`. Ese es el `TELEGRAM_TOKEN`.
2. Abre un chat con tu bot recién creado y mándale cualquier cosa (`hola`). Sin ese
   primer mensaje, Telegram no deja que el bot te escriba.
3. Para saber tu `TELEGRAM_CHAT_ID`, habla con **@userinfobot** y te devuelve tu ID
   numérico. También sirve abrir en el navegador
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` y buscar `"chat":{"id":...}`.

### Subirlo a GitHub

El repositorio local ya está creado y con el primer commit hecho. Falta el remoto:

1. En github.com → **New repository** → nombre `radar-pokemon`, **Public**, y sin
   añadir README ni .gitignore (ya los hay).
2. Desde esta carpeta:

```bash
git remote add origin https://github.com/<tu-usuario>/radar-pokemon.git
git branch -M main
git push -u origin main
```

**Hazlo público.** No es capricho: en repos públicos los minutos de Actions son
ilimitados, y en privados gastas la cuota gratuita de 2.000 minutos al mes. Una
pasada cada 10 minutos son unas 4.300 al mes, así que en privado te quedarías sin
cuota antes de mitad de mes. En el repo no hay ningún secreto: el token y tu chat
van en los secrets de GitHub, no en el código.

### Meter las credenciales

En el repo → **Settings › Secrets and variables › Actions › New repository secret**:

| Nombre | Valor |
|---|---|
| `TELEGRAM_TOKEN` | el token de BotFather |
| `TELEGRAM_CHAT_ID` | tu ID numérico |

### Arrancar

En la pestaña **Actions**, acepta la activación de workflows, entra en *radar* y dale
a **Run workflow**. La primera pasada siembra el estado (unos 11.000 productos) y te
manda un mensaje por tienda diciendo cuántos ha anotado. No te avisa de nada más:
sería un bombardeo.

A partir de ahí va sola cada 10 minutos.

---

## 2. El rodaje: las primeras 2 horas

Durante las **12 primeras pasadas** el radar anota productos nuevos sin avisarte.
Cuando termina te manda un mensaje diciéndolo.

El motivo es que el buscador de Amazon no devuelve siempre el mismo conjunto: con la
misma consulta y a un minuto de distancia aparecen y desaparecen artículos de la cola
larga. Medido en pruebas: 20 falsos "nuevos" en la segunda pasada, 8 en la siguiente,
2 en la siguiente y 0 después. El Corte Inglés y MediaMarkt, cero desde el principio.

Las bajadas de precio y las vueltas de stock **sí avisan desde el minuto uno**: esas se
comparan contra un producto ya conocido y la rotación del buscador no las afecta.

---

## 3. Manejarlo desde el móvil

Escríbele al bot. Los comandos se leen al principio de cada pasada, así que tardan como
mucho un ciclo en aplicarse.

```
/estado              qué vigila, cuánto lleva visto y salud de cada tienda
/palabras            ver las palabras clave
/add pokemon 151     añadir palabra clave
/quitar pokemon 151  quitarla
/tiendas             ver tiendas y si están encendidas
/tienda amazon off   encender o apagar una tienda
/precio 5            avisar solo si el precio baja un 5% o más
/max 15              máximo de avisos por ciclo
/vigilar <url>       vigilar el stock de un producto concreto
/dejar <url>         quitarlo de la lista
/pausa  /reanudar    dejar de avisar (sigue tomando nota) y volver
/prueba              mandar un aviso de prueba
```

El bot **solo obedece a tu chat**. Si alguien más da con él, sus comandos se ignoran.

---

## 4. Qué vigila cada tienda, y con qué límites

Ninguna de estas seis webs ofrece una API pública de catálogo, así que cada una se
ataca por donde se deja. Esto es lo que hay:

| Tienda | Por dónde | Detecta | Dónde corre |
|---|---|---|---|
| Amazon.es | buscador por novedades | nuevo, stock, precio | GitHub, cada pasada |
| El Corte Inglés | buscador | nuevo, stock, precio | GitHub, cada pasada |
| GAME | sitemap + fichas | nuevo, **stock**, precio | GitHub, catálogo cada 30 min |
| Carrefour | sitemap + fichas | nuevo, **stock**, precio | manual, catálogo cada 6 h |
| MediaMarkt | buscador | nuevo, stock, precio | manual, desde casa |
| Pokémon Center | — | apagada | — |

**Por qué dos van a mano.** MediaMarkt y Carrefour devuelven 403 a los runners de
GitHub, que salen por rangos de IP de centro de datos. Es bloqueo por reputación de la
IP: desde una conexión doméstica responden sin pelear.

Contra eso se probaron dos cosas, y las dos ganaron terreno:

- **Pedir la portada antes**, para entrar con cookie de sesión en vez de en frío.
  Recuperó Amazon.
- **`curl_cffi` imitando la huella TLS de Chrome.** `requests` tiene un apretón de
  manos SSL reconocible por mucho que le arregles las cabeceras, y es una señal
  independiente de la IP. Recuperó El Corte Inglés.

Con MediaMarkt y Carrefour ninguna de las dos basta.

**Filtro temático.** Sin él, "pokemon" en Carrefour saca 981 productos que son mochilas,
funkos, peluches, tazas y sábanas: solo 27 llevaban "cartas" en el nombre. La lista
`tematica` de `config.json` exige que el nombre contenga algo de TCG (cartas, sobres,
latas, ETB, displays...). Carrefour queda en 91 y GAME en 63, que además es lo que hace
viable comprobarles el stock uno por uno.

**Por qué unas dan precio y otras no.** GAME pinta el buscador con JavaScript y lo
protege con reCAPTCHA; Carrefour tapa buscador y API con Cloudflare (devuelven 403 y
502); Pokémon Center da 403 en `/search`. Los tres, en cambio, publican su sitemap sin
pelear. Un sitemap da la URL pero no el precio ni el stock, así que de esas tres solo
se pueden detectar altas nuevas. Cuando aparece una, se abre la ficha para sacar título
y precio de verdad y que el aviso sea útil.

Ventaja inesperada: como el sitemap es el catálogo completo y no una muestra rotatoria,
en esas tres un "nuevo" es fiable desde la primera pasada, sin rodaje.

**Cobertura de El Corte Inglés.** Su buscador es de scroll infinito y no expone
paginación, así que devuelve unos 10 productos por palabra clave y no publica sitemap.
Ahí las palabras clave importan más que en el resto: si quieres cubrir más, añade
términos con `/add` (`sobres pokemon`, `etb pokemon`, `lata pokemon`...).

**Carrefour son 40 sitemaps de 1,5 MB**, por eso corre cada 6 horas. Barrerlos cada 10
minutos sería tirar ancho de banda sin ganar nada: las altas nuevas no van tan rápido.

---

## 5. La pasada manual (MediaMarkt y Carrefour)

Doble clic en **`revisar.bat`**. Consulta MediaMarkt y Carrefour desde tu conexión y te
avisa por el mismo bot. Tarda un par de minutos, casi todo Carrefour, que son 40
sitemaps de 1,5 MB.

Antes de la primera vez, prepara las credenciales:

1. Copia `.env.ejemplo` a `.env`
2. Rellénalo con los dos mismos valores que pusiste en los secrets de GitHub

El `.env` está en `.gitignore`, así que el token no sale de tu ordenador.

Lleva **memoria propia** (`estado-local.json`), separada de la que mantiene GitHub. Eso
significa que las dos mitades no se pisan: puedes lanzar la manual mientras el cron
sigue con Amazon y las oficiales. La primera pasada siembra y no avisa de nada, como en
GitHub; a partir de la segunda ya avisa de novedades, stock y bajadas.

Si algún día quieres que esas tres se revisen solas mientras el PC está encendido, el
mismo `.bat` vale para el Programador de tareas de Windows.

---

## 6. Cómo se vigila el stock en GAME y Carrefour

En estas dos el sitemap dice que un producto existe, pero no si se puede comprar. Para
saberlo hay que abrir su ficha, y son 154: no caben en una pasada. Así que se guarda la
lista en el estado y se repasan por turnos, 25 fichas por pasada, con esta prioridad:

1. **Los que constan agotados.** Son los únicos que pueden dar la noticia, así que se
   miran siempre y primero.
2. **Los que no se han mirado nunca.** Es el barrido inicial, que descubre cuáles están
   agotados hoy. Con 154 productos son unas seis pasadas, o sea una hora larga.
3. **Los que constan disponibles**, por si se agotan. Por turnos y sin prisa.

Cuando uno pasa de agotado a comprable, salta el aviso de VUELVE EL STOCK.

Puedes añadir productos concretos con **`/vigilar`** y el enlace, vengan de donde vengan.
Entran en la cola con prioridad de "nunca mirado", así que se comprueban en la pasada
siguiente. `/dejar` y el enlace los saca de la lista.

Cómo se lee la disponibilidad en cada una: Carrefour lo declara en su `ld+json` de
Schema.org; GAME no, ahí la señal es si existe el botón de añadir a la cesta.

---

## 7. Por qué las bajadas se confirman en la ficha

El precio de la tarjeta del buscador y el de la ficha del producto no siempre
coinciden. Pasa sobre todo en Amazon: ofertas de otros vendedores que ganan y
pierden la caja de compra, variantes del mismo producto con precios distintos,
promociones que caducan entre que se detecta y se avisa. El resultado era recibir
"ha bajado a 34,98" y encontrarte 69,95 al pinchar.

Ahora, antes de mandar un aviso de bajada, se abre la ficha:

- Si la ficha **no confirma** la bajada, no se avisa. Y además se corrige la memoria
  con el precio de la ficha; sin eso el precio malo se quedaría de referencia y
  dispararía el mismo aviso falso una y otra vez.
- Si la **confirma**, el aviso lleva el precio de la ficha, no el del buscador, para
  que el mensaje y la página digan lo mismo.
- Si la ficha **no se puede abrir**, se avisa igual pero diciéndolo en el mensaje.
  Mejor un aviso con una reserva que perderse una bajada real.

Cuesta una petición por bajada, y las bajadas reales son pocas. El tope está en 12
comprobaciones por pasada.

---

## 8. Lo que puede salir mal

**Amazon corta de vez en cuando.** Devolvió 403 y 503 desde GitHub hasta que se añadió
lo de pedir la portada primero. Aun así cuenta con cortes sueltos: el radar reintenta,
cambia de user-agent y, si una tienda falla, sigue con las demás. Te avisa al tercer
fallo seguido, no al primero, para que un corte pasajero no te suene el móvil.

Si algún día Amazon se cerrara del todo, pasaría al grupo de las manuales: `revisar.bat`
admite cualquier combinación (`python radar.py --solo amazon --estado estado-local.json`).

**Los cron de GitHub llegan tarde.** El mínimo es 5 minutos, pero cuando hay cola los
retrasos son de 10-20 minutos y a veces se salta una ejecución. Para novedades y
reposiciones va sobrado; para pelear un drop al segundo contra bots dedicados, no.

**Si una tienda cambia la maquetación**, el adaptador deja de encontrar productos y te
llega un aviso diciendo cuál y con qué error. El resto sigue funcionando.

**GitHub desactiva los cron** en repos sin actividad durante 60 días. Aquí no pasa: cada
pasada escribe en la rama `estado`, y eso cuenta como actividad.

---

## 9. Ficheros

| Fichero | Qué hace |
|---|---|
| `radar.py` | orquestador: una pasada, compara con lo guardado y avisa |
| `tiendas.py` | los seis adaptadores de tienda |
| `avisos.py` | Telegram: mandar avisos y atender comandos |
| `config.json` | palabras clave, tiendas activas, umbrales |
| `estado.json` | memoria entre pasadas (no está en `main`, ver abajo) |
| `.github/workflows/radar.yml` | el cron de GitHub Actions |
| `revisar.bat` | pasada manual de las tres tiendas que bloquean a GitHub |
| `.env` | credenciales para las pasadas desde casa (no se sube) |
| `estado-local.json` | memoria de la pasada manual (no se sube) |

El estado vive en una **rama huérfana llamada `estado`**, que se reescribe entera con un
único commit en cada pasada. Así el historial de `main` no se llena de 144 commits
automáticos al día y el repo no engorda. `config.json` viaja con él, que es lo que
permite que los cambios que haces por Telegram sobrevivan de una pasada a la siguiente.

---

## 10. Probarlo en local

```bash
pip install -r requirements.txt
python radar.py                 # sin token: los avisos salen por pantalla
```

Con credenciales, en PowerShell:

```powershell
$env:TELEGRAM_TOKEN="..."; $env:TELEGRAM_CHAT_ID="..."; python radar.py
```

`python radar.py --resembrar` vuelve a tomar la foto inicial sin avisar de nada. Útil si
cambias mucho las palabras clave y no quieres que la siguiente pasada te mande cien
avisos de golpe.

Ojo: el `estado.json` local y el de GitHub son independientes. Si pruebas en local
mientras el workflow corre, cada uno lleva su propia cuenta.

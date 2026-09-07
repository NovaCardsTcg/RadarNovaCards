# Radar Pokémon

Vigila las novedades de producto Pokémon en seis tiendas y te lo dice por Telegram.
Corre en GitHub Actions, así que no necesita el PC encendido.

Avisa de tres cosas:

- **NUEVO** — una referencia que no había visto nunca en esa tienda.
- **VUELVE EL STOCK** — un producto conocido pasa de agotado a comprable.
- **BAJA DE PRECIO** — cae respecto a la última vez (por defecto, un 5% y 1 € como mínimo).

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
/pausa  /reanudar    dejar de avisar (sigue tomando nota) y volver
/prueba              mandar un aviso de prueba
```

El bot **solo obedece a tu chat**. Si alguien más da con él, sus comandos se ignoran.

---

## 4. Qué vigila cada tienda, y con qué límites

Ninguna de estas seis webs ofrece una API pública de catálogo, así que cada una se
ataca por donde se deja. Esto es lo que hay:

| Tienda | Por dónde | Detecta | Cada |
|---|---|---|---|
| Amazon.es | buscador ordenado por novedades | nuevo, stock, precio | cada pasada |
| El Corte Inglés | buscador | nuevo, stock, precio | cada pasada |
| MediaMarkt | buscador | nuevo, stock, precio | cada pasada |
| GAME | sitemap de productos | solo nuevo | 30 min |
| Pokémon Center | sitemap de productos | solo nuevo | 30 min |
| Carrefour | sitemap de productos | solo nuevo | 6 h |

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

## 5. Lo que puede salir mal

**Amazon puede bloquear desde GitHub.** Los runners de Actions salen por IPs de centro
de datos, que es justo lo que Amazon filtra más. En local funciona bien y las pruebas
salieron limpias, pero cuenta con cortes intermitentes. El radar los aguanta: reintenta,
cambia de user-agent, y si una tienda falla sigue con las demás. Te avisa al tercer
fallo seguido, no al primero, para que un corte suelto no te suene el móvil.

Si Amazon acaba bloqueando de forma sistemática, la salida es un **self-hosted runner**
en tu PC (el mismo workflow, ejecutándose desde tu IP doméstica), asumiendo que solo
avisaría con el PC encendido. Cambia `runs-on: ubuntu-latest` por `runs-on: self-hosted`.

**Los cron de GitHub llegan tarde.** El mínimo es 5 minutos, pero cuando hay cola los
retrasos son de 10-20 minutos y a veces se salta una ejecución. Para novedades y
reposiciones va sobrado; para pelear un drop al segundo contra bots dedicados, no.

**Si una tienda cambia la maquetación**, el adaptador deja de encontrar productos y te
llega un aviso diciendo cuál y con qué error. El resto sigue funcionando.

**GitHub desactiva los cron** en repos sin actividad durante 60 días. Aquí no pasa: cada
pasada escribe en la rama `estado`, y eso cuenta como actividad.

---

## 6. Ficheros

| Fichero | Qué hace |
|---|---|
| `radar.py` | orquestador: una pasada, compara con lo guardado y avisa |
| `tiendas.py` | los seis adaptadores de tienda |
| `avisos.py` | Telegram: mandar avisos y atender comandos |
| `config.json` | palabras clave, tiendas activas, umbrales |
| `estado.json` | memoria entre pasadas (no está en `main`, ver abajo) |
| `.github/workflows/radar.yml` | el cron de GitHub Actions |

El estado vive en una **rama huérfana llamada `estado`**, que se reescribe entera con un
único commit en cada pasada. Así el historial de `main` no se llena de 144 commits
automáticos al día y el repo no engorda. `config.json` viaja con él, que es lo que
permite que los cambios que haces por Telegram sobrevivan de una pasada a la siguiente.

---

## 7. Probarlo en local

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

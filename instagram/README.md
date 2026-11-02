# Publicador de Instagram de Cromel

Publica solo en el Instagram de cromeltcg.com, con datos reales del radar, y pasa
a Instagram los TikToks que quieras.

```
GitHub Actions cada 20 min ─► instagram/publicar.py
   ├─ TikTok nuevo en tu perfil ─► cola (modo todo / elegir / nada)
   ├─ ¿ha llegado una hora de post? ─► contenido.py elige los datos ─► plantilla.html ─► JPEG
   ├─ sube las fotos o el vídeo a Supabase Storage (bucket "instagram", público)
   ├─ API de Instagram: contenedor ─► espera a Meta ─► publicar
   └─ te avisa por Telegram con el enlace al post
```

## Qué publica

| Cuándo (hora de España) | Post |
|---|---|
| 13:30 todos los días | **Chollos del día**: lo mejor de sellado de las últimas 36 h |
| 18:00 todos los días | **Publicidad de la app** (`app_promo.py`): 6 series con 3 ganchos cada una, 18 portadas sin repetir |
| 20:30 lunes | Cartas que más suben en 7 días |
| 20:30 martes | Reposiciones |
| 20:30 miércoles | Las cartas más caras de una colección reciente |
| 20:30 jueves | Próximos lanzamientos |
| 20:30 viernes | Funko con un 15 % de descuento o más |
| 20:30 sábado | One Piece, Lorcana y otros juegos |
| 20:30 domingo | Resumen de la semana con cifras |

Si un día no hay material para un post (por ejemplo, menos de 3 reposiciones),
sale el siguiente de la reserva: cartas más caras, chollos, cartas que suben. Un
producto o carta no se repite en 7 días; una colección, en 28. Cada carrusel lleva
portada, de 3 a 6 fichas y una diapositiva final con cromeltcg.com y @cromeltcg.
Las horas se cambian con `/ig horas`.

## Puesta en marcha (una vez)

1. **Supabase:** ejecutar la migración 13 (`supabase/migrations/20261102000000_instagram.sql`
   en el repo de la app) en el SQL Editor.
2. **Cuenta profesional:** en la app de Instagram, Configuración → Tipo de cuenta
   y herramientas → Cambiar a cuenta profesional (Creador o Empresa). No hace falta
   página de Facebook.
3. **App de Meta:** en [developers.facebook.com](https://developers.facebook.com/apps)
   → Crear app → caso de uso «Gestionar mensajes y contenido en Instagram». Dentro:
   Instagram → «Configuración de la API con inicio de sesión de Instagram» →
   «Generar identificadores de acceso» → Añadir cuenta → entrar con el Instagram de
   Cromel → Generar identificador. No necesitas la revisión de la app: es tu propia
   cuenta y eres administrador de la app.
4. **Token:** copiar ese identificador directamente a GitHub → RadarNovaCards →
   Settings → Secrets and variables → Actions → New repository secret, con nombre
   **`IG_TOKEN`**. No lo pegues en ningún chat. El publicador lo renueva solo cada
   semana (dura 60 días) y lo guarda en la tabla `ig_token`.
5. **Telegram:** `/ig usuario tu_usuario_de_tiktok`, y luego `/ig` para comprobar
   que sale la cuenta de Instagram.

Para probar sin esperar a la hora: Actions → instagram → Run workflow → elegir un
tipo en «ya». O desde Telegram: `/ig ya chollos`.

## TikTok → Instagram

- **`todo`** (el modo por defecto): cada TikTok nuevo pasa como Reel 60 minutos
  después (`/ig margen` para cambiarlo). El bot te avisa con el número y lo paras
  con `/ig no N`.
- **`elegir`**: el bot pregunta por cada TikTok nuevo y solo pasa lo que apruebes
  con `/ig si N`.
- **`nada`**: no mira tu perfil.
- En cualquier modo, `/ig <enlace>` pasa ese TikTok en la siguiente pasada.

La primera vez solo toma nota de lo que ya tienes publicado; pasa lo que subas a
partir de entonces. Los posts de fotos de TikTok pasan como carrusel. El vídeo se
baja sin marca de agua, se sube a Supabase el tiempo justo y se borra al publicar.

Puntos débiles conocidos:

- **yt-dlp:** TikTok cambia su web cada pocas semanas. Si un día no puede leer el
  perfil, el bot avisa (una vez al día) y puedes mandar enlaces con `/ig <enlace>`.
- **Música:** el audio de tendencia de TikTok puede salir silenciado en Instagram.
  Para esos vídeos, súbelo a mano y elige la música en Instagram.

## Comandos

`/ig ayuda` los lista todos: `on`/`off`, `si N`/`no N`, `tiktok todo|elegir|nada`,
`usuario`, `margen`, `horas`, `ya <tipo>`. Con varias horas, la primera es de chollos,
la última va según el día de la semana y las del medio son publicidad de la app.

## Límites

- 50 publicaciones por API cada 24 h (se publican unas 2-4 al día).
- Vídeos de hasta 50 MB (límite de Storage en el plan gratis de Supabase).
- Las fotos se borran del almacén a los 30 días; Instagram guarda su copia.

## Probar en el PC

```powershell
$env:SUPABASE_URL="..."; $env:SUPABASE_SERVICE_KEY="..."; $env:IG_NAVEGADOR="msedge"
python -m instagram.publicar --prueba            # todos los tipos, a instagram/prueba/
python -m instagram.publicar --prueba chollos    # solo uno
```

No publica nada: deja las imágenes y el texto en `instagram/prueba/`.

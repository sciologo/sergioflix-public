# Guía de SergioFlix

Esta guía describe el código de este repositorio y permite preparar una instalación
propia. Todos los ejemplos de IP y rutas son genéricos. Ejecuta los comandos desde
la raíz del proyecto, salvo que se indique otra carpeta.

## Índice

1. [Sistema y funcionamiento](#sistema-y-funcionamiento)
2. [Requisitos](#requisitos)
3. [Instalar el servidor](#instalar-el-servidor)
4. [Configurar fuentes y funciones opcionales](#configurar-fuentes-y-funciones-opcionales)
5. [Aplicación Android y Chromecast](#aplicación-android-y-chromecast)
6. [Aplicación Samsung Tizen](#aplicación-samsung-tizen)
7. [Uso cotidiano](#uso-cotidiano)
8. [Configuración](#configuración)
9. [API y organización del código](#api-y-organización-del-código)
10. [Operación, actualización y respaldo](#operación-actualización-y-respaldo)
11. [Pruebas y solución de problemas](#pruebas-y-solución-de-problemas)
12. [Trabajar con un LLM](#trabajar-con-un-llm)

## Sistema y funcionamiento

SergioFlix utiliza un PC encendido como servidor doméstico. PostgreSQL conserva el
catálogo, configuración cifrada de proveedores, dispositivos, favoritos, progreso y
preferencias. FastAPI centraliza la lógica y exige autenticación en las operaciones
protegidas. El panel web administra el servidor; Android y Tizen son sus clientes.

Al buscar, la API obtiene fichas del catálogo y devuelve películas o series. Al
reproducir, consulta proveedores configurados, valida y ordena fuentes, prepara una
sesión y entrega el contenido mediante el proxy local. FFmpeg inspecciona o adapta
el medio cuando hace falta. Los clientes guardan avance y preferencias a través de
la API. El asistente opcional interpreta consultas y resuelve sus elecciones contra
el catálogo; requiere una clave y un modelo configurados por el usuario.

Las fichas del catálogo no representan archivos incluidos en el repositorio. Cada
proveedor puede imponer condiciones, límites, idiomas o restricciones geográficas.
Usa fuentes y archivos para los que tengas autorización.

## Requisitos

| Componente | Requisito o función |
| --- | --- |
| Servidor principal | Windows, Python 3.11 y una IPv4 LAN estable |
| Base de datos | Docker Desktop en ejecución; Compose inicia PostgreSQL 17 |
| Dependencias Python | `requirements.lock.txt` fija las versiones verificadas; `requirements.txt` define rangos |
| Video | FFmpeg y FFprobe accesibles desde PATH, o en `tools/<distribucion>/bin/` |
| Pruebas TV | Node.js para ejecutar el simulador JavaScript |
| Android | Android Studio/SDK 35, JDK 17 y el Gradle Wrapper 8.11.1 incluido |
| Samsung TV | Televisor Tizen, Tizen Studio, TV Extensions, Samsung Certificate Extension y perfil propio |
| Chromecast | Receptor compatible y teléfono/PC en la misma red local |

La aplicación Android tiene `minSdk=26` (Android 8) y utiliza Media3. El arranque de
TV declara Tizen 2.4; la interfaz se mantiene en JavaScript ES5 para motores antiguos.
Esto describe los requisitos del código: cada modelo físico requiere su validación.
Los scripts de firewall e inicio automático son específicos de Windows. No hay
instaladores equivalentes verificados para Linux/macOS ni clientes nativos de webOS,
Android TV o Apple TV.

Reserva una dirección para el PC en el router o usa otra forma estable de asignar
su IP. La red de invitados puede impedir que teléfono, PC y TV se comuniquen.
Los puertos predeterminados son 8765 (HTTP local/medios), 8443 (HTTPS/API) y
55439 (PostgreSQL solo en loopback). Ejecuta un servidor SergioFlix por PC con esos
puertos. Los scripts operativos asumen esos valores predeterminados.

## Instalar el servidor

### 1. Descargar y crear el entorno

Clona el repositorio o descarga su ZIP. Dentro de la carpeta del proyecto:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
```

Utiliza siempre ese intérprete para instalar, migrar y ejecutar. La dependencia
`libtorrent` requiere una distribución compatible con Python y el sistema; si no
hay una rueda disponible, debe resolverse esa instalación antes de continuar.

Instala FFmpeg/FFprobe desde una distribución indicada por el
[proyecto FFmpeg](https://ffmpeg.org/download.html). Comprueba en una terminal nueva:

```powershell
ffmpeg -version
ffprobe -version
```

No es necesario copiar binarios al repositorio. La aplicación conserva compatibilidad
con una distribución local en `tools/<distribucion>/bin/ffmpeg.exe` y `ffprobe.exe`.

### 2. Crear configuración propia

Sustituye la IP de ejemplo por la IPv4 LAN del PC, no por la del televisor:

```powershell
.\.venv\Scripts\python.exe -m app.manage init --lan-ip 192.168.1.10
```

El comando genera `.env`, claves independientes y una contraseña PostgreSQL. Si
`.env` ya existe, lo conserva. `.env.example` es una referencia; los marcadores
`GENERAR` no son credenciales válidas. Sin `--lan-ip`, se genera loopback para
pruebas locales y debe configurarse `VIDEO_LAN_IP` antes de conectar otros equipos.

### 3. Preparar PostgreSQL y el certificado

Con Docker Desktop iniciado:

```powershell
docker compose up -d --wait
.\.venv\Scripts\python.exe -m app.manage migrate
.\.venv\Scripts\python.exe -m app.manage tls
```

Las migraciones son aditivas y recuerdan sus checksums. El certificado y la clave
HTTPS se crean en `private/`. El comando TLS conserva una pareja existente: cambiar
la IP después requiere revisar su SAN y volver a preparar los clientes; editar
solo `.env` no renueva automáticamente el certificado.

### 4. Iniciar y comprobar

Abre **Iniciar aplicación.cmd**. El lanzador inicia o reutiliza el supervisor y abre
el panel autenticado. El supervisor vigila el backend, prepara Docker/migraciones
cuando corresponde y vuelve a levantar el proceso si falla.

```powershell
.\.venv\Scripts\python.exe -m app.manage health
```

El panel está en `http://127.0.0.1:8765/`; `/health` debe responder 200. Para ejecutar
en primer plano durante diagnóstico, usa `python -m app.manage serve` en lugar del
supervisor, evitando iniciar ambos sobre los mismos puertos.

### 5. Acceso desde la red local e inicio automático

Con `VIDEO_LAN_IP` correcto, ejecuta **Configurar red.cmd** y acepta la elevación de
Windows. El script crea su propia regla `SergioFlix-LAN`, limitada a Python, a la IP
del PC, a los dos puertos de la app y a `LocalSubnet` en el perfil **Privado**.
Si hay un bloqueo explícito previo, revísalo en Windows: el script público no
modifica reglas de otras aplicaciones. `Restaurar-red.ps1` retira esa regla propia.

Ejecuta **Instalar inicio automático.cmd** si quieres iniciar el supervisor al
entrar en Windows. El PC y Docker deben permanecer disponibles; suspender o apagar
el PC interrumpe el servicio. Estos scripts no se ejecutan al clonar ni al instalar
dependencias. Para esta arquitectura doméstica no se requiere abrir puertos del router.

## Configurar fuentes y funciones opcionales

Desde el panel, configura las instancias que quieras utilizar. Sus manifiestos
personales se guardan cifrados en PostgreSQL y no se incorporan a Android ni a Tizen.

| Integración implementada | Variable de dominios permitidos | Sitio de configuración de ejemplo |
| --- | --- | --- |
| PenguPlay | `VIDEO_PENGUPLAY_HOSTS` | https://pengu.uk/ |
| HDHub | `VIDEO_HDHUB_HOSTS` | https://hdhub.thevolecitor.qzz.io/configure |
| Flix-Streams | `VIDEO_FLIXSTREAMS_HOSTS` | https://flixnest.app/flix-streams/configure |
| MediaFusion | `VIDEO_MEDIAFUSION_HOSTS` | https://mediafusionfortheweebs.midnightignite.me/configure |

Son ejemplos de integraciones del código, no servicios operados por este proyecto.
No necesitas usar todos. La disponibilidad depende de cada servicio y no se
garantizan cuentas, contenido, idioma o compatibilidad de cada fuente.

Para usar otra instancia del mismo proveedor, incorpora su dominio a la variable
correspondiente y reinicia el backend. El manifiesto debe ser HTTPS, pertenecer a
un dominio permitido y terminar en `/manifest.json`. La validación de identidad,
DNS público y protección contra destinos locales debe conservarse.

Para añadir **otro proveedor**, no basta con pegar una URL: hay que declarar el
proveedor en `app/providers/stremio.py`, sus dominios en `app/settings.py`, su nombre
en los modelos de `app/main.py` y comprobar su integración en el panel y clientes.
Una API ajena al protocolo Stremio requiere un adaptador. Las dependencias de
software (FastAPI, PostgreSQL, Media3, etc.) son parte de la implementación y no se
pueden sustituir arbitrariamente desde la configuración de contenido.

Funciones opcionales:

- **Asistente de búsqueda:** configura `VIDEO_OPENAI_API_KEY` y `VIDEO_OPENAI_MODEL`
  con tus propias credenciales y un identificador disponible en tu cuenta. Sin
  ambos, el asistente devuelve que no está configurado. Su uso consume tu API.
- **SubDL:** `VIDEO_SUBDL_API_KEY` añade ese catálogo de subtítulos; otras rutas de
  subtítulos implementadas pueden funcionar sin esa clave.
- **Karaoke local:** coloca una subcarpeta por artista en
  `~/Videos/SergioFlix/Karaoke`, o configura `VIDEO_KARAOKE_ROOT`. Dentro van los
  videos propios y, opcionalmente, `poster.jpg`. El sistema permite versiones y favoritos.
- **TV en directo:** la integración reúne señales verificadas del catálogo externo.
  La disponibilidad y las restricciones de cada emisor pueden cambiar.
- **Colecciones:** `python -m app.manage seed` carga las colecciones editoriales.
  `python -m app.seed` realiza una carga ampliada paginada de metadatos; requiere red.

## Aplicación Android y Chromecast

Cada instalación compila un cliente que confía en **su propio certificado público**.
No se publica un APK preconfigurado para un servidor particular.

1. Instala Android Studio/SDK 35 y JDK 17. AGP 8.9.2 y Kotlin 2.1.20 ya están
   declarados; el Gradle Wrapper usa Gradle 8.11.1 con checksum de distribución.
2. Genera TLS en el servidor con la IP correcta. Copia solamente el certificado
   público, nunca `server.key`, al recurso local excluido de Git:

   ```powershell
   New-Item -ItemType Directory -Force android/app/src/main/res/raw
   Copy-Item private/server.crt android/app/src/main/res/raw/home_server.crt
   ```

3. Abre `android/` en Android Studio y configura tu SDK, o usa `ANDROID_HOME`.
   Compila un APK de prueba:

   ```powershell
   cd android
   .\gradlew.bat assembleDebug
   cd ..
   New-Item -ItemType Directory -Force dist
   Copy-Item android/app/build/outputs/apk/debug/app-debug.apk dist/Videoteca.apk
   ```

4. Instala el APK en tu teléfono. El panel puede servir `dist/Videoteca.apk` por su
   enlace de descarga. Genera una conexión Android en el panel y abre el enlace de
   emparejamiento en el teléfono. El código es temporal y de un solo uso.
5. Para Chromecast, conecta teléfono, receptor y PC a la misma red; usa el botón
   Cast de la aplicación. El PC sigue preparando y entregando el video.

Para mantener actualizaciones de una instalación, conserva la misma clave de
firma. La configuración opcional de release busca `private/android-release.p12` y
`private/signing.properties`, con `storePassword`, `keyAlias` y `keyPassword`.
Cada usuario crea su almacén y su configuración; no hay una firma compartida.
Un APK debug sirve para pruebas, no sustituye una distribución release propia.

Referencias oficiales: [compilar con Gradle](https://developer.android.com/build),
[instalar en un dispositivo](https://developer.android.com/studio/run/device) y
[firmar aplicaciones](https://developer.android.com/studio/publish/app-signing).

## Aplicación Samsung Tizen

El paquete instalado en la TV es un arranque pequeño. Abre `/tv/` en el servidor y
la interfaz se actualiza desde allí. El JavaScript del televisor se mantiene en ES5.

1. Instala Tizen Studio, TV Extensions y Samsung Certificate Extension.
2. Activa el modo desarrollador del televisor y configura la IP de tu PC siguiendo
   la [guía de Samsung para dispositivos](https://developer.samsung.com/smarttv/develop/getting-started/using-sdk/tv-device.html).
3. Crea un perfil Samsung propio y registra tus dispositivos en Certificate Manager.
   Los identificadores y claves de ese perfil son locales. Consulta la
   [guía oficial de certificados](https://developer.samsung.com/smarttv/develop/getting-started/setting-up-sdk/creating-certificates.html).
4. Con el servidor accesible y la TV encendida, ejecuta:

   ```powershell
   & '.\Instalar en televisor.cmd' 192.168.1.50 MiPerfilSamsung
   ```

   La dirección del ejemplo es la de **la TV**. El instalador toma la IP del
   servidor desde `.env` y prepara una copia en `private/tizen-app/`. Ajusta allí
   la URL y `allow-navigation`; firma e instala su `.buildResult/SergioFlix.wgt`.
   Las plantillas `tizen/app/` permanecen genéricas y sin firmas.

5. Abre SergioFlix en la TV. En el panel del PC genera una conexión para TV e
   introduce con el mando el código de ocho dígitos. Caduca a los cinco minutos.

Cambiar solo `tv/app.js` o `tv/style.css` se entrega al reabrir la app con el
servidor disponible. Cambiar la dirección del servidor o el propio arranque exige
volver a preparar el paquete. La aceptación de certificados y los pasos concretos
del modo desarrollador dependen del modelo y de las herramientas Samsung.

## Uso cotidiano

- Navega por películas, series, favoritos y continuar viendo. En TV, las flechas
  mueven la selección y OK abre o confirma.
- En Buscar, escribe un título; Derecha o Abajo selecciona el botón y OK consulta.
  Izquierda permite editar, Arriba desde los resultados vuelve al botón y Backspace
  borra texto conservando la edición.
- El acceso GPT permite describir una búsqueda cuando el asistente está configurado.
  Las elecciones incluyen motivos; volver de una ficha recupera los resultados.
- El reproductor permite reanudar, buscar otra fuente, cambiar audio y subtítulos
  y avanzar o retroceder. Algunas fuentes requieren preparación y otras fallan;
  la app utiliza los mecanismos de reintento disponibles.
- Cada cliente conserva autenticación y la biblioteca doméstica comparte favoritos
  e historial. El diseño actual no equivale a perfiles independientes por persona.

## Configuración

| Variable | Uso |
| --- | --- |
| `VIDEO_API_KEY` | Credencial administrativa generada localmente; mínimo 32 caracteres |
| `VIDEO_ENCRYPTION_KEY` | Clave Fernet para secretos persistidos; conservarla con el respaldo |
| `VIDEO_DATABASE_URL` | Conexión PostgreSQL del backend |
| `VIDEO_POSTGRES_PASSWORD` | Contraseña con la que Compose inicializa PostgreSQL |
| `VIDEO_LAN_IP` | IPv4 estable del PC, utilizada en enlaces y certificados |
| `VIDEO_*_HOSTS` | Lista de dominios permitidos de cada proveedor, separados por coma |
| `VIDEO_OPENAI_API_KEY`, `VIDEO_OPENAI_MODEL` | Configuración opcional del asistente |
| `VIDEO_SUBDL_API_KEY` | Catálogo opcional de subtítulos |
| `VIDEO_KARAOKE_ROOT` | Carpeta de karaoke propia; por defecto bajo el directorio del usuario |
| `VIDEO_DIRECT_ONLY` | Por defecto true: ofrecer fuentes directas en lugar de P2P |
| `VIDEO_PROBE_SOURCES` | Por defecto true: medir/inspeccionar fuentes antes de ordenarlas |

Reinicia el backend para aplicar cambios de configuración. Cambiar una contraseña
en `.env` no cambia automáticamente una base PostgreSQL ya inicializada. Cambiar
la clave de cifrado sin migrar los datos impide leer los secretos previamente guardados.

## API y organización del código

Las operaciones protegidas utilizan `X-API-Key`. El emparejamiento crea claves de
dispositivo; las operaciones administrativas requieren el actor administrador.
La sesión del panel se obtiene mediante el lanzador local. Las entregas de medios
utilizan identificadores/enlaces temporales para que los reproductores no necesiten
incorporar las credenciales administrativas. La API interactiva de documentación
está deshabilitada; el contrato actual está en los modelos y rutas de `app/main.py`.

| Área | Rutas representativas |
| --- | --- |
| Salud | `GET /health` |
| Proveedores | `GET /api/providers`, `PUT /api/providers/{provider}` con `manifest_url` |
| Dispositivos | `POST /api/devices/pairing`, `/pairing/tv`, `/pair` |
| Catálogo | `GET /api/catalog/{media_type}`, `/api/content/{media_type}/{content_id}` |
| Asistente | `POST /api/assistant/search` con `prompt` |
| Biblioteca | `/api/library/favorites`, `/api/library/progress`, `/api/library/continue` |
| Reproducción | `/api/playback/sources/{media_type}/{video_id}`, `/start`, `/stop`, `/cast`, `/progress` |
| Audio/subtítulos | `/api/playback/audio/{source_id}`, `/subtitles/{source_id}`, `/subtitles/sync` |
| Diario | `POST /api/playback/event`, `GET /api/playback/analysis` |

| Directorio o módulo | Responsabilidad |
| --- | --- |
| `app/main.py` | Contratos HTTP, autenticación, validación y composición de servicios |
| `app/settings.py`, `app/manage.py` | Configuración y comandos de operación |
| `app/storage.py`, `migrations/` | PostgreSQL, persistencia y evolución del esquema |
| `app/providers/`, `app/catalog.py` | Adaptadores, validación y caché de metadatos |
| `app/playback*.py`, `app/source_*.py`, `app/stream_ranking.py` | Sesiones, ranking, identidad y recuperación |
| `app/media_tools.py`, `app/torrents.py`, `app/subtitle*.py` | Proxy, FFmpeg, torrents y subtítulos |
| `app/devices.py`, `app/admin_session.py`, `app/observability.py` | Acceso, emparejamiento y auditoría |
| `frontend/` | Panel de administración HTML/CSS/JS |
| `tv/` | Interfaz TV servida por el backend, JavaScript ES5 |
| `tizen/app/`, `tizen/direccion.py` | Plantillas de arranque y preparación local del paquete |
| `android/` | Cliente Kotlin/Compose, Media3 y Google Cast |
| `tests/` | Pruebas de API, módulos, PostgreSQL y simulador de TV |

Los logs de auditoría están en `logs/audit.jsonl`; servidor y supervisor utilizan
sus propios archivos rotatorios. Se registran actores, operaciones, tiempos y
errores controlados, evitando cuerpos, claves y URLs privadas. PostgreSQL contiene
también el diario de reproducción. `private/`, `cache/`, `logs/`, `dist/` y las
herramientas locales forman parte de la instalación, no del código público.

## Operación, actualización y respaldo

Antes de actualizar, conserva `.env`, `private/` y un respaldo de PostgreSQL. Estos
datos permiten conservar acceso, descifrado, certificados, firma y biblioteca.
Aplica las migraciones publicadas sin modificar archivos SQL ya aplicados. Programa
los reinicios cuando no haya reproducción activa. Una actualización del servidor no
debe renovar claves ni desinstalar clientes que conservan su autenticación.

Para exportar la base sin redirigir bytes binarios desde PowerShell:

```powershell
New-Item -ItemType Directory -Force private/backup
docker compose exec -T postgres pg_dump -U android_video -d android_video -f /tmp/sergioflix.sql
docker compose cp postgres:/tmp/sergioflix.sql private/backup/database.sql
```

Ese archivo contiene datos privados. Una restauración se realiza en una base vacía
y separada, usando `psql -v ON_ERROR_STOP=1 -f ...`, y debe comprobarse antes de
sustituir una instalación. Conserva por separado sus secretos y archivos locales.
`docker compose down -v` elimina el volumen de la biblioteca: no es un paso de actualización.

## Pruebas y solución de problemas

Usa una configuración de pruebas con credenciales nuevas y una base PostgreSQL
independiente, en otro puerto o contenedor. Las pruebas PostgreSQL crean esquemas
temporales; si no hay una conexión disponible se omiten. Las pruebas de API usan
dobles y no necesitan claves de proveedores o IA reales.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node tests/prueba-salto.js
node --check tv/app.js
```

| Problema | Comprobación |
| --- | --- |
| Falta configuración o falla autenticación inicial | Ejecutar init; no reutilizar los marcadores de `.env.example` |
| PostgreSQL no responde | Docker iniciado, `docker compose ps`, puerto y credenciales coincidentes |
| El servidor no aparece en el teléfono/TV | IP LAN correcta, misma red, perfil Privado, firewall y `/health` |
| Android no compila por `home_server` | Generar TLS y copiar el certificado público al recurso local indicado |
| Android rechaza HTTPS | Certificado correspondiente al servidor y a la IP usada al emparejar |
| Descarga de APK devuelve 404 | Compilar y colocar un APK propio en `dist/Videoteca.apk` |
| Tizen rechaza el paquete | Perfil propio, dispositivo incluido en el certificado y modo desarrollador |
| Tizen build-web falla por `org/eclipse/core/runtime/Plugin` | Completar o reparar la instalación del SDK y TV Extensions antes de continuar con la firma |
| Faltan herramientas de video | FFmpeg/FFprobe en PATH o distribución local admitida bajo tools/ |
| Hay fichas pero no video | Revisar fuentes configuradas, disponibilidad y diagnóstico de reproducción |
| El asistente devuelve no configurado | Completar tanto la clave como el modelo en `.env` |

La compilación y el simulador no sustituyen probar reproducción, mando, audio y
subtítulos en tus dispositivos físicos. `VALIDACION.md` describe lo comprobado
para esta preparación pública y no garantiza disponibilidad de proveedores externos.

## Trabajar con un LLM

Proporciona este documento, `AGENTS.md`, la tarea concreta y el código relevante.
El agente debe identificar el entorno, revisar evidencia y hacer cambios puntuales.
No necesita recibir `.env`, claves, certificados privados, volcados de base ni logs
completos. Debe crear su entorno Python y una configuración de pruebas separada.

Al ampliar una función, sigue su recorrido completo: cliente → modelo HTTP →
servicio → persistencia/proveedor → respuesta → estado y foco del cliente. Conserva
API-Key, límites, validación DNS, cifrado, trazabilidad y compatibilidad ES5 de TV.
Verifica los módulos afectados y explica cualquier límite de plataforma o dispositivo.
Las nuevas integraciones se documentan según el código implementado y probado.

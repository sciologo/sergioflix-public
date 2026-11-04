# SergioFlix

Videoteca doméstica con servidor propio, aplicación Android con Google Cast y una
interfaz para televisores Samsung Tizen. El PC organiza el catálogo, conserva la
biblioteca y prepara el video; el teléfono y el televisor se comunican con su API.

Incluye películas y series, favoritos, continuar viendo, selección de audio y
subtítulos, búsqueda por título y un asistente de búsqueda opcional. También puede
mostrar canales de TV y reproducir una colección local de karaoke.

El catálogo contiene fichas y metadatos. Los videos dependen de las fuentes que
configure cada usuario; el proyecto no incluye una biblioteca audiovisual ni
credenciales, certificados o paquetes personalizados de otra instalación.

## Empezar

Consulta [DOCUMENTACION.md](DOCUMENTACION.md): describe el sistema, los requisitos,
la instalación paso a paso, Android, Tizen, proveedores, configuración, operación y
el mapa de código para desarrolladores y asistentes LLM.

La ruta de instalación principal es **Windows + Python 3.11 + PostgreSQL 17 en
Docker**. Android requiere Android 8 o superior. El cliente de TV está dirigido a
Samsung Tizen; no es una aplicación nativa para Android TV, webOS o Apple TV.
Otros sistemas de servidor requieren adaptar y comprobar las tareas operativas.

```text
Android / Samsung Tizen / panel web
                 │ API local
                 ▼
         Python + FastAPI
          │             │
     PostgreSQL     proveedores / FFmpeg
```

## Datos de cada instalación

Cada usuario genera su propia configuración `.env`, claves, certificado HTTPS y
paquetes de cliente. La guía explica cómo hacerlo. Los datos locales se excluyen
de Git y el empaquetado Tizen se prepara en `private/tizen-app/`.

Los cuatro proveedores de ejemplo implementados son PenguPlay, HDHub, Flix-Streams
y MediaFusion. Son integraciones opcionales, con disponibilidad externa. La guía
explica cómo configurar instancias permitidas y qué requiere añadir otra fuente.

## Desarrollo y verificación

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node tests/prueba-salto.js
```

Las pruebas PostgreSQL necesitan una base de verificación independiente. Consulta
la guía antes de ejecutar pruebas o migraciones sobre una instalación existente.
Los cambios futuros también se revisan con el flujo de escaneo de secretos de
GitHub Actions. Las reglas para agentes están en [AGENTS.md](AGENTS.md).

La preparación pública conserva la aplicación existente y añade configuración de
red propia, plantillas genéricas de TV y la búsqueda de FFmpeg en PATH. Las
comprobaciones realizadas y sus límites se registran en [VALIDACION.md](VALIDACION.md).

## Licencia

La elección de una licencia de software está pendiente. Este repositorio todavía
no declara una licencia MIT, Apache u otra licencia de software.

Para cuestiones de exposición de datos, consulta [SECURITY.md](SECURITY.md).

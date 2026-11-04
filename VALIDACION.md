# Comprobaciones de la preparación pública

Fecha: 4 de noviembre de 2026. Las comprobaciones se realizaron en una carpeta de
publicación independiente, con entorno Python nuevo, claves recién generadas y
PostgreSQL 17 de verificación separado de la instalación doméstica.

## Resultados

- Instalación desde `requirements.lock.txt` en Python 3.11: correcta.
- Las 17 migraciones se aplicaron sobre una base de verificación nueva.
- Suite Python: **467 pruebas aprobadas, ninguna omitida**. Incluye API,
  PostgreSQL, reproducción, subtítulos, supervisor y seis pruebas nuevas de
  configuración, aislamiento de plantillas Tizen y localización de FFmpeg.
- El simulador de TV incluido en la suite ejecuta **354 comprobaciones** de mando,
  búsqueda, recuperación, reproducción y estado del cliente.
- Sintaxis ES5 de `tv/app.js` y `tizen/app/arranque.js`: aceptada por Acorn 8.15.0.
- Servidor de prueba: diez comprobaciones HTTP/HTTPS correctas. Salud e interfaz
  TV responden 200, proveedores exige API-Key (401 sin clave y 200 con clave),
  y HTTPS valida el certificado generado para esta verificación.
- Gradle Wrapper 8.11.1 generado con checksum oficial de distribución.
- Android: `assembleDebug` y `lintDebug` aprobados. Lint no encontró errores;
  conserva 33 advertencias y 10 observaciones informativas. El APK de verificación
  y su certificado local están excluidos de Git.
- Tizen: preparación de la copia privada, actualización de URL/navegación,
  conservación de plantillas y limpieza de salida anterior comprobadas con pruebas.

La ejecución completa de pytest requiere FFmpeg/FFprobe disponibles, como indica
la guía. La primera ejecución detectó que faltaban en PATH; la ejecución aprobada
utilizó las herramientas instaladas sin incorporarlas al repositorio.

## Límites de esta comprobación

El SDK Tizen instalado en el equipo de verificación falló al ejecutar `build-web`
por `NoClassDefFoundError: org/eclipse/core/runtime/Plugin`. El empaquetado firmado
y la instalación física deben comprobarse con un SDK completo y el perfil propio
de cada usuario. No se alteraron las herramientas globales para ocultar ese error.

Esta preparación no repitió reproducción en TV, teléfono o Chromecast físicos,
ni probó credenciales reales de servicios externos. Compilación, pruebas y
simuladores no garantizan disponibilidad de fuentes o compatibilidad de todos los
modelos. Los pasos de instalación detallan qué elementos son específicos de Windows.

La publicación incorpora un escaneo automatizado de secretos y una comparación
independiente con la configuración privada antes de distribuir su contenido.
Las evidencias con datos de instalación se conservan localmente, fuera de Git.

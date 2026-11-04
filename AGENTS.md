# Trabajar en SergioFlix

Lee README.md y DOCUMENTACION.md antes de cambiar la instalación o el contrato.

- Confirma la ruta, rama y entorno de trabajo. Una instalación doméstica activa y
  una copia de desarrollo pueden existir en el mismo PC.
- Conserva los avances existentes. Usa cambios puntuales, módulos claros y la
  solución más simple que resuelva el problema observado.
- Crea un entorno Python local. PostgreSQL es la persistencia y FastAPI centraliza
  funcionalidades, validación, autenticación y acceso a datos.
- Las pruebas con PostgreSQL deben utilizar una base o contenedor de verificación
  independiente. No inicialices, borres ni restaures la base de una instalación activa.
- Conserva la autenticación X-API-Key y los permisos de administrador/dispositivo.
  Nunca registres claves, cuerpos privados o URLs de manifiestos y medios firmados.
- Mantén JavaScript ES5 en tv/app.js, y comprueba mando, foco y respuestas tardías
  mediante tests/prueba-salto.js cuando cambie la interfaz de TV.
- Las plantillas Tizen son genéricas. Las direcciones, firmas y paquetes se preparan
  localmente bajo private/tizen-app/. Cada usuario utiliza sus propios certificados.
- Conserva secretos, bases, binarios de instalación, logs y datos personales fuera
  de Git. Revisa los archivos que vas a incorporar antes de crear un commit.
- Las migraciones publicadas son inmutables; incorpora una nueva cuando sea necesaria.
- Comenta decisiones no evidentes, documenta cambios de instalación o API y ejecuta
  las comprobaciones relevantes. No reescribas componentes estables sin necesidad.
- La licencia del proyecto está pendiente. No añadas una licencia por suposición.
- Publicar código no demuestra compatibilidad física con otro sistema o dispositivo:
  distingue las comprobaciones automatizadas de las verificaciones reales.

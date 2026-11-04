# Datos locales y exposición

SergioFlix está diseñado como una instalación doméstica en una red local confiable.
Su publicación describe y distribuye el código; no convierte su despliegue en un
servicio de Internet ni introduce cuentas públicas multiusuario.

Cada instalación genera claves y certificados propios. `.env`, `private/`, logs,
datos de PostgreSQL, manifiestos privados, APK/WGT personalizados y archivos de
firma deben permanecer fuera del repositorio. `.gitignore` cubre sus rutas y
extensiones habituales; revisa también cualquier archivo nuevo antes de subirlo.

El flujo Secret scanning de GitHub Actions ejecuta Gitleaks sobre la historia con
resultados redactados. Es una ayuda de detección, no una prueba absoluta de ausencia
de secretos. Conserva habilitadas las protecciones de secretos que GitHub ofrezca
para el repositorio público. Ninguna clave real es necesaria para ejecutar ese flujo.

Si detectas una exposición, evita copiar sus valores a issues, comentarios o logs.
Revoca o rota la credencial afectada y revisa también el historial y los artefactos.
Para describir un fallo, comparte una reproducción con datos sintéticos y el mínimo
contexto técnico necesario.

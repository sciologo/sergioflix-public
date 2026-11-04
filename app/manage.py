"""Operación local: secretos, migraciones y comprobación del servicio."""
import argparse
import hashlib
import ipaddress
import secrets
import sys

import httpx
from cryptography.fernet import Fernet

from app.settings import ROOT, Settings
from app.storage import Store


def initialize(lan_ip="127.0.0.1"):
    path = ROOT / ".env"
    if path.exists():
        print("La configuración ya existe; se conserva.")
        return
    # El cliente Tizen usa direcciones IPv4; validar antes de crear secretos o archivos.
    ipaddress.IPv4Address(lan_ip)
    password = secrets.token_urlsafe(32)
    content = "\n".join([
        "VIDEO_API_KEY=" + secrets.token_urlsafe(36),
        "VIDEO_ENCRYPTION_KEY=" + Fernet.generate_key().decode(),
        "VIDEO_DATABASE_URL=postgresql://android_video:" + password + "@127.0.0.1:55439/android_video",
        "VIDEO_POSTGRES_PASSWORD=" + password,
        "VIDEO_LAN_IP=" + lan_ip,
        "VIDEO_PENGUPLAY_HOSTS=pengu.uk,www.pengu.uk",
        "VIDEO_FLIXSTREAMS_HOSTS=flixnest.app,free.flixnest.app",
        "VIDEO_HDHUB_HOSTS=hdhub.thevolecitor.qzz.io",
        "VIDEO_MEDIAFUSION_HOSTS=mediafusion.elfhosted.com,mediafusionfortheweebs.midnightignite.me",
        "VIDEO_OPENAI_API_KEY=",
        "VIDEO_OPENAI_MODEL=",
        "",
    ])
    # Modo exclusivo: nunca reemplazar los secretos existentes.
    with path.open("x", encoding="utf-8") as output:
        output.write(content)
    print("Configuración local creada. No compartas el archivo .env.")


def migrate():
    store = Store(Settings())
    with store.connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(71949321)")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migration(name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        for file in sorted((ROOT / "migrations").glob("*.sql")):
            script = file.read_text(encoding="utf-8")
            checksum = hashlib.sha256(script.encode()).hexdigest()
            row = conn.execute("SELECT sha256 FROM schema_migration WHERE name=%s", (file.name,)).fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError("Una migración aplicada fue modificada: " + file.name)
                continue
            conn.execute(script)
            conn.execute("INSERT INTO schema_migration(name,sha256) VALUES (%s,%s)", (file.name, checksum))
            print("Aplicada " + file.name)
    print("Base de datos actualizada.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "migrate", "health", "serve", "tls", "seed"])
    parser.add_argument("--lan-ip", default="127.0.0.1", help="IPv4 del servidor en la red local, usada por init")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.lan_ip)
    elif args.command == "migrate":
        migrate()
    elif args.command == "health":
        response = httpx.get("http://127.0.0.1:8765/health", timeout=10)
        print("Servicio disponible" if response.status_code == 200 else "Servicio no disponible")
        sys.exit(0 if response.status_code == 200 else 1)
    elif args.command == "serve":
        import asyncio
        import uvicorn
        from app.main import create_app
        from app.tls import generate
        settings=Settings()
        cert,key=generate(settings.lan_ip)
        application=create_app(settings)
        async def run():
            import ctypes
            if sys.platform=='win32':ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            secure=uvicorn.Server(uvicorn.Config(application,host='0.0.0.0',port=settings.https_port,
                access_log=False,ssl_certfile=str(cert),ssl_keyfile=str(key),lifespan='on'))
            media=uvicorn.Server(uvicorn.Config(application,host='0.0.0.0',port=settings.media_port,
                access_log=False,lifespan='off'))
            try:await asyncio.gather(secure.serve(),media.serve())
            finally:
                if sys.platform=='win32':ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        asyncio.run(run())
    elif args.command == 'seed':
        import asyncio
        import httpx as client_module
        from app.collections import seed
        async def run():
            async with client_module.AsyncClient(timeout=client_module.Timeout(20, connect=5), trust_env=False) as client:
                await seed(Store(Settings()), client)
        asyncio.run(run())
    elif args.command == 'tls':
        from app.tls import generate
        generate(Settings().lan_ip)
        print('Certificado local preparado.')


if __name__ == "__main__":
    main()

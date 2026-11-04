"""Prepara una copia privada del arranque Tizen con la dirección de esta instalación.

Las plantillas públicas permanecen genéricas. Certificados, firmas y el paquete
se generan en private/tizen-app; nunca se reescriben dentro del árbol público.
"""
import ipaddress
import re
import shutil

from app.settings import ROOT, Settings


def prepare(settings):
    """Copia las plantillas y actualiza URL y navegación antes del empaquetado."""
    ipaddress.IPv4Address(settings.lan_ip)
    source = ROOT / "tizen" / "app"
    destination = ROOT / "private" / "tizen-app"
    # Solo se limpia la salida generada del SDK, después de resolver su destino.
    build = destination / ".buildResult"
    if build.exists():
        if not build.resolve().is_relative_to((ROOT / "private").resolve()):
            raise RuntimeError("La salida Tizen queda fuera de private/.")
        shutil.rmtree(build)
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".buildResult", "*-signature.xml", "signature1.xml", "*.wgt"))
    script = destination / "arranque.js"
    text, count = re.subn(r"(var BASE = ')[^']*(')",
                         lambda m: m[1] + f"http://{settings.lan_ip}:{settings.media_port}" + m[2],
                         script.read_text(encoding="utf-8"), count=1)
    if count != 1:
        raise RuntimeError("No se encontró la dirección en la plantilla Tizen.")
    script.write_text(text, encoding="utf-8")
    config = destination / "config.xml"
    text, count = re.subn(r"(<tizen:allow-navigation>)[^<]*(</tizen:allow-navigation>)",
                         lambda m: m[1] + settings.lan_ip + m[2],
                         config.read_text(encoding="utf-8"), count=1)
    if count != 1:
        raise RuntimeError("No se encontró allow-navigation en la plantilla Tizen.")
    config.write_text(text, encoding="utf-8")
    return destination


def main():
    destination = prepare(Settings())
    print("Arranque Tizen preparado en " + str(destination.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

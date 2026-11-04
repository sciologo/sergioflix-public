"""Rechazos comprobables de archivos; una URL HTTP por sí sola no acredita video."""
import re
from urllib.parse import unquote, urlsplit

from app.providers.stremio import ProviderError


ARCHIVE_EXTENSION = re.compile(r'\.(?:zip|rar|7z)(?=$|[\s\]\)\"\'])', re.IGNORECASE)
ARCHIVE_SIGNATURES = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08',
                      b'Rar!\x1a\x07\x00', b'Rar!\x1a\x07\x01\x00', b'7z\xbc\xaf\x27\x1c')


def declared_archive(stream: dict) -> bool:
    """Solo extensiones de archivo explícitas; no palabras como «rar» en un título."""
    hints = stream.get('behaviorHints') or {}
    filename = str(hints.get('filename') or '')
    path = unquote(urlsplit(str(stream.get('url') or '')).path)
    if any(value.lower().endswith(('.zip', '.rar', '.7z')) for value in (filename, path)):
        return True
    return any(ARCHIVE_EXTENSION.search(str(stream.get(key) or ''))
               for key in ('name', 'title', 'description'))


def archive_signature(prefix: bytes) -> bool:
    """La firma se comprueba solo al principio del archivo, nunca en un rango interior."""
    return prefix.startswith(ARCHIVE_SIGNATURES)


def archive_error() -> ProviderError:
    return ProviderError('archive_source',
        'Esta fuente es un archivo comprimido, no un video reproducible. Se intentará otra edición.', 422)

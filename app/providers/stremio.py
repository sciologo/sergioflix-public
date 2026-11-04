"""Adaptador Stremio genérico. Los tres proveedores comparten manifiesto y /stream/{tipo}/{id}.json."""
import asyncio
import ipaddress
import json
import re
import socket
from collections import Counter
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

from app.observability import audit

MAX_RESPONSE = 2_000_000
VIDEO_ID = re.compile(r"^tt\d{5,12}(?::\d{1,3}:\d{1,4})?$")


class ProviderError(Exception):
    """Solo transporta códigos estables y mensajes sin datos del proveedor."""
    def __init__(self, code: str, message: str, status: int = 502):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


class ProviderSpec:
    """Describe un addon concreto: nombre visible, identidad esperada y prioridad."""
    def __init__(self, key: str, display: str, id_tokens: tuple[str, ...], configure_url: str, priority: int):
        self.key, self.display, self.id_tokens = key, display, id_tokens
        self.configure_url, self.priority = configure_url, priority


# Este diccionario fija el orden en que se nombran los proveedores. La prioridad solo
# desempata al final del ranking: nunca adelanta a una fuente mejor.
PROVIDERS: dict[str, ProviderSpec] = {
    "penguplay": ProviderSpec("penguplay", "PenguPlay", ("penguplay",), "https://pengu.uk/", 12),
    # HDHub es de la misma familia que PenguPlay —sirve el archivo por HTTPS, sin pares y
    # sin cuenta de pago— y sobre diez títulos de esta videoteca entregó 219 enlaces
    # frente a 71. Entra con prioridad algo menor porque todavía no tiene historial aquí.
    "hdhub": ProviderSpec("hdhub", "HDHub", ("hdhub",),
                          "https://hdhub.thevolecitor.qzz.io/configure", 10),
    "flixstreams": ProviderSpec("flixstreams", "Flix-Streams", ("flixstream", "flickystream"),
                                "https://flixnest.app/flix-streams/configure", 6),
    "mediafusion": ProviderSpec("mediafusion", "MediaFusion", ("mediafusion",),
                                "https://mediafusionfortheweebs.midnightignite.me/configure", 0),
}
PROVIDER_ORDER = tuple(PROVIDERS)


def normalize_manifest(value: str, allowed_hosts: set[str]) -> str:
    value = value.strip()
    if value.startswith("stremio://"):
        value = "https://" + value[len("stremio://"):]
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ProviderError("invalid_manifest", "El enlace de configuración no es válido.", 422) from None
    if (len(value) > 16000 or parts.scheme != "https" or parts.hostname not in allowed_hosts
            or port not in (None, 443) or parts.username or parts.password or parts.query or parts.fragment
            or not parts.path.endswith("/manifest.json") or "\\" in value
            or any(ord(char) < 32 for char in value)):
        raise ProviderError("invalid_manifest", "Usa un manifiesto HTTPS de una instancia permitida para este proveedor.", 422)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


async def public_dns(host: str) -> None:
    """Las instancias son públicas; una URL no puede convertir la API en proxy LAN."""
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
            raise ProviderError("invalid_host", "La instancia no tiene una dirección pública válida.", 422)
    except OSError:
        raise ProviderError("unreachable", "No se pudo localizar el proveedor. Intenta nuevamente.") from None


def classify_stream(item: dict) -> dict:
    """Clasifica el transporte; nunca deduce español o compatibilidad Cast de una etiqueta."""
    http_url = item.get("url")
    scheme = urlsplit(http_url).scheme if isinstance(http_url, str) else ""
    if scheme in ("http", "https"):
        kind = "http"
    elif item.get("infoHash"):
        kind = "torrent"
    elif item.get("externalUrl"):
        kind = "external"
    else:
        kind = "unsupported"
    hints = item.get("behaviorHints") if isinstance(item.get("behaviorHints"), dict) else {}
    return {
        "kind": kind,
        "requires_headers": bool(hints.get("proxyHeaders")),
        "subtitle_entries": len(item.get("subtitles", [])) if isinstance(item.get("subtitles"), list) else 0,
        "spanish_verified": False,
        "cast_verified": False,
    }


class StremioAddon:
    """Cliente mínimo del protocolo Stremio. No carga ni ejecuta código del complemento."""
    spec = PROVIDERS["mediafusion"]
    # Los proveedores privados no siguen redirecciones. Solo Cinemeta declara
    # los dominios públicos entre los que puede trasladar una respuesta.
    redirect_hosts = frozenset()

    def __init__(self, client: httpx.AsyncClient, manifest_url: str, allowed_hosts: set[str], dns_check=public_dns):
        self.client = client
        self.manifest_url = normalize_manifest(manifest_url, allowed_hosts)
        self.base_url = self.manifest_url.removesuffix("/manifest.json")
        self.dns_check = dns_check

    @property
    def display(self) -> str:
        return self.spec.display

    async def _json(self, url: str) -> dict:
        name = self.spec.display
        try:
            # Límite global adicional: evita lecturas lentas indefinidas.
            async with asyncio.timeout(25):
                for hop in range(3):
                    await self.dns_check(urlsplit(url).hostname)
                    async with self.client.stream("GET", url, follow_redirects=False) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get('location', '')
                            try:
                                target = urljoin(url, location)
                                parts = urlsplit(target)
                                port = parts.port
                            except ValueError:
                                raise ProviderError('provider_response', f'{name} no entregó una respuesta válida.') from None
                            if (hop == 2 or not location or urlsplit(url).hostname not in self.redirect_hosts
                                    or parts.hostname not in self.redirect_hosts or parts.scheme != 'https'
                                    or port not in (None, 443) or parts.username or parts.password
                                    or parts.fragment or '\\' in location or any(ord(c) < 32 for c in location)):
                                raise ProviderError('provider_response', f'{name} no entregó una respuesta válida.')
                            # El contexto cierra la respuesta anterior antes del siguiente
                            # salto; cada destino vuelve a pasar por la comprobación DNS.
                            url = target
                            audit('catalog_redirect', provider=self.spec.key, count=hop + 1)
                            continue
                        if response.status_code == 429:
                            raise ProviderError("rate_limited", f"{name} limita las consultas. Espera antes de reintentar.", 503)
                        if response.status_code in (401, 403):
                            raise ProviderError("provider_auth", "La instancia rechazó la configuración. Revisa el acceso.", 502)
                        if response.status_code != 200:
                            raise ProviderError("provider_response", f"{name} no entregó una respuesta válida.")
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE:
                                raise ProviderError("response_too_large", "La respuesta supera el límite de seguridad.")
                            chunks.append(chunk)
                    data = json.loads(b"".join(chunks))
                    if not isinstance(data, dict):
                        raise ValueError("object required")
                    return data
        except (httpx.TimeoutException, TimeoutError):
            raise ProviderError("timeout", f"{name} tardó demasiado. Puedes reintentar.", 504) from None
        except httpx.HTTPError:
            raise ProviderError("unreachable", f"No se pudo conectar con {name}.") from None
        except (ValueError, UnicodeError):
            raise ProviderError("invalid_json", f"{name} devolvió datos no válidos.") from None

    async def manifest(self) -> dict:
        data = await self._json(self.manifest_url)
        provider_id = data.get("id")
        resources = data.get("resources", [])
        identified = isinstance(provider_id, str) and any(token in provider_id.lower() for token in self.spec.id_tokens)
        if not identified or not isinstance(resources, list):
            raise ProviderError("wrong_provider", f"El enlace no corresponde a un manifiesto {self.spec.display} válido.", 422)
        names = sorted({resource if isinstance(resource, str) else resource.get("name", "") for resource in resources if isinstance(resource, (str, dict))})
        if "stream" not in names:
            raise ProviderError("missing_stream", "Este manifiesto no ofrece búsqueda de fuentes.", 422)
        catalogs = data.get("catalogs", [])
        if not isinstance(catalogs, list):
            raise ProviderError("invalid_manifest", "El manifiesto tiene catálogos no válidos.")
        # Una lista vacía es válida: Cinemeta puede aportar las fichas.
        return {"provider": self.spec.display, "provider_key": self.spec.key,
                "version": str(data.get("version", "desconocida"))[:40],
                "resources": [name for name in names if name in ("catalog", "meta", "stream", "subtitles", "addon_catalog")],
                "catalog_count": len(catalogs)}

    async def raw_streams(self, media_type: str, video_id: str) -> list[dict]:
        if media_type not in ("movie", "series") or not VIDEO_ID.fullmatch(video_id):
            raise ProviderError("invalid_video", "El identificador de película o episodio no es válido.", 422)
        if (media_type == "movie" and ":" in video_id) or (media_type == "series" and video_id.count(":") != 2):
            raise ProviderError("invalid_video", "Selecciona una película o un episodio concreto.", 422)
        data = await self._json(f"{self.base_url}/stream/{media_type}/{quote(video_id, safe=':')}.json")
        streams = data.get("streams")
        if not isinstance(streams, list) or any(not isinstance(item, dict) for item in streams):
            raise ProviderError("invalid_streams", f"{self.spec.display} devolvió una lista de fuentes no válida.")
        return streams

    async def streams(self, media_type: str, video_id: str) -> dict:
        streams = await self.raw_streams(media_type, video_id)
        candidates = [classify_stream(item) for item in streams]
        counts = Counter(candidate["kind"] for candidate in candidates)
        # El diagnóstico deliberadamente no entrega las URLs privadas ni reproduce archivos.
        return {"video_id": video_id, "provider": self.spec.key, "returned_count": len(candidates),
                "counts": {kind: counts[kind] for kind in ("http", "torrent", "external", "unsupported")},
                "candidates": candidates, "playback_verified": False}


# Complementos públicos de apoyo: no entregan video ni se configuran desde el panel.
class Cinemeta(StremioAddon):
    spec = ProviderSpec("cinemeta", "Cinemeta", ("cinemeta",), "https://www.stremio.com/", 0)
    redirect_hosts = frozenset({'v3-cinemeta.strem.io', 'cinemeta-live.strem.io', 'cinemeta-catalogs.strem.io'})


class OpenSubtitles(StremioAddon):
    spec = ProviderSpec("opensubtitles", "OpenSubtitles", ("opensubtitles",), "https://www.stremio.com/", 0)


class PenguPlay(StremioAddon):
    spec = PROVIDERS["penguplay"]


class HDHub(StremioAddon):
    spec = PROVIDERS["hdhub"]


class FlixStreams(StremioAddon):
    spec = PROVIDERS["flixstreams"]


class MediaFusion(StremioAddon):
    spec = PROVIDERS["mediafusion"]


ADAPTERS: dict[str, type[StremioAddon]] = {
    "penguplay": PenguPlay,
    "hdhub": HDHub,
    "flixstreams": FlixStreams,
    "mediafusion": MediaFusion,
}


async def gather_streams(configured, media_type: str, video_id: str, adapter, health=None):
    """Consulta a la vez a todos los proveedores configurados.

    En serie, un proveedor que tarda su tiempo de espera completo retrasa a los que
    vienen detrás, y con tres configurados la espera se triplicaba: cuarenta y cinco
    segundos mirando «Buscando fuentes» aunque dos de los tres respondieran al instante.
    A la vez, la espera es la del más lento. El orden de la respuesta se conserva, que es
    el de preferencia, y el fallo de uno no afecta a los demás.
    """
    health = health or {}

    async def consultar(saved):
        return await adapter(saved["manifest_url"], saved["provider"]).raw_streams(media_type, video_id)

    resultados = await asyncio.gather(*(consultar(saved) for saved in configured), return_exceptions=True)
    groups, failures = [], []
    for saved, resultado in zip(configured, resultados):
        provider = saved["provider"]
        if isinstance(resultado, ProviderError):
            # El mensaje viene saneado del adaptador: nunca contiene la URL privada.
            failures.append({"provider": provider, "name": PROVIDERS[provider].display,
                             "code": resultado.code, "message": resultado.message})
            continue
        if isinstance(resultado, BaseException):
            raise resultado
        groups.append({"provider": provider, "streams": resultado, "health": health.get(provider, 50)})
    return groups, failures

"""Averiguar a qué servidor lleva realmente cada enlace, antes de elegirlo.

Los proveedores entregan direcciones propias de un solo uso que redirigen a
distintos CDN. La huella del enlace nunca se repite, así que lo único estable
—y lo único que predice la velocidad— es el destino final de la redirección.
"""
import asyncio
from urllib.parse import urljoin, urlsplit

from app.stream_ranking import url_key

# Este sondeo corre mientras el usuario espera la lista, así que va acotado:
# pocos candidatos y un límite corto. Lo que no responda a tiempo no puntúa.
# Se sondean seis: el orden nuevo se decide con lo medido, así que conviene que la
# medición alcance a todas las que pelean el primer puesto.
PROBE_CANDIDATES = 6
PROBE_HOPS = 3
PROBE_SECONDS = 1.5


async def final_host(client, url):
    """Sigue las redirecciones sin descargar el video y devuelve el servidor final."""
    for _ in range(PROBE_HOPS):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname:
            return None
        response = await client.send(
            client.build_request('GET', url, headers={'Range': 'bytes=0-0'}), stream=True)
        try:
            location = response.headers.get('location', '')
            status = response.status_code
        finally:
            await response.aclose()
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            continue
        return urlsplit(url).hostname
    return None


async def resolve_speeds(client, urls, host_speeds):
    """Velocidad esperable de cada enlace, según el CDN al que lleva.

    Solo se consultan unos pocos candidatos y con un límite corto: esto corre
    mientras el usuario espera la lista, y una fuente sin medir no se penaliza.
    """
    if not host_speeds or not urls:
        return {}

    # Un enlace que ya apunta al CDN conocido no necesita ninguna petición. Esto
    # permite reconocer todas las ediciones de R2, no solo las seis que alcanzan a
    # entrar en el sondeo de red después del orden preliminar.
    result={url_key(url):host_speeds[urlsplit(url).hostname]
            for url in urls if urlsplit(url).hostname in host_speeds}
    unresolved=[url for url in urls if url_key(url) not in result]

    async def resolve(url):
        try:
            async with asyncio.timeout(PROBE_SECONDS):
                return url, await final_host(client, url)
        except Exception:
            return url, None

    found = await asyncio.gather(*(resolve(url) for url in unresolved[:PROBE_CANDIDATES]))
    result.update({url_key(url): host_speeds[host] for url, host in found if host in host_speeds})
    return result

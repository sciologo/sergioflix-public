"""Identidad del archivo y alcance explícito de la comprobación de subtítulos."""
import hashlib
import json
import re

REVISION = 4
SAMPLE_BYTES = 65536


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def declared_identity(source, source_id):
    """Un torrent identifica bytes; una etiqueta solo vale dentro de esta fuente temporal."""
    stream = source.get('stream', {})
    info_hash, index = str(stream.get('infoHash', '')).lower(), stream.get('fileIdx')
    if re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', info_hash) and isinstance(index, int) and index >= 0:
        return {'key': digest(['torrent-file', info_hash, index]), 'status': 'confirmed', 'method': 'torrent_file'}
    return {'key': digest(['temporary', source_id, source.get('fingerprint')]),
            'status': 'approximate', 'method': 'metadata'}


async def sampled_identity(client, url):
    """Firma acotada de bytes reales. Nunca descarga el archivo si el servidor ignora Range.

    Es una firma de principio/final y tamaño, no un hash íntegro ni una revisión editorial.
    La ruta recibida es exclusivamente el proxy local validado de reproducción.
    """
    async def read(byte_range, expected_start=None, expected_size=None):
        async with client.stream('GET', url, headers={'Range': byte_range}, timeout=4) as response:
            match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('content-range', ''))
            if response.status_code != 206 or not match:
                raise ValueError('range_unavailable')
            start, end, size = map(int, match.groups())
            if (end - start + 1 != SAMPLE_BYTES or size < SAMPLE_BYTES * 2 or
                    (expected_start is not None and start != expected_start) or
                    (expected_size is not None and size != expected_size)):
                raise ValueError('invalid_range')
            chunks, count = [], 0
            async for chunk in response.aiter_bytes():
                count += len(chunk)
                if count > SAMPLE_BYTES:
                    raise ValueError('range_too_large')
                chunks.append(chunk)
            if count != SAMPLE_BYTES:
                raise ValueError('incomplete_range')
            return b''.join(chunks), size

    first, size = await read(f'bytes=0-{SAMPLE_BYTES - 1}', 0)
    last, _ = await read(f'bytes={size - SAMPLE_BYTES}-{size - 1}', size - SAMPLE_BYTES, size)
    key = hashlib.sha256(str(size).encode() + b':' + first + last).hexdigest()
    return {'key': key, 'status': 'sampled', 'method': 'file_signature'}


def verification(asset, embedded=False, hash_match=False, metrics=None):
    """La compatibilidad técnica no certifica traducción, integridad ni significado."""
    return {'revision': REVISION,
            'identity': {'status': 'confirmed' if embedded or hash_match else 'supported',
                         'method': 'embedded' if embedded else 'provider_hash' if hash_match else 'episode_metadata',
                         'asset_status': asset['status'], 'asset_method': asset['method']},
            'timing': {'status': 'inherited' if embedded or hash_match else 'matched',
                       'method': 'container' if embedded else 'provider_hash' if hash_match else 'voice_activity',
                       **{key: value for key, value in (metrics or {}).items()
                          if key in ('quality', 'samples', 'max_residual_seconds', 'scale', 'offset_seconds')}},
            'content': {'status': 'not_reviewed'}}

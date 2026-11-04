"""Búsqueda y sincronización segura de subtítulos en español."""
import asyncio
import hashlib
import io
import json
import math
import re
import secrets
import subprocess
import sys
import threading
import time
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit

import httpx
import pysubs2
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.media_tools import executable
from app.observability import audit
from app.stream_ranking import source_fingerprint
from app.providers.stremio import OpenSubtitles, ProviderError, public_dns
from app.settings import ROOT
from app.subtitle_identity import filename as video_filename, extended, identity_reason
from app.subtitle_assets import REVISION, declared_identity, sampled_identity, digest, verification

MAX_SUBTITLE_BYTES = 2_000_000
SUBTITLE_SUFFIXES = {".srt", ".vtt", ".ass", ".ssa"}
GENERIC_RELEASE_WORDS = {
    "movie", "series", "season", "episode", "spanish", "espanol", "sub", "subs",
    "subtitle", "subtitles", "proper", "repack", "complete",
}
SUBTITLECAT_HOST = "www.subtitlecat.com"
# Un plazo por experiencia, incluidas alternativas: consultar el estado no lo renueva.
PREPARATION_SECONDS = 10
SEARCH_SECONDS = 3
SYNC_SECONDS = 4
HARD_REJECTIONS = frozenset({'episode_mismatch', 'series_year_mismatch', 'series_country_mismatch',
    'edition_mismatch', 'subtitle_duration_mismatch', 'subtitle_part_mismatch'})


class _Links(HTMLParser):
    """Enlaces y texto de una página de resultados sin ejecutar su JavaScript."""
    def __init__(self):
        super().__init__()
        self.links, self.href, self.text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href, self.text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self.href:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.links.append(("".join(self.text).strip(), self.href))
            self.href, self.text = None, []


def _links(html):
    parser = _Links()
    parser.feed(html)
    return parser.links


def _episode_marker(video_id):
    parts = video_id.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        return None
    season, episode = map(int, parts[1:])
    return re.compile(rf"(?:s0*{season}e0*{episode}(?!\d)|(?<!\d)0*{season}x0*{episode}(?!\d))", re.I)


def webvtt(text):
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("WEBVTT"):
        return text
    if not re.search(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s+-->", text):
        raise ProviderError("invalid_subtitle", "Este subtítulo no tiene un formato válido.")
    return "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", text)


TIMESTAMP = re.compile(r"(\d{2,}):(\d{2}):(\d{2})\.(\d{3})")


def _millis(match):
    return (
        int(match.group(1)) * 3600000
        + int(match.group(2)) * 60000
        + int(match.group(3)) * 1000
        + int(match.group(4))
    )


def stamp(ms):
    ms = max(0, int(ms))
    return "%02d:%02d:%02d.%03d" % (
        ms // 3600000,
        ms // 60000 % 60,
        ms // 1000 % 60,
        ms % 1000,
    )


def shift(vtt, offset_ms):
    """Resta `offset_ms` a cada tiempo del subtítulo."""
    if not offset_ms:
        return vtt
    lines = []
    for line in vtt.split("\n"):
        if "-->" in line:
            for match in list(TIMESTAMP.finditer(line))[::-1]:
                line = line[: match.start()] + stamp(_millis(match) - offset_ms) + line[match.end() :]
        lines.append(line)
    return "\n".join(lines)


def prune(vtt):
    """Descarta los bloques que quedaron enteros antes del inicio tras desplazar."""
    blocks = vtt.split("\n\n")
    if not blocks:
        return "WEBVTT\n"
    kept = [blocks[0] if blocks[0].lstrip().startswith("WEBVTT") else "WEBVTT"]
    for block in blocks[1:] if blocks[0].lstrip().startswith("WEBVTT") else blocks:
        times = list(TIMESTAMP.finditer(block))
        if len(times) >= 2 and _millis(times[1]) <= 0:
            continue
        if block.strip():
            kept.append(block)
    return "\n\n".join(kept) + "\n"


def _plain(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def release_words(value):
    """Palabras comparables de una edición, sin adornos que no ayudan a emparejarla."""
    return {
        word
        for word in re.findall(r"[a-z0-9]+", _plain(value))
        if len(word) > 1 and word not in GENERIC_RELEASE_WORDS
    }


def release_score(video_filename, subtitle_filename):
    video, subtitle = release_words(video_filename), release_words(subtitle_filename)
    if not video or not subtitle:
        return 0
    shared = video & subtitle
    # Coincidir en WEB-DL/BluRay, grupo y códec pesa más que una palabra aislada del título.
    score = len(shared) * 10 + round(30 * len(shared) / len(video | subtitle))
    if _plain(Path(video_filename).stem) == _plain(Path(subtitle_filename).stem):
        score += 100
    return score


def _decode(raw):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def subtitle_payload(raw, filename=""):
    """Convierte texto o ZIP a WebVTT sin extraer archivos en el disco."""
    if raw.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                members = [
                    item for item in archive.infolist()
                    if not item.is_dir()
                    and Path(item.filename).suffix.lower() in SUBTITLE_SUFFIXES
                    and 0 < item.file_size <= MAX_SUBTITLE_BYTES
                ]
                if not members:
                    raise ProviderError("invalid_subtitle", "El paquete no contiene un subtítulo compatible.")
                members.sort(key=lambda item: release_score(filename, item.filename), reverse=True)
                chosen = members[0]
                raw, filename = archive.read(chosen), chosen.filename
        except ProviderError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError):
            raise ProviderError("invalid_subtitle", "El paquete de subtítulos no es válido.") from None

    text = _decode(raw)
    suffix = Path(filename).suffix.lower()
    if suffix in {".ass", ".ssa"} or text.lstrip().startswith("[Script Info]"):
        try:
            format_ = suffix.lstrip(".") if suffix in {".ass", ".ssa"} else "ass"
            return pysubs2.SSAFile.from_string(text, format_=format_).to_string("vtt")
        except Exception:
            raise ProviderError("invalid_subtitle", "Este subtítulo no tiene un formato válido.") from None
    return webvtt(text)


def _timings(text, format_):
    try:
        return [(event.start, event.end) for event in pysubs2.SSAFile.from_string(text, format_=format_).events]
    except Exception:
        return []


class Subtitles:
    def __init__(self, client, playback, media_tools=None):
        self.client, self.playback = client, playback
        self.media_tools = media_tools
        self.sync_lock = asyncio.Lock()
        self.copy_lock = threading.Lock()
        self.sync_root = (ROOT / "cache" / "subtitles").resolve()
        self.sync_root.mkdir(parents=True, exist_ok=True)
        self.search_cache = {}
        self.search_reports = {}
        self.download_cache = {}
        self.asset_cache = {}
        self.preparations = {}
        self.candidates = {}
        self.ahead_task = None
        self.ready_items = {}
        self.preparation_states = {}
        self.fresh_searches = {}
        self.manual_items = {}

    def _remember_ready(self, source_id, audio, result):
        self.ready_items[(source_id, audio)] = [result['item']]
        while len(self.ready_items) > 32:self.ready_items.pop(next(iter(self.ready_items)))
        return result

    async def close(self):
        tasks = list(self.preparations.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def asset_context(self, source_id, source):
        """Reutilizar identidad obtenida del archivo, nunca una etiqueta aproximada."""
        asset = self.asset_cache.get(source_id)
        if not asset:
            asset = declared_identity(source, source_id)
            stream = source.get('stream', {})
            from app.media_tools import native_hls
            if asset['status'] == 'approximate' and self.client and self.media_tools and not native_hls(stream):
                try:
                    async with asyncio.timeout(0.75):
                        asset = await sampled_identity(self.client, self.media_tools.source_url(source_id))
                except Exception:
                    # Sin Range el video sigue funcionando, pero no autoriza reutilización entre sesiones.
                    pass
            self.asset_cache[source_id] = asset
            while len(self.asset_cache) > 48: self.asset_cache.pop(next(iter(self.asset_cache)))
        selected = self.media_tools.audio_selections.get(source_id) if self.media_tools else None
        # El índice real identifica la pista dentro del mismo archivo. Consultar la
        # identidad no debe iniciar otro FFprobe ni variar al llegar sus metadatos.
        return asset, digest({'index': selected}), selected

    def _write_copy(self, token, body, evidence):
        """La copia visible se publica al final; PostgreSQL conserva el resultado aprobado."""
        path = self.sync_root / (token + '.vtt')
        # Se ejecuta en un worker: serializar solo la publicación evita choques de
        # os.replace en Windows, sin poner las pistas listas detrás del VAD.
        with self.copy_lock:
            try:
                saved = json.loads(path.with_suffix('.json').read_text('utf-8'))
                if (saved.get('verification_revision') == REVISION and
                        saved.get('verification') == evidence.get('verification') and
                        saved.get('body_sha256') == evidence.get('body_sha256') and
                        hashlib.sha256(path.read_text('utf-8').encode()).hexdigest() == evidence.get('body_sha256')):
                    return
            except (OSError, ValueError):
                pass
            temporary = path.with_suffix('.' + secrets.token_hex(6) + '.tmp')
            metadata = temporary.with_suffix('.json.tmp')
            try:
                temporary.write_text(body, encoding='utf-8')
                metadata.write_text(json.dumps(evidence), encoding='utf-8')
                metadata.replace(path.with_suffix('.json'))
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
                metadata.unlink(missing_ok=True)

    async def approved(self, source_id, source):
        store = getattr(self.playback, 'store', None)
        if not store or not hasattr(store, 'subtitle_binding'): return None
        asset, audio_key, selected = await self.asset_context(source_id, source)
        if asset['status'] == 'approximate': return None
        binding = await run_in_threadpool(store.subtitle_binding, source['video_id'], asset['key'], audio_key, REVISION)
        if not binding or hashlib.sha256(binding['body'].encode()).hexdigest() != binding['body_sha256']:
            return None
        if self.media_tools and self.media_tools.audio_selections.get(source_id) != selected: return None
        evidence = {'verification_revision': REVISION, 'verification': binding['verification'],
                    'body_sha256': binding['body_sha256']}
        await run_in_threadpool(self._write_copy, binding['token'], binding['body'], evidence)
        audit('subtitle_reused', video_id=source['video_id'], source_fingerprint=source.get('fingerprint'),
              subtitle_key=binding['subtitle_key'])
        return self._synced_result(binding['token'], binding['subtitle_key'], binding['verification'])['item']

    def _preparation_key(self, source_id):
        return (source_id, self.media_tools.audio_selections.get(source_id) if self.media_tools else None)

    def _preparation_state(self, key, state, items=(), reason='', message=''):
        # La disponibilidad no depende del VAD. Solo las pistas aprobadas entran
        # en subtitles; las alternativas conservan aparte su sincronía sin comprobar.
        items = list(items)
        ready_keys = {item.get('key') for item in items}
        alternatives = []
        for item in self.manual_items.get(key, []):
            if item.get('key') in ready_keys: continue
            duration = self._known_duration(key[0], {'expected_duration_ms': item['_expected_duration_ms']})
            if self._coverage_reason(item['_end_seconds'], duration): continue
            alternatives.append({name:value for name,value in item.items() if not name.startswith('_')})
        if alternatives and not items and state not in ('searching', 'retrying'):
            state, message = 'available', 'Hay alternativas en español. Puedes elegir una; su sincronía no está comprobada.'
        elif self.manual_items.get(key) and not alternatives and not items and state not in ('searching', 'retrying'):
            state, reason = 'unavailable', 'subtitle_duration_mismatch'
            message = 'Los subtítulos completos no cubren la duración de esta edición. Elige otra versión del video.'
        result = {'subtitles': list(items), 'subtitles_pending': state in ('searching','retrying'),
                  'subtitle_state': state, 'subtitle_reason': reason, 'subtitle_message': message,
                  'subtitle_alternatives': alternatives}
        self.preparation_states[key] = result
        return result

    def start_prepare(self, source_id, refresh=False):
        """Un trabajo compartido por fuente/audio; los clientes no duplican el VAD."""
        key = self._preparation_key(source_id)
        audio = key[1]
        existing = self.preparations.get(key)
        if existing and (not existing.done() or not refresh): return existing
        # La preparación del episodio siguiente siempre cede a una petición actual.
        if self.ahead_task and self.ahead_task is not asyncio.current_task() and not self.ahead_task.done():
            self.ahead_task.cancel()
        for (other_source, other_audio), previous in self.preparations.items():
            if other_source == source_id and other_audio != audio and not previous.done(): previous.cancel()
        if refresh:
            self._forget_search(source_id)
            previous = self.preparation_states.get(key, {}).get('subtitles', [])
            self.fresh_searches[key] = {item['key'] for item in previous if item.get('key')}
            self.ready_items.pop(key, None)
            self.manual_items.pop(key, None)
        self._preparation_state(key, 'searching', message='Buscando subtítulos para esta versión…')
        task = asyncio.create_task(self.prepare(source_id))
        self.preparations[key] = task
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        # Retirar solo resultados terminados, nunca dejar un proceso huérfano.
        for old, saved in list(self.preparations.items()):
            if len(self.preparations) <= 32: break
            if saved.done() and old != key:
                self.preparations.pop(old, None)
                self.preparation_states.pop(old, None)
                self.manual_items.pop(old, None)
        return task

    async def prepare(self, source_id):
        """Prepara antes de mostrar texto, sin crear una sesión ni modificar el progreso."""
        key = self._preparation_key(source_id)
        try:
            async with asyncio.timeout(PREPARATION_SECONDS):
                control = getattr(self.playback, 'control', None)
                if control:
                    async with control.operation(source_id): return await self._prepare(source_id)
                return await self._prepare(source_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            reason = 'search_timeout' if isinstance(error, TimeoutError) else (
                error.code if isinstance(error, ProviderError) else 'subtitle_unavailable')
            audit('subtitle_preparation_failed', error_code=reason)
            return self._preparation_state(key, 'failed', reason=reason,
                message='No pudimos completar la búsqueda. Puedes volver a buscar.')
        finally:
            self.fresh_searches.pop(key, None)

    async def _prepare(self, source_id):
        key = self._preparation_key(source_id)
        seen = set(self.fresh_searches.get(key, ()))
        reason = 'no_compatible_subtitles'
        # El segundo intento consulta de nuevo los proveedores; nunca vuelve a
        # calibrar las mismas candidatas solo porque cambiaron sus IDs temporales.
        for attempt in range(2):
            if attempt:
                self._forget_search(source_id)
                self._preparation_state(key, 'retrying', reason=reason,
                    message='Buscando otras alternativas para esta versión…')
                audit('subtitle_search_retry', reason=reason)
            try:
                # La primera ronda necesita buscar, descargar y comprobar. Reservar
                # solo el último 20 % para recuperar fallos evita cortarla a los 5 s.
                async with asyncio.timeout(PREPARATION_SECONDS * (.8 if not attempt else .2)):
                    tracks = await self.tracks(source_id)
                    self.candidates[source_id] = tracks
                    while len(self.candidates) > 32: self.candidates.pop(next(iter(self.candidates)))
                    ready = [track for track in tracks if track.get('sync_token') or
                             (not track.get('source_id') and not track.get('validation_required'))]
                    if ready:
                        return self._preparation_state(key, 'ready', ready, message='Subtítulos disponibles.')
                    await self._prepare_alternatives(source_id, tracks)
                    self._preparation_state(key, 'searching' if not attempt else 'retrying',
                        message='Comprobando la sincronía de los subtítulos encontrados.')
                    result = await self.resolve(source_id, None, tracks=tracks, seen=seen)
                    if result.get('verified'):
                        return self._preparation_state(key, 'ready', [result['item']], message='Subtítulos disponibles.')
                    next_reason = result.get('reason', 'no_compatible_subtitles')
                    if next_reason != 'no_compatible_subtitles' or reason == 'no_compatible_subtitles':
                        reason = next_reason
            except (TimeoutError, ProviderError) as error:
                reason = error.code if isinstance(error, ProviderError) else 'search_timeout'
        state = 'unavailable' if reason in HARD_REJECTIONS | {'no_compatible_subtitles','identity_unverified'} else 'failed'
        messages = {
            'subtitle_search_timeout': 'La consulta a los proveedores no terminó a tiempo. Puedes volver a buscar.',
            'subtitle_search_failed': 'No pudimos consultar los proveedores de subtítulos. Puedes volver a buscar.',
            'subtitle_search_incomplete': 'La búsqueda quedó incompleta porque un proveedor no respondió. Puedes volver a buscar.',
            'subtitle_provider_auth': 'Un proveedor rechazó su clave de acceso. Revisa su configuración en el PC.',
            'subtitle_provider_limited': 'Un proveedor limitó las consultas. Espera un momento y vuelve a buscar.',
            'subtitle_provider_response': 'Un proveedor de subtítulos no respondió correctamente. Puedes volver a buscar.',
            'search_timeout': 'La comprobación de subtítulos no terminó a tiempo. Puedes volver a buscar.',
            'audio_read_timeout': 'Encontramos subtítulos, pero no terminó la lectura del audio para comprobarlos.',
            'audio_decode_failed': 'Encontramos subtítulos, pero no se pudo analizar el audio para comprobarlos.',
            'subtitle_sync_timeout': 'Encontramos subtítulos, pero su comprobación de sincronía no terminó a tiempo.',
            'subtitle_download_timeout': 'La descarga de los subtítulos no terminó a tiempo. Puedes volver a buscar.',
            'subtitle_unavailable': 'No pudimos descargar uno de los subtítulos encontrados. Puedes volver a buscar.',
            'identity_unverified': 'Encontramos subtítulos, pero no pudimos confirmar que correspondan a este contenido.',
            'subtitle_duration_mismatch': 'Los subtítulos encontrados no cubren esta edición. Puedes elegir otra versión del video.',
        }
        fallback = ('No encontramos una pista compatible en los proveedores consultados. Puedes volver a buscar o elegir otra versión del video.'
                    if state == 'unavailable' else
                    'Encontramos candidatos, pero no pudimos completar su comprobación. Puedes volver a buscar.')
        return self._preparation_state(key, state, reason=reason, message=messages.get(reason, fallback))

    async def prepared(self, source_id, budget=0.1):
        key = self._preparation_key(source_id)
        ready = self.ready_items.get(key)
        if ready:return self._preparation_state(key, 'ready', ready, message='Subtítulos disponibles.')
        task = self.start_prepare(source_id)
        try:
            return await asyncio.wait_for(asyncio.shield(task), budget)
        except TimeoutError:
            return self.preparation_states[key]
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling(): raise
            return self._preparation_state(key, 'failed', reason='reference_changed',
                message='Cambió la reproducción. Vuelve a buscar para el audio actual.')
        except Exception:
            audit('subtitle_preparation_failed', error_code='subtitle_unavailable')
            return self._preparation_state(key, 'failed', reason='subtitle_unavailable',
                message='No pudimos completar la búsqueda. Puedes volver a buscar.')

    def _search_key(self, source):
        hints = source.get('stream', {}).get('behaviorHints') or {}
        return (source['video_id'], hints.get('videoHash'), hints.get('videoSize'), video_filename(source.get('stream', {})))

    def _forget_search(self, source_id):
        source = self.playback.source(source_id)
        self.search_cache.pop(self._search_key(source), None)
        self.search_reports.pop(self._search_key(source), None)

    async def _query_provider(self, provider, search, source):
        """Medir cada consulta sin convertir fallos ni falta de clave en cero resultados."""
        started = time.monotonic()
        try:
            rows = await asyncio.wait_for(search(source), SEARCH_SECONDS)
            status = 'found' if rows else 'empty'
            if provider == 'SubDL' and not self.subdl_key: status = 'not_configured'
            report = {'status': status, 'count': len(rows)}
        except (TimeoutError, httpx.TimeoutException):
            rows, report = [], {'status': 'failed', 'count': 0, 'reason': 'subtitle_search_timeout'}
        except Exception as error:
            reason = error.code if isinstance(error, ProviderError) else 'subtitle_search_failed'
            if reason == 'timeout': reason = 'subtitle_search_timeout'
            rows, report = [], {'status': 'failed', 'count': 0, 'reason': reason}
        report['elapsed_ms'] = round((time.monotonic() - started) * 1000)
        audit('subtitle_provider_search', provider=provider, video_id=source['video_id'], **report)
        return rows, report

    async def _search(self, source):
        """Una búsqueda por episodio; el filtrado de montaje se hace por edición."""
        key = self._search_key(source)
        cached = self.search_cache.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        providers = (('OpenSubtitles', self._opensubtitles), ('SubDL', self._subdl), ('SubtitleCat', self._subtitlecat))
        searches = await asyncio.gather(*(self._query_provider(name, search, source) for name, search in providers))
        rows = [row for found, _ in searches for row in found]
        reports = {name: report for (name, _), (_, report) in zip(providers, searches)}
        self.search_reports[key] = reports
        while len(self.search_reports) > 32: self.search_reports.pop(next(iter(self.search_reports)))
        failures = [report['reason'] for report in reports.values() if report['status'] == 'failed']
        if not rows and failures:
            raise ProviderError(failures[0], 'No pudimos completar la búsqueda de alternativas.')
        # Una respuesta parcial no se recuerda cinco minutos como un catálogo completo.
        if not failures: self.search_cache[key] = (time.monotonic(), rows)
        while len(self.search_cache) > 32:
            self.search_cache.pop(next(iter(self.search_cache)))
        return rows

    async def prefer_editions(self, groups, video_id, detail):
        """Con alternativas normales, no elegir un montaje extendido sin pistas para él."""
        streams = [stream for group in groups for stream in group['streams']]
        if not any(extended(video_filename(stream)) for stream in streams):
            return
        if not any(not extended(video_filename(stream)) for stream in streams):
            return
        source = {'video_id': video_id, 'content_name': detail.get('name', ''),
                  'stream': next(stream for stream in streams if extended(video_filename(stream)))}
        try:
            rows = await asyncio.wait_for(self._search(source), 5)
        except (TimeoutError, ProviderError):
            return  # Una búsqueda caída no demuestra incompatibilidad.
        for stream in streams:
            if extended(video_filename(stream)):
                stream['subtitleEditionUnavailable'] = bool(rows) and not any(extended(row['name']) for row in rows)

    def _key(self, stream):
        if stream.get('embeddedVideo'):
            identity = f"embedded:{stream.get('videoAssetKey') or stream.get('videoFingerprint')}:{stream['embeddedIndex']}"
        else:
            identity = f"{stream.get('subtitleProvider')}:{urlsplit(stream.get('url','')).path}:{stream.get('name','')}"
        return hashlib.sha256(identity.encode()).hexdigest()[:32]

    async def _embedded(self, source_id, source):
        if not self.media_tools:
            return []
        info = await self.media_tools.inspect(source_id)
        asset, _, _ = await self.asset_context(source_id, source)
        return [{'url': f"https://subtitle.invalid/embedded/{source_id}/{track['index']}",
                 'name': f"{video_filename(source['stream'])}.es.{track['index']}.vtt",
                 'provider': 'Archivo', 'hearing_impaired': False, 'embedded': True,
                 'embeddedVideo': source_id, 'embeddedIndex': track['index'],
                 'videoFingerprint': source.get('fingerprint', ''), 'videoAssetKey': asset['key']}
                for track in info.get('streams', []) if track.get('codec_type') == 'subtitle'
                and track.get('codec_name') in ('subrip','ass','ssa','webvtt','mov_text','text')
                and str((track.get('tags') or {}).get('language','')).lower() in ('spa','es','esp','es-419','es-es','es-mx')]

    @property
    def subdl_key(self):
        secret = getattr(self.playback.settings, "subdl_api_key", None)
        return secret.get_secret_value().strip() if secret else ""

    async def _opensubtitles(self, source):
        video = source["video_id"]
        kind = "series" if ":" in video else "movie"
        adapter = OpenSubtitles(
            self.client,
            "https://opensubtitles-v3.strem.io/manifest.json",
            {"opensubtitles-v3.strem.io"},
        )
        hints = source.get('stream', {}).get('behaviorHints') or {}
        extra = {key: hints[key] for key in ('videoHash','videoSize') if hints.get(key)}
        name = video_filename(source.get('stream', {}))
        if name:
            extra['filename'] = name
        suffix = '/' + urlencode(extra, quote_via=quote) if extra else ''
        data = await adapter._json(f"https://opensubtitles-v3.strem.io/subtitles/{kind}/{video}{suffix}.json")
        return [
            {
                "url": row["url"],
                "name": str(row.get("subtitleFileName") or "subtitulo.srt"),
                "provider": "OpenSubtitles",
                "season": row.get('season'), "episode": row.get('episode'),
                "hash_match": bool(extra.get('videoHash') and row.get('moviehash_match') is True),
                "hearing_impaired": bool(row.get("hearingImpaired")),
                "language_label": "Español latino" if row.get("lang") in ("spl", "es-419") else "Español",
            }
            for row in data.get("subtitles", [])
            if isinstance(row, dict)
            and row.get("lang") in ("spa", "es", "spl", "es-419")
            and str(row.get("url", "")).startswith("https://")
        ]

    async def _subdl(self, source):
        """Segundo catálogo opcional. La clave queda solo en `.env` y nunca llega al TV."""
        key = self.subdl_key
        if not key:
            return []
        parts = source["video_id"].split(":")
        params = {
            "api_key": key,
            "imdb_id": parts[0],
            "type": "tv" if len(parts) >= 3 else "movie",
            "languages": "ES",
            "subs_per_page": 30,
            "releases": 1,
            # Omitir hi incluye tanto pistas normales como pistas para hipoacusia.
            "unpack": 1,
            "client": "custom_integration",
        }
        if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
            params.update(season_number=int(parts[1]), episode_number=int(parts[2]))
        response = await self.client.get("https://api.subdl.com/api/v1/subtitles", params=params)
        if response.status_code in (401, 403):
            raise ProviderError('subtitle_provider_auth', 'SubDL rechazó la clave configurada.')
        if response.status_code == 429:
            raise ProviderError('subtitle_provider_limited', 'SubDL limitó las consultas.')
        if response.status_code != 200:
            raise ProviderError('subtitle_provider_response', 'SubDL no respondió correctamente.')
        data = response.json()
        if not isinstance(data, dict) or data.get("status") is not True:
            raise ProviderError('subtitle_provider_response', 'SubDL no entregó una respuesta válida.')
        result = []
        wanted_season, wanted_episode = params.get("season_number"), params.get("episode_number")
        for row in data.get("subtitles", []):
            if not isinstance(row, dict):
                continue
            files = row.get("unpack_files") if isinstance(row.get("unpack_files"), list) else []
            for item in files or [row]:
                if not isinstance(item, dict):
                    continue
                language = str(item.get("language") or row.get("language") or "ES").upper()
                if language not in ("ES", "SPA", "SPANISH"):
                    continue
                # Los archivos individuales mandan; en una pista suelta se conserva
                # también la identidad declarada por el proveedor para comprobarla después.
                season, episode = item.get("season"), item.get("episode")
                if wanted_season and season not in (None, 0, wanted_season):
                    continue
                if wanted_episode and episode not in (None, 0, wanted_episode):
                    continue
                path = str(item.get("url") or row.get("url") or "")
                url = urljoin("https://dl.subdl.com/", path)
                if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "dl.subdl.com":
                    continue
                name = str(
                    item.get("release_name") or item.get("name")
                    or row.get("release_name") or row.get("name") or "subtitulo.srt"
                )
                result.append({
                    "url": url,
                    "name": name,
                    "provider": "SubDL",
                    "hearing_impaired": bool(item.get("hi", row.get("hi", False))),
                    "season": season, "episode": episode,
                })
        return result

    async def _subtitlecat(self, source):
        """Mismo proveedor conocido: episodio exacto o título y año de la película."""
        title = str(source.get("content_name") or "").strip()
        marker = _episode_marker(source["video_id"])
        movie_words = release_words(title)
        year = re.search(r'\b(?:19|20)\d{2}\b', str((source.get('subtitle_context') or {}).get('releaseInfo')
                         or video_filename(source.get('stream', {}))))
        if not title or (not marker and (':' in source['video_id'] or not movie_words or not year)):
            return []
        search_url = f"https://{SUBTITLECAT_HOST}/index.php?search={quote(title)}"
        response = await self.client.get(search_url)
        if response.status_code != 200:
            raise ProviderError('subtitle_provider_response', 'SubtitleCat no respondió correctamente.')
        releases = []
        for label, href in _links(response.text):
            candidate = urljoin(f"https://{SUBTITLECAT_HOST}/", href)
            parts = urlsplit(candidate)
            matches = (bool(marker.search(unquote(label + " " + parts.path))) if marker else
                       movie_words <= release_words(label) and year.group() in release_words(label))
            if (parts.scheme == "https" and parts.hostname == SUBTITLECAT_HOST and matches
                    and parts.path.startswith("/subs/") and parts.path.endswith(".html")):
                releases.append((label, candidate))
        # Una película requiere título y año; después sigue pasando por identidad y cobertura.
        if not marker:
            releases.sort(key=lambda item: release_score(video_filename(source.get('stream', {})), item[0]), reverse=True)
        unique_releases = list(dict.fromkeys(releases))[:8]
        pages = await asyncio.gather(*(self.client.get(url) for _, url in unique_releases), return_exceptions=True)
        result, failed = [], False
        for (release, _), page in zip(unique_releases, pages):
            if isinstance(page, Exception) or page.status_code != 200:
                failed = True
                continue
            for _, href in _links(page.text):
                candidate = urljoin(f"https://{SUBTITLECAT_HOST}/", href)
                parts = urlsplit(candidate)
                path = unquote(parts.path).lower()
                if (parts.scheme == "https" and parts.hostname == SUBTITLECAT_HOST
                        and parts.path.startswith("/subs/")
                        and (path.endswith("-es.srt") or path.endswith("-es-419.srt"))):
                    language = "es-419" if path.endswith("-es-419.srt") else "es"
                    result.append({"url": candidate, "name": f"{release}.{language}.srt",
                        "provider": "SubtitleCat", "hearing_impaired": False,
                        "language_label": "Español latino" if language == "es-419" else "Español"})
        if failed and not result:
            raise ProviderError('subtitle_provider_response', 'No pudimos consultar las pistas de SubtitleCat.')
        return result

    async def tracks(self, source_id):
        source = await run_in_threadpool(self.playback.source, source_id)
        approved = None if self._preparation_key(source_id) in self.fresh_searches else await self.approved(source_id, source)
        if approved: return [approved]
        searches = await asyncio.gather(self._search(source),
            asyncio.wait_for(self._embedded(source_id, source), SEARCH_SECONDS), return_exceptions=True)
        rows = [row for found in searches if isinstance(found, list) for row in found]
        if not rows and any(isinstance(found, Exception) for found in searches):
            for found in searches:
                if isinstance(found, ProviderError): raise found
            raise ProviderError('subtitle_search_failed','No pudimos completar la búsqueda de alternativas.')
        audit('subtitle_candidates', video_id=source['video_id'], status='found', count=len(rows))
        accepted = []
        for row in rows:
            reason = identity_reason(source, row)
            if reason and reason != 'identity_unverified':
                audit('subtitle_candidate', status='rejected', reason=reason,
                      video_id=source['video_id'], source_fingerprint=source.get('fingerprint'))
                continue
            accepted.append(dict(row, identity_reason=reason))
        rows = accepted
        audit('subtitle_candidates', video_id=source['video_id'], status='identity_accepted', count=len(rows))
        if not rows and any(report['status'] == 'failed' for report in self.search_reports.get(self._search_key(source), {}).values()):
            raise ProviderError('subtitle_search_incomplete', 'La búsqueda quedó incompleta; no se pudo consultar un proveedor.')

        stream = source["stream"]
        # HdHub guarda la publicación en description; name solo dice proveedor/resolución.
        filename = video_filename(stream)
        rows.sort(
            key=lambda row: (
                bool(row.get('embedded')), bool(row.get('hash_match')), not bool(row.get('identity_reason')),
                release_score(filename, row["name"]) - (3 if row["hearing_impaired"] else 0),
                row["provider"] == "OpenSubtitles",
            ),
            reverse=True,
        )
        unique, seen_urls = [], set()
        for row in rows:
            # Un nombre genérico repetido no demuestra que las descargas sean iguales.
            if row["url"] in seen_urls:
                continue
            seen_urls.add(row["url"])
            unique.append(row)
            if len(unique) >= 6:
                break

        result = []
        for index, row in enumerate(unique):
            stream = {
                "url": row["url"],
                "name": row["name"],
                "behaviorHints": {"filename": row["name"]},
                "subtitleProvider": row["provider"],
                "subtitleVideo": source_id,
                "subtitleIdentityReason": row.get('identity_reason', ''),
                "subtitleMetadata": {key: row[key] for key in ('season','episode','hash_match','embedded') if key in row},
                **{key: row[key] for key in ('embeddedVideo','embeddedIndex','videoFingerprint','videoAssetKey') if key in row},
            }
            saved = await run_in_threadpool(
                self.playback.save_sources, source["content_id"], source["video_id"], [stream]
            )
            if not saved:
                continue
            identifier = saved[0]["source_id"]
            suffix = "opción 1" if index == 0 else f"alternativa {index + 1}"
            result.append({
                # Ignorar firmas de la URL y el número de opción, que cambian al buscar.
                "key": self._key(stream),
                "source_id": identifier,
                "url": (
                    f"http://{self.playback.settings.lan_ip}:"
                    f"{self.playback.settings.media_port}/subtitles/{identifier}.vtt"
                ),
                "mime": "text/vtt",
                "language": "es",
                "label": f"{row.get('language_label', 'Español')} · {row['provider']} · {suffix}",
                "provider": row["provider"],
                "validation_required": True,
                "verified": bool(row.get('embedded')),
                "verification": verification({'status':'approximate','method':'metadata'}, embedded=True)
                    if row.get('embedded') else {'revision': REVISION,
                        'identity': {'status': 'unverified' if row.get('identity_reason') else 'supported'},
                        'timing': {'status': 'not_checked'}, 'content': {'status': 'not_reviewed'}},
            })
        audit('subtitle_candidates', video_id=source['video_id'], status='selected', count=len(result))
        return result

    async def _download_vtt(self, source_id):
        source = await run_in_threadpool(self.playback.source, source_id)
        if source.get("provider") != "subtitulos":
            raise ProviderError("invalid_subtitle", "La pista de subtítulos no es válida.", 422)
        stream = source["stream"]
        if source_id in self.download_cache:
            return self.download_cache[source_id]
        if stream.get('embeddedVideo'):
            return await self._download_embedded(stream)
        url = str(stream.get("url", ""))
        filename = str((stream.get("behaviorHints") or {}).get("filename") or stream.get("name") or "")
        try:
            for _ in range(5):
                parts = urlsplit(url)
                if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
                    raise ProviderError("invalid_subtitle", "El enlace del subtítulo no es válido.")
                await public_dns(parts.hostname)
                # Las claves gratuitas autentican la búsqueda; la descarga individual
                # usa el cupo anónimo documentado por SubDL y no expone la clave.
                async with self.client.stream("GET", url, follow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    if response.status_code != 200:
                        raise ProviderError("subtitle_unavailable", "Este subtítulo no está disponible.")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_SUBTITLE_BYTES:
                            raise ProviderError("subtitle_too_large", "Este subtítulo es demasiado grande.")
                        chunks.append(chunk)
                    body = subtitle_payload(b"".join(chunks), filename)
                    self.download_cache[source_id] = body
                    while len(self.download_cache) > 48:
                        self.download_cache.pop(next(iter(self.download_cache)))
                    return body
        except ProviderError:
            raise
        except httpx.TimeoutException:
            raise ProviderError('subtitle_download_timeout', 'La descarga del subtítulo no terminó a tiempo.') from None
        except Exception:
            raise ProviderError("subtitle_unavailable", "No se pudo descargar el subtítulo.") from None
        raise ProviderError("subtitle_redirect", "Este subtítulo no está disponible.")

    def _known_duration(self, source_id, source):
        """La inspección ya obtenida manda; consultar opciones nunca abre otro FFprobe."""
        inspections = getattr(self.media_tools, 'inspections', {})
        value = inspections.get(source_id, {}).get('format', {}).get('duration')
        try:
            duration = float(value)
            if math.isfinite(duration) and duration > 0: return duration
        except (TypeError, ValueError): pass
        return float(source.get('expected_duration_ms') or 0) / 1000

    @staticmethod
    def _coverage_reason(end, duration):
        return 'subtitle_duration_mismatch' if duration and (
            end < duration * .78 or end > max(duration * 1.12, duration + 120)) else ''

    async def _manual_vtt(self, source_id):
        """Texto original elegido manualmente: identidad y cobertura, sin afirmar sincronía."""
        subtitle = await run_in_threadpool(self.playback.source, source_id)
        stream = subtitle['stream']
        video_source_id = stream.get('subtitleVideo')
        if subtitle.get('provider') != 'subtitulos' or not video_source_id:
            raise ProviderError('invalid_subtitle', 'La pista no pertenece a esta reproducción.', 422)
        video = await run_in_threadpool(self.playback.source, video_source_id)
        if (subtitle['content_id'], subtitle['video_id']) != (video['content_id'], video['video_id']):
            raise ProviderError('invalid_subtitle', 'La pista no pertenece a esta reproducción.', 422)
        reason = identity_reason(video, {'name':stream.get('name',''), **stream.get('subtitleMetadata', {})})
        if reason:
            raise ProviderError(reason, 'Esta pista no corresponde a la edición elegida.', 422)
        body = await self._download_vtt(source_id)
        timings = _timings(body, 'vtt')
        if not timings: raise ProviderError('invalid_subtitle', 'La pista no contiene texto válido.', 422)
        end = max(end for _, end in timings) / 1000
        if self._coverage_reason(end, self._known_duration(video_source_id, video)):
            raise ProviderError('subtitle_duration_mismatch', 'Esta pista no cubre la edición del video. Elige otra versión.', 422)
        return body, end, video.get('expected_duration_ms', 0)

    async def _prepare_alternatives(self, source_id, tracks):
        """Comprobar archivos de texto pequeños en paralelo, sin esperar análisis de audio."""
        key = self._preparation_key(source_id)
        self.manual_items.setdefault(key, [])
        known = {item['key'] for item in self.manual_items[key]}
        async def check(track):
            if not track.get('source_id') or track.get('verified') or track.get('key') in known: return
            try:
                async with asyncio.timeout(SEARCH_SECONDS):
                    _, end, expected = await self._manual_vtt(track['source_id'])
                self.manual_items[key].append(dict(track, manual_only=True,
                    url=track['url'] + '?manual=true', _end_seconds=end, _expected_duration_ms=expected))
                self._preparation_state(key, 'searching', message='Hay alternativas en español; comprobando sincronía.')
            except (ProviderError, TimeoutError, KeyError) as error:
                reason = error.code if isinstance(error, ProviderError) else (
                    'subtitle_download_timeout' if isinstance(error, TimeoutError) else 'invalid_subtitle')
                audit('subtitle_option_rejected', subtitle_key=track.get('key'), reason=reason,
                      status='rejected' if reason in HARD_REJECTIONS else 'inconclusive')
        await asyncio.gather(*(check(track) for track in tracks))
        order = {track['key']: index for index, track in enumerate(tracks) if track.get('key')}
        self.manual_items[key].sort(key=lambda item: order.get(item.get('key'), 0))
        source = await run_in_threadpool(self.playback.source, source_id)
        audit('subtitle_options', video_id=source['video_id'], count=len(self.manual_items[key]))

    async def _download_embedded(self, stream):
        """Extrae una pista de texto del mismo archivo, con tiempo y tamaño acotados."""
        source = await run_in_threadpool(self.playback.source, stream['embeddedVideo'])
        if source.get('fingerprint') != stream.get('videoFingerprint'):
            raise ProviderError('invalid_subtitle', 'La pista pertenece a otra edición.', 422)
        asset, _, _ = await self.asset_context(stream['embeddedVideo'], source)
        if stream.get('videoAssetKey') and stream['videoAssetKey'] != asset['key']:
            raise ProviderError('invalid_subtitle', 'La pista pertenece a otro archivo.', 422)
        path = self.sync_root / ('embedded-' + digest([asset['key'],stream['embeddedIndex']]) + '.vtt')
        if path.exists():
            return await run_in_threadpool(path.read_text, 'utf-8')
        command = [executable('ffmpeg'), '-nostdin','-v','error','-rw_timeout','10000000',
                   '-i', self.media_tools.source_url(stream['embeddedVideo']),
                   '-map', f"0:{int(stream['embeddedIndex'])}", '-vn','-an','-c:s','webvtt','-f','webvtt','pipe:1']
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        try:
            body, _ = await asyncio.wait_for(process.communicate(), 35)
            if process.returncode or len(body) > MAX_SUBTITLE_BYTES:
                raise ProviderError('subtitle_unavailable', 'No se pudo extraer esta pista.')
            # FFmpeg puede emitir MM:SS; normalizar a HH:MM:SS permite aplicar offset
            # al retomar o enviar a Chromecast con la misma ruta que el resto de pistas.
            text = pysubs2.SSAFile.from_string(webvtt(body.decode('utf-8-sig')), format_='vtt').to_string('vtt')
            temporary = path.with_suffix('.tmp')
            await run_in_threadpool(temporary.write_text, text, 'utf-8')
            temporary.replace(path)
            return text
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def deliver(self, source_id, offset_ms=0, manual=False):
        if manual:
            body, _, _ = await self._manual_vtt(source_id)
            audit('subtitle_manual_delivery', status='unverified')
            return Response(prune(shift(body, offset_ms)), media_type='text/vtt; charset=utf-8')
        source = await run_in_threadpool(self.playback.source, source_id)
        if not source['stream'].get('embeddedVideo'):
            video_id = source['stream'].get('subtitleVideo')
            if not video_id:
                raise ProviderError('subtitle_verification_required', 'Vuelve a buscar subtítulos para verificar esta pista.', 409)
            result = await self.sync(video_id, source_id)
            if not result.get('verified'):
                raise ProviderError('subtitle_incompatible', 'Esta pista no se pudo verificar para el video.', 422)
            return await self.deliver_synced(result['item']['sync_token'], offset_ms)
        body = await self._download_vtt(source_id)
        if offset_ms:
            body = prune(shift(body, offset_ms))
        return Response(body, media_type="text/vtt", headers={"Access-Control-Allow-Origin": "*"})

    async def _run_sync(self, video_source_id, input_path, output_path, audio_index=None):
        """Usa audio completo ya preparado o muestras cuya ausencia no se cuenta como silencio."""
        folder = (self.media_tools.root if self.media_tools else ROOT / "cache" / "cast") / video_source_id
        def generation():
            try: return (folder / 'generation').read_text()
            except OSError: return None
        reference_generation = generation()
        if self.media_tools and self.media_tools.audio_selections.get(video_source_id) != audio_index:
            return {'accepted': False, 'reason': 'reference_changed'}
        local = False
        try:
            playlist = (folder / "index.m3u8").read_text()
            local = int((folder / "offset").read_text()) == 0 and "#EXT-X-ENDLIST" in playlist
            if self.media_tools:
                local = local and (folder / 'conversion').read_text() == self.media_tools.conversion_identity(audio_index)
        except (OSError, ValueError):
            playlist = ""
        reference = (str(folder / "index.m3u8") if local else
                     f"http://127.0.0.1:{self.playback.settings.media_port}/media/{video_source_id}")
        audio_stream = "0:a:0" if local else None
        if local:
            duration = sum(float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist))
        else:
            if not self.media_tools:
                raise ProviderError("subtitle_sync_failed", "No se pudo inspeccionar el audio de esta edición.")
            from app.media_tools import pick_audio
            info = await self.media_tools.inspect(video_source_id)
            duration = float(info.get("format", {}).get("duration", 0))
            selected = pick_audio(info, audio_index)
            audio = [row for row in info["streams"] if row.get("codec_type") == "audio"]
            audio_stream = next((f"0:a:{i}" for i, row in enumerate(audio)
                                 if row.get("index") == selected.get("index")), None)
        job = {"reference": reference, "local": local, "duration": duration,
               "audio_stream": audio_stream, "input": str(input_path), "output": str(output_path),
               "ffmpeg_path": str(Path(executable("ffmpeg")).parent)}
        video = await run_in_threadpool(self.playback.source, video_source_id)
        asset, audio_key, selected_index = await self.asset_context(video_source_id, video)
        if selected_index != audio_index:return {'accepted': False, 'reason': 'reference_changed'}
        voice_key = digest(['voice-v3-sampled',video['video_id'],asset['key'],audio_key])
        job['voice_cache'] = str(self.sync_root / (voice_key + '.npz'))
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.subtitle_sync", cwd=ROOT,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        async def stop():
            if process.returncode is None:
                # El VAD abre FFmpeg: cancelar también sus hijos evita descargas huérfanas.
                if sys.platform == "win32":
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(process.pid), "/T", "/F",
                        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    await killer.wait()
                if process.returncode is None:
                    process.kill()
                await process.wait()
        try:
            output, _ = await asyncio.wait_for(process.communicate(json.dumps(job).encode()), SYNC_SECONDS)
        except asyncio.CancelledError:
            await stop()
            raise
        except TimeoutError:
            await stop()
            raise ProviderError(
                "subtitle_sync_timeout", "La sincronización tardó demasiado; se conservó el original.", 504
            ) from None
        if ((self.media_tools and self.media_tools.audio_selections.get(video_source_id) != audio_index)
                or generation() != reference_generation):
            # Solo esta señal generada queda invalidada; no se toca el video ni el original.
            Path(job['voice_cache']).unlink(missing_ok=True)
            return {'accepted': False, 'reason': 'reference_changed'}
        if process.returncode or len(output) > 10000:
            raise ProviderError(
                "subtitle_sync_failed", "No se pudo sincronizar esta pista; se conservó el original."
            )
        try:
            result = json.loads(output)
        except (ValueError, UnicodeError):
            raise ProviderError("subtitle_sync_failed", "No se pudo analizar esta pista; se conservó el original.") from None
        if not isinstance(result, dict) or "accepted" not in result:
            raise ProviderError("subtitle_sync_failed", "No se pudo analizar esta pista; se conservó el original.")
        if result["accepted"] and not output_path.exists():
            raise ProviderError("subtitle_sync_failed", "No se pudo guardar esta pista; se conservó el original.")
        audit("subtitle_alignment", status="accepted" if result["accepted"] else "rejected", **result)
        return result

    async def _verdict(self, token, video, stream, status, reason=''):
        key = self._key(stream)
        audit('subtitle_verdict', status=status, reason=reason, video_id=video['video_id'],
              source_fingerprint=video.get('fingerprint'), subtitle_key=key)
        store = getattr(self.playback, 'store', None)
        if store and hasattr(store, 'save_subtitle_verdict'):
            await run_in_threadpool(store.save_subtitle_verdict, token, video, key, status, reason)

    async def resolve(self, video_source_id, preferred_id, tracks=None, seen=None):
        """La incertidumbre de una pista conduce a otra candidata, no a una espera."""
        seen = seen if seen is not None else set()
        result = {'verified': False, 'reason': 'no_compatible_subtitles'}
        if tracks is None:
            # Compatibilidad con la sincronización explícita de una pista elegida.
            if preferred_id:
                try:
                    result = await self.sync(video_source_id, preferred_id)
                except ProviderError as error:
                    result = {'verified': False, 'reason': error.code}
                if result.get('verified') or result.get('reason') == 'reference_changed': return result
            tracks = await self.tracks(video_source_id)
        for track in tracks:
            if track.get('sync_token'):
                return self._synced_result(track['sync_token'], track.get('key'), track.get('verification'))
            identity = track.get('key') or track.get('source_id')
            if not track.get('source_id') or track['source_id'] == preferred_id or identity in seen:
                continue
            try:
                result = await self.sync(video_source_id, track['source_id'])
            except ProviderError as error:
                seen.add(identity)
                result = {'verified': False, 'reason': error.code}
                continue
            # Una cancelación del plazo no es una comprobación terminada. El
            # reintento puede aprovechar la inspección que ya alcanzó a terminar.
            seen.add(identity)
            if result.get('verified'):
                return result
            if result.get('reason') == 'reference_changed':
                return result
        return {'changed': False, 'verified': False, 'reason': result.get('reason','no_compatible_subtitles'),
                'message': 'No encontramos subtítulos compatibles con esta versión.'}

    async def sync(self, video_source_id, subtitle_source_id):
        video = await run_in_threadpool(self.playback.source, video_source_id)
        subtitle = await run_in_threadpool(self.playback.source, subtitle_source_id)
        if (
            subtitle.get("provider") != "subtitulos"
            or subtitle["content_id"] != video["content_id"]
            or subtitle["video_id"] != video["video_id"]
        ):
            raise ProviderError("invalid_subtitle", "La pista no pertenece a esta reproducción.", 422)

        stream = subtitle['stream']
        reason = identity_reason(video, {'name': stream.get('name',''), **stream.get('subtitleMetadata', {})})
        if reason:
            audit('subtitle_candidate', status='unconfirmed', reason=reason, video_id=video['video_id'])
            return {'changed':False,'verified':False,'reason':reason,
                    'message':'No se pudo confirmar la identidad de esta pista. Buscaremos alternativas.'}
        vtt = await self._download_vtt(subtitle_source_id)
        fingerprint = video.get("fingerprint") or source_fingerprint(video.get("provider", ""), video["stream"])
        audio = self.media_tools.audio_selections.get(video_source_id) if self.media_tools else None
        # La calibración pertenece a la edición/audio y al contenido del subtítulo,
        # no a IDs temporales que cambian en cada búsqueda. La revisión invalida el cálculo viejo.
        video = dict(video, fingerprint=fingerprint)
        asset, audio_key, selected = await self.asset_context(video_source_id, video)
        if selected != audio:
            return {'changed': False, 'verified': False, 'reason': 'reference_changed'}
        subtitle_hash = hashlib.sha256(vtt.encode()).hexdigest()
        identity = f"sync-v{REVISION}:{video['video_id']}:{asset['key']}:{audio_key}:{subtitle_hash}"
        token = hashlib.sha256(identity.encode()).hexdigest()[:40]
        output_path = self.sync_root / f"{token}.vtt"
        if output_path.exists():
            return self._remember_ready(video_source_id, audio, self._synced_result(token, self._key(stream)))

        reason = None
        if not reason:
            timings = _timings(vtt, 'vtt')
            duration = 0
            folder = (self.media_tools.root if self.media_tools and hasattr(self.media_tools,'root') else ROOT / 'cache' / 'cast') / video_source_id
            try:
                playlist = (folder / 'index.m3u8').read_text()
                if (folder / 'offset').read_text() == '0' and '#EXT-X-ENDLIST' in playlist:
                    duration = sum(map(float, re.findall(r'#EXTINF:([0-9.]+)', playlist)))
            except OSError:
                pass
            if not duration and self.media_tools and hasattr(self.media_tools, 'inspect'):
                info = await self.media_tools.inspect(video_source_id)
                duration = float(info.get('format', {}).get('duration', 0))
            # Una pista corta no cubre un montaje con escenas insertadas. Los créditos
            # y silencios finales normales admiten margen; nunca se estira por duración.
            if timings and duration and not stream.get('embeddedVideo'):
                end = max(end for _, end in timings) / 1000
                if end < duration * .78 or end > max(duration * 1.12, duration + 120):
                    reason = 'subtitle_duration_mismatch'
        store = getattr(self.playback, 'store', None)
        if not reason and store and hasattr(store, 'subtitle_verdict'):
            previous = await run_in_threadpool(store.subtitle_verdict, token)
            if previous and previous['status'] == 'rejected' and previous['reason'] in HARD_REJECTIONS:
                reason = previous['reason']
        if reason:
            if reason in HARD_REJECTIONS:
                await self._verdict(token, video, stream, 'rejected', reason)
            return {'changed': False, 'verified': False, 'reason': reason,
                    'message': 'Esta pista no es compatible o no permite verificar la identidad del episodio.'}

        # Una pista del archivo o una asociación de hash del proveedor no espera
        # detrás de otro VAD. Su procedencia no se confunde con revisión editorial.
        inherited = bool(stream.get('embeddedVideo') or stream.get('subtitleMetadata', {}).get('hash_match'))
        async with (asyncio.Lock() if inherited else self.sync_lock):
            if output_path.exists():
                return self._remember_ready(video_source_id, audio, self._synced_result(token, self._key(stream)))
            try:
                srt = pysubs2.SSAFile.from_string(vtt, format_="vtt").to_string("srt")
            except Exception:
                raise ProviderError("invalid_subtitle", "Este subtítulo no se puede sincronizar.") from None
            # Las copias heredadas pueden prepararse en paralelo: cada petición
            # limpia únicamente sus propios temporales, incluso para el mismo token.
            temporary_key = token + '.' + secrets.token_hex(6)
            input_path = self.sync_root / f"{temporary_key}.original.srt"
            synced_srt = self.sync_root / f"{temporary_key}.synced.srt"
            await run_in_threadpool(input_path.write_text, srt, "utf-8")
            try:
                if inherited:
                    await run_in_threadpool(synced_srt.write_text, srt, 'utf-8')
                    metrics = {'accepted': True}
                else:
                    metrics = await self._run_sync(video_source_id, input_path, synced_srt, audio)
                if self.media_tools and self.media_tools.audio_selections.get(video_source_id) != audio:
                    metrics = {'accepted': False, 'reason': 'reference_changed'}
                if metrics and not metrics.get("accepted"):
                    reason = metrics.get('reason', 'weak_alignment')
                    # Errores de lectura no condenan una pista como contenido incorrecto.
                    if reason in HARD_REJECTIONS:
                        await self._verdict(token, video, stream, 'rejected', reason)
                    return {'changed': False, 'verified': False, 'reason': reason,
                            'message': 'No se pudo verificar esta pista contra el video.'}
                synced_text = await run_in_threadpool(synced_srt.read_text, "utf-8")
                original_times, synced_times = _timings(srt, "srt"), _timings(synced_text, "srt")
                if not original_times or not synced_times:
                    raise ProviderError(
                        "subtitle_sync_failed", "No se pudo sincronizar esta pista; se conservó el original."
                    )
                synced_vtt = pysubs2.SSAFile.from_string(synced_text, format_="srt").to_string("vtt")
                evidence = verification(asset, bool(stream.get('embeddedVideo')),
                    bool(stream.get('subtitleMetadata', {}).get('hash_match')), metrics)
                body_hash = hashlib.sha256(synced_vtt.encode()).hexdigest()
                if store and hasattr(store, 'save_subtitle_binding'):
                    await run_in_threadpool(store.save_subtitle_binding, {
                        'token': token, 'video_id': video['video_id'], 'asset_key': asset['key'],
                        'audio_key': audio_key, 'subtitle_sha256': subtitle_hash, 'subtitle_key': self._key(stream),
                        'revision': REVISION, 'verification': evidence, 'body': synced_vtt, 'body_sha256': body_hash})
                await run_in_threadpool(self._write_copy, token, synced_vtt,
                    dict(metrics or {}, verification_revision=REVISION, verification=evidence, body_sha256=body_hash))
                await self._verdict(token, video, stream, 'verified')
            finally:
                for path in (input_path, synced_srt):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
        return self._remember_ready(video_source_id, audio, self._synced_result(token, self._key(stream)))

    def _synced_result(self, token, key=None, evidence=None):
        if evidence is None:
            try: evidence = json.loads((self.sync_root / (token + '.json')).read_text('utf-8')).get('verification')
            except (OSError, ValueError): evidence = None
        return {
            "changed": True,
            "verified": True,
            "message": "Subtítulos disponibles.",
            "item": {
                "sync_token": token,
                "key": key,
                "verified": True,
                "validation_required": True,
                "url": (
                    f"http://{self.playback.settings.lan_ip}:"
                    f"{self.playback.settings.media_port}/subtitles/synced/{token}.vtt"
                ),
                "mime": "text/vtt",
                "language": "es",
                "label": "Español · misma edición" if evidence and evidence.get('timing', {}).get('method') == 'provider_hash'
                    else "Español · pista del archivo" if evidence and evidence.get('timing', {}).get('status') == 'inherited'
                    else "Español · sincronía comprobada",
                "automatic": True,
                "verification": evidence,
            },
        }

    async def deliver_synced(self, token, offset_ms=0):
        if not re.fullmatch(r"[a-f0-9]{40}", token):
            raise ProviderError("invalid_subtitle", "La pista de subtítulos no es válida.", 422)
        path = self.sync_root / f"{token}.vtt"
        store = getattr(self.playback, 'store', None)
        if not path.exists() and store and hasattr(store, 'subtitle_binding_token'):
            binding = await run_in_threadpool(store.subtitle_binding_token, token)
            if (binding and binding['verification'].get('revision') == REVISION and
                    hashlib.sha256(binding['body'].encode()).hexdigest() == binding['body_sha256']):
                await run_in_threadpool(self._write_copy, token, binding['body'],
                    {'verification_revision': REVISION, 'verification': binding['verification'],
                     'body_sha256': binding['body_sha256']})
        if not path.exists() or path.stat().st_size > MAX_SUBTITLE_BYTES:
            raise ProviderError("source_expired", "El subtítulo sincronizado ya no está disponible.", 410)
        try:
            metrics = json.loads(await run_in_threadpool(path.with_suffix('.json').read_text, 'utf-8'))
            if metrics.get('verification_revision') != REVISION:
                raise ValueError('old_validation')
        except (OSError, ValueError):
            raise ProviderError('subtitle_revalidation_required', 'Esta pista necesita verificarse de nuevo.', 410) from None
        body = await run_in_threadpool(path.read_text, "utf-8")
        if metrics.get('body_sha256') and hashlib.sha256(body.encode()).hexdigest() != metrics['body_sha256']:
            raise ProviderError('subtitle_revalidation_required', 'Esta copia necesita verificarse de nuevo.', 410)
        if offset_ms:
            body = prune(shift(body, offset_ms))
        return Response(body, media_type="text/vtt", headers={"Access-Control-Allow-Origin": "*"})

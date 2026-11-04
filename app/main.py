"""API local: catálogo, dispositivos, historial y reproducción con proveedores Stremio."""
import mimetypes
import secrets
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, Request, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, model_validator
from starlette.concurrency import run_in_threadpool

from app.observability import audit
from app.lan import lan_client
from app.providers.stremio import ADAPTERS, PROVIDER_ORDER, PROVIDERS, ProviderError, gather_streams
from app.settings import ROOT, Settings
from app.storage import Store, HARD_SOURCE_ERRORS
from app.catalog import Catalog
from app.library import Library
from app.devices import Devices
from app.admin_session import AdminSession
from app.assistant import Assistant, collect_results, resolve_episode
from app.playback_events import PlaybackEvents, failure_code
from app.source_validation import declared_archive

# Instancias públicas sin configuración personal: solo sirven para comprobar la conexión.
PUBLIC_MANIFEST = {"mediafusion": "https://mediafusion.elfhosted.com/manifest.json",
                   "penguplay": "https://pengu.uk/manifest.json",
                   "hdhub": "https://hdhub.thevolecitor.qzz.io/manifest.json",
                   "flixstreams": "https://flixnest.app/flix-streams/manifest.json"}
ProviderName = Literal["mediafusion", "penguplay", "hdhub", "flixstreams"]
key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class ProviderConfig(BaseModel):
    manifest_url: SecretStr = Field(max_length=16000)


class Probe(BaseModel):
    media_type: Literal["movie", "series"]
    video_id: str = Field(min_length=7, max_length=35)


class FrontEvent(BaseModel):
    event: Literal["login", "logout", "configuration_opened", "connection_checked", "stream_check_requested",
                   "playback_seek_completed", "playback_seek_failed"]
    operation_id: int | None = Field(default=None,ge=0)
    target_ms: int | None = Field(default=None,ge=0,le=86_400_000)
    # Estado numérico del reproductor al cerrar un salto; sin URLs ni texto libre.
    position_ms: int | None = Field(default=None,ge=0,le=86_400_000)
    elapsed_ms: int | None = Field(default=None,ge=0,le=86_400_000)
    ready_state: int | None = Field(default=None,ge=0,le=4)
    paused: bool | None = None
    seeking: bool | None = None
    waiting: bool | None = None
    reason: Literal['clock','available','timeout','request'] | None = None


class PairRequest(BaseModel):
    code: SecretStr = Field(max_length=100)


class FavoriteRequest(BaseModel):
    enabled: bool


class KaraokePreferenceRequest(BaseModel):
    status: Literal["favorite", "bad", "neutral"]


class KaraokePlayRequest(BaseModel):
    version_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{20}$")


class BeginPlayback(BaseModel):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{20,80}$")
    prepare: bool = True
    # Punto del video que debe quedar disponible: retomar donde se dejó o adelantar.
    position_ms: int = Field(default=0, ge=0, le=86_400_000)
    # La TV lo activa cuando ignoró currentTime: el equipo debe entregar una lista
    # que empiece físicamente en ese punto, aunque el archivo sea MP4 o HLS directo.
    force_adapted: bool = False
    # Índice real informado por FFprobe. Al elegirlo se adapta también una fuente que
    # normalmente sería directa, porque Chromecast no expone de forma fiable sus pistas.
    audio_stream_index: int | None = Field(default=None, ge=0, le=255)
    operation_id: int | None = Field(default=None,ge=0)
    # IDs de telemetría no secretos. Opcionales para clientes anteriores.
    attempt_id: str | None = Field(default=None,pattern=r'^[A-Za-z0-9_-]{16,80}$')
    playback_id: str | None = Field(default=None,pattern=r'^[A-Za-z0-9_-]{16,80}$')
    client: Literal['tv','android','cast','legacy'] = 'legacy'


class StopPlayback(BaseModel):
    source_id: str | None = Field(default=None,pattern=r"^[A-Za-z0-9_-]{20,80}$")


class PlaybackPreferences(BaseModel):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{20,80}$")
    audio_stream_index: int | None = Field(default=None, ge=0, le=255)
    subtitle_key: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    subtitle_sync_token: str | None = Field(default=None, pattern=r"^[a-f0-9]{40}$")
    subtitle_offset_ms: int = Field(default=0, ge=-43_200_000, le=43_200_000)
    subtitle_disabled: bool = False
    subtitle_manual: bool = False
    subtitle_verified: bool = False
    subtitle_style: Literal['white-outline','yellow-outline','black-background','clean','floating'] = 'white-outline'


class ProgressRequest(BaseModel):
    session_id: str = Field(max_length=100)
    sequence: int = Field(ge=0)
    position_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    completed: bool = False
    # Tiempo que el video avanzó realmente en esta sesión; saltar no lo incrementa.
    watched_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    # Opcional para conservar compatibilidad con Android y clientes anteriores.
    preferences: PlaybackPreferences | None = None


class TuneRequest(BaseModel):
    channel_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")


class MetricRequest(BaseModel):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{20,80}$")
    event: Literal["first_frame", "buffering", "error"]
    start_ms: int | None = Field(default=None, ge=0, le=600000)
    bitrate_kbps: int | None = Field(default=None, ge=0, le=200000)


class PlaybackEventRequest(BaseModel):
    attempt_id: str = Field(pattern=r'^[A-Za-z0-9_-]{16,80}$')
    event_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    event: Literal['first_frame','buffering','buffering_end','stable','error','source_switch',
                   'recovery_started','recovered','cancelled','ended','subtitle_render']
    stage: Literal['start','prepare','cast','player','seek'] = 'player'
    error_code: str | None = Field(default=None,pattern=r'^[a-z][a-z0-9_]{0,63}$')
    position_ms: int | None = Field(default=None,ge=0,le=86_400_000)
    elapsed_ms: int | None = Field(default=None,ge=0,le=86_400_000)
    duration_ms: int | None = Field(default=None,ge=0,le=86_400_000)
    buffer_ms: int | None = Field(default=None,ge=0,le=86_400_000)
    switch_reason: Literal['automatic','manual'] | None = None
    next_fingerprint: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    subtitle_key: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')
    subtitle_token: str | None = Field(default=None,pattern=r'^[a-f0-9]{40}$')
    render_context: Literal['start','resume','seek','audio','continuous'] | None = None
    render_samples: int | None = Field(default=None,ge=1,le=10000)
    render_lag_ms: int | None = Field(default=None,ge=0,le=60000)
    render_total_lag_ms: int | None = Field(default=None,ge=0,le=600000000)

    @model_validator(mode='after')
    def subtitle_measurement(self):
        if self.event == 'subtitle_render':
            if any(value is None for value in (self.render_context,self.render_samples,self.render_lag_ms,self.render_total_lag_ms)):
                raise ValueError('La medición de subtítulos está incompleta.')
            if self.render_total_lag_ms > self.render_lag_ms * self.render_samples:
                raise ValueError('El total de demora no coincide con las muestras.')
        return self


class SubtitleSyncRequest(BaseModel):
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{20,80}$")
    subtitle_source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{20,80}$")
    recover: bool = False


class AssistantRequest(BaseModel):
    # Mismo límite que el campo de TV: admite el doble de texto dictado sin recortarlo.
    prompt: str = Field(min_length=2, max_length=800)


def create_app(settings: Settings | None = None, store=None, transport=None, dns_check=None):
    settings = settings or Settings()
    if len(settings.api_key.get_secret_value()) < 32:
        raise RuntimeError("La API-Key local debe tener al menos 32 caracteres.")
    store = store or Store(settings)

    @asynccontextmanager
    async def lifespan(app):
        from app.torrents import TorrentEngine
        from app.playback import Playback
        from app.media_tools import MediaTools
        from app.subtitles import Subtitles
        from app.playback_control import PlaybackControl
        # Un solo cliente sirve al catálogo, a los proveedores y a los subtítulos. Con
        # cuatro conexiones se quedaba corto: bastaba que la pantalla de inicio estuviera
        # cargando para que una consulta de fuentes esperase turno hasta agotar su tiempo
        # y se anunciara como «el proveedor tardó demasiado», que no era verdad. El plazo
        # de la cola va aparte y es corto, para no confundir esperar con no contestar.
        async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5, pool=5), transport=transport, trust_env=False,
                                     limits=httpx.Limits(max_connections=16, max_keepalive_connections=8)) as client:
            app.state.http = client
            app.state.torrents = TorrentEngine(settings)
            app.state.playback = Playback(store,settings,app.state.torrents)
            app.state.media_tools = MediaTools(settings,app.state.playback.source)
            app.state.control=PlaybackControl(app.state.torrents,app.state.media_tools)
            app.state.playback.control=app.state.control
            reaper=asyncio.create_task(app.state.control.reap())
            app.state.subtitles = Subtitles(client,app.state.playback,app.state.media_tools)
            # Referencia viva de lo que se adelanta: sin ella el recolector puede
            # llevarse la tarea a medias y dejar la preparación sin terminar.
            app.state.warming = set()
            try: yield
            finally:
                reaper.cancel();await asyncio.gather(reaper,return_exceptions=True)
                for task in app.state.warming: task.cancel()
                await asyncio.gather(*app.state.warming,return_exceptions=True)
                await app.state.subtitles.close()
                await app.state.control.close()
                await app.state.media_tools.close()
                await app.state.torrents.close()

    app = FastAPI(title="SergioFlix · proveedores Stremio", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.playback_events=PlaybackEvents(store)

    async def note_playback(state,event,stage='prepare',event_id=None,**fields):
        """El diario es secundario: un fallo de telemetría nunca interrumpe el video."""
        if not state or not state.get('attempt_id'):return
        try:
            return await run_in_threadpool(app.state.playback_events.record,state['actor'],state['attempt_id'],
                event_id or 'server-'+event,event,stage,**fields)
        except Exception:audit('playback_journal_failed',error_code='journal_unavailable')

    async def note_failure(state,error,stage):
        if not state or state.get('_journal_error_recorded'):return
        state['_journal_error_recorded']=True
        code=failure_code(error)
        # Una contradicción comprobada del archivo prevalece sobre una caída anterior.
        cause={} if code in HARD_SOURCE_ERRORS else state.get('upstream_failure',{})
        state['_error_events']=state.get('_error_events',0)+1
        await note_playback(state,'error',stage,event_id='server-error-'+str(state['_error_events']),
            error_code=cause.get('code') or code,
            upstream_status=cause.get('status') or getattr(error,'upstream_status',None))

    def adapter(manifest, provider="mediafusion"):
        extra = {"dns_check": dns_check} if dns_check else {}
        return ADAPTERS[provider](app.state.http, manifest, settings.provider_hosts(provider), **extra)

    async def authorized(request: Request, key: str | None = Depends(key_header)):
        if not key:
            raise ProviderError("unauthorized", "Introduce la clave de acceso de la videoteca.", 401)
        actor="admin-local" if secrets.compare_digest(key,settings.api_key.get_secret_value()) else await run_in_threadpool(Devices(store).identify,key)
        if not actor and await run_in_threadpool(AdminSession(store).valid,key): actor='admin-local'
        if not actor: raise ProviderError("unauthorized","El dispositivo no está conectado a la videoteca.",401)
        request.state.actor=actor
        return actor

    async def learn_source_failure(source,error):
        """Una reproducción rota cuenta una vez, aunque el televisor repita el fragmento."""
        code=failure_code(error)
        if code not in HARD_SOURCE_ERRORS:
            code=source.get('upstream_failure',{}).get('code') or code
        if source.get('_failure_recorded') and not (code in HARD_SOURCE_ERRORS and
            source.get('_failure_code') not in HARD_SOURCE_ERRORS):return
        # Marcar antes de escribir cierra la carrera entre varias peticiones del mismo
        # fragmento. Si no era un error que aprende, se libera la marca.
        source['_failure_recorded']=True
        source['_failure_code']=code
        try:
            if source.get('attempt_id'):
                learned=await run_in_threadpool(app.state.playback_events.learn_failure,
                    source['actor'],source['attempt_id'],code)
            else:learned=await run_in_threadpool(store.record_source_failure,source,code)
        except Exception:
            # La memoria secundaria no puede impedir la recuperación del reproductor.
            audit('playback_journal_failed',error_code='reputation_unavailable')
            learned=False
        if not learned:source.pop('_failure_recorded',None)

    async def admin(actor=Depends(authorized)):
        if actor!='admin-local': raise ProviderError('admin_required','Esta operación se realiza desde el panel de administración.',403)
        return actor

    @app.middleware("http")
    async def request_audit(request, call_next):
        request_id, started = secrets.token_hex(8), time.monotonic()
        try:
            # El televisor no puede confiar en el certificado propio del PC, así que la API
            # también responde por HTTP dentro de la red local. Sigue exigiendo la clave.
            local=request.client and lan_client(request.client.host)
            if request.url.path.startswith('/api/') and request.url.scheme!='https' and not local:
                response=JSONResponse({'code':'https_required','message':'Conecta con la dirección HTTPS de la videoteca.'},status_code=403)
            else:
                response = await call_next(request)
        except Exception:
            # Nada de traceback ni cadenas de conexión en la respuesta o el log.
            audit("internal_error", getattr(request.state, "actor", "anonymous"), request_id=request_id, error_code="internal_error")
            response = JSONResponse({"code": "internal_error", "message": "No se pudo completar la operación. Revisa el servicio local."}, status_code=503)
        route = request.scope.get("route")
        operation = getattr(route, "name", "static_or_unknown")
        audit("request", getattr(request.state, "actor", "anonymous"), request_id=request_id,
              operation=operation, status=response.status_code, elapsed_ms=round((time.monotonic()-started)*1000))
        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: https:; media-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(ProviderError)
    async def provider_error(request, error):
        audit("operation_failed", getattr(request.state, "actor", "anonymous"), error_code=error.code)
        return JSONResponse({"code": error.code, "message": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Pydantic puede incluir el dato introducido; un manifiesto puede contener secretos.
        return JSONResponse({"code": "invalid_input", "message": "Revisa los datos introducidos."}, status_code=422)

    @app.get("/", name="panel")
    def panel():
        return FileResponse(ROOT / "frontend" / "index.html")

    @app.get('/connect',name='connect_phone')
    def connect_phone():return FileResponse(ROOT/'frontend'/'connect.html')

    @app.get("/health", name="health")
    async def health():
        await run_in_threadpool(store.health)
        return {"status": "ok", "module": "stremio-providers"}

    @app.post('/api/admin/session',name='admin_login')
    async def admin_login(data:PairRequest,request:Request):
        if not request.client or request.client.host not in ('127.0.0.1','::1') or request.url.hostname not in ('127.0.0.1','localhost','::1'):
            raise ProviderError('local_only','Abre el panel en la videoteca.',403)
        return await run_in_threadpool(AdminSession(store).exchange,data.code.get_secret_value())

    @app.get("/api/providers", name="provider_list")
    async def provider_list(actor=Depends(authorized)):
        # El resumen guardado describe el manifiesto (versión y catálogos), nunca su URL privada.
        saved = {row["provider"]: row["summary"] for row in await run_in_threadpool(store.configured_providers)}
        health = await run_in_threadpool(store.provider_health)
        return {"order": list(PROVIDER_ORDER), "items": [
            {"provider": key, "name": PROVIDERS[key].display, "configure_url": PROVIDERS[key].configure_url,
             "priority": PROVIDERS[key].priority, "configured": key in saved,
             "summary": saved.get(key), "health": health.get(key, 50)}
            for key in PROVIDER_ORDER]}

    @app.get("/api/providers/{provider}", name="provider_status")
    async def provider_status(provider: ProviderName, actor=Depends(authorized)):
        saved = await run_in_threadpool(store.provider, provider)
        return {"provider": provider, "name": PROVIDERS[provider].display,
                "configured": saved is not None, "summary": saved["summary"] if saved else None,
                "playback_implemented": True}

    @app.put("/api/providers/{provider}", name="provider_configure")
    async def provider_configure(provider: ProviderName, config: ProviderConfig, actor=Depends(admin)):
        client = adapter(config.manifest_url.get_secret_value(), provider)
        summary = await client.manifest()
        await run_in_threadpool(store.save_provider, client.manifest_url, summary, actor, provider)
        return {"provider": provider, "configured": True, "summary": summary}

    @app.delete("/api/providers/{provider}", name="provider_forget")
    async def provider_forget(provider: ProviderName, actor=Depends(admin)):
        removed = await run_in_threadpool(store.forget_provider, provider, actor)
        return {"provider": provider, "configured": False, "removed": removed}

    @app.post("/api/providers/{provider}/check", name="provider_check")
    async def provider_check(provider: ProviderName, actor=Depends(authorized)):
        saved = await run_in_threadpool(store.provider, provider)
        summary = await adapter(saved["manifest_url"] if saved else PUBLIC_MANIFEST[provider], provider).manifest()
        await run_in_threadpool(store.record, actor, "provider.check")
        return {"provider": provider, "configured": saved is not None, "summary": summary}

    @app.post("/api/providers/{provider}/streams/check", name="streams_check")
    async def streams_check(provider: ProviderName, probe: Probe, actor=Depends(authorized)):
        saved = await run_in_threadpool(store.provider, provider)
        client = adapter(saved["manifest_url"] if saved else PUBLIC_MANIFEST[provider], provider)
        result = await client.streams(probe.media_type, probe.video_id)
        await run_in_threadpool(store.record, actor, "streams.check")
        return result

    @app.post("/api/events", name="frontend_event", status_code=204)
    async def frontend_event(event: FrontEvent, actor=Depends(authorized)):
        await run_in_threadpool(store.record, actor, "frontend." + event.event)
        if event.event.startswith('playback_seek_'):
            audit(event.event,actor,**event.model_dump(exclude={'event'},exclude_none=True))

    @app.post('/api/devices/pairing/tv',name='pairing_tv')
    async def pairing_tv(actor=Depends(admin)):
        """Código corto para escribir con el mando del televisor. Un solo uso y cinco minutos."""
        code=await run_in_threadpool(Devices(store).short_pairing)
        return {'code':code,'address':f'http://{settings.lan_ip}:{settings.media_port}/tv/','minutes':5}

    @app.post('/api/devices/pairing',name='pairing_create')
    async def pairing_create(actor=Depends(admin)):
        from urllib.parse import urlencode
        import segno
        code=await run_in_threadpool(Devices(store).pairing)
        link='videoteca://pair?'+urlencode({'server':f'https://{settings.lan_ip}:{settings.https_port}','code':code})
        landing=f'http://{settings.lan_ip}:{settings.media_port}/connect#'+urlencode({'server':f'https://{settings.lan_ip}:{settings.https_port}','code':code})
        return {'link':link,'qr':segno.make(landing).svg_data_uri(scale=5,border=3)}

    @app.get('/download/videoteca.apk',name='apk_download')
    async def apk_download():
        path=ROOT/'dist'/'Videoteca.apk'
        if not path.is_file(): raise ProviderError('not_ready','El instalador todavía se está preparando.',404)
        return FileResponse(path,media_type='application/vnd.android.package-archive',filename='SergioFlix.apk')

    @app.post('/api/devices/pair',name='pairing_exchange')
    async def pairing_exchange(data:PairRequest):
        return await run_in_threadpool(Devices(store).exchange,data.code.get_secret_value())

    @app.get('/api/catalog/{media_type}',name='catalog_page')
    async def catalog_page(media_type:Literal['movie','series'],skip:int=Query(0,ge=0,le=50000),genre:str|None=Query(None,max_length=40),search:str|None=Query(None,max_length=120),daily:bool=False,actor=Depends(authorized)):
        return await Catalog(store,app.state.http).page(media_type,skip,genre,search,daily=daily)

    @app.post('/api/assistant/search', name='assistant_search')
    async def assistant_search(data: AssistantRequest, actor=Depends(authorized)):
        """Luna interpreta texto; la videoteca conserva el control de la búsqueda."""
        configured = settings.openai_api_key
        if not configured or not settings.openai_model.strip():
            raise ProviderError(
                'assistant_not_configured', 'El asistente todavía no está configurado.', 503
            )
        prompt = data.prompt.strip()
        if len(prompt) < 2:
            raise ProviderError('invalid_input', 'Dime qué quieres buscar.', 422)
        plan = await Assistant(
            app.state.http, configured.get_secret_value(), settings.openai_model
        ).interpret(prompt)
        catalog = Catalog(store, app.state.http)
        items = await collect_results(catalog, plan)
        items, episode = await resolve_episode(catalog, items, plan)
        await run_in_threadpool(store.record, actor, 'assistant.search')
        return {
            'message': str(plan.get('message', ''))[:280],
            'items': items,
            'start_seconds': plan.get('start_seconds'),
            'approximate': bool(plan.get('approximate')),
            'episode': episode,
        }

    @app.get('/api/tv/channels',name='tv_channels')
    async def tv_channels(section:Literal['nacional','cable','deportes']=Query('nacional'),actor=Depends(authorized)):
        from app.television import Television
        return {'section':section,'items':await Television(store,app.state.http).channels(section)}

    @app.post('/api/tv/tune',name='tv_tune')
    async def tv_tune(data:TuneRequest,actor=Depends(authorized)):
        """Devuelve una fuente igual que una película: el reproductor no cambia."""
        from app.television import Television
        result=await Television(store,app.state.http).source(data.channel_id)
        await run_in_threadpool(store.record,actor,'tv.tune')
        return result

    @app.get('/api/karaoke', name='karaoke_catalog')
    async def karaoke_catalog(actor=Depends(authorized)):
        from app.karaoke import Karaoke
        return await run_in_threadpool(Karaoke(store, settings).catalog)

    @app.get('/api/karaoke/artists/{artist_id}', name='karaoke_artist')
    async def karaoke_artist(artist_id: str, actor=Depends(authorized)):
        from app.karaoke import Karaoke
        return await run_in_threadpool(Karaoke(store, settings).artist, artist_id)

    @app.put('/api/karaoke/songs/{song_id}/versions/{video_id}', name='karaoke_preference')
    async def karaoke_preference(song_id: str, video_id: str, data: KaraokePreferenceRequest,
                                 actor=Depends(authorized)):
        from app.karaoke import Karaoke
        return await run_in_threadpool(
            Karaoke(store, settings).preference, song_id, video_id, data.status, actor
        )

    @app.post('/api/karaoke/songs/{song_id}/play', name='karaoke_play')
    async def karaoke_play(song_id: str, data: KaraokePlayRequest, actor=Depends(authorized)):
        """Emite un enlace breve: el televisor nunca recibe la ruta privada del disco."""
        from app.karaoke import Karaoke
        result = await run_in_threadpool(Karaoke(store, settings).play, song_id, data.version_id)
        await run_in_threadpool(store.record, actor, 'karaoke.play')
        return result

    @app.api_route('/karaoke/media/{song_id}', methods=['GET', 'HEAD'], name='karaoke_media')
    async def karaoke_media(song_id: str, request: Request,
                            version_id: str = Query(pattern=r'^[a-f0-9]{20}$'),
                            expires: int = Query(ge=0),
                            signature: str = Query(pattern=r'^[a-f0-9]{64}$')):
        """Archivo local con Range; solo una URL firmada dentro de la casa puede leerlo."""
        if not request.client or not lan_client(request.client.host):
            raise ProviderError('local_only', 'Este video solo está disponible en la red de casa.', 403)
        from app.karaoke import Karaoke
        path = await run_in_threadpool(
            Karaoke(store, settings).media, song_id, version_id, expires, signature
        )
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or 'video/mp4')

    @app.get('/karaoke/artists/{artist_id}/{kind}', name='karaoke_artist_art')
    async def karaoke_artist_art(artist_id: str, kind: Literal['poster', 'background'], request: Request):
        if not request.client or not lan_client(request.client.host):
            raise ProviderError('local_only', 'Esta imagen solo está disponible en la red de casa.', 403)
        from app.karaoke import Karaoke
        karaoke = Karaoke(store, settings)
        path, title, art_kind = await run_in_threadpool(karaoke.artist_art, artist_id, kind)
        if path:
            return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or 'image/jpeg')
        subtitle = 'Artista local' if art_kind == 'poster' else 'Canciones para cantar'
        return Response(karaoke.placeholder(title, subtitle, art_kind == 'background'),
                        media_type='image/svg+xml')

    @app.get('/karaoke/songs/{song_id}/thumbnail', name='karaoke_song_art')
    async def karaoke_song_art(song_id: str, request: Request):
        if not request.client or not lan_client(request.client.host):
            raise ProviderError('local_only', 'Esta imagen solo está disponible en la red de casa.', 403)
        from app.karaoke import Karaoke
        karaoke = Karaoke(store, settings)
        path, title, artist = await run_in_threadpool(karaoke.song_art, song_id)
        if path:
            return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or 'image/jpeg')
        return Response(karaoke.placeholder(title, artist, True), media_type='image/svg+xml')

    @app.get('/api/collections',name='collections')
    async def collections(actor=Depends(authorized)):
        from app.collections import shelves
        return {'items':await run_in_threadpool(shelves,store)}

    @app.get('/api/content/{media_type}/{content_id}',name='content_detail')
    async def content_detail(media_type:Literal['movie','series'],content_id:str,actor=Depends(authorized)):
        import re
        if not re.fullmatch(r'tt\d{5,12}',content_id): raise ProviderError('invalid_id','Identificador no válido.',422)
        detail=await Catalog(store,app.state.http).detail(media_type,content_id)
        playback=await run_in_threadpool(Library(store).detail_progress,content_id)
        return {**detail,'playback':playback}

    @app.get('/api/library/favorites',name='favorites')
    async def favorites(actor=Depends(authorized)):
        return {'items':await run_in_threadpool(Library(store).favorites)}

    @app.put('/api/library/favorites/{content_id}',name='favorite_set')
    async def favorite_set(content_id:str,data:FavoriteRequest,actor=Depends(authorized)):
        result=await run_in_threadpool(Library(store).favorite,content_id,data.enabled)
        await run_in_threadpool(store.record,actor,'favorite.update')
        return result

    @app.get('/api/library/progress',name='library_progress')
    async def library_progress(actor=Depends(authorized)):
        """Lo empezado y lo visto, para marcarlo sobre las carátulas del catálogo."""
        return {'items':await run_in_threadpool(Library(store).progress_map)}

    @app.get('/api/library/continue',name='continue_watching')
    async def continue_watching(actor=Depends(authorized)):
        return {'items':await run_in_threadpool(Library(store).continuing)}

    async def prepare_ahead(actor,source_id):
        """Prepara en segundo plano; aprende contradicciones comprobadas del archivo.
        Si no cuaja, la reproducción normal conserva su preparación y respaldos."""
        source=None
        try:
            source=await run_in_threadpool(app.state.playback.source,source_id)
            await app.state.control.activate(actor,source_id,source,warm=True)
            # Dentro de `operation` esto es cancelable: si el usuario pulsa Reproducir
            # otra cosa, el adelanto se detiene en vez de dejar una conversión suelta.
            async with app.state.control.operation(source_id):
                # Se adelanta desde donde se va a retomar, no desde el principio: convertir
                # el inicio de una película que se sigue por la mitad no ahorra nada.
                start=await run_in_threadpool(Library(store).position,source['video_id'])
                await app.state.media_tools.cast_path(source_id,start,source.get('expected_duration_ms',0))
                await app.state.subtitles.prepared(source_id,0.1)
        except Exception as error:
            if source:await learn_source_failure(source,error)
            audit('warm_skipped',actor,error_code='warm_skipped')

    @app.get('/api/playback/sources/{media_type}/{video_id}',name='playback_sources')
    async def playback_sources(media_type:Literal['movie','series'],video_id:str,
                               warm:bool=Query(False),actor=Depends(authorized)):
        content_id=video_id.split(':')[0]
        detail=await Catalog(store,app.state.http).detail(media_type,content_id)
        configured=await run_in_threadpool(store.configured_providers)
        if not configured: raise ProviderError('provider_not_configured','Configura PenguPlay, Flix-Streams o MediaFusion desde el panel.',409)
        health=await run_in_threadpool(store.provider_health)
        groups,failures=await gather_streams(configured,media_type,video_id,adapter,health)
        for failure in failures:
            audit('provider_unavailable',actor,error_code=failure['code'])
            await run_in_threadpool(store.record_metric,failure['provider'],'error')
        if not groups:
            raise ProviderError('providers_unavailable','Ningún proveedor entregó fuentes. Revisa la configuración o reintenta.',502)
        # Algunos proveedores mezclan búsquedas por nombre pese a recibir el IMDb
        # correcto. La identidad se comprueba antes de sondear y ordenar por calidad.
        from app.source_identity import movie_source_mismatch
        identity_discarded=0
        for group in groups:
            accepted=[];reasons={}
            for stream in group['streams']:
                reason='archive_source' if declared_archive(stream) else movie_source_mismatch(stream,detail,media_type)
                if reason:reasons[reason]=reasons.get(reason,0)+1
                else:accepted.append(stream)
            group['streams']=accepted
            for reason,count in reasons.items():
                identity_discarded+=count
                audit('source_filtered',actor,error_code=reason,provider=group['provider'],count=count)
        # Antes de elegir, averiguar a qué CDN lleva cada candidato: los enlaces de los
        # proveedores son de un solo uso, así que solo el destino final predice la velocidad.
        from app.probing import resolve_speeds
        from app.stream_ranking import original_audio_languages,rank_catalog,runtime_seconds
        known=await run_in_threadpool(store.host_speeds) if settings.probe_sources else {}
        provider_speeds=await run_in_threadpool(store.provider_speeds) if settings.probe_sources else {}
        # La duración convierte el tamaño en bitrate, que es lo que se compara con el caudal.
        duration=runtime_seconds(detail)
        languages=original_audio_languages(detail)
        preliminary=rank_catalog(groups,None,duration,languages,provider_speeds=provider_speeds)
        rejected={}
        for _,analysis in preliminary:
            if analysis['suspected_ad']:
                provider=analysis['provider'];rejected[provider]=rejected.get(provider,0)+1
        for provider,count in rejected.items():
            audit('source_filtered',actor,error_code='suspected_ad',provider=provider,count=count)
        urls=[stream.get('url') for stream,analysis in preliminary
              if stream.get('url') and not analysis['suspected_ad']]
        speeds=await resolve_speeds(app.state.http,urls,known)
        await app.state.subtitles.prefer_editions(groups,video_id,detail)
        items=await run_in_threadpool(app.state.playback.save_catalog,content_id,video_id,groups,
                                     speeds,duration,languages,provider_speeds)
        preferences=await run_in_threadpool(Library(store).preferences,video_id)
        found=identity_discarded+sum(len(group['streams']) for group in groups)
        # Adelantar la preparación mientras el usuario todavía mira la ficha: cuando
        # pulse Reproducir, el PC ya lleva hechos los primeros segundos.
        if warm and items:
            preferred=next((item for item in items if item.get('fingerprint')==preferences.get('source_fingerprint')
                and not item.get('reputation') and not item.get('subtitle_edition_unavailable')),items[0])
            task=asyncio.create_task(prepare_ahead(actor,preferred['source_id']))
            app.state.warming.add(task);task.add_done_callback(app.state.warming.discard)
        # Lo descartado es lo que este PC no puede abrir: sin pares, demasiado grande o no compatible.
        return {'items':items,'preferences':preferences,'providers':[group['provider'] for group in groups],'failures':failures,
                'found':found,'discarded':max(0,found-len(items))}

    @app.post('/api/playback/start',name='playback_start')
    async def playback_start(data:BeginPlayback,actor=Depends(authorized)):
        source=await run_in_threadpool(app.state.playback.source,data.source_id)
        if not data.prepare:
            raise ProviderError('update_required','Actualiza SergioFlix: el siguiente episodio ahora se prepara al terminar el actual.',409)
        provider,started=source.get('provider','mediafusion'),time.monotonic()
        attempt_id=data.attempt_id or secrets.token_hex(16)
        # Reintentar la misma solicitud conserva el ID. El cliente usa uno nuevo al
        # iniciar otra fuente, pero los saltos/audio permanecen en el mismo intento.
        playback_id=data.playback_id or attempt_id
        try:
            await run_in_threadpool(app.state.playback_events.begin,actor,source,attempt_id,playback_id,data.client)
        except ProviderError:raise
        except Exception:audit('playback_journal_failed',actor,error_code='journal_unavailable')
        await app.state.control.activate(actor,data.source_id,source)
        state=app.state.control.require(data.source_id)
        if state.get('attempt_id')!=attempt_id:
            state.pop('_failure_recorded',None);state.pop('_journal_error_recorded',None)
            state.pop('upstream_failure',None)
        state.update(attempt_id=attempt_id,playback_id=playback_id)
        try:
            async with app.state.control.operation(data.source_id) as state:
                async with asyncio.timeout(75):
                    # Los subtítulos se buscan mientras la fuente arranca. Pedirlos después
                    # sumaba su espera a la del video y, con el proveedor lento, dejaba la
                    # película entera sin texto porque cinco segundos no daban para nada.
                    app.state.subtitles.start_prepare(data.source_id)
                    ready=await app.state.playback.ready(data.source_id)
                    result=await run_in_threadpool(app.state.playback.begin,data.source_id,actor)
                    # El trabajo compartido continúa si vence el presupuesto del arranque.
                    result.update(await app.state.subtitles.prepared(data.source_id,0.1))
                    state['stage']='ready'
        except asyncio.CancelledError:
            await note_playback(state,'cancelled','prepare')
            raise ProviderError('playback_stopped','Preparación cancelada.',409) from None
        except Exception as error:
            await note_failure(state,error,'prepare')
            await app.state.control.stop(actor,data.source_id)
            await run_in_threadpool(store.record_metric,provider,'error')
            await learn_source_failure(state,error)
            if isinstance(error,TimeoutError):raise ProviderError('preparation_timeout','Esta fuente no inició a tiempo. Puedes probar otra edición.',504) from None
            raise
        await run_in_threadpool(store.record,actor,'playback.start')
        await run_in_threadpool(store.record_metric,provider,'prepared',round((time.monotonic()-started)*1000))
        await note_playback(state,'prepared',elapsed_ms=round((time.monotonic()-started)*1000))
        return {**result,**ready,'attempt_id':attempt_id,'playback_id':state['playback_id']}

    @app.post('/api/playback/stop',name='playback_stop')
    async def playback_stop(data:StopPlayback,actor=Depends(authorized)):
        stopped=await app.state.control.stop(actor,data.source_id)
        await run_in_threadpool(store.record,actor,'playback.stop')
        return {'stopped':stopped,'active':actor in app.state.control.actors}

    @app.get('/api/playback/status',name='playback_status')
    async def playback_status(actor=Depends(authorized)):
        return app.state.control.status(actor)

    @app.get('/api/playback/diagnostics',name='playback_diagnostics')
    async def playback_diagnostics(actor=Depends(admin)):
        engine,media=app.state.torrents,app.state.media_tools
        rows=[]
        for source_id,state in app.state.control.sources.items():
            handle=engine.handles.get(state['hash'])
            status=handle.status() if handle and handle.is_valid() else None
            job=media.jobs.get(source_id,{})
            rows.append({'content_id':state['content_id'],'video_id':state['video_id'],'stage':state['stage'],
                'provider':state.get('provider','mediafusion'),
                'readers':engine.active.get(state['hash'],0),'peers':status.num_peers if status else 0,
                'download_bytes_per_second':status.download_rate if status else 0,
                'converted_seconds':job.get('seconds',0),'conversion_error':job.get('error')})
        return {'active':rows,'torrents':len(engine.handles),'readers':sum(engine.active.values()),
            'conversions':sum(job['process'].returncode is None for job in media.jobs.values()),'probes':len(media.probes)}

    @app.post('/api/playback/metric',name='playback_metric',status_code=204)
    async def playback_metric(data:MetricRequest,actor=Depends(authorized)):
        """Señal secundaria del ranking. No identifica el video ni el enlace de la fuente."""
        source=await run_in_threadpool(app.state.playback.source,data.source_id)
        await run_in_threadpool(store.record_metric,source.get('provider','mediafusion'),data.event,data.start_ms,data.bitrate_kbps)

    @app.post('/api/playback/event',name='playback_event')
    async def playback_event(data:PlaybackEventRequest,actor=Depends(authorized)):
        journal=app.state.playback_events
        source=await run_in_threadpool(journal.context,actor,data.attempt_id)
        fields=data.model_dump(exclude={'attempt_id','event_id','event','stage'},exclude_none=True)
        accepted=await run_in_threadpool(journal.record,actor,data.attempt_id,'client-'+data.event_id,
            data.event,data.stage,**fields)
        # Los cambios automáticos tras un error del backend solo se registran: el
        # backend ya aprendió ese fallo. Los errores propios del reproductor cuentan
        # una vez gracias al ID idempotente del evento.
        if data.event=='error':
            await run_in_threadpool(journal.learn_failure,actor,data.attempt_id,data.error_code or 'player_error')
        if accepted and data.event=='stable' and (data.duration_ms or 0)>=60000:
            stable=await run_in_threadpool(journal.stable,actor,data.attempt_id)
            running=app.state.control.sources.get(app.state.control.actors.get(actor))
            if stable and running and running.get('attempt_id')==data.attempt_id:
                running.pop('_failure_recorded',None)
                running.pop('_journal_error_recorded',None)
                running.pop('upstream_failure',None)
        return {'accepted':accepted}

    @app.get('/api/playback/analysis',name='playback_analysis')
    async def playback_analysis(days:int=Query(7,ge=1,le=90),actor=Depends(admin)):
        return await run_in_threadpool(app.state.playback_events.summary,days)

    @app.put('/api/playback/progress',name='playback_progress')
    async def playback_progress(data:ProgressRequest,actor=Depends(authorized)):
        result=await run_in_threadpool(Library(store).progress,data.session_id,data.sequence,data.position_ms,
                                      data.duration_ms,data.completed,actor,data.watched_ms,
                                      data.preferences.model_dump() if data.preferences else None)
        if result['accepted']:
            await run_in_threadpool(store.record,actor,'playback.progress')
        return result

    @app.post('/api/playback/cast',name='cast_prepare')
    async def cast_prepare(data:BeginPlayback,actor=Depends(authorized)):
        state=app.state.control.require(data.source_id)
        if state['actor']!=actor:raise ProviderError('unauthorized','Esta fuente pertenece a otro teléfono.',403)
        provider,started=state.get('provider','mediafusion'),time.monotonic()
        previous_stage=state['stage']
        continuing=previous_stage in ('ready','streaming','adapting')
        tools=app.state.media_tools
        before=tools.playlist_version(tools.root/data.source_id)
        try:
            async with app.state.control.operation(data.source_id):
                state['stage']='adapting'
                # Incluye la cola por fuente: el cliente nunca espera indefinidamente.
                async with asyncio.timeout(115):
                    result=await tools.cast_path(data.source_id,data.position_ms,
                        state.get('expected_duration_ms',0),data.force_adapted,data.audio_stream_index)
                result.update(await app.state.subtitles.prepared(data.source_id,0.15))
                state['stage']='ready'
                audit('playback_seek_prepared',actor,target_ms=data.position_ms,
                      offset_ms=result.get('offset_ms',0),operation_id=data.operation_id,
                      elapsed_ms=round((time.monotonic()-started)*1000))
                state['_cast_events']=state.get('_cast_events',0)+1
                await note_playback(state,'cast_ready','cast',event_id='server-cast-'+str(state['_cast_events']),
                    position_ms=data.position_ms,elapsed_ms=round((time.monotonic()-started)*1000))
                if data.audio_stream_index is not None:
                    await run_in_threadpool(store.record,actor,'playback.audio.change')
                await run_in_threadpool(store.record_metric,provider,'cast_started',round((time.monotonic()-started)*1000))
                return result
        except asyncio.CancelledError:raise ProviderError('playback_stopped','Preparación cancelada.',409) from None
        except Exception as error:
            if isinstance(error,TimeoutError):
                error=ProviderError('cast_prepare_timeout','No se pudo preparar ese punto a tiempo.',504)
            await note_failure(state,error,'cast')
            # Un cambio fallido no revoca una reproducción que ya estaba activa. La
            # TV recibe si debe recuperar imagen porque el material anterior cambió.
            if continuing and isinstance(error,ProviderError):
                state['stage']=previous_stage
                after=tools.playlist_version(tools.root/data.source_id)
                audit('playback_seek_failed',actor,target_ms=data.position_ms,
                      operation_id=data.operation_id,error_code=error.code)
                await run_in_threadpool(store.record_metric,provider,'error')
                await learn_source_failure(state,error)
                return JSONResponse({'code':error.code,'message':error.message,
                                     'recovery_required':before!=after},status_code=error.status)
            await app.state.control.stop(actor,data.source_id)
            await run_in_threadpool(store.record_metric,provider,'error')
            await learn_source_failure(state,error)
            raise error

    @app.api_route('/cast/{source_id}/{filename}',methods=['GET','HEAD'],name='cast_segment')
    async def cast_segment(source_id:str,filename:str,start:int=Query(0,ge=0,le=43_200_000),
                           generation:str|None=Query(None,max_length=80,pattern=r'^[A-Za-z0-9_-]+$'),
                           v:str|None=Query(None,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')):
        # `start` recorta la lista para que empiece donde se pidió adelantar. El televisor
        # reproduce desde el principio de lo que recibe, que es lo único que hace siempre.
        async with app.state.control.operation(source_id) as state:
            app.state.control.touch(source_id);state['stage']='streaming'
            try:
                version=generation or (v if filename!='index.m3u8' else None)
                if version is not None:
                    return await app.state.media_tools.segment(source_id,filename,start,version)
                return await app.state.media_tools.segment(source_id,filename,start)
            except ProviderError as error:
                if error.code!='cast_replaced':
                    await note_failure(state,error,'player')
                    await learn_source_failure(state,error)
                raise

    @app.get('/api/playback/subtitles/{source_id}',name='playback_subtitles')
    async def playback_subtitles(source_id:str,actor=Depends(authorized)):
        """Consulta rápida del mismo trabajo; no renueva su plazo ni lo reinicia."""
        state=app.state.control.require(source_id)
        if state['actor']!=actor:raise ProviderError('unauthorized','Esta reproducción pertenece a otro teléfono.',403)
        prepared=await app.state.subtitles.prepared(source_id,0.05)
        return {'items':prepared['subtitles'],'pending':prepared['subtitles_pending'],
                'alternatives':prepared.get('subtitle_alternatives', []),
                'state':prepared['subtitle_state'],'reason':prepared['subtitle_reason'],
                'message':prepared['subtitle_message']}

    @app.post('/api/playback/subtitles/{source_id}/retry',name='subtitle_search_retry')
    async def subtitle_search_retry(source_id:str,actor=Depends(authorized)):
        """Volver a buscar es explícito; clics concurrentes comparten el trabajo."""
        state=app.state.control.require(source_id)
        if state['actor']!=actor:raise ProviderError('unauthorized','Esta reproducción pertenece a otro teléfono.',403)
        app.state.subtitles.start_prepare(source_id,refresh=True)
        prepared=await app.state.subtitles.prepared(source_id,0.05)
        await run_in_threadpool(store.record,actor,'playback.subtitle_search_retry')
        return {'items':prepared['subtitles'],'pending':prepared['subtitles_pending'],
                'alternatives':prepared.get('subtitle_alternatives', []),
                'state':prepared['subtitle_state'],'reason':prepared['subtitle_reason'],
                'message':prepared['subtitle_message']}

    @app.post('/api/playback/subtitles/next',status_code=202,name='subtitle_next_prepare')
    async def subtitle_next_prepare(data:BeginPlayback,actor=Depends(authorized)):
        """Anticipa subtítulos sin activar otro video ni alterar sesiones/progreso."""
        current=app.state.control.require(data.source_id)
        if current['actor']!=actor:raise ProviderError('unauthorized','Esta reproducción pertenece a otro teléfono.',403)
        ahead_task=app.state.subtitles.ahead_task
        if ahead_task and not ahead_task.done():return {'scheduled':False}
        if current.get('_subtitle_next_started'):return {'scheduled':False}
        current['_subtitle_next_started']=True

        async def ahead():
            try:
                async with app.state.control.operation(data.source_id):
                    source=await run_in_threadpool(app.state.playback.source,data.source_id)
                    following=await run_in_threadpool(app.state.playback.next_video,source)
                    if not following:return
                    async with asyncio.timeout(160):
                        found=await playback_sources('series',following,False,actor)
                        target=next((item for item in found['items'] if item['transport']=='http'),None)
                        if not target:return
                        child=target['source_id']
                        # Los lectores pertenecen al video actual y se cancelan con él.
                        app.state.control.children[child]=data.source_id
                        await app.state.subtitles.prepare(child)
                        audit('subtitle_next_prepared',actor,video_id=following)
            except asyncio.CancelledError:raise
            except Exception:audit('subtitle_next_skipped',actor,error_code='subtitle_unavailable')

        task=asyncio.create_task(ahead())
        app.state.subtitles.ahead_task=task
        app.state.warming.add(task);task.add_done_callback(app.state.warming.discard)
        return {'scheduled':True}

    @app.get('/api/playback/audio/{source_id}',name='playback_audio')
    async def playback_audio(source_id:str,actor=Depends(authorized)):
        """Pistas embebidas, inspeccionadas en paralelo después de iniciar el video."""
        state=app.state.control.require(source_id)
        if state['actor']!=actor:
            raise ProviderError('unauthorized','Esta fuente pertenece a otro teléfono.',403)
        try:
            async with asyncio.timeout(40):
                result=await app.state.media_tools.audio_tracks(source_id)
                await run_in_threadpool(store.record,actor,'playback.audio.list')
                return result
        except TimeoutError:
            raise ProviderError('audio_probe_timeout','No se pudieron leer las pistas de audio a tiempo.',504) from None
        except ProviderError as error:
            await learn_source_failure(state,error)
            raise

    @app.post('/api/playback/subtitles/sync',name='playback_subtitles_sync')
    async def playback_subtitles_sync(data:SubtitleSyncRequest,actor=Depends(authorized)):
        """Crea una copia sincronizada; nunca modifica ni reemplaza la pista original."""
        state=app.state.control.require(data.source_id)
        if state['actor']!=actor:
            raise ProviderError('unauthorized','Esta fuente pertenece a otro teléfono.',403)
        ahead=app.state.subtitles.ahead_task
        if ahead and not ahead.done():
            ahead.cancel();await asyncio.gather(ahead,return_exceptions=True)
        try:
            async with app.state.control.operation(data.source_id):
                if data.recover:
                    # Los clientes antiguos también comparten la búsqueda acotada.
                    prepared=await app.state.subtitles.prepared(data.source_id,0.05)
                    ready=prepared['subtitles']
                    result={'changed':bool(ready),'verified':bool(ready),
                        'pending':prepared['subtitles_pending'],'state':prepared['subtitle_state'],
                        'reason':prepared['subtitle_reason'],'message':prepared['subtitle_message']}
                    if ready:result['item']=ready[0]
                else:
                    async with asyncio.timeout(10):
                        result=await app.state.subtitles.sync(data.source_id,data.subtitle_source_id)
        except TimeoutError:
            result={'changed':False,'verified':False,'reason':'subtitle_sync_timeout','state':'failed',
                    'message':'No se pudo confirmar esta pista. Vuelve a buscar alternativas.'}
        await run_in_threadpool(store.record,actor,'playback.subtitle_sync')
        return result

    @app.get('/subtitles/{source_id}.vtt',name='subtitles_delivery')
    async def subtitles_delivery(source_id:str,offset:int=Query(0,ge=-43_200_000,le=43_200_000),manual:bool=False):
        """`offset` alinea el subtítulo con una conversión que no empieza en cero."""
        return await app.state.subtitles.deliver(source_id,offset,manual=manual)

    @app.get('/subtitles/synced/{token}.vtt',name='synced_subtitles_delivery')
    async def synced_subtitles_delivery(token:str,offset:int=Query(0,ge=-43_200_000,le=43_200_000)):
        return await app.state.subtitles.deliver_synced(token,offset)

    @app.api_route('/media/{source_id}',methods=['GET','HEAD'],name='media_delivery')
    async def media_delivery(source_id:str,request:Request):
        async with app.state.control.operation(source_id) as state:
            if request.client and request.client.host not in ('127.0.0.1','::1'):
                app.state.control.touch(source_id);state['stage']='streaming'
            try:
                return await app.state.playback.media(source_id,request)
            except ProviderError as error:
                await note_failure(state,error,'upstream')
                await learn_source_failure(state,error)
                raise

    @app.options('/media/{source_id}',name='media_cors')
    async def media_cors(source_id:str):
        from fastapi.responses import Response
        return Response(status_code=204,headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Methods':'GET,HEAD,OPTIONS','Access-Control-Allow-Headers':'Range','Access-Control-Max-Age':'600'})

    # Aplicación para el televisor: se abre desde su navegador o se empaqueta como Tizen.
    if (ROOT / "tv").is_dir():
        app.mount("/tv", StaticFiles(directory=ROOT / "tv", html=True), name="television_app")
    app.mount("/assets", StaticFiles(directory=ROOT / "frontend"), name="assets")
    return app

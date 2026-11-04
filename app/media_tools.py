"""Inspección real y adaptación a H.264/AAC para Chromecast 2/3 mediante FFmpeg."""
import asyncio
import json
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from contextlib import asynccontextmanager

from urllib.parse import urlsplit

from fastapi.responses import FileResponse,Response
from starlette.datastructures import Headers
from starlette.responses import MalformedRangeHeader,RangeNotSatisfiable
from app.providers.stremio import ProviderError
from app.settings import ROOT
from app.playback_events import probe_cause

# Fragmentos de cuatro segundos y tres por delante antes de servir. Se probaron
# fragmentos de dos: en una fuente que hay que recodificar ahorraban 2,4 s, pero en
# la mayoría —las que solo se copian— el límite es la descarga y no cambió nada,
# mientras el colchón inicial bajaba de 12 s a 6 s. No compensa perder margen.
SEGMENT_SECONDS=4
SEGMENT_WARMUP=3
# Cuánto aguanta una conversión sin que nadie le pida fragmentos antes de soltarla.
# Con 90 s, pausar el video y atender un mensaje costaba la conversión entera.
JOB_IDLE_SECONDS=900
# Conversiones simultáneas. Al llegar al tope se cede el sitio de la más olvidada:
# negarse en seco por algo que ya nadie mira es peor que rehacerlo si vuelve.
MAX_JOBS=2
BUSY_SECONDS=60
# Inspecciones recordadas. Volver a abrir la misma edición costaba otros veinte segundos
# de FFprobe sobre el mismo archivo remoto, y lo que describe no cambia mientras viva la fuente.
INSPECTION_MEMORY=32
# Una lista HLS convertida no describe por sí sola qué pista quedó dentro. Guardar una
# revisión y el índice elegido impide reutilizar una conversión vieja con otro idioma.
CONVERSION_REVISION='audio-v1'


class FragmentResponse(Response):
    """Retiene solo el fragmento solicitado, nunca una ruta que pueda cambiar al saltar.

    Conserva HEAD y rangos HTTP usando el parser y las cabeceras de Starlette. Los
    bytes dejan de depender de la conversión antes de que empiece el envío ASGI.
    """
    def __init__(self,file):
        self.path=file
        self.file_headers=FileResponse(file,stat_result=file.stat(),media_type='video/mp2t',
                                      headers={'Access-Control-Allow-Origin':'*','Cache-Control':'no-store'})
        super().__init__(file.read_bytes(),media_type='video/mp2t',headers=dict(self.file_headers.headers))

    async def __call__(self,scope,receive,send):
        request=Headers(scope=scope)
        body,status,headers=self.body,200,dict(self.headers)
        wanted=request.get('range')
        if wanted and (not request.get('if-range') or self.file_headers._should_use_range(request['if-range'])):
            try:ranges=FileResponse._parse_range_header(wanted,len(body))
            except MalformedRangeHeader:
                return await Response(status_code=400)(scope,receive,send)
            except RangeNotSatisfiable:
                return await Response(status_code=416,headers={'Content-Range':f'bytes */{len(body)}'})(scope,receive,send)
            if len(ranges)==1:
                start,end=ranges[0]
                headers['content-range']=f'bytes {start}-{end-1}/{len(body)}'
                headers['content-length']=str(end-start)
                body,status=body[start:end],206
            elif ranges:
                boundary=secrets.token_hex(13)
                length,part_header=self.file_headers.generate_multipart(ranges,boundary,len(body),self.media_type)
                headers['content-type']='multipart/byteranges; boundary='+boundary
                headers['content-length']=str(length)
                body=b''.join(part_header(start,end)+body[start:end]+b'\r\n' for start,end in ranges)+f'--{boundary}--'.encode()
                status=206
        if scope['method']=='HEAD':body=b''
        await Response(body,status_code=status,headers=headers)(scope,receive,send)


@asynccontextmanager
async def bounded_lock(lock):
    try:await asyncio.wait_for(lock.acquire(),2)
    except TimeoutError:raise ProviderError('cast_busy','Se está preparando otro video. Intenta de nuevo en unos segundos.',409) from None
    try:yield
    finally:lock.release()


def executable(name):
    found=next((ROOT/'tools').glob('*/bin/'+name+'.exe'),None)
    # En otras instalaciones se pueden usar FFmpeg y FFprobe instalados en PATH.
    if not found: found=shutil.which(name)
    if not found: raise ProviderError('media_tools_missing','Faltan las herramientas de video de la videoteca.',503)
    return str(found)


def cast_video_compatible(info):
    videos=[s for s in info.get('streams',[]) if s.get('codec_type')=='video']
    if not videos:return False
    video=videos[0]
    try:
        numerator,denominator=video.get('avg_frame_rate','0/1').split('/')
        fps=float(numerator)/max(float(denominator),1)
    except (ValueError,TypeError):return False
    return (video.get('codec_name')=='h264' and video.get('width',0)<=1920 and video.get('height',0)<=1080
        and video.get('level',999)<=41 and fps<=30.1 and video.get('pix_fmt','yuv420p') in ('yuv420p','yuvj420p'))


def cast_compatible(info):
    audios=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
    return cast_video_compatible(info) and all(a.get('codec_name') in ('aac','mp3') for a in audios)


def video_arguments(info):
    """Decide el video por su cuenta, no por el audio que lo acompaña.

    La mayoría de las ediciones traen H.264 perfectamente reproducible junto a un
    audio EAC3 o AC3. Antes bastaba ese audio para re-codificar también el video,
    con dos costes: media hora de CPU por película y una bajada de 1080p a 720p.
    Copiar el video y convertir solo el audio evita ambos.
    """
    if cast_video_compatible(info):return ['-c:v','copy']
    return ['-c:v','libx264','-preset','veryfast','-threads','4','-crf','21',
        '-vf','scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30',
        '-pix_fmt','yuv420p','-profile:v','high','-level:v','3.1','-maxrate','4000k','-bufsize','8000k','-g','120']


# Idiomas de doblaje que esta casa nunca quiere. Muchas publicaciones traen el original
# y el doblaje en el mismo archivo, con el doblaje en la pista cero: quedarse con la
# primera pista sin mirar era ponerse a ver la pelicula en un idioma que nadie entiende.
DUBS=('hin','tam','tel','mal','ben','kan','mar','pan','urd')
AUDIO_LANGUAGE_NAMES={
    'spa':'Español','es':'Español','eng':'Inglés','en':'Inglés','hin':'Hindi',
    'por':'Portugués','pt':'Portugués','fra':'Francés','fre':'Francés','fr':'Francés',
    'ita':'Italiano','it':'Italiano','deu':'Alemán','ger':'Alemán','de':'Alemán',
    'rus':'Ruso','ru':'Ruso','jpn':'Japonés','ja':'Japonés','kor':'Coreano','ko':'Coreano',
    'zho':'Chino','chi':'Chino','zh':'Chino','ara':'Árabe','ar':'Árabe',
    'tam':'Tamil','tel':'Telugu','mal':'Malayalam','ben':'Bengalí','und':'Sin identificar',
}

# Códigos reales de FFprobe. Una etiqueta desconocida no prueba que el audio sea ajeno.
AUDIO_CODES={code:language for language,codes in (
    ('spanish',('spa','esp','es')),('english',('eng','en')),('hindi',('hin','hi')),
    ('tamil',('tam','ta')),('telugu',('tel','te')),('malayalam',('mal','ml')),
    ('bengali',('ben','bn')),('french',('fra','fre','fr')),('italian',('ita','it')),
    ('german',('deu','ger','de')),('portuguese',('por','pt')),('russian',('rus','ru')),
    ('ukrainian',('ukr','uk')),('polish',('pol','pl')),('turkish',('tur','tr')),
    ('korean',('kor','ko')),('japanese',('jpn','ja')),('chinese',('zho','chi','zh')),
    ('arabic',('ara','ar')),('dutch',('nld','dut','nl')),('swedish',('swe','sv')),
    ('danish',('dan','da')),('norwegian',('nor','no')),('finnish',('fin','fi')),
    ('persian',('fas','per','fa')),('thai',('tha','th')),
) for code in codes}


def verify_audio_languages(info,original_languages):
    """Rechaza solo una contradicción comprobada: todas las pistas identificadas y ajenas.

    La publicación puede anunciar Hindi/English y contener únicamente Hindi. Una
    etiqueta comercial no puede sustituir las pistas reales del archivo inspeccionado.
    """
    originals=set(original_languages or ())
    if not originals:return
    tracks=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
    languages=[AUDIO_CODES.get(str((s.get('tags') or {}).get('language') or '').lower().split('-')[0])
               for s in tracks]
    if languages and all(languages) and not set(languages)&(originals|{'spanish'}):
        raise ProviderError('audio_language_mismatch',
            'Esta edición no contiene audio original ni español. Se intentará otra fuente.',422)


def pick_audio(info,preferred_index=None):
    """La pista de audio con la que conviene convertir.

    Espanol si lo hay. Si no, la primera que no sea un doblaje de los que no queremos,
    respetando el orden del archivo, que es el que su autor eligio. Y si todas lo son
    —una pelicula india, por ejemplo— la primera, porque quedarse sin sonido es peor.
    """
    audio=[s for s in info['streams'] if s.get('codec_type')=='audio']
    if not audio:return None

    if preferred_index is not None:
        return next((s for s in audio if s.get('index')==preferred_index),None)

    def idioma(track):return str((track.get('tags') or {}).get('language') or '').lower()

    spanish=next((s for s in audio if idioma(s) in ('spa','es','es-es','es-mx','esp')),None)
    if spanish:return spanish
    return next((s for s in audio if idioma(s)[:3] not in DUBS),audio[0])


def default_audio(info):
    """Pista que una URL sin adaptar entregaría por defecto al televisor."""
    tracks=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
    return next((s for s in tracks if (s.get('disposition') or {}).get('default')),tracks[0] if tracks else None)


def audio_choices(info,preferred_index=None):
    """Pistas comprensibles para la interfaz y la elección automática ya resuelta."""
    chosen=pick_audio(info,preferred_index)
    rows=[]
    for position,track in enumerate(s for s in info.get('streams',[]) if s.get('codec_type')=='audio'):
        index=track.get('index')
        code=str((track.get('tags') or {}).get('language') or 'und').lower()
        name=AUDIO_LANGUAGE_NAMES.get(code,AUDIO_LANGUAGE_NAMES.get(code[:2],code.upper()))
        rows.append({'stream_index':index,'language':code,'label':name or f'Audio {position+1}',
                     'selected':bool(chosen and chosen.get('index')==index)})
    return rows


def native_hls(stream):
    """Un HLS del proveedor ya es adaptativo: el proxy local basta y evita reconvertir."""
    url=str((stream or {}).get('url') or '')
    if not url:return False
    path=urlsplit(url).path.lower()
    hints=stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'),dict) else {}
    declared=str(hints.get('videoMimeType') or hints.get('mimeType') or '').lower()
    return path.endswith('.m3u8') or 'mpegurl' in declared


class MediaTools:
    def __init__(self,settings,lookup=None):
        self.settings=settings
        # `lookup(source_id)` devuelve la fuente guardada; permite reconocer un HLS original.
        self.lookup=lookup
        self.inspections={}
        self.probes={}
        self.jobs={}
        # Índice real de FFprobe, no la posición ordinal dentro de las pistas de audio.
        self.audio_selections={}
        # Segundo del video en el que empieza cada conversión: adelantar = reconvertir desde ahí.
        self.offsets={}
        self.lock=asyncio.Lock()
        # La selección de audio y la preparación deben corresponder a una sola operación.
        # Este candado por fuente no bloquea la entrega de fragmentos ni otras películas.
        self.preparations={}
        self.limit=asyncio.Semaphore(2)
        self.root=(ROOT/'cache'/'cast').resolve()
        self.root.mkdir(parents=True,exist_ok=True)

    def source_url(self,source_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{20,80}',source_id): raise ProviderError('invalid_source','Fuente no válida.',422)
        return f'http://127.0.0.1:{self.settings.media_port}/media/{source_id}'

    async def inspect(self,source_id):
        if source_id in self.inspections:return self.inspections[source_id]
        # Compartir la inspección cuando Android y el receptor solicitan la misma fuente.
        task=self.probes.get(source_id)
        if task is None:
            task=asyncio.create_task(self._inspect(source_id));self.probes[source_id]=task
            def finished(done):
                if self.probes.get(source_id) is done:self.probes.pop(source_id,None)
                # Un cliente que cancela no deja excepciones de FFprobe sin recoger.
                if not done.cancelled():done.exception()
            task.add_done_callback(finished)
        try:
            info=await asyncio.shield(task)
            self.inspections[source_id]=info
            while len(self.inspections)>INSPECTION_MEMORY:
                self.inspections.pop(next(iter(self.inspections)))
            return info
        finally:
            if task.done() and self.probes.get(source_id) is task:self.probes.pop(source_id,None)

    async def _inspect(self,source_id):
        async with bounded_lock(self.limit):
            command=[executable('ffprobe'),'-v','error','-rw_timeout','120000000',
                '-protocol_whitelist','http,tcp,crypto','-analyzeduration','5000000','-probesize','8000000',
                '-show_entries','stream=index,codec_name,codec_type,width,height,level,avg_frame_rate,pix_fmt,sample_rate,channels:stream_tags=language:stream_disposition=default:format=duration,format_name',
                '-of','json',self.source_url(source_id)]
            process=await asyncio.create_subprocess_exec(*command,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                output,diagnostic=await asyncio.wait_for(process.communicate(),35)
            except TimeoutError:
                if process.returncode is None:process.kill();await process.wait()
                raise ProviderError('video_probe_failed','Esta fuente no entregó un video válido a tiempo. Se puede intentar otra.',504) from None
            except asyncio.CancelledError:
                if process.returncode is None:process.kill();await process.wait()
                raise
            try:
                # Un proceso remoto que corta puede ser red y no condena la edición. Una
                # respuesta completa que no describe video sí es evidencia del archivo.
                if process.returncode or len(output)>1_000_000:
                    raise ProviderError(probe_cause(diagnostic),'Esta fuente no entregó un video válido a tiempo. Se puede intentar otra.',504)
                info=json.loads(output)
                if not isinstance(info,dict) or not any(isinstance(s,dict) and s.get('codec_type')=='video'
                                                         for s in info.get('streams',[])):
                    raise ProviderError('video_invalid','Esta fuente no contiene un video válido. Se intentará otra.',422)
            except (ValueError,UnicodeError,json.JSONDecodeError):
                raise ProviderError('video_invalid','Esta fuente no contiene un video válido. Se intentará otra.',422) from None
            return info

    async def original_stream(self,source_id):
        if not self.lookup:return None
        from starlette.concurrency import run_in_threadpool
        try:return (await run_in_threadpool(self.lookup,source_id)).get('stream')
        except ProviderError:return None

    async def validate_source_audio(self,source_id,info):
        if not self.lookup:return
        from starlette.concurrency import run_in_threadpool
        source=await run_in_threadpool(self.lookup,source_id)
        verify_audio_languages(info,source.get('original_languages'))

    async def cast_path(self,source_id,start_ms=0,expected_duration_ms=0,force_adapted=False,
                        audio_stream_index=None):
        lock=self.preparations.setdefault(source_id,asyncio.Lock())
        async with lock:
            return await self._cast_path(source_id,start_ms,expected_duration_ms,force_adapted,audio_stream_index)

    async def _cast_path(self,source_id,start_ms=0,expected_duration_ms=0,force_adapted=False,
                        audio_stream_index=None):
        """`start_ms` es el segundo del video que debe quedar al principio de la lista.

        Una fuente que necesita conversión solo se puede ver desde donde FFmpeg ya
        convirtió; para retomar o adelantar hay que reconvertir desde ese punto.
        """
        # En el arranque el proxy HLS es la ruta más rápida. Si la TV ya demostró que
        # ignora currentTime, hay que convertir desde el punto pedido: devolver la misma
        # lista completa solo consigue que vuelva al minuto cero.
        already_adapted=source_id in self.offsets or source_id in self.jobs
        if native_hls(await self.original_stream(source_id)) and not force_adapted and audio_stream_index is None and not already_adapted:
            # Canales/HLS sin política de idioma conservan su ruta rápida. Para un
            # título conocido, no prometer audio original sin mirar sus pistas reales.
            from starlette.concurrency import run_in_threadpool
            source=await run_in_threadpool(self.lookup,source_id) if self.lookup else {}
            if not source.get('original_languages'):
                return {'stream_url':f'http://{self.settings.lan_ip}:{self.settings.media_port}/media/{source_id}',
                    'content_type':'application/vnd.apple.mpegurl','adapted':False,'offset_ms':0,'cast_verified':False}
            info=await self.inspect(source_id)
            verify_audio_languages(info,source['original_languages'])
            chosen=pick_audio(info)
            first=default_audio(info)
            if chosen is None or chosen is first:
                return {'stream_url':f'http://{self.settings.lan_ip}:{self.settings.media_port}/media/{source_id}',
                    'content_type':'application/vnd.apple.mpegurl','adapted':False,'offset_ms':0,'cast_verified':False,
                    'audio_tracks':audio_choices(info),'audio_stream_index':chosen.get('index') if chosen else None}
        info=await self.inspect(source_id)
        await self.validate_source_audio(source_id,info)
        preferred=audio_stream_index if audio_stream_index is not None else self.audio_selections.get(source_id)
        chosen=pick_audio(info,preferred)
        if audio_stream_index is not None and chosen is None:
            raise ProviderError('audio_track_not_found','Esa pista de audio ya no está disponible.',422)
        chosen_index=chosen.get('index') if chosen else None
        await self.select_audio(source_id,chosen_index)
        tracks=audio_choices(info,chosen_index)
        direct=cast_compatible(info) and 'mp4' in info.get('format',{}).get('format_name','')
        try:total=int(float(info.get('format',{}).get('duration',0))*1000)
        except (ValueError,TypeError):total=0
        # FFprobe confirma que hay un video, pero una publicidad también lo es. Cuando el
        # catálogo conoce el minutaje, una fuente que no llega ni al 35 % es otro contenido
        # con certeza suficiente para descartarla sin castigar montajes o cortes distintos.
        if expected_duration_ms and total and total<expected_duration_ms*.35:
            raise ProviderError('content_mismatch',
                'La fuente entregó un video demasiado corto para corresponder a este título. Se intentará otra.',422)
        first_audio=default_audio(info)
        # Una URL directa reproduce su pista predeterminada. Cuando se eligió otra,
        # hay que adaptarla: informar «Inglés» no cambia por sí solo el audio de la TV.
        selects_other=chosen is not None and first_audio is not None and chosen.get('index')!=first_audio.get('index')
        if direct and not selects_other and not force_adapted and audio_stream_index is None and not already_adapted:
            return {'stream_url':f'http://{self.settings.lan_ip}:{self.settings.media_port}/media/{source_id}',
                'content_type':'video/mp4','adapted':False,'offset_ms':0,'duration_ms':total,'cast_verified':False,
                'audio_tracks':tracks,'audio_stream_index':chosen_index}
        wanted=max(0,min(int(start_ms),max(0,total-15000) if total else int(start_ms)))
        offset=await self.convert_from(source_id,wanted)
        playlist=(self.root/source_id/'index.m3u8').read_text()
        ready=sum(float(n) for n in re.findall(r'#EXTINF:([0-9.]+)',playlist))
        # Lo pedido puede caer dentro de lo ya convertido: entonces no se reconvierte, se
        # recorta. El cliente recibe una lista que empieza donde pidió, y un desplazamiento
        # que ya incluye el recorte, así que la barra y los subtítulos siguen cuadrando.
        skip=max(0,wanted-offset)
        _,cut=self.cut_playlist(playlist,skip) if skip else (playlist,0)
        version=self.playlist_version(self.root/source_id)
        from app.observability import audit
        audit('playback_prepared',target_ms=wanted,offset_ms=offset+cut,
              available_ms=int(ready*1000),conversion=version)
        return {'stream_url':f'http://{self.settings.lan_ip}:{self.settings.media_port}/cast/{source_id}/index.m3u8?start={cut}&generation={version}',
            'content_type':'application/vnd.apple.mpegurl','adapted':True,'cast_verified':False,
            'offset_ms':offset+cut,'duration_ms':total,'seekable_ms':max(0,int((ready-8)*1000)-cut),
            'audio_tracks':tracks,'audio_stream_index':chosen_index}

    async def audio_tracks(self,source_id):
        """Pistas reales bajo demanda, con la misma validación de idioma que el arranque."""
        info=await self.inspect(source_id)
        await self.validate_source_audio(source_id,info)
        chosen=self.audio_selections.get(source_id)
        tracks=audio_choices(info,chosen)
        if chosen is None:
            automatic=pick_audio(info)
            chosen=automatic.get('index') if automatic else None
            tracks=audio_choices(info,chosen)
        return {'items':tracks,'audio_stream_index':chosen}

    @staticmethod
    def conversion_identity(audio_index):
        return f'{CONVERSION_REVISION}:{audio_index if audio_index is not None else "none"}'

    async def select_audio(self,source_id,audio_index):
        """Fija la pista para la siguiente conversión e invalida solo la incompatible."""
        expected=self.conversion_identity(audio_index)
        folder=self.root/source_id
        try:cached=(folder/'conversion').read_text()
        except OSError:cached=''
        previous=self.audio_selections.get(source_id)
        self.audio_selections[source_id]=audio_index
        if (previous is not None and previous!=audio_index) or ((folder/'index.m3u8').is_file() and cached!=expected):
            await self.stop_conversion(source_id)
            self.audio_selections[source_id]=audio_index

    @staticmethod
    def cut_playlist(text,skip_ms):
        """Devuelve la lista empezando en el fragmento que cubre `skip_ms`.

        Es lo que hace que adelantar funcione en un televisor que ignora la orden de
        salto: en vez de pedirle que se coloque en el minuto veinte de una lista que
        empieza en cero —cosa que su reproductor descarta mientras la lista no lleve
        marca de final, porque la trata como emisión en directo—, se le entrega una
        lista que empieza en el minuto veinte. Reproducir desde el principio sí sabe.

        No cuesta nada: los fragmentos ya están convertidos y son independientes entre
        sí, así que basta con no nombrar los anteriores.

        Devuelve la lista recortada y cuántos milisegundos se dejaron fuera, que es el
        desplazamiento real con el que hay que contar el tiempo y los subtítulos.
        """
        if skip_ms<=0:return text,0
        lines=text.split('\n')
        head,items=[],[]
        current=None
        for line in lines:
            if line.startswith('#EXTINF:'):
                current=float(re.findall(r'#EXTINF:([0-9.]+)',line)[0]);continue
            if current is not None and line.strip() and not line.startswith('#'):
                items.append((current,line));current=None;continue
            if line.startswith('#EXT-X-ENDLIST'):continue
            if not items:head.append(line)
        if not items:return text,0
        # Nunca dejar la lista sin material: si lo pedido está al filo de lo convertido,
        # se retrocede lo justo para que queden fragmentos que reproducir.
        limit=max(0,len(items)-SEGMENT_WARMUP)
        skipped=0.0
        index=0
        while index<limit and skipped+items[index][0]<=skip_ms/1000:
            skipped+=items[index][0];index+=1
        if not index:return text,0
        kept=[]
        for line in head:
            if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):kept.append('#EXT-X-MEDIA-SEQUENCE:%d'%index);continue
            kept.append(line)
        while kept and not kept[-1].strip():kept.pop()
        for duration,name in items[index:]:
            kept.append('#EXTINF:%.6f,'%duration);kept.append(name)
        if '#EXT-X-ENDLIST' in text:kept.append('#EXT-X-ENDLIST')
        return '\n'.join(kept)+'\n',int(round(skipped*1000))

    @staticmethod
    def version_segments(text,version):
        """Distingue fragmentos rehechos desde minutos diferentes.

        Algunos televisores conservan `part000000.ts` aunque la respuesta diga
        `no-store`. La lista sí cambia de URL, pero su query no se hereda a los hijos;
        añadir el punto inicial impide que un salto reproduzca un fragmento antiguo.
        """
        token=re.sub(r'[^A-Za-z0-9_-]','',str(version or '0')) or '0'
        lines=[]
        for line in text.splitlines():
            if re.fullmatch(r'part\d{6}\.ts',line.strip()):line=line.strip()+'?v='+token
            lines.append(line)
        return '\n'.join(lines)+'\n'

    @staticmethod
    def playlist_version(folder):
        """Identidad del material, también cuando se reconstruye desde el mismo minuto."""
        if not folder.is_dir():return None
        try:version=(folder/'offset').read_text()
        except OSError:version='0'
        try:
            identity=(folder/'conversion').read_text()
            if not identity.endswith(':none'):version+='-'+identity
        except OSError:pass
        # Mantener la identidad de cachés anteriores; las nuevas añaden una generación.
        token=re.sub(r'[^0-9]','',version) or '0'
        try:token+='-'+(folder/'generation').read_text().strip()
        except OSError:pass
        return token

    def require_version(self,source_id,version):
        if version is not None and version!=self.playlist_version(self.root/source_id):
            raise ProviderError('cast_replaced','Esta lista fue reemplazada por otro punto del video.',410)

    def converted_ms(self,source_id):
        """Cuánto video lleva convertido la lista actual."""
        try:playlist=(self.root/source_id/'index.m3u8').read_text()
        except OSError:return 0
        return int(sum(float(n) for n in re.findall(r'#EXTINF:([0-9.]+)',playlist))*1000)

    def started_at(self,source_id):
        """Punto en que arrancó la conversión que hay en disco.

        Se guarda junto a los fragmentos porque sin él, tras soltar el proceso, lo que
        quedó convertido se serviría creyendo que empieza en el punto que se acaba de
        pedir: el video arrancaba desde el principio con la barra en otro sitio.
        """
        try:return int((self.root/source_id/'offset').read_text())
        except (OSError,ValueError):return None

    async def convert_from(self,source_id,offset):
        """Devuelve el punto en que empieza la lista disponible.

        Si lo pedido ya está dentro de lo convertido no se rehace nada: enviar a la
        TV lo que se está viendo en el teléfono debe ser inmediato, no volver a
        convertir la película desde ese minuto.
        """
        current=self.offsets.get(source_id)
        if current is None:
            # Lo dejado por una sesión anterior sigue valiendo, pero solo desde su punto.
            current=self.started_at(source_id)
            if current is not None:self.offsets[source_id]=current
        if current is not None and (source_id in self.jobs or self.converted_ms(source_id)):
            job=self.jobs.get(source_id)
            failed=job and (job.get('error') or (job.get('process') and job['process'].returncode not in (None,0)))
            # Nunca prometer escenas anteriores al material que existe. Una conversión
            # interrumpida tampoco puede reaprovecharse para recuperar el reproductor.
            available=self.converted_ms(source_id)
            if not failed and current<=offset and (offset<current+available or (offset==current and source_id in self.jobs)):
                from app.observability import audit
                audit('playback_conversion',target_ms=offset,offset_ms=current,reason='reuse')
                await self.segment(source_id,'index.m3u8')
                return current
            await self.stop_conversion(source_id)
        from app.observability import audit
        audit('playback_conversion',target_ms=offset,offset_ms=offset,reason='rebuild')
        self.offsets[source_id]=offset
        await self.segment(source_id,'index.m3u8')
        return offset

    async def release(self,source_id):
        """Suelta el proceso de una conversión sin borrar lo que ya dejó en disco."""
        job=self.jobs.pop(source_id,None)
        if not job:return
        job['stopping']=True
        if job['process'].returncode is None:
            job['process'].terminate()
            try:await asyncio.wait_for(job['process'].wait(),3)
            except TimeoutError:job['process'].kill();await job['process'].wait()
        if job.get('monitor'):await asyncio.gather(job['monitor'],return_exceptions=True)

    async def stop_conversion(self,source_id):
        """Detiene solo la conversión: la inspección y la fuente siguen sirviendo."""
        await self.release(source_id)
        folder=(self.root/source_id).resolve()
        if folder.is_relative_to(self.root) and folder.is_dir():
            await asyncio.to_thread(shutil.rmtree,folder,True)

    def from_disk(self,folder,filename,skip_ms=0):
        """Responde con lo que ya hay convertido, cuando sirve tal cual.

        Un fragmento que existe siempre vale. La lista solo vale terminada: una parcial
        de un trabajo que se soltó a medias hay que continuarla, no anunciarla.
        """
        file=folder/filename
        if not file.is_file():return None
        expected=self.conversion_identity(self.audio_selections.get(folder.name))
        try:identity=(folder/'conversion').read_text()
        except OSError:return None
        if identity!=expected:return None
        if filename=='index.m3u8' and '#EXT-X-ENDLIST' not in file.read_text():return None
        return self.file_response(file,skip_ms)

    async def recycle(self):
        """Hace sitio para una conversión nueva. Fuera del candado: esperar a que un
        proceso termine no puede dejar sin fragmentos al video que se está viendo."""
        # Preparar otro video no debe matar la conversión que alguien está viendo, pero un
        # trabajo terminado, o sin peticiones en un cuarto de hora, ya no lo mira nadie.
        for old,job in list(self.jobs.items()):
            if job['process'].returncode is not None or time.monotonic()-job['access']>JOB_IDLE_SECONDS:
                await self.release(old)
        if len(self.jobs)>=MAX_JOBS:
            spare=min(self.jobs,key=lambda key:self.jobs[key]['access'])
            if time.monotonic()-self.jobs[spare]['access']<BUSY_SECONDS:
                raise ProviderError('cast_busy','Ya hay dos videos preparándose. Detén uno para continuar.',409)
            await self.release(spare)

    async def launch(self,source_id,folder,info):
        """Arranca FFmpeg para esta fuente. Se entra con el candado tomado."""
        chosen=pick_audio(info,self.audio_selections.get(source_id))
        folder.mkdir(exist_ok=True)
        # No anunciar como lista una conversión parcial de una ejecución anterior.
        (folder/'index.m3u8').unlink(missing_ok=True)
        offset=self.offsets.get(source_id,0)
        (folder/'offset').write_text(str(offset))
        (folder/'conversion').write_text(self.conversion_identity(chosen.get('index') if chosen else None))
        (folder/'generation').write_text(secrets.token_hex(8))
        command=[executable('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-y','-progress','pipe:1','-stats_period','2',
            '-rw_timeout','120000000','-protocol_whitelist','http,tcp,crypto']
        # Buscar antes de -i usa el índice del archivo: saltar a la hora 1 es inmediato.
        if offset>0:command+=['-ss',f'{offset/1000:.3f}']
        command+=['-i',self.source_url(source_id),'-map','0:v:0']
        if chosen:command+=['-map',f"0:{chosen['index']}"]
        command+=video_arguments(info)
        command+=['-c:a','aac','-ac','2','-b:a','160k','-sn','-f','hls','-hls_time',str(SEGMENT_SECONDS),'-hls_playlist_type','event',
            '-hls_flags','independent_segments+temp_file','-hls_segment_filename',str(folder/'part%06d.ts'),str(folder/'index.m3u8')]
        process=await asyncio.create_subprocess_exec(*command,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
        job={'process':process,'access':time.monotonic(),'seconds':0,'error':None,'offset':offset}
        self.jobs[source_id]=job
        job['monitor']=asyncio.create_task(self.monitor(job,info))

    async def segment(self,source_id,filename,skip_ms=0,version=None):
        if filename!='index.m3u8' and not re.fullmatch(r'part\d{6}\.ts',filename):
            raise ProviderError('not_found','Fragmento no disponible.',404)
        self.source_url(source_id)
        folder=(self.root/source_id).resolve()
        if not folder.is_relative_to(self.root): raise ProviderError('not_found','Fragmento no disponible.',404)
        self.require_version(source_id,version)
        previous=self.jobs.get(source_id)
        # FFmpeg publica mediante temp_file: un .ts definitivo ya está completo.
        # Un productor interrumpido no invalida esos bytes. Solo se rechaza al llegar
        # al límite preparado; la generación se comprueba antes y después de leer.
        file=folder/filename
        if previous and file.is_file() and file.stat().st_size>0:
            ready=filename!='index.m3u8'
            if filename=='index.m3u8':
                text=file.read_text()
                parts=re.findall(r'^part\d{6}\.ts$',text,re.M)
                ready=bool(parts) and all((folder/name).is_file() for name in parts)
                ready=ready and (len(parts)>=SEGMENT_WARMUP or '#EXT-X-ENDLIST' in text
                    or bool(previous.get('error')) or previous['process'].returncode not in (None,0))
                if skip_ms:ready=ready and bool(re.search(r'#EXTINF:',self.cut_playlist(text,skip_ms)[0]))
            if ready:
                previous['access']=time.monotonic()
                response=self.file_response(file,skip_ms)
                self.require_version(source_id,version)
                return response
        if previous and (previous.get('error') or previous['process'].returncode not in (None,0)):
            raise ProviderError('cast_conversion_failed','Esta fuente interrumpió la conversión para TV. Detén y elige otra edición.',502)
        if source_id not in self.jobs:
            # Lo ya convertido se sigue sirviendo aunque el proceso se haya soltado: una
            # pausa larga, o una película ya convertida entera, no obligan a rehacer nada.
            done=self.from_disk(folder,filename,skip_ms)
            if done is not None:return done
            if filename!='index.m3u8':raise ProviderError('cast_expired','Vuelve a enviar el video al Chromecast.',410)
            # Inspeccionar y limpiar fuera del candado: mientras esto dura —hasta medio
            # minuto— los fragmentos del video que ya se está viendo tienen que salir igual.
            info=await self.inspect(source_id)
            from app.cache_cleanup import cleanup
            await asyncio.to_thread(cleanup,self.root,set(self.jobs)|set(self.offsets)|{source_id},12*1024**3)
            if shutil.disk_usage(self.root).free<12*1024**3:
                raise ProviderError('disk_full','No hay espacio suficiente para preparar el video de la TV.',507)
            await self.recycle()
            async with bounded_lock(self.lock):
                # Otra petición pudo crear el mismo trabajo mientras se inspeccionaba.
                if source_id not in self.jobs:await self.launch(source_id,folder,info)
        job=self.jobs[source_id];job['access']=time.monotonic()
        file=folder/filename
        try:
            async with asyncio.timeout(30):
                while True:
                    exists=file.exists()
                    # Empezar con varios fragmentos reduce cortes inmediatamente después del inicio.
                    ready=exists
                    if exists and filename=='index.m3u8':
                        playlist=file.read_text()
                        ready=playlist.count('#EXTINF:')>=SEGMENT_WARMUP or '#EXT-X-ENDLIST' in playlist
                    if job['process'].returncode is not None and job.get('monitor'):
                        await job['monitor']
                    if ready:break
                    if job.get('error'):raise ProviderError('cast_conversion_failed','La fuente se interrumpió durante la preparación. Elige otra edición.',502)
                    if job['process'].returncode is not None:raise ProviderError('cast_conversion_failed','No se pudo preparar esta edición para la TV.')
                    await asyncio.sleep(.2)
        except TimeoutError: raise ProviderError('cast_buffer_timeout','El video todavía no entrega este fragmento. Prueba otra edición.',504) from None
        self.require_version(source_id,version)
        return self.file_response(file,skip_ms)

    @staticmethod
    def file_response(file,skip_ms=0):
        if file.suffix=='.m3u8':
            # FFmpeg reemplaza la lista mientras crece; enviar una instantánea coherente.
            text=file.read_text()
            if skip_ms:text,_=MediaTools.cut_playlist(text,skip_ms)
            version=MediaTools.playlist_version(file.parent)
            text=MediaTools.version_segments(text,version)
            return Response(text.encode(),media_type='application/vnd.apple.mpegurl',headers={'Access-Control-Allow-Origin':'*','Cache-Control':'no-store'})
        # Tras reconvertir, `part000003.ts` es otro trozo de película: nunca se cachea.
        try:return FragmentResponse(file)
        except FileNotFoundError:
            raise ProviderError('cast_replaced','Esta lista fue reemplazada por otro punto del video.',410) from None

    async def close(self):
        tasks=list(self.probes.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        for job in self.jobs.values():
            if job['process'].returncode is None:job['process'].terminate();await job['process'].wait()
        await asyncio.gather(*(j['monitor'] for j in self.jobs.values() if 'monitor' in j),return_exceptions=True)

    async def monitor(self,job,info):
        """Leer progreso real sin guardar comandos, enlaces privados ni salida del proveedor."""
        process=job['process']
        async for line in process.stdout:
            if line.startswith(b'out_time_us='):
                try:job['seconds']=max(0,int(line.split(b'=')[1])/1_000_000)
                except ValueError:pass
        await process.wait()
        try:duration=max(0.0,float(info.get('format',{}).get('duration',0))-job.get('offset',0)/1000)
        except (ValueError,TypeError):duration=0
        if not job.get('stopping') and (process.returncode or (duration>0 and job['seconds']<duration-5)):
            job['error']='conversion_interrupted'
            from app.observability import audit
            audit('conversion_failed',error_code='conversion_interrupted')

    async def stop(self,source_id):
        """Terminar FFprobe/FFmpeg del video abandonado, sin tocar otros teléfonos."""
        task=self.probes.pop(source_id,None)
        if task and not task.done():
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        self.offsets.pop(source_id,None)
        self.audio_selections.pop(source_id,None)
        await self.release(source_id)
        self.preparations.pop(source_id,None)
        # La inspección se conserva: describe el archivo, no la sesión, y volver a abrir
        # la misma edición no puede costar otra vez veinte segundos de FFprobe.

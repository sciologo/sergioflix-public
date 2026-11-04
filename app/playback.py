"""Fuentes temporales, sesiones de reproducción y entrega limitada al video elegido."""
import json
import mimetypes
import time
import re
import secrets
import hashlib
from urllib.parse import urljoin,urlsplit

import httpx
import anyio
from fastapi.responses import Response,StreamingResponse

from app.library import Library
from app.providers.stremio import PROVIDERS,ProviderError,classify_stream,public_dns
from app.stream_ranking import rank_catalog,runtime_seconds,original_audio_languages
from app.source_identity import movie_source_mismatch
from app.source_validation import declared_archive, archive_signature, archive_error
from app.torrents import parse_range

# Reconexiones permitidas cuando el CDN cierra antes de entregar su Content-Length.
RESUME_LIMIT=4
# Una medición de caudal solo vale si hubo suficientes bytes y suficiente tiempo:
# los primeros instantes de una conexión no representan su velocidad real.
SPEED_MIN_BYTES=3*1024*1024
SPEED_MIN_SECONDS=3.0


class ClosingStreamingResponse(StreamingResponse):
    """Liberar lectores al cortar el socket, incluso entre dos bloques de video."""
    def __init__(self,*args,control=None,source_id=None,closer=None,**kwargs):
        super().__init__(*args,**kwargs)
        self.control,self.source_id,self.closer=control,source_id,closer

    async def __call__(self,scope,receive,send):
        try:
            if self.control:
                async with self.control.operation(self.source_id):await super().__call__(scope,receive,send)
            else:await super().__call__(scope,receive,send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()
                # El primer bloque se leyó antes de devolver la respuesta. Incluso si no
                # llegó a iniciarse body(), ese lector tiene que cerrarse.
                if self.closer:await self.closer()


class Playback:
    def __init__(self,store,settings,engine,transport=None):
        self.store,self.settings,self.engine=store,settings,engine
        self.control=None
        # Solo las pruebas sustituyen el transporte; en producción se abre uno real por fuente.
        self.transport=transport

    def touch_viewer(self,source_id,request):
        client=getattr(request,'client',None)
        if self.control and client and client.host not in ('127.0.0.1','::1'):
            self.control.touch(source_id)

    def save_sources(self,content_id,video_id,streams,provider='subtitulos',health=50):
        """Guarda enlaces sueltos —hoy solo subtítulos— reutilizando la misma tabla.

        La etiqueta no es un proveedor de video: llamarla como uno hacía creer que
        MediaFusion seguía aportando fuentes cuando ya no estaba configurado.
        """
        return self.save_catalog(content_id,video_id,[{'provider':provider,'streams':streams,'health':health}])

    def save_catalog(self,content_id,video_id,groups,speeds=None,duration=0,original_languages=None,
                     provider_speeds=None):
        """Guarda las fuentes de todos los proveedores ya ordenadas por el ranking técnico.

        Las fuentes que este PC no puede abrir —un torrent sin pares o mayor que la caché—
        se descartan: ofrecerlas solo gasta la espera del usuario en un fallo seguro.
        Si ninguna queda en pie se conservan igualmente, para no dejar la pantalla vacía.

        Con `direct_only` solo se guarda lo de alojador: enlace HTTP que empieza al
        instante. Es una regla, no una lista de proveedores vetados, así que vale
        también para el que se añada mañana.
        """
        candidates=[]
        aceptados=('http',) if self.settings.direct_only else ('http','torrent')
        # La memoria solo altera el orden que ya produce el ranking; no elimina fuentes.
        # Así una edición castigada sigue disponible como último recurso.
        reputations=self.store.source_reputation(video_id)
        ranked=rank_catalog(groups,speeds,duration,original_languages,reputations,provider_speeds)
        directas=[row for row in ranked if classify_stream(row[0])['kind'] in aceptados]
        # El idioma no admite respaldo. Una fuente sin pares o demasiado grande se ofrece
        # igual cuando no hay nada mejor, porque puede que funcione; una doblada a un
        # idioma que en esta casa nadie entiende no se puede ver ni con suerte.
        directas=[row for row in directas if not row[1]['sin_audio_util'] and not row[1]['suspected_ad']
                  and row[1]['reputation'] not in ('audio_rejected','archive_rejected')
                  and (row[1]['provider']=='subtitulos' or not declared_archive(row[0]))]
        offered=[row for row in directas if row[1]['playable']] or directas
        with self.store.connect() as conn:
            conn.execute('DELETE FROM media_source WHERE expires_at<now()')
            for rank_position,(stream,analysis) in enumerate(offered[:40],1):
                info=classify_stream(stream)
                if info['kind'] not in aceptados: continue
                source_id=secrets.token_urlsafe(32)
                data=self.store.cipher.encrypt(json.dumps(stream).encode()).decode()
                # Instantánea técnica permitida, sin etiquetas/URLs del proveedor.
                snapshot={key:analysis[key] for key in ('score','transport','resolution','delivered',
                    'ranking_tier','margin','demand_mbps','measured_bps','speed_scope','transcode','reputation')}
                conn.execute('''INSERT INTO media_source
                    (id,content_id,video_id,encrypted_data,provider,rank_score,source_fingerprint,rank_position,ranking_snapshot)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)''',
                    (source_id,content_id,video_id,data,analysis['provider'],analysis['score'],analysis['fingerprint'],
                     rank_position,json.dumps(snapshot)))
                label=str(stream.get('description') or stream.get('title') or stream.get('name') or 'Fuente disponible')
                label=re.sub(r'https?://\S+','[enlace]',label)[:400]
                spec=PROVIDERS.get(analysis['provider'])
                candidates.append({'source_id':source_id,'fingerprint':analysis['fingerprint'],'label':label,'transport':info['kind'],
                    'seeders':analysis['seeders'],'provider':analysis['provider'],
                    'provider_name':spec.display if spec else analysis['provider'],
                    'score':analysis['score'],'rank_position':rank_position,'reasons':analysis['reasons'][:4],
                    'size_gb':analysis['size_gb'],'playable':analysis['playable'],'blockers':analysis['blockers'],
                    'measured_bps':analysis['measured_bps'],'margin':analysis['margin'],
                    'demand_mbps':analysis['demand_mbps'],'delivered':analysis['delivered'],
                    'speed_scope':analysis['speed_scope'],'ranking_tier':analysis['ranking_tier'],
                    'origin':analysis['origin'],'languages':analysis['languages'],
                    'spanish_verified':False,'cast_verified':False,
                    'reputation':analysis['reputation'],
                    'subtitle_edition_unavailable':bool(stream.get('subtitleEditionUnavailable'))})
        return candidates

    def source(self,source_id):
        with self.store.connect() as conn:
            row=conn.execute('''SELECT m.content_id,m.video_id,m.encrypted_data,m.provider,c.data,m.source_fingerprint,c.name,
                    r.audio_rejected_until>now(),m.rank_position,m.ranking_snapshot,r.archive_rejected_until>now(),
                    EXISTS(SELECT 1 FROM content other WHERE lower(other.name)=lower(c.name)
                        AND other.id<>c.id AND other.media_type=c.media_type)
                FROM media_source m LEFT JOIN content c ON c.id=m.content_id
                LEFT JOIN source_reputation r ON r.video_id=m.video_id AND r.source_fingerprint=m.source_fingerprint
                WHERE m.id=%s AND m.expires_at>now()''',(source_id,)).fetchone()
        if not row: raise ProviderError('source_expired','La fuente venció. Vuelve a abrir la ficha.',410)
        if row[7]:raise ProviderError('audio_language_mismatch',
            'Esta edición no contiene audio original ni español. Se intentará otra fuente.',422)
        expected=runtime_seconds(row[4])*1000 if row[4] else 0
        stream=json.loads(self.store.cipher.decrypt(row[2].encode()))
        # Protege también URLs anteriores al filtro y ediciones con URL opaca.
        if row[3]!='subtitulos' and (row[10] or declared_archive(stream)):
            raise archive_error()
        # También protege enlaces guardados antes de introducir el filtro del listado.
        if row[3]!='subtitulos' and movie_source_mismatch(stream,row[4]):
            raise ProviderError('content_mismatch',
                'La fuente corresponde a otro contenido. Se intentará otra edición.',422)
        return {'content_id':row[0],'video_id':row[1],
                'stream':stream,'provider':row[3],
                'expected_duration_ms':expected,'fingerprint':row[5],
                'rank_position':row[8],'ranking_snapshot':row[9],
                'original_languages':sorted(original_audio_languages(row[4])),
                # El buscador de subtítulos necesita el título, pero no la ficha completa.
                'content_name':row[6] or '',
                'subtitle_context':{key:(row[4] or {}).get(key) for key in ('name','country','releaseInfo')} |
                    {'ambiguous':bool(row[11]), 'episode_name':next((episode.get('name','')
                        for episode in (row[4] or {}).get('episodes',[]) if episode.get('id')==row[1]),'')}}

    def begin(self,source_id,actor):
        source=self.source(source_id)
        session=Library(self.store).session(source['content_id'],source['video_id'],actor)
        stream=source['stream']
        from app.media_tools import native_hls
        filename=str((stream.get('behaviorHints') or {}).get('filename',''))
        mime=('application/vnd.apple.mpegurl' if native_hls(stream)
              else mimetypes.guess_type(filename or urlsplit(str(stream.get('url',''))).path)[0] or 'video/mp4')
        return {**session,'video_id':source['video_id'],'source_id':source_id,'source_fingerprint':source['fingerprint'],
                'provider':source.get('provider','mediafusion'),
                'stream_url':f'http://{self.settings.lan_ip}:{self.settings.media_port}/media/{source_id}',
                'cast_url':f'http://{self.settings.lan_ip}:{self.settings.media_port}/cast/{source_id}/index.m3u8',
                'content_type':mime,'cast_verified':False}

    def next_video(self, source):
        """Solo el siguiente episodio real del catálogo, nunca un ID aportado por el cliente."""
        with self.store.connect() as conn:
            row = conn.execute('SELECT data FROM content WHERE id=%s AND media_type=\'series\'',
                               (source['content_id'],)).fetchone()
        episodes = (row[0] or {}).get('episodes', []) if row else []
        for index, episode in enumerate(episodes[:-1]):
            if episode.get('id') == source['video_id']:
                return episodes[index + 1].get('id')
        return None

    async def ready(self,source_id):
        """Comprobar datos reales antes de abrir el reproductor o reemplazar su sesión."""
        from starlette.concurrency import run_in_threadpool
        source=await run_in_threadpool(self.source,source_id)
        stream=source['stream']
        if stream.get('infoHash'):
            video=await self.engine.prepare(stream['infoHash'],stream.get('fileIdx'),stream.get('sources'),
                filename=(stream.get('behaviorHints') or {}).get('filename'),video_id=source['video_id'])
            import asyncio
            iterator=self.engine.chunks(video,0,min(2*1024*1024-1,video['size']-1))
            try:
                async with asyncio.timeout(20):
                    async for _ in iterator:pass
            except TimeoutError:raise ProviderError('slow_source','Esta fuente entrega datos demasiado lentamente. Prueba otra.',504) from None
            finally: await iterator.aclose()
            return {'content_type':mimetypes.guess_type(video['filename'])[0] or 'video/x-matroska'}
        return {}

    async def media(self,source_id,request):
        from starlette.concurrency import run_in_threadpool
        source=await run_in_threadpool(self.source,source_id)
        stream=source['stream']
        if stream.get('url'):
            return await self.http_media(stream,request,source_id,source)
        if stream.get('infoHash'):
            video=await self.engine.prepare(stream['infoHash'],stream.get('fileIdx'),stream.get('sources'),
                filename=(stream.get('behaviorHints') or {}).get('filename'),video_id=source['video_id'])
            start,end,status=parse_range(request.headers.get('range'),video['size'])
            headers={'Accept-Ranges':'bytes','Content-Length':str(end-start+1),'Access-Control-Allow-Origin':'*',
                     'Access-Control-Expose-Headers':'Content-Length,Content-Range,Accept-Ranges'}
            if status==206: headers['Content-Range']=f"bytes {start}-{end}/{video['size']}"
            mime=mimetypes.guess_type(video['filename'])[0] or 'application/octet-stream'
            if request.method=='HEAD': return Response(status_code=status,headers=headers,media_type=mime)
            # Esperar el primer bloque antes de enviar 200; el cliente puede mostrar un error real.
            iterator=self.engine.chunks(video,start,end)
            first=await anext(iterator)
            async def body():
                try:
                    self.touch_viewer(source_id,request)
                    yield first
                    async for chunk in iterator:
                        self.touch_viewer(source_id,request)
                        yield chunk
                finally:
                    # Cerrar el consumidor incluso si Android/FFprobe corta su conexión.
                    await iterator.aclose()
            return ClosingStreamingResponse(body(),status_code=status,headers=headers,media_type=mime,
                control=self.control,source_id=source_id,closer=iterator.aclose)
        raise ProviderError('unsupported_source','Esta fuente requiere otro tipo de reproductor.',422)

    async def remember_speed(self,host,reading,original,provider=None):
        """Guarda el caudal observado y a qué servidor llevó el enlace.

        Una lectura corta no dice nada de la velocidad y se descarta.
        """
        elapsed=time.monotonic()-reading['since']
        if not host or reading['bytes']<SPEED_MIN_BYTES or elapsed<SPEED_MIN_SECONDS:return
        from starlette.concurrency import run_in_threadpool
        def save():
            self.store.record_speed(host,int(reading['bytes']/elapsed),provider)
            self.store.record_route(original,host)
        try:await run_in_threadpool(save)
        except Exception:pass

    def child_source(self,parent_id,source,url):
        return self.child_sources(parent_id,source,[url])[url]

    def child_sources(self,parent_id,source,urls):
        """Registra de una vez todas las direcciones de una lista HLS.

        Una señal en directo rehace su lista cada pocos segundos. Guardarlas de una en una
        costaba una conexión a la base de datos por fragmento, y eso se nota al sintonizar.
        """
        pending,result={},{}
        origin=urlsplit(source['stream'].get('url','')).netloc
        for url in urls:
            if url in result:continue
            child=hashlib.sha256((parent_id+url).encode()).hexdigest()
            if self.control:self.control.children[child]=self.control.children.get(parent_id,parent_id)
            data={**source['stream'],'url':url}
            if origin!=urlsplit(url).netloc:
                hints=dict(data.get('behaviorHints') or {})
                proxy=dict(hints.get('proxyHeaders') or {})
                proxy['request']={key:value for key,value in (proxy.get('request') or {}).items() if key.lower()!='authorization'}
                hints['proxyHeaders']=proxy;data['behaviorHints']=hints
            pending[child]=self.store.cipher.encrypt(json.dumps(data).encode()).decode()
            result[url]=f'http://{self.settings.lan_ip}:{self.settings.media_port}/media/{child}'
        with self.store.connect() as conn:
            for child,encrypted in pending.items():
                conn.execute("""INSERT INTO media_source(id,content_id,video_id,encrypted_data,provider) VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT(id) DO UPDATE SET expires_at=now()+interval '12 hours'""",
                    (child,source['content_id'],source['video_id'],encrypted,source.get('provider','mediafusion')))
        return result

    async def http_media(self,stream,request,source_id,source):
        url=stream['url']
        def upstream_failure(code,status=None):
            # FFprobe solo ve la respuesta del proxy. La causa original permanece
            # en el intento compartido por audio, preparación y reproducción.
            if self.control:
                self.control.require(source_id)['upstream_failure']={'code':code,'status':status}
        headers={}
        # No transportar cookies de administración ni cabeceras arbitrarias.
        hints=stream.get('behaviorHints') or {}
        declared=(hints.get('proxyHeaders') or {}).get('request') or {}
        for key,value in declared.items():
            if key.lower() in ('user-agent','referer','origin','authorization') and isinstance(value,str) and '\n' not in value and '\r' not in value:
                headers[key]=value
        if request.headers.get('range'): headers['Range']=request.headers['range']
        client=httpx.AsyncClient(timeout=httpx.Timeout(90,connect=15),trust_env=False,follow_redirects=False,transport=self.transport)
        try:
            for attempt in range(5):
                parts=urlsplit(url)
                if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
                    raise ProviderError('invalid_source','El enlace de video no es válido.')
                await public_dns(parts.hostname)
                response=await client.send(client.build_request(request.method,url,headers=headers),stream=True)
                if response.status_code not in (301,302,303,307,308): break
                target=urljoin(url,response.headers.get('location',''))
                await response.aclose()
                if urlsplit(target).netloc!=parts.netloc:
                    headers={key:value for key,value in headers.items() if key.lower()!='authorization'}
                url=target
            if response.status_code not in (200,206):
                upstream_status=response.status_code
                await response.aclose()
                code=('upstream_rejected' if upstream_status in (401,403) else
                      'upstream_missing' if upstream_status in (404,410) else 'video_unavailable')
                error=ProviderError(code,'La fuente no entregó el video. Elige otra fuente.')
                error.upstream_status=upstream_status
                # FFprobe consume el proxy: conservar la causa real en el estado de
                # esta fuente evita confundir un 403 remoto con un archivo inválido.
                upstream_failure(code,upstream_status)
                raise error
            output={key:value for key,value in response.headers.items() if key.lower() in ('content-type','content-length','content-range','accept-ranges')}
            output['Access-Control-Allow-Origin']='*'
            output['Access-Control-Expose-Headers']='Content-Length,Content-Range,Accept-Ranges'
            if request.method=='HEAD':
                await response.aclose();await client.aclose()
                return Response(status_code=response.status_code,headers=output)
            mime=response.headers.get('content-type','').lower()
            if 'mpegurl' in mime or urlsplit(url).path.lower().endswith('.m3u8'):
                # Reescribir también claves y pistas: todas las URLs deben resolver desde Cast.
                from starlette.concurrency import run_in_threadpool
                chunks,size=[],0
                prefix=b''
                async for chunk in response.aiter_bytes():
                    # Un comprimido no se vuelve HLS por declarar ese MIME.
                    prefix+=chunk[:8-len(prefix)]
                    if archive_signature(prefix):
                        upstream_failure('archive_source')
                        await response.aclose()
                        raise archive_error()
                    size+=len(chunk)
                    if size>2_000_000: raise ProviderError('playlist_too_large','La lista de reproducción es demasiado grande.')
                    chunks.append(chunk)
                await response.aclose();await client.aclose()
                playlist=b''.join(chunks).decode('utf-8-sig')
                if not playlist.lstrip().startswith('#EXTM3U'): raise ProviderError('invalid_playlist','La lista HLS no es válida.')
                # Primero se reúnen todas las direcciones y se registran juntas: una lista
                # en directo trae varias por refresco y una a una se hacían notar.
                targets=[]
                for line in playlist.splitlines():
                    if line and not line.startswith('#'):targets.append(urljoin(url,line.strip()))
                    elif 'URI="' in line:
                        targets+=[urljoin(url,match.group(1)) for match in re.finditer(r'URI="([^"]+)"',line)]
                proxied=await run_in_threadpool(self.child_sources,source_id,source,targets) if targets else {}
                lines=[]
                for line in playlist.splitlines():
                    if line and not line.startswith('#'):
                        line=proxied.get(urljoin(url,line.strip()),line)
                    elif 'URI="' in line:
                        for match in list(re.finditer(r'URI="([^"]+)"',line)):
                            rewritten=proxied.get(urljoin(url,match.group(1)))
                            if rewritten:line=line.replace(match.group(0),'URI="'+rewritten+'"')
                    lines.append(line)
                return Response('\n'.join(lines)+'\n',media_type='application/vnd.apple.mpegurl',headers={'Access-Control-Allow-Origin':'*'})
            # Punto de partida real y bytes prometidos: permiten reanudar donde se cortó.
            begin=0
            span=response.headers.get('content-range','')
            match=re.match(r'bytes\s+(\d+)-',span)
            if match:begin=int(match.group(1))
            elif request.headers.get('range'):
                start=re.match(r'bytes=(\d+)-',request.headers['range'])
                if start:begin=int(start.group(1))
            try:promised=int(response.headers.get('content-length') or 0)
            except ValueError:promised=0
            iterator=response.aiter_raw()
            initial=[]
            if begin==0 or response.status_code==200:
                # Reutiliza los bytes ya recibidos: sin otra petición, sin descargar
                # el archivo completo y sin perder bytes al entregarlos al cliente.
                prefix=b''
                while len(prefix)<8:
                    chunk=await anext(iterator,None)
                    if chunk is None:break
                    initial.append(chunk);prefix+=chunk[:8-len(prefix)]
                if archive_signature(prefix):
                    upstream_failure('archive_source')
                    await response.aclose()
                    raise archive_error()
            async def buffered():
                for chunk in initial:yield chunk
                async for chunk in iterator:yield chunk
            state={'response':response}
            # Caudal real de este servidor: es lo que decide si una edición arranca
            # en segundos o en medio minuto, y no se puede adivinar de la etiqueta.
            reading={'bytes':0,'since':time.monotonic()}
            host=urlsplit(url).hostname or ''

            async def resume(sent):
                """Varios CDN cierran antes de entregar su propio Content-Length.

                Reabrir desde el byte alcanzado evita que el reproductor reciba una
                respuesta truncada, que es lo que aparece como «la fuente dejó de responder».
                """
                retry=dict(headers)
                retry['Range']=f'bytes={begin+sent}-'
                parts=urlsplit(url)
                await public_dns(parts.hostname)
                fresh=await client.send(client.build_request('GET',url,headers=retry),stream=True)
                if fresh.status_code not in (200,206):
                    code=('upstream_rejected' if fresh.status_code in (401,403) else
                          'upstream_missing' if fresh.status_code in (404,410) else 'video_unavailable')
                    upstream_failure(code,fresh.status_code)
                    await fresh.aclose();return None
                return fresh

            async def body():
                sent=0
                incoming=buffered()
                transfer_code=None
                try:
                    for attempt in range(RESUME_LIMIT+1):
                        try:
                            async for chunk in incoming:
                                sent+=len(chunk);reading['bytes']+=len(chunk)
                                self.touch_viewer(source_id,request)
                                yield chunk
                        except httpx.TimeoutException:
                            transfer_code='network_timeout'
                        except httpx.HTTPError:
                            transfer_code='network_error'
                        if not promised or sent>=promised or attempt==RESUME_LIMIT:break
                        await state['response'].aclose()
                        following=await resume(sent)
                        if following is None:break
                        state['response']=following
                        incoming=following.aiter_raw()
                    if promised and sent<promised:
                        # Cerrar sin completar la longitud declarada: el cliente reintenta el rango.
                        if not self.control or not self.control.require(source_id).get('upstream_failure'):
                            upstream_failure(transfer_code or 'video_truncated')
                        raise ProviderError('video_truncated','La fuente cortó la entrega del video.',502)
                finally:
                    await state['response'].aclose();await client.aclose()
            async def close_http():
                await state['response'].aclose();await client.aclose()
                await self.remember_speed(host,reading,stream.get('url',''),source.get('provider'))
            return ClosingStreamingResponse(body(),status_code=response.status_code,headers=output,
                control=self.control,source_id=source_id,closer=close_http)
        except ProviderError:
            await client.aclose();raise
        except httpx.TimeoutException:
            upstream_failure('network_timeout')
            await client.aclose()
            raise ProviderError('network_timeout','La fuente tardó demasiado en responder. Puedes probar otra.',504) from None
        except (httpx.HTTPError,ValueError):
            upstream_failure('network_error')
            await client.aclose()
            raise ProviderError('network_error','Se interrumpió la conexión con el video.',502) from None

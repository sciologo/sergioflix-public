"""Entrega HTTP por rangos usando libtorrent. Un torrent nunca se envía al Chromecast."""
import asyncio
import ipaddress
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import libtorrent as lt

from app.providers.stremio import ProviderError,public_dns
from app.settings import ROOT
from app.stream_ranking import MAX_CACHED_BYTES

# Espera máxima para obtener los metadatos de un magnet antes de darlo por muerto.
METADATA_TIMEOUT=40


def parse_range(header: str | None, size: int):
    if not header: return 0,size-1,200
    match=re.fullmatch(r"bytes=(\d*)-(\d*)",header)
    if not match or not any(match.groups()): raise ProviderError('invalid_range','Rango no válido.',416)
    left,right=match.groups()
    if not left:
        if int(right)==0: raise ProviderError('invalid_range','Rango no válido.',416)
        start,end=max(0,size-int(right)),size-1
    else: start,end=int(left),min(int(right),size-1) if right else size-1
    if start>=size or start>end: raise ProviderError('invalid_range','Rango fuera del archivo.',416)
    return start,end,206


def select_video(files,safe,declared=None,filename=None,video_id=None):
    """Recuperar índices obsoletos sin elegir por accidente otro episodio de un pack."""
    episode=re.fullmatch(r'tt\d+:(\d+):(\d+)',video_id or '')
    candidates=list(safe)
    if episode:
        season,number=map(int,episode.groups())
        pattern=re.compile(rf'(?i)(?:s0*{season}e0*{number}(?!\d)|(?<!\d)0*{season}x0*{number}(?!\d))')
        matches=[i for i in candidates if pattern.search(Path(files.file_path(i)).name)]
        if matches:candidates=matches
        elif len(candidates)>1 or any(re.search(r'(?i)(?:s\d+e\d+|\d+x\d+)',Path(files.file_path(i)).name) for i in candidates):
            raise ProviderError('episode_not_identified','No se pudo identificar este episodio dentro de la fuente. Prueba otra edición.')
    if filename:
        exact=[i for i in candidates if Path(files.file_path(i)).name.casefold()==Path(filename).name.casefold()]
        if exact:return exact[0]
    if isinstance(declared,int) and declared in candidates:return declared
    if len(candidates)==1:return candidates[0]
    if declared is None and not episode and candidates:return max(candidates,key=files.file_size)
    raise ProviderError('wrong_file','La fuente no identifica un archivo de video válido.')


def lan_interfaces(lan_ip):
    """Anunciar solo por la tarjeta de la LAN.

    Con 0.0.0.0 libtorrent también anuncia por las interfaces virtuales de Docker
    (172.17.x.x), que no encaminan a Internet: cada anuncio por ahí falla con
    «red no accesible» y desperdicia la mitad de los intentos de tracker.
    """
    try:
        address=ipaddress.ip_address(lan_ip)
    except ValueError:
        return '0.0.0.0:6881',''
    if address.is_loopback or not address.version==4:
        return '0.0.0.0:6881',''
    return f'{lan_ip}:6881',lan_ip


class TorrentEngine:
    def __init__(self,settings=None):
        self.root=(ROOT/'cache'/'torrents').resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        listen,outgoing=lan_interfaces(getattr(settings,'lan_ip','') or '')
        self.session=lt.session({'enable_dht':True,'enable_lsd':False,'enable_upnp':False,'enable_natpmp':False,
                                 'connections_limit':100,'active_downloads':2,'upload_rate_limit':256000,
                                 'announce_to_all_trackers':True,'announce_to_all_tiers':True,
                                 'listen_interfaces':listen,'outgoing_interfaces':outgoing,
                                 'alert_mask':int(lt.alert.category_t.storage_notification | lt.alert.category_t.error_notification)})
        self.handles={}
        self.last_used={}
        self.active={}
        self.pending={}
        self.pump=None
        self.lock=asyncio.Lock()
        self.read_limit=asyncio.Semaphore(8)

    async def alerts(self):
        while True:
            for alert in self.session.pop_alerts():
                if isinstance(alert,lt.read_piece_alert):
                    key=(str(alert.handle.info_hash()),alert.piece)
                    future=self.pending.pop(key,None)
                    if future and not future.done():
                        if alert.error.value(): future.set_exception(ProviderError('piece_error','No se pudo leer una parte del video.'))
                        else: future.set_result(bytes(alert.buffer))
            await asyncio.sleep(.05)

    async def prepare(self,info_hash,file_index=None,sources=None,filename=None,video_id=None):
        if not re.fullmatch(r'[0-9a-fA-F]{40}',info_hash): raise ProviderError('invalid_torrent','La fuente no tiene un identificador válido.',422)
        info_hash=info_hash.lower()
        async with self.lock:
            if not self.pump: self.pump=asyncio.create_task(self.alerts())
            if info_hash not in self.handles:
                from app.cache_cleanup import cleanup
                await asyncio.to_thread(cleanup,self.root,set(self.handles)|{info_hash})
                if len(self.handles)>=2:
                    idle=[key for key in self.handles if not self.active.get(key,0)]
                    if not idle: raise ProviderError('torrent_limit','La videoteca tiene dos fuentes ocupadas. Pulsa Detener y liberar, o detén la reproducción del otro teléfono.',409)
                    oldest=min(idle,key=lambda key:self.last_used.get(key,0))
                    self.session.remove_torrent(self.handles.pop(oldest))
                    self.last_used.pop(oldest,None)
                if shutil.disk_usage(self.root).free<20*1024**3: raise ProviderError('disk_full','Se necesitan al menos 20 GB libres para la caché de video.',507)
                path=(self.root/info_hash).resolve()
                if not path.is_relative_to(self.root): raise ProviderError('invalid_path','Ruta de caché no válida.')
                trackers=[]
                async def checked_tracker(source):
                    if not isinstance(source,str) or not source.startswith('tracker:'): return None
                    tracker=source[len('tracker:'):]
                    parts=urlsplit(tracker)
                    if parts.scheme not in ('udp','http','https') or not parts.hostname or parts.username: return None
                    try: await asyncio.wait_for(public_dns(parts.hostname),3)
                    except (ProviderError,TimeoutError): return None
                    return '&tr='+quote(tracker,safe='')
                trackers=[t for t in await asyncio.gather(*(checked_tracker(s) for s in (sources or [])[:60])) if t]
                params=lt.parse_magnet_uri('magnet:?xt=urn:btih:'+info_hash+''.join(trackers))
                params.save_path=str(path)
                cached=path/'.metadata.torrent'
                if cached.is_file() and cached.stat().st_size<4_000_000:
                    try:
                        info=lt.torrent_info(cached.read_bytes())
                        if str(info.info_hash())==info_hash: params.ti=info
                    except (RuntimeError,OSError): pass
                params.flags|=lt.torrent_flags.default_dont_download
                params.flags&=~lt.torrent_flags.auto_managed
                params.flags&=~lt.torrent_flags.paused
                handle=self.session.add_torrent(params)
                self.handles[info_hash]=handle
            handle=self.handles[info_hash]
            self.last_used[info_hash]=time.monotonic()
            self.active[info_hash]=self.active.get(info_hash,0)+1
        try:
            # Un enjambre lento puede tardar; por debajo de 40 s se descartaban fuentes vivas.
            async with asyncio.timeout(METADATA_TIMEOUT):
                while not handle.status().has_metadata: await asyncio.sleep(.25)
        except TimeoutError:
            raise ProviderError('torrent_metadata_timeout','No hay suficientes pares para iniciar esta fuente. Prueba otra.',504) from None
        finally:
            if self.handles.get(info_hash) is handle:self.active[info_hash]=max(0,self.active.get(info_hash,1)-1)
        info=handle.torrent_file()
        path=self.root/info_hash
        path.mkdir(parents=True,exist_ok=True)
        cached=path/'.metadata.torrent'
        if not cached.exists(): cached.write_bytes(lt.bencode(lt.create_torrent(info).generate()))
        files=info.files()
        safe=[]
        for index in range(files.num_files()):
            path=(self.root/info_hash/files.file_path(index)).resolve()
            if not path.is_relative_to(self.root/info_hash) or path.suffix.lower() not in ('.mp4','.mkv','.webm','.avi','.m4v','.ts','.m2ts'):
                continue
            if files.file_flags(index)&lt.file_storage.flag_symlink: continue
            safe.append(index)
        if not safe: raise ProviderError('no_video_file','No se encontró un archivo de video en esta fuente.')
        file_index=select_video(files,safe,file_index,filename,video_id)
        size=files.file_size(file_index)
        if size>MAX_CACHED_BYTES: raise ProviderError('file_too_large','Esta edición excede el límite inicial de caché de 12 GB.',422)
        # Si otro consumidor lee el mismo pack, no desactivar su episodio.
        priorities=list(handle.file_priorities()) if self.active.get(info_hash,0)>0 else [0]*files.num_files()
        priorities[file_index]=4
        handle.prioritize_files(priorities)
        handle.set_flags(lt.torrent_flags.sequential_download)
        return {'handle':handle,'index':file_index,'size':size,'offset':files.file_offset(file_index),
                'piece_length':info.piece_length(),'filename':Path(files.file_path(file_index)).name,'hash':info_hash}

    async def piece(self,video,index):
        key=(video['hash'],index)
        async with self.read_limit:
            future=self.pending.get(key)
            if future is None:
                future=asyncio.get_running_loop().create_future()
                self.pending[key]=future
                video['handle'].set_piece_deadline(index,0,lt.deadline_flags_t.alert_when_available)
            try: return await asyncio.wait_for(asyncio.shield(future),20)
            except TimeoutError:
                self.pending.pop(key,None)
                raise ProviderError('torrent_buffer_timeout','La fuente dejó de entregar datos. Prueba otra.',504) from None

    async def chunks(self,video,start,end):
        current=video['offset']+start
        absolute_end=video['offset']+end
        length=video['piece_length']
        key=video['hash']
        self.active[key]=self.active.get(key,0)+1
        try:
            while current<=absolute_end:
                self.last_used[key]=time.monotonic()
                index=current//length
                data=await self.piece(video,index)
                offset=current%length
                amount=min(len(data)-offset,absolute_end-current+1)
                if amount<=0: raise ProviderError('invalid_piece','Una parte del video no es válida.')
                yield data[offset:offset+amount]
                current+=amount
        finally:
            if self.handles.get(key) is video['handle']:self.active[key]=max(0,self.active.get(key,1)-1)

    async def close(self):
        if self.pump:
            self.pump.cancel()
            try: await self.pump
            except asyncio.CancelledError: pass
        self.session.pause()

    async def stop(self,info_hash):
        """Se invoca después de cerrar los lectores: conservar archivos, liberar recursos."""
        async with self.lock:
            handle=self.handles.pop(info_hash,None)
            if handle and handle.is_valid():self.session.remove_torrent(handle)
            self.last_used.pop(info_hash,None)
            self.active.pop(info_hash,None)
            for key in [k for k in self.pending if k[0]==info_hash]:
                future=self.pending.pop(key)
                if not future.done():future.cancel()

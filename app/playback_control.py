"""Una reproducción activa por teléfono; cancelar libera tareas, conversión y torrent."""
import asyncio
import time
from contextlib import asynccontextmanager
from app.providers.stremio import ProviderError

# Un intento abandonado se suelta pronto; algo que ya llegó a reproducirse aguanta una
# pausa larga: ni el televisor detenido ni un teléfono en segundo plano piden fragmentos,
# y eso no significa que la sesión deba morir.
# Diez minutos para lo que aún se prepara: contestar un mensaje mientras se convierte el
# video es normal, y al volver hay que encontrar el trabajo hecho, no empezar de cero.
IDLE_TIMEOUT=600
STREAMING_TIMEOUT=7200
# El trabajo que nadie pidió se suelta antes: una ficha que se miró y se dejó no puede
# tener el equipo convirtiendo diez minutos. Lo que sí se mandó preparar aguanta más.
WARM_TIMEOUT=180
LIVE_STAGES=('streaming','ready')


class PlaybackControl:
    def __init__(self,engine,media_tools):
        self.engine,self.media_tools=engine,media_tools
        self.sources={}
        self.children={}
        self.actors={}
        # Un cierre puede adelantar por milisegundos a la petición de inicio que cancela.
        # Recordar esa fuente impide que la petición atrasada nazca después de su cierre.
        self.revoked={}
        self.lock=asyncio.Lock()

    def revoke(self,actor,source_id):
        now=time.monotonic()
        self.revoked={(owner,key):at for (owner,key),at in self.revoked.items()
                      if now-at<IDLE_TIMEOUT}
        if source_id:self.revoked[(actor,source_id)]=now

    def require(self,source_id):
        state=self.sources.get(self.children.get(source_id,source_id))
        if not state:raise ProviderError('playback_stopped','Esta reproducción terminó. Vuelve a elegir una fuente.',410)
        return state

    def touch(self,source_id):self.require(source_id)['access']=time.monotonic()

    @asynccontextmanager
    async def operation(self,source_id):
        state=self.require(source_id);task=asyncio.current_task()
        state['tasks'].add(task)
        try:yield state
        finally:state['tasks'].discard(task)

    async def activate(self,actor,source_id,source,warm=False):
        async with self.lock:
            self.revoke(actor,None)
            if (actor,source_id) in self.revoked:
                raise ProviderError('playback_stopped','Esta reproducción ya fue cerrada.',409)
            existing=self.sources.get(source_id)
            if existing and existing['actor']!=actor:raise ProviderError('unauthorized','Esta fuente pertenece a otro teléfono.',403)
            # Volver a activar lo mismo conserva el trabajo ya hecho: es lo que permite
            # que una conversión adelantada siga sirviendo cuando llega la reproducción.
            if self.actors.get(actor)==source_id and existing:
                existing['access']=time.monotonic()
                # Al reproducirlo de verdad deja de ser trabajo adelantado y desechable.
                if not warm:existing['warm']=False
                return
            # Adelantar trabajo nunca pisa una reproducción viva: abrir una ficha para leer
            # la sinopsis no puede cortar lo que se está viendo en el teléfono o en la TV.
            running=self.sources.get(self.actors.get(actor))
            if warm and running and not running.get('warm'):
                raise ProviderError('playback_busy','Ya hay una reproducción en marcha.',409)
            # Un nuevo intento sustituye todos los trabajos anteriores de este teléfono.
            await self._stop(actor)
            self.sources[source_id]={'actor':actor,'hash':source['stream'].get('infoHash','').lower(),
                'content_id':source['content_id'],'video_id':source['video_id'],
                'expected_duration_ms':source.get('expected_duration_ms',0),
                'fingerprint':source.get('fingerprint'),
                'provider':source.get('provider','mediafusion'),'warm':warm,
                'tasks':set(),'access':time.monotonic(),'stage':'preparing'}
            self.actors[actor]=source_id

    async def _stop(self,actor):
        source_id=self.actors.pop(actor,None)
        state=self.sources.pop(source_id,None)
        if not state:return False
        self.revoke(actor,source_id)
        self.children={child:parent for child,parent in self.children.items() if parent!=source_id}
        # Revocar primero: un receptor con una URL vieja no puede volver a abrirla.
        tasks=[t for t in state['tasks'] if t is not asyncio.current_task() and not t.done()]
        for task in tasks:task.cancel()
        await self.media_tools.stop(source_id)
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        info_hash=state['hash']
        if info_hash and not any(s['hash']==info_hash for s in self.sources.values()):
            await self.engine.stop(info_hash)
        return True

    async def stop(self,actor,source_id=None):
        async with self.lock:
            # Un error atrasado de una fuente no debe detener la que acaba de sustituirla.
            if source_id is not None and self.actors.get(actor)!=source_id:
                self.revoke(actor,source_id)
                return False
            return await self._stop(actor)

    def status(self,actor):
        state=self.sources.get(self.actors.get(actor))
        if not state:return {'active':False,'stage':'idle'}
        state['access']=time.monotonic()
        result={'active':True,'stage':state['stage'],'content_id':state['content_id'],'video_id':state['video_id'],
            'provider':state.get('provider','mediafusion'),'source_id':self.actors.get(actor)}
        if state.get('attempt_id'):
            result.update(attempt_id=state['attempt_id'],playback_id=state.get('playback_id'))
        handle=self.engine.handles.get(state['hash'])
        if handle and handle.is_valid():
            status=handle.status()
            result.update(peers=status.num_peers,download_bytes_per_second=status.download_rate)
        return result

    def expiry(self,state):
        """Cuánto aguanta una sesión sin que nadie pida nada, según lo que sea."""
        if state['stage'] in LIVE_STAGES:return STREAMING_TIMEOUT
        return WARM_TIMEOUT if state.get('warm') else IDLE_TIMEOUT

    async def reap(self):
        while True:
            await asyncio.sleep(15)
            for actor,source_id in list(self.actors.items()):
                state=self.sources.get(source_id)
                # Una pausa conserva la sesión; los intentos abandonados vencen antes.
                if state and time.monotonic()-state['access']>self.expiry(state):
                    await self.stop(actor,source_id)

    async def close(self):
        for actor in list(self.actors):await self.stop(actor)

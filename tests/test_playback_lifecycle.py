"""La prueba clave: cambiar y detener repetidamente no acumula trabajos ni bloquea otros teléfonos."""
import asyncio
from types import SimpleNamespace
import pytest
from app.playback_control import PlaybackControl
from app.providers.mediafusion import ProviderError


class Engine:
    def __init__(self):self.handles={};self.stopped=[]
    async def stop(self,key):self.stopped.append(key);self.handles.pop(key,None)


class Media:
    def __init__(self):self.stopped=[]
    async def stop(self,key):self.stopped.append(key)


def source(key='hash'):
    return {'stream':{'infoHash':key},'content_id':'tt123','video_id':'tt123:1:1'}


def test_repeated_replacement_cancels_readers_and_revokes_old_urls():
    async def scenario():
        engine,media=Engine(),Media();control=PlaybackControl(engine,media)
        closed=[]
        for index in range(8):
            name=str(index);await control.activate('phone',name,source(name))
            ready=asyncio.Event()
            async def reader(current=name):
                async with control.operation(current):
                    try:ready.set();await asyncio.Event().wait()
                    finally:closed.append(current)
            asyncio.create_task(reader());await ready.wait()
            if index:
                with pytest.raises(ProviderError):control.require(str(index-1))
                assert closed==[str(i) for i in range(index)]
        assert await control.stop('phone')
        assert closed==list(map(str,range(8)))
        assert len(media.stopped)==len(engine.stopped)==8
        assert control.status('phone')=={'active':False,'stage':'idle'}
        assert not await control.stop('phone')
    asyncio.run(scenario())


def test_late_stop_and_other_phone_cannot_interrupt_new_source():
    async def scenario():
        control=PlaybackControl(Engine(),Media())
        await control.activate('a','old',source())
        await control.activate('a','new',source())
        await control.activate('b','other',source('other'))
        assert not await control.stop('a','old')
        with pytest.raises(ProviderError):await control.activate('b','new',source())
        await control.stop('a')
        assert control.status('b')['active']
        await control.close()
    asyncio.run(scenario())


def test_a_stop_that_arrives_before_start_prevents_the_abandoned_source_from_being_born():
    """Las peticiones HTTP pueden cruzarse: cerrar primero también debe ganar."""
    async def scenario():
        control=PlaybackControl(Engine(),Media())
        assert not await control.stop('tv','future')
        with pytest.raises(ProviderError) as error:
            await control.activate('tv','future',source('future'))
        assert error.value.code=='playback_stopped'
        await control.activate('tv','new',source('new'))
        assert control.status('tv')['source_id']=='new'
    asyncio.run(scenario())


def test_shared_torrent_is_retained_until_last_phone_stops():
    async def scenario():
        engine=Engine();control=PlaybackControl(engine,Media())
        await control.activate('a','one',source())
        await control.activate('b','two',source())
        await control.stop('a');assert engine.stopped==[]
        await control.stop('b');assert engine.stopped==['hash']
    asyncio.run(scenario())


def test_cancellation_before_response_headers_closes_primed_iterator():
    from app.playback import ClosingStreamingResponse
    from starlette.requests import ClientDisconnect
    async def scenario():
        released=[]
        async def reader():
            try:yield b'first';yield b'second'
            finally:released.append(True)
        iterator=reader();first=await anext(iterator)
        async def body():
            try:
                yield first
                async for block in iterator:yield block
            finally:await iterator.aclose()
        async def send(_):raise OSError('closed before headers')
        async def receive():return {'type':'http.request','body':b''}
        response=ClosingStreamingResponse(body(),closer=iterator.aclose)
        with pytest.raises(ClientDisconnect):await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
        assert released==[True]
    asyncio.run(scenario())


def test_stop_reaps_converter_and_probe_processes():
    from app.media_tools import MediaTools
    class Process:
        returncode=None
        def terminate(self):self.returncode=-1
        async def wait(self):return self.returncode
    async def scenario():
        media=MediaTools(SimpleNamespace());process=Process()
        media.jobs['test']={'process':process,'access':0}
        media.probes['test']=asyncio.create_task(asyncio.sleep(100))
        await media.stop('test')
        assert process.returncode==-1 and not media.jobs and not media.probes
    asyncio.run(scenario())


def test_fallback_across_five_providers_leaves_no_active_resource():
    """El fallback automático prueba varias fuentes y ninguna queda ocupando el PC."""
    async def scenario():
        engine,media=Engine(),Media();control=PlaybackControl(engine,media)
        chain=[('penguplay','p1'),('penguplay','p2'),('flixstreams','f1'),('flixstreams','f2'),('mediafusion','m1')]
        for provider,name in chain:
            await control.activate('phone',name,{**source(name),'provider':provider})
            assert control.status('phone')['provider']==provider
        # Cada intento sustituyó al anterior: solo la última fuente sigue viva.
        assert list(control.sources)==['m1']
        assert media.stopped==[name for _,name in chain[:-1]]
        assert engine.stopped==[name for _,name in chain[:-1]]
        assert await control.stop('phone','m1')
        assert not control.sources and not control.actors
        assert engine.handles=={} and control.status('phone')=={'active':False,'stage':'idle'}
    asyncio.run(scenario())


def test_a_paused_broadcast_survives_a_long_wait_but_an_abandoned_attempt_does_not():
    """Con la TV en pausa nadie pide fragmentos: la sesión no puede caducar en minutos."""
    import time as clock
    from app.playback_control import IDLE_TIMEOUT,LIVE_STAGES,STREAMING_TIMEOUT
    # Lo que aún se prepara aguanta unos minutos en segundo plano —contestar un mensaje
    # no puede costar la conversión— pero media hora abandonada sí caduca.
    assert STREAMING_TIMEOUT>=2*3600 and 300<=IDLE_TIMEOUT<=900
    # Una reproducción pausada en segundo plano tampoco pide fragmentos.
    assert 'streaming' in LIVE_STAGES and 'ready' in LIVE_STAGES
    async def scenario():
        control=PlaybackControl(Engine(),Media())
        await control.activate('phone','emitiendo',source('emitiendo'))
        await control.activate('otro','intento',source('intento'))
        control.sources['emitiendo']['stage']='streaming'
        # Ambas llevan media hora sin que nadie pida un fragmento.
        for state in control.sources.values():state['access']=clock.monotonic()-1800
        reaper=asyncio.create_task(control.reap())
        await asyncio.sleep(0)
        for actor,source_id in list(control.actors.items()):
            state=control.sources.get(source_id)
            timeout=STREAMING_TIMEOUT if state and state['stage'] in LIVE_STAGES else IDLE_TIMEOUT
            if state and clock.monotonic()-state['access']>timeout:await control.stop(actor,source_id)
        reaper.cancel();await asyncio.gather(reaper,return_exceptions=True)
        assert 'emitiendo' in control.sources and 'intento' not in control.sources
    asyncio.run(scenario())


def test_reactivating_the_same_source_keeps_the_work_already_done():
    """El precalentado solo sirve si reproducir después no lo tira a la basura."""
    async def scenario():
        engine,media=Engine(),Media();control=PlaybackControl(engine,media)
        await control.activate('phone','uno',source('uno'))
        control.sources['uno']['stage']='ready'
        marker=control.sources['uno']
        # La reproducción real llega con la misma fuente ya calentada.
        await control.activate('phone','uno',source('uno'))
        assert control.sources['uno'] is marker, 'no debe recrearse el estado'
        assert control.sources['uno']['stage']=='ready' and media.stopped==[]
        # Cambiar de fuente sí libera la anterior.
        await control.activate('phone','dos',source('dos'))
        assert media.stopped==['uno'] and list(control.sources)==['dos']
    asyncio.run(scenario())


def test_warm_work_never_replaces_a_live_playback():
    """Abrir una ficha para leer la sinopsis no puede cortar lo que se está viendo."""
    async def scenario():
        engine,media=Engine(),Media();control=PlaybackControl(engine,media)
        await control.activate('phone','viendo',source('viendo'))
        with pytest.raises(ProviderError) as error:
            await control.activate('phone','adelanto',source('adelanto'),warm=True)
        assert error.value.code=='playback_busy'
        assert control.require('viendo')['stage']=='preparing'
        assert media.stopped==[] and engine.stopped==[], 'no se toca nada de la sesión viva'
        assert control.status('phone')['source_id']=='viendo'
    asyncio.run(scenario())


def test_one_warm_gives_way_to_the_next_and_stops_being_warm_when_played():
    """Entre adelantos sí se cede el sitio; en cuanto se reproduce, deja de ser desechable."""
    async def scenario():
        engine,media=Engine(),Media();control=PlaybackControl(engine,media)
        await control.activate('phone','ficha-a',source('a'),warm=True)
        await control.activate('phone','ficha-b',source('b'),warm=True)
        assert media.stopped==['ficha-a']
        await control.activate('phone','ficha-b',source('b'))
        assert media.stopped==['ficha-a'], 'reproducir lo adelantado conserva su trabajo'
        with pytest.raises(ProviderError):
            await control.activate('phone','ficha-c',source('c'),warm=True)
        assert control.require('ficha-b')
    asyncio.run(scenario())


def test_unrequested_work_is_released_sooner_than_what_the_viewer_asked_for():
    """Una ficha que se miró y se dejó no puede tener el equipo convirtiendo diez minutos,
    pero lo que sí se mandó preparar aguanta una pausa larga."""
    from app.playback_control import IDLE_TIMEOUT,STREAMING_TIMEOUT,WARM_TIMEOUT
    async def scenario():
        control=PlaybackControl(Engine(),Media())
        await control.activate('phone','adelanto',source('adelanto'),warm=True)
        assert control.expiry(control.sources['adelanto'])==WARM_TIMEOUT
        # Pulsar Reproducir sobre lo adelantado le da el margen de una preparación normal.
        await control.activate('phone','adelanto',source('adelanto'))
        assert control.expiry(control.sources['adelanto'])==IDLE_TIMEOUT
        control.sources['adelanto']['stage']='streaming'
        assert control.expiry(control.sources['adelanto'])==STREAMING_TIMEOUT
        assert WARM_TIMEOUT<IDLE_TIMEOUT<STREAMING_TIMEOUT
    asyncio.run(scenario())

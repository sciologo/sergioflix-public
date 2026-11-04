"""Regresiones de los bloqueos detectados al reproducir desde Android y Chromecast."""
import asyncio
from types import SimpleNamespace
import pytest
from app.torrents import select_video
from app.playback import Playback
from app.media_tools import MediaTools, bounded_lock, cast_compatible, cast_video_compatible
from app.providers.mediafusion import ProviderError


class Files:
    def __init__(self,*names):self.names=names
    def file_path(self,index):return self.names[index]
    def file_size(self,index):return 100


def test_stale_provider_index_finds_only_video():
    files=Files('readme.txt','Silo.S01E01.mkv')
    assert select_video(files,[1],0,video_id='tt14688458:1:1')==1


def test_pack_uses_requested_episode_instead_of_wrong_index():
    files=Files('Silo.S01E02.mkv','Silo.S01E01.mkv','Silo.S01E10.mkv')
    assert select_video(files,[0,1,2],0,video_id='tt14688458:1:1')==1


@pytest.mark.parametrize('names',[('Silo.S01E10.mkv',),('Silo.S02E01.mkv',),('Silo.S01E02.mkv','Silo.S01E03.mkv')])
def test_never_substitutes_another_episode(names):
    with pytest.raises(ProviderError) as error:select_video(Files(*names),list(range(len(names))),0,video_id='tt14688458:1:1')
    assert error.value.code=='episode_not_identified'


def test_disconnect_releases_torrent_consumer():
    class Engine:
        active=0
        async def prepare(self,*args,**kwargs):return {'filename':'test.mp4','size':8}
        async def chunks(self,*args):
            self.active+=1
            try:
                yield b'1234'
                yield b'5678'
            finally:self.active-=1
    async def scenario():
        engine=Engine();playback=Playback(None,None,engine)
        playback.source=lambda _: {'video_id':'tt123','stream':{'infoHash':'a'*40}}
        response=await playback.media('test',SimpleNamespace(headers={},method='GET'))
        assert await anext(response.body_iterator)==b'1234'
        assert engine.active==1
        await response.body_iterator.aclose()
        assert engine.active==0
    asyncio.run(scenario())


def test_failed_producer_keeps_complete_fragments_but_rejects_missing_and_old_generation(tmp_path):
    async def scenario():
        media=MediaTools(SimpleNamespace(media_port=8765));media.root=tmp_path
        source='buffered-producer-regression';folder=tmp_path/source;folder.mkdir()
        (folder/'generation').write_text('current')
        (folder/'offset').write_text('60000')
        (folder/'part000000.ts').write_bytes(b'complete-video')
        (folder/'part000001.ts.tmp').write_bytes(b'partial-video')
        (folder/'index.m3u8').write_text('#EXTM3U\n#EXTINF:4,\npart000000.ts\n')
        media.jobs[source]={'process':SimpleNamespace(returncode=1),'error':'conversion_interrupted','access':0}
        response=await media.segment(source,'part000000.ts',version='60000-current')
        assert response.body==b'complete-video'
        playlist=await media.segment(source,'index.m3u8',version='60000-current')
        assert b'part000000.ts' in playlist.body and b'ENDLIST' not in playlist.body
        for filename,version,code in [('part000001.ts','60000-current','cast_conversion_failed'),
                                      ('part000000.ts','old','cast_replaced')]:
            with pytest.raises(ProviderError) as error:await media.segment(source,filename,version=version)
            assert error.value.code==code
        assert (folder/'part000001.ts.tmp').read_bytes()==b'partial-video'
    asyncio.run(scenario())


def test_concurrent_inspection_is_shared_and_survives_one_cancelled_client():
    async def scenario():
        media=MediaTools(SimpleNamespace());calls=[];started=asyncio.Event();release=asyncio.Event()
        async def probe(_):
            calls.append(1);started.set();await release.wait();return {'streams':[]}
        media._inspect=probe
        first=asyncio.create_task(media.inspect('same'))
        await started.wait()
        second=asyncio.create_task(media.inspect('same'))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        release.set()
        assert await second=={'streams':[]} and len(calls)==1
        await media.close()
    asyncio.run(scenario())


def test_socket_failure_closes_stream_without_waiting_for_garbage_collection():
    from app.playback import ClosingStreamingResponse
    from starlette.requests import ClientDisconnect
    async def scenario():
        released=[]
        async def chunks():
            try:
                yield b'first'
                yield b'second'
            finally:released.append(True)
        async def receive():return {'type':'http.request','body':b''}
        async def send(message):
            if message['type']=='http.response.body':raise OSError('socket closed')
        response=ClosingStreamingResponse(chunks())
        with pytest.raises(ClientDisconnect):await response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send)
        assert released==[True]
    asyncio.run(scenario())


def test_busy_conversion_returns_retryable_error_instead_of_unbounded_queue():
    async def scenario():
        lock=asyncio.Lock();await lock.acquire()
        try:
            with pytest.raises(ProviderError) as error:
                async with bounded_lock(lock):pytest.fail('The occupied lock was acquired')
            assert error.value.status==409 and error.value.code=='cast_busy'
            assert lock.locked()
        finally:lock.release()
    asyncio.run(scenario())


def test_audio_conversion_does_not_require_transcoding_compatible_video():
    info={'streams':[{'codec_type':'video','codec_name':'h264','width':1920,'height':1080,'level':41,'avg_frame_rate':'24/1','pix_fmt':'yuv420p'},
                     {'codec_type':'audio','codec_name':'ac3'}]}
    assert cast_video_compatible(info) and not cast_compatible(info)
    info['streams'][0]['pix_fmt']='yuv420p10le'
    assert not cast_video_compatible(info)


def test_playlist_response_is_snapshot(tmp_path):
    async def scenario():
        media=MediaTools(SimpleNamespace(media_port=8765));media.root=tmp_path
        source='a'*32;folder=tmp_path/source;folder.mkdir()
        playlist=folder/'index.m3u8'
        original=b'#EXTM3U\n#EXTINF:4,\npart000000.ts\n#EXT-X-ENDLIST\n'
        playlist.write_bytes(original);(folder/'part000000.ts').write_bytes(b'segment')
        response=media.file_response(playlist)
        playlist.write_bytes(original+b'\n')
        expected=original.replace(b'part000000.ts',b'part000000.ts?v=0')
        assert response.body==expected and int(response.headers['content-length'])==len(expected)
        assert media.file_response(folder/'part000000.ts').path==folder/'part000000.ts'
        assert not media.jobs
        await media.close()
    asyncio.run(scenario())

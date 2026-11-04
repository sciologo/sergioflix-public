"""Los destinos y las respuestas deben describir el mismo material disponible."""
import asyncio
from types import SimpleNamespace

import pytest

from app.media_tools import MediaTools
from app.providers.stremio import ProviderError

SOURCE = 'seek-regression-source-001'
INFO = {'streams': [
    {'codec_type': 'video', 'codec_name': 'hevc'},
    {'codec_type': 'audio', 'codec_name': 'aac', 'index': 1, 'tags': {'language': 'spa'}},
    {'codec_type': 'audio', 'codec_name': 'aac', 'index': 2, 'tags': {'language': 'eng'}}],
    'format': {'format_name': 'matroska', 'duration': '7200'}}


def media(root):
    tools = MediaTools(SimpleNamespace(lan_ip='127.0.0.1', media_port=8765))
    tools.root = root
    async def inspect(_): return INFO
    async def segment(key, _):
        folder = root / key
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'offset').write_text(str(tools.offsets[key]))
        (folder / 'conversion').write_text(tools.conversion_identity(tools.audio_selections.get(key)))
        (folder / 'index.m3u8').write_text('#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n' + ''.join(
            '#EXTINF:4.0,\npart%06d.ts\n' % i for i in range(10)))
    tools.inspect, tools.segment = inspect, segment
    return tools


@pytest.mark.parametrize('back_ms', [5000, 10000, 30000, 120000])
def test_rewind_after_resume_contains_the_requested_scene(tmp_path, back_ms):
    async def scenario():
        tools = media(tmp_path)
        await tools.cast_path(SOURCE, 1800000, force_adapted=True)
        target = 1800000 - back_ms
        result = await tools.cast_path(SOURCE, target, force_adapted=True)
        assert result['offset_ms'] == target
        assert tools.started_at(SOURCE) == target
    asyncio.run(scenario())


def test_backward_seek_inside_material_reuses_it_and_preserves_audio(tmp_path):
    async def scenario():
        tools = media(tmp_path)
        await tools.cast_path(SOURCE, 1800000, force_adapted=True, audio_stream_index=2)
        ahead = await tools.cast_path(SOURCE, 1820000, force_adapted=True)
        back = await tools.cast_path(SOURCE, 1808000, force_adapted=True)
        assert ahead['offset_ms'] == 1820000 and back['offset_ms'] == 1808000
        assert tools.started_at(SOURCE) == 1800000
        assert ahead['audio_stream_index'] == back['audio_stream_index'] == 2
    asyncio.run(scenario())


def test_preparations_of_the_same_source_do_not_overwrite_each_other(tmp_path):
    async def scenario():
        tools = media(tmp_path)
        original = tools.segment
        waiting, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def prepare(key, name):
            calls.append(tools.offsets[key])
            await original(key, name)
            if len(calls) == 1:
                waiting.set()
                await release.wait()
        tools.segment = prepare
        first = asyncio.create_task(tools.cast_path(SOURCE, 1800000, force_adapted=True))
        await waiting.wait()
        second = asyncio.create_task(tools.cast_path(SOURCE, 1700000, force_adapted=True))
        await asyncio.sleep(0)
        assert not second.done() and calls == [1800000]
        assert tools.started_at(SOURCE) == 1800000
        release.set()
        old, new = await asyncio.gather(first, second)
        assert old['offset_ms'] == 1800000 and new['offset_ms'] == 1700000
        assert tools.started_at(SOURCE) == 1700000
    asyncio.run(scenario())


def test_retired_fragment_is_rejected_instead_of_serving_new_video(tmp_path):
    async def scenario():
        tools = MediaTools(SimpleNamespace(media_port=8765))
        tools.root = tmp_path
        folder = tmp_path / SOURCE
        folder.mkdir()
        (folder / 'offset').write_text('1700000')
        (folder / 'conversion').write_text(tools.conversion_identity(None))
        (folder / 'part000000.ts').write_bytes(b'NEW VIDEO')
        with pytest.raises(ProviderError) as error:
            await tools.segment(SOURCE, 'part000000.ts', version='1800000')
        assert error.value.code == 'cast_replaced'
        current = await tools.segment(SOURCE, 'part000000.ts', version='1700000')
        assert current.path == folder / 'part000000.ts'
    asyncio.run(scenario())


def test_rebuild_of_the_same_minute_uses_distinct_fragment_urls(tmp_path):
    folder = tmp_path / SOURCE
    folder.mkdir()
    (folder / 'offset').write_text('1800000')
    (folder / 'conversion').write_text('audio-v1:2')
    (folder / 'generation').write_text('aaaa1111')
    playlist = folder / 'index.m3u8'
    playlist.write_text('#EXTM3U\n#EXTINF:4.0,\npart000000.ts\n#EXT-X-ENDLIST\n')
    before = MediaTools.file_response(playlist).body
    (folder / 'generation').write_text('bbbb2222')
    after = MediaTools.file_response(playlist).body
    assert before != after
    assert b'aaaa1111' in before and b'bbbb2222' in after


def test_a_validated_fragment_does_not_change_before_asgi_sends_it(tmp_path):
    async def scenario():
        piece = tmp_path / 'part000000.ts'
        piece.write_bytes(b'OLD VIDEO')
        response = MediaTools.file_response(piece)
        piece.write_bytes(b'NEW VIDEO')
        sent = []
        async def receive():return {'type':'http.request'}
        async def send(message):sent.append(message)
        await response({'type':'http','method':'GET','headers':[]},receive,send)
        assert b''.join(row.get('body',b'') for row in sent) == b'OLD VIDEO'
    asyncio.run(scenario())


def test_failed_conversion_can_be_recovered_at_the_same_point(tmp_path):
    async def scenario():
        tools = media(tmp_path)
        await tools.cast_path(SOURCE, 1800000, force_adapted=True)
        stopped = []
        tools.jobs[SOURCE] = {'error': 'conversion_interrupted', 'process': SimpleNamespace(returncode=1)}
        async def stop(key):
            stopped.append(key)
            tools.jobs.pop(key)
        tools.stop_conversion = stop
        result = await tools.cast_path(SOURCE, 1800000, force_adapted=True)
        assert stopped == [SOURCE] and result['offset_ms'] == 1800000
    asyncio.run(scenario())

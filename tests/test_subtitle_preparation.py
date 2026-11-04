"""Identidad real, aprobaciones durables y preparación compartida sin servicios externos."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.subtitle_assets import declared_identity, sampled_identity, SAMPLE_BYTES, REVISION
from app.subtitles import Subtitles
from app.providers.stremio import ProviderError

VTT = 'WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nHola\n'


class Bindings:
    def __init__(self): self.rows = {}
    def save_subtitle_binding(self, row): self.rows[row['token']] = dict(row)
    def subtitle_binding(self, video, asset, audio, revision):
        return next((r for r in self.rows.values() if
                     (r['video_id'],r['asset_key'],r['audio_key'],r['revision']) == (video,asset,audio,revision)),None)
    def subtitle_binding_token(self, token): return self.rows.get(token)


def service(tmp_path, strong=True, store=None):
    stream = {'infoHash': 'a' * 40, 'fileIdx': 0} if strong else {'name':'Same label'}
    video = {'content_id':'tt1234567','video_id':'tt1234567','provider':'penguplay',
             'fingerprint':'same-label','stream': stream}
    sub = {'content_id':'tt1234567','video_id':'tt1234567','provider':'subtitulos','stream':{}}
    sources = {'v1':video,'v2':{**video,'stream':{**stream,'name':'Renamed'}},'s1':sub,'s2':sub}
    playback = SimpleNamespace(source=lambda key:sources[key],store=store or Bindings(),
                               settings=SimpleNamespace(lan_ip='127.0.0.1',media_port=8765))
    media = SimpleNamespace(audio_selections={'v1':1,'v2':1})
    subs = Subtitles(None,playback,media); subs.sync_root = tmp_path
    calls = []
    async def download(key): return VTT if key == 's1' else VTT.replace('Hola','Adios')
    async def align(key,input_path,output_path,audio=None):
        calls.append(key); output_path.write_text(input_path.read_text('utf-8'),encoding='utf-8')
        return {'accepted':True,'quality':0.9,'max_residual_seconds':0.1,'samples':8}
    subs._download_vtt, subs._run_sync = download, align
    return subs, calls


def test_torrent_file_zero_is_strong_but_a_pack_without_file_index_is_not():
    source = {'stream':{'infoHash':'a'*40,'fileIdx':0}}
    first = declared_identity(source,'temporary-a')
    assert first['status'] == 'confirmed'
    assert first == declared_identity(source,'temporary-b')
    assert first['key'] != declared_identity({'stream':{'infoHash':'a'*40,'fileIdx':1}},'a')['key']
    assert declared_identity({'stream':{'infoHash':'a'*40}},'a')['status'] == 'approximate'


def test_file_signature_uses_real_ranges_and_changes_with_tail_bytes():
    async def run():
        data = bytearray(b'a' * (SAMPLE_BYTES * 3))
        requests = []
        def respond(request):
            requests.append(request.headers['range'])
            start,end = map(int,request.headers['range'].split('=')[1].split('-'))
            return httpx.Response(206,headers={'content-range':f'bytes {start}-{end}/{len(data)}'},
                                  content=bytes(data[start:end+1]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            first = await sampled_identity(client,'http://local/media/one')
            same = await sampled_identity(client,'http://local/media/new-url')
            data[-1] = ord('b')
            changed = await sampled_identity(client,'http://local/media/one')
        assert first == same and first['status'] == 'sampled'
        assert changed['key'] != first['key']
        assert all(int(r.split('-')[1])-int(r.split('=')[1].split('-')[0])+1 == SAMPLE_BYTES for r in requests)
    asyncio.run(run())


def test_a_server_ignoring_range_is_closed_without_consuming_video():
    class Unreadable(httpx.AsyncByteStream):
        read = False
        closed = False
        async def __aiter__(self):
            self.read = True
            yield b'not allowed'
        async def aclose(self): self.closed = True
    async def run():
        body = Unreadable()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=body))) as client:
            with pytest.raises(ValueError,match='range_unavailable'):
                await sampled_identity(client,'http://local/media/one')
        assert body.closed and not body.read
    asyncio.run(run())


def test_same_labels_do_not_reuse_approval_between_different_temporary_sources(tmp_path):
    subs,calls = service(tmp_path,strong=False)
    async def run():
        first = await subs.sync('v1','s1'); second = await subs.sync('v2','s1')
        assert first['item']['sync_token'] != second['item']['sync_token']
        assert first['item']['verification']['identity']['asset_status'] == 'approximate'
        assert calls == ['v1','v2']
    asyncio.run(run())


def test_approval_survives_cache_removal_and_source_renaming_without_provider_search(tmp_path):
    subs,calls = service(tmp_path)
    async def run():
        first = await subs.sync('v1','s1')
        token = first['item']['sync_token']
        saved_body = (tmp_path/(token+'.vtt')).read_text('utf-8')
        (tmp_path/(token+'.vtt')).unlink(); (tmp_path/(token+'.json')).unlink()
        # Nueva instancia: tampoco se depende de la memoria del proceso anterior.
        reopened = Subtitles(None,subs.playback,subs.media_tools); reopened.sync_root = tmp_path
        async def forbidden(_): pytest.fail('No debe buscar en proveedores una asociación ya aprobada')
        reopened._search = forbidden
        tracks = await reopened.tracks('v2')
        assert tracks[0]['sync_token'] == token and calls == ['v1']
        assert (await reopened.deliver_synced(token)).body.decode() == saved_body
        assert tracks[0]['verification']['content']['status'] == 'not_reviewed'
    asyncio.run(run())


def test_associations_separate_audio_and_subtitle_content(tmp_path):
    subs,_ = service(tmp_path)
    async def run():
        first = await subs.sync('v1','s1')
        second = await subs.sync('v1','s2')
        assert first['item']['sync_token'] != second['item']['sync_token']
        subs.media_tools.audio_selections['v2'] = 2
        assert await subs.approved('v2',subs.playback.source('v2')) is None
        third = await subs.sync('v2','s1')
        assert third['item']['sync_token'] != first['item']['sync_token']
        assert len(subs.playback.store.rows) == 3
    asyncio.run(run())


def test_corrupt_durable_copy_is_not_reused(tmp_path):
    subs,_ = service(tmp_path)
    async def run():
        result = await subs.sync('v1','s1')
        token = result['item']['sync_token']
        subs.playback.store.rows[token]['body'] = 'different text'
        assert await subs.approved('v2',subs.playback.source('v2')) is None
        (tmp_path/(token+'.vtt')).unlink()
        with pytest.raises(ProviderError): await subs.deliver_synced(token)
    asyncio.run(run())


def test_semantic_review_is_never_inferred_from_temporal_alignment(tmp_path):
    subs,_ = service(tmp_path)
    async def run():
        result = await subs.sync('v1','s1')
        evidence = result['item']['verification']
        assert evidence['revision'] == REVISION
        assert evidence['identity']['status'] == 'supported'
        assert evidence['timing']['status'] == 'matched'
        assert evidence['timing']['max_residual_seconds'] == 0.1
        assert evidence['content'] == {'status':'not_reviewed'}
    asyncio.run(run())


def test_embedded_origin_inherits_timing_without_claiming_editorial_review(tmp_path):
    subs,calls = service(tmp_path)
    subs.playback.source('s1')['stream']['embeddedVideo'] = 'v1'
    subs.playback.source('s1')['stream']['embeddedIndex'] = 3
    async def run():
        evidence = (await subs.sync('v1','s1'))['item']['verification']
        assert evidence['identity']['status'] == 'confirmed'
        assert evidence['timing']['status'] == 'inherited'
        assert evidence['content']['status'] == 'not_reviewed' and calls == []
    asyncio.run(run())


def test_preparation_timeout_keeps_one_job_alive_and_reuses_its_result(tmp_path):
    subs,_ = service(tmp_path)
    async def run():
        gate = asyncio.Event(); calls = []
        async def tracks(source):
            calls.append(source); await gate.wait()
            return [{'sync_token':'a'*40,'verified':True}]
        subs.tracks = tracks
        first = await subs.prepared('v1',0.001)
        assert first['subtitles_pending'] and not subs.start_prepare('v1').cancelled()
        gate.set()
        second,third = await asyncio.gather(subs.prepared('v1',1),subs.prepared('v1',1))
        assert not second['subtitles_pending'] and second == third and calls == ['v1']
        await subs.close()
    asyncio.run(run())


def test_shutdown_cancels_pending_preparation(tmp_path):
    subs,_ = service(tmp_path)
    async def run():
        async def tracks(_): await asyncio.Event().wait()
        subs.tracks = tracks
        task = subs.start_prepare('v1')
        await asyncio.sleep(0)
        await subs.close()
        assert task.cancelled()
    asyncio.run(run())


def test_uncertainty_runs_a_fresh_search_and_uses_a_new_alternative(tmp_path):
    subs,_ = service(tmp_path)
    queries,attempts = [],[]
    async def tracks(source):
        queries.append(source)
        # IDs distintos para el mismo contenido no deben repetir el análisis.
        return [{'source_id':'old-'+str(len(queries)),'key':'same'}] + (
            [{'source_id':'new','key':'new'}] if len(queries)>1 else [])
    async def sync(source,subtitle):
        attempts.append(subtitle)
        return {'verified':True,'item':{'sync_token':'ready','verified':True}} if subtitle=='new' else {
            'verified':False,'reason':'alignment_inconclusive'}
    subs.tracks,subs.sync = tracks,sync
    subs.search_cache[subs._search_key(subs.playback.source('v1'))] = (0,['stale'])
    async def run():
        result=await subs.prepared('v1',1)
        assert result['subtitle_state']=='ready' and not result['subtitles_pending']
        assert len(queries)==2 and attempts==['old-1','new']
        assert not subs.search_cache
        await subs.close()
    asyncio.run(run())


def test_failure_is_terminal_retains_reason_and_never_offers_raw_candidates(tmp_path):
    subs,_ = service(tmp_path)
    searches=[]
    async def tracks(source):searches.append(source);return [{'source_id':'s1','key':'same'}]
    async def fail(*_):return {'verified':False,'reason':'audio_decode_failed'}
    subs.tracks,subs.sync=tracks,fail
    async def run():
        result=await subs.prepared('v1',1)
        assert result['subtitle_state']=='failed' and result['subtitle_reason']=='audio_decode_failed'
        assert result['subtitles']==[] and not result['subtitles_pending']
        for _ in range(4):assert await subs.prepared('v1',1)==result
        assert searches==['v1','v1']
        first=subs.start_prepare('v1',refresh=True)
        assert subs.start_prepare('v1',refresh=True) is first
        await first
        assert len(searches)==4
        await subs.close()
    asyncio.run(run())


def test_total_deadline_does_not_restart_on_status_polls(tmp_path,monkeypatch):
    import time
    monkeypatch.setattr('app.subtitles.PREPARATION_SECONDS',.12)
    subs,_=service(tmp_path)
    calls=[]
    async def slow(source):calls.append(source);await asyncio.Event().wait()
    subs.tracks=slow
    async def run():
        started=time.monotonic()
        first=await subs.prepared('v1',.001)
        assert first['subtitles_pending']
        task=subs.start_prepare('v1')
        final=await asyncio.wait_for(task,.5)
        assert time.monotonic()-started<.5
        assert not final['subtitles_pending'] and final['subtitle_state']=='failed'
        for _ in range(4):
            assert (await subs.prepared('v1',.01))==final
            assert subs.start_prepare('v1') is task
        # El plazo global tiene prioridad: Windows puede agotar este presupuesto
        # mínimo antes de iniciar la segunda ronda, pero nunca se crea una tercera.
        assert 1<=len(calls)<=2
        await subs.close()
    asyncio.run(run())


def test_hash_association_bypasses_vad_and_other_videos_analysis_lock(tmp_path):
    subs,calls=service(tmp_path)
    subs.playback.source('s1')['stream']['subtitleMetadata']={'hash_match':True}
    async def run():
        await subs.sync_lock.acquire()
        try:result=await asyncio.wait_for(subs.sync('v1','s1'),.5)
        finally:subs.sync_lock.release()
        assert result['verified'] and calls==[]
        assert result['item']['verification']['timing']=={'status':'inherited','method':'provider_hash'}
        assert result['item']['verification']['content']=={'status':'not_reviewed'}
    asyncio.run(run())


def test_concurrent_inherited_copies_publish_one_valid_track_without_shared_temporaries(tmp_path):
    subs,calls=service(tmp_path)
    subs.playback.source('s1')['stream']['subtitleMetadata']={'hash_match':True}
    async def run():
        results=await asyncio.gather(*(subs.sync('v1','s1') for _ in range(5)))
        assert all(result['verified'] for result in results) and not calls
        assert len({result['item']['sync_token'] for result in results})==1
        assert not list(tmp_path.glob('*.srt'))
        token=results[0]['item']['sync_token']
        assert (await subs.deliver_synced(token)).body.decode().startswith('WEBVTT')
    asyncio.run(run())


def test_reusing_an_intact_copy_does_not_rewrite_files_while_a_player_reads_them(tmp_path):
    import os
    subs,_=service(tmp_path)
    async def run():
        result=await subs.sync('v1','s1');token=result['item']['sync_token']
        paths=[tmp_path/(token+suffix) for suffix in ('.vtt','.json')]
        for path in paths:os.utime(path,(1_000_000_000,1_000_000_000))
        assert (await subs.approved('v2',subs.playback.source('v2')))['sync_token']==token
        assert all(path.stat().st_mtime==1_000_000_000 for path in paths)
    asyncio.run(run())


def test_inconclusive_analysis_does_not_persist_a_content_rejection(tmp_path):
    subs,_=service(tmp_path)
    verdicts=[]
    subs.playback.store.save_subtitle_verdict=lambda *args:verdicts.append(args)
    async def fail(*args):return {'accepted':False,'reason':'alignment_inconclusive'}
    subs._run_sync=fail
    async def run():
        result=await subs.sync('v1','s1')
        assert not result['verified'] and not verdicts and not subs.playback.store.rows
        assert not list(tmp_path.glob('*.vtt'))
    asyncio.run(run())


def test_previous_uncertain_verdict_cannot_block_a_later_success(tmp_path):
    subs,calls=service(tmp_path)
    subs.playback.store.subtitle_verdict=lambda token:{'status':'rejected','reason':'insufficient_dialogue'}
    async def run():
        assert (await subs.sync('v1','s1'))['verified'] and calls==['v1']
    asyncio.run(run())


def test_explicit_search_can_find_an_alternative_to_an_already_approved_track(tmp_path):
    subs,_=service(tmp_path)
    old={'key':'old','sync_token':'approved','verified':True}
    queries=[]
    async def approved(*args):return old
    async def search(_):
        queries.append(1)
        return [{'url':'https://subs.example/new','name':'Movie.es.srt','provider':'OpenSubtitles','hearing_impaired':False}]
    async def empty(*args):return []
    subs.playback.save_sources=lambda *args:[{'source_id':'new'}]
    subs.approved,subs._search,subs._embedded=approved,search,empty
    async def sync(*args):return {'verified':True,'item':{'key':'new','sync_token':'alternative','verified':True}}
    subs.sync=sync
    async def run():
        assert (await subs.prepared('v1',1))['subtitles']==[old]
        assert not queries
        subs.start_prepare('v1',refresh=True)
        result=await subs.prepared('v1',1)
        assert result['subtitles'][0]['key']=='new' and queries==[1]
        await subs.close()
    asyncio.run(run())


def test_slow_provider_does_not_hide_fast_results(tmp_path,monkeypatch):
    import time
    monkeypatch.setattr('app.subtitles.SEARCH_SECONDS',.06)
    subs,_=service(tmp_path)
    async def slow(_):await asyncio.Event().wait()
    async def fast(_):return [{'name':'matching'}]
    subs._opensubtitles,subs._subdl,subs._subtitlecat=fast,slow,slow
    async def run():
        started=time.monotonic()
        assert await subs._search(subs.playback.source('v1'))==[{'name':'matching'}]
        assert time.monotonic()-started<.5
    asyncio.run(run())


def test_partial_provider_results_are_usable_but_are_not_cached_as_complete(tmp_path, monkeypatch):
    subs,_ = service(tmp_path)
    queries, events = [], []
    async def found(_):
        queries.append(1)
        return [{'name':'matching'}]
    async def failed(_): raise ProviderError('subtitle_provider_auth','private key rejected')
    async def empty(_): return []
    subs._opensubtitles,subs._subdl,subs._subtitlecat = found,failed,empty
    monkeypatch.setattr('app.subtitles.audit',lambda event, **fields:events.append((event,fields)))
    async def run():
        source = subs.playback.source('v1')
        assert await subs._search(source) == [{'name':'matching'}]
        assert not subs.search_cache
        assert await subs._search(source) == [{'name':'matching'}] and len(queries)==2
        report = subs.search_reports[subs._search_key(source)]
        assert report['SubDL']['status']=='failed' and report['SubDL']['reason']=='subtitle_provider_auth'
        assert report['OpenSubtitles']['count']==1
        assert 'private' not in str(events)
    asyncio.run(run())


def test_missing_subdl_key_is_observable_and_complete_empty_search_can_be_cached(tmp_path,monkeypatch):
    subs,_ = service(tmp_path)
    events=[]
    async def empty(_):return []
    subs._opensubtitles,subs._subtitlecat=empty,empty
    monkeypatch.setattr('app.subtitles.audit',lambda event, **fields:events.append((event,fields)))
    async def run():
        source=subs.playback.source('v1')
        assert await subs._search(source)==[]
        assert subs.search_cache
        report=subs.search_reports[subs._search_key(source)]
        assert report['SubDL']['status']=='not_configured'
        assert report['OpenSubtitles']['status']=='empty'
        assert all('elapsed_ms' in fields and fields['count']==0 for _,fields in events)
    asyncio.run(run())


def test_incomplete_search_is_not_reported_as_a_definitive_identity_rejection(tmp_path):
    subs,_ = service(tmp_path)
    source=subs.playback.source('v1')
    source.update(video_id='tt0386676:2:8')
    async def wrong_episode(_):return [{'name':'The.Office.US.S02E09.srt'}]
    async def failed(_):raise ProviderError('subtitle_provider_limited','Limitado')
    async def empty(*_):return []
    subs._opensubtitles,subs._subdl,subs._subtitlecat,subs._embedded=wrong_episode,failed,empty,empty
    async def run():
        result=await subs.prepared('v1',1)
        assert result['subtitle_state']=='failed' and result['subtitle_reason']=='subtitle_search_incomplete'
        assert not result['subtitles'] and not result['subtitles_pending']
        assert 'incompleta' in result['subtitle_message']
    asyncio.run(run())


def test_first_round_can_finish_after_the_previous_half_budget_without_extending_total(tmp_path,monkeypatch):
    subs,_=service(tmp_path)
    monkeypatch.setattr('app.subtitles.PREPARATION_SECONDS',.8)
    async def tracks(_):
        await asyncio.sleep(.24)
        return [{'source_id':'s1','key':'same'}]
    async def alternatives(*_):await asyncio.sleep(.12)
    async def sync(*_):
        await asyncio.sleep(.12)
        return {'verified':True,'item':{'key':'same','sync_token':'approved','verified':True}}
    subs.tracks,subs._prepare_alternatives,subs.sync=tracks,alternatives,sync
    async def run():
        result=await subs.prepared('v1',1)
        assert result['subtitle_state']=='ready' and result['subtitles'][0]['verified']
    asyncio.run(run())


def test_search_timeout_is_distinct_from_successful_search_without_results(tmp_path,monkeypatch):
    subs,_=service(tmp_path)
    monkeypatch.setattr('app.subtitles.SEARCH_SECONDS',.02)
    async def slow(_):await asyncio.Event().wait()
    async def empty(*_):return []
    subs._opensubtitles,subs._subdl,subs._subtitlecat,subs._embedded=slow,empty,empty,empty
    async def run():
        result=await subs.prepared('v1',1)
        assert result['subtitle_state']=='failed' and result['subtitle_reason']=='subtitle_search_timeout'
        assert 'tiempo' in result['subtitle_message'] and not subs.search_cache
    asyncio.run(run())

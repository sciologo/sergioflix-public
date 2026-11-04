"""Sérpico/Tarde de perros: disponibilidad independiente del análisis temporal."""
import asyncio
from types import SimpleNamespace

import pytest

from app.providers.stremio import ProviderError
from app.subtitle_identity import identity_reason
from app.subtitles import Subtitles, stamp


def movie(tmp_path, video_id='tt0070666', duration=7806.887, end=7709.895):
    common = {'content_id': video_id, 'video_id': video_id}
    name = 'Dog.Day.Afternoon.1975' if video_id=='tt0072890' else 'Serpico.1973'
    video = {**common, 'provider':'penguplay', 'stream':{'name':name+'.720p.mp4'},
             'expected_duration_ms':7440000 if video_id=='tt0072890' else 7740000, 'content_name':name}
    subtitle = {**common, 'provider':'subtitulos', 'stream':{
        'name':name+'.BluRay.srt', 'subtitleVideo':'video', 'subtitleProvider':'OpenSubtitles'}}
    sources = {'video':video, 'full':subtitle}
    playback = SimpleNamespace(source=lambda key:sources[key], settings=SimpleNamespace(lan_ip='127.0.0.1',media_port=8765))
    media = SimpleNamespace(audio_selections={}, inspections={'video':{'format':{'duration':str(duration)}}})
    subs = Subtitles(None, playback, media); subs.sync_root = tmp_path
    body = 'WEBVTT\n\n00:01:10.000 --> 00:01:11.000\nHola\n\n'+stamp(end*1000-1000)+' --> '+stamp(end*1000)+'\nFin\n'
    async def download(_): return body
    subs._download_vtt = download
    track = {'key':'full-key','source_id':'full','url':'http://local/subtitles/full.vtt',
             'label':'Español · OpenSubtitles', 'validation_required':True,'verified':False}
    return subs, video, subtitle, track


@pytest.mark.parametrize('video_id,duration,end', [('tt0070666',7806.887,7709.895),('tt0072890',7440,7397.924)])
def test_complete_movie_options_survive_inconclusive_voice_analysis(tmp_path, video_id, duration, end):
    subs,_,_,track = movie(tmp_path,video_id,duration,end)
    async def tracks(_):return [track]
    async def sync(*_):return {'verified':False,'reason':'audio_read_timeout'}
    subs.tracks,subs.sync = tracks,sync
    async def run():
        result = await subs.prepared('video',1)
        assert result['subtitle_state']=='available' and not result['subtitles_pending']
        assert result['subtitles']==[]  # Nunca se activa ni se anuncia como verificada.
        alternative, = result['subtitle_alternatives']
        assert alternative['manual_only'] and not alternative['verified']
        assert alternative['url'].endswith('?manual=true')
        assert not any(key.startswith('_') for key in alternative)
        assert await subs.prepared('video',1)==result
    asyncio.run(run())


def test_manual_choice_downloads_original_without_running_vad_or_saving_approval(tmp_path):
    subs,_,_,_ = movie(tmp_path)
    async def forbidden(*_):pytest.fail('Una elección manual no debe iniciar el VAD ni guardar una aprobación')
    subs.sync,subs._run_sync = forbidden,forbidden
    response = asyncio.run(subs.deliver('full',500,manual=True))
    assert '00:01:09.500' in response.body.decode()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('name', ['Serpico - CD2.srt','serpico.1973.cd2.dvdrip.xvid-anbc.srt',
    'dog.day.afternoon.1975.dvdrip.cd1.spa.srt'])
def test_disc_subtitles_are_rejected_for_a_complete_movie(tmp_path,name):
    _,video,_,_ = movie(tmp_path)
    assert identity_reason(video,{'name':name})=='subtitle_part_mismatch'


def test_matching_disc_video_is_still_supported(tmp_path):
    _,video,_,_ = movie(tmp_path)
    video['stream']['name']='Serpico.1973.CD2.avi'
    assert not identity_reason(video,{'name':'Serpico - CD2.srt'})


def test_actual_short_edition_keeps_rejecting_full_length_subtitles(tmp_path):
    subs,_,_,track = movie(tmp_path,duration=6495.360)
    async def run():
        with pytest.raises(ProviderError,match='subtitle_duration_mismatch'):
            await subs.deliver('full',manual=True)
        await subs._prepare_alternatives('video',[track])
        assert subs._preparation_state(('video',None),'failed')['subtitle_alternatives']==[]
    asyncio.run(run())


def test_late_inspection_retracts_options_for_an_incompatible_edition(tmp_path):
    subs,_,_,track = movie(tmp_path)
    subs.media_tools.inspections.clear()
    async def run():
        await subs._prepare_alternatives('video',[track])
        assert subs._preparation_state(('video',None),'searching')['subtitle_alternatives']
        subs.media_tools.inspections['video']={'format':{'duration':'6495.360'}}
        result = subs._preparation_state(('video',None),'failed')
        assert result['subtitle_state']=='unavailable' and not result['subtitle_alternatives']
        assert result['subtitle_reason']=='subtitle_duration_mismatch'
        with pytest.raises(ProviderError):await subs.deliver('full',manual=True)
    asyncio.run(run())


def test_identity_ambiguity_and_cross_video_binding_cannot_be_bypassed_manually(tmp_path):
    subs,video,subtitle,_ = movie(tmp_path)
    video['subtitle_context']={'ambiguous':True}
    subtitle['stream']['name']='subtitulo.srt'
    with pytest.raises(ProviderError,match='identity_unverified'):
        asyncio.run(subs.deliver('full',manual=True))
    video['subtitle_context']={}; subtitle['video_id']='tt0072890'
    with pytest.raises(ProviderError,match='invalid_subtitle'):
        asyncio.run(subs.deliver('full',manual=True))


def test_quick_options_are_published_before_the_shared_deadline_and_retained_on_retry(tmp_path,monkeypatch):
    subs,_,_,track = movie(tmp_path)
    monkeypatch.setattr('app.subtitles.PREPARATION_SECONDS',.12)
    async def tracks(_):return [track]
    async def slow(*_):await asyncio.Event().wait()
    subs.tracks,subs.sync = tracks,slow
    async def run():
        task = subs.start_prepare('video')
        for _ in range(40):
            if subs.preparation_states[('video',None)].get('subtitle_alternatives'):break
            await asyncio.sleep(.001)
        current = await subs.prepared('video',.001)
        assert current['subtitles_pending'] and current['subtitle_alternatives']
        final = await asyncio.wait_for(task,.5)
        assert final['subtitle_state']=='available' and final['subtitle_alternatives']
        assert await subs.prepared('video',.01)==final
    asyncio.run(run())

"""Integración PostgreSQL real en un esquema temporal exclusivo; conserva los datos del usuario."""
import json
import uuid
import pytest
import psycopg
from psycopg import sql
from app.settings import ROOT,Settings
from app.storage import Store
from app.library import Library
from app.devices import Devices
from app.admin_session import AdminSession
from app.providers.mediafusion import ProviderError
from app.playback import Playback


@pytest.fixture
def store():
    settings=Settings()
    schema='test_video_'+uuid.uuid4().hex
    base=Store(settings)
    try:conn=base.connect()
    except psycopg.OperationalError:pytest.skip('PostgreSQL local no está iniciado')
    with conn:conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    class IsolatedStore(Store):
        def connect(self):
            conn=super().connect()
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            return conn
    isolated=IsolatedStore(settings)
    try:
        with isolated.connect() as conn:
            for file in sorted((ROOT/'migrations').glob('*.sql')):conn.execute(file.read_text(encoding='utf-8'))
            conn.execute("INSERT INTO content VALUES ('tt1234567','movie','Prueba',%s::jsonb,now())",(json.dumps({'id':'tt1234567','name':'Prueba','type':'movie'}),))
        yield isolated
    finally:
        assert schema.startswith('test_video_') and len(schema)==43
        with base.connect() as conn:conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_subtitle_binding_persists_content_audio_and_evidence_without_urls(store):
    import hashlib
    from app.subtitle_assets import verification
    evidence=verification({'status':'sampled','method':'file_signature'})
    body='WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nTexto\n'
    row={'token':'a'*40,'video_id':'tt1234567','asset_key':'asset-a','audio_key':'audio-a',
         'subtitle_sha256':'b'*64,'subtitle_key':'c'*32,'revision':4,'verification':evidence,
         'body':body,'body_sha256':hashlib.sha256(body.encode()).hexdigest()}
    store.save_subtitle_binding(row)
    loaded=store.subtitle_binding('tt1234567','asset-a','audio-a',4)
    assert loaded['body']==body and loaded['verification']['content']['status']=='not_reviewed'
    assert store.subtitle_binding('tt1234567','asset-a','audio-b',4) is None
    assert store.subtitle_binding('tt1234567','asset-b','audio-a',4) is None
    assert store.subtitle_binding('tt1234567','asset-a','audio-a',3) is None
    assert store.subtitle_binding_token(row['token'])==loaded
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM playback_progress').fetchone()[0]==0


def test_render_measurements_are_idempotent_and_have_weighted_summary(store):
    from app.playback_events import PlaybackEvents
    journal=PlaybackEvents(store)
    source={'content_id':'tt1234567','video_id':'tt1234567','provider':'penguplay','fingerprint':'f'*32}
    journal.begin('tv-test',source,'subtitle-attempt-0001','subtitle-playback-001','tv')
    fields={'subtitle_key':'b'*32,'subtitle_token':'c'*40,'render_context':'continuous',
            'render_samples':2,'render_lag_ms':80,'render_total_lag_ms':100}
    assert journal.record('tv-test','subtitle-attempt-0001','cue-batch-1','subtitle_render',**fields)
    assert not journal.record('tv-test','subtitle-attempt-0001','cue-batch-1','subtitle_render',**fields)
    fields.update(render_samples=1,render_lag_ms=20,render_total_lag_ms=20)
    journal.record('tv-test','subtitle-attempt-0001','cue-batch-2','subtitle_render',**fields)
    assert journal.summary()['subtitle_render']==[{'client':'tv','context':'continuous','samples':3,
        'average_lag_ms':40,'max_lag_ms':80}]


def test_next_episode_prefetch_preserves_the_current_session_and_progress(store,monkeypatch):
    import asyncio
    import httpx
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.catalog import Catalog
    import app.main as main_module
    import app.probing as probing
    title='tt9999999';current=title+':1:1';following=title+':1:2'
    detail={'id':title,'name':'Serie','type':'series','episodes':[
        {'id':current,'season':1,'episode':1},{'id':following,'season':1,'episode':2}]}
    with store.connect() as conn:
        conn.execute('INSERT INTO content(id,media_type,name,data) VALUES (%s,\'series\',\'Serie\',%s::jsonb)',
                     (title,json.dumps(detail)))
    store.save_provider('https://pengu.uk/private/manifest.json',{},'test','penguplay')
    settings=Settings(); playback=Playback(store,settings,None)
    first=playback.save_catalog(title,current,[{'provider':'penguplay','streams':[
        {'url':'https://cdn.example/current.mp4','name':'720p H264 AAC'}]}])[0]['source_id']
    async def catalog_detail(*_):return detail
    async def streams(*_):return ([{'provider':'penguplay','streams':[
        {'url':'https://cdn.example/next.mp4','name':'720p H264 AAC'}]}],[])
    async def speeds(*_):return {}
    monkeypatch.setattr(Catalog,'detail',catalog_detail)
    monkeypatch.setattr(main_module,'gather_streams',streams)
    monkeypatch.setattr(probing,'resolve_speeds',speeds)
    app=create_app(settings,store,httpx.MockTransport(lambda _:httpx.Response(200)))
    prepared=[]
    async def prepare(source_id):prepared.append(source_id);return []
    async def no_wait(*_):return {}
    with TestClient(app) as browser:
        app.state.playback.ready=no_wait
        app.state.subtitles.prepare=prepare
        app.state.subtitles.prefer_editions=no_wait
        headers={'X-API-Key':settings.api_key.get_secret_value()}
        assert browser.post('/api/playback/start',headers=headers,json={'source_id':first}).status_code==200
        owners=dict(app.state.control.actors)
        assert browser.post('/api/playback/subtitles/next',headers=headers,json={'source_id':first}).status_code==202
        async def finished():await asyncio.gather(*app.state.warming)
        browser.portal.call(finished)
        assert len(prepared)==2 and prepared[0]==first and prepared[1]!=first
        assert app.state.control.actors==owners and list(app.state.control.sources)==[first]
        assert not browser.post('/api/playback/subtitles/next',headers=headers,json={'source_id':first}).json()['scheduled']
        with store.connect() as conn:
            assert conn.execute('SELECT video_id FROM playback_progress').fetchall()==[(current,)]
            assert conn.execute('SELECT video_id FROM playback_attempt').fetchall()==[(current,)]
            assert conn.execute('SELECT video_id FROM media_source WHERE id=%s',(prepared[1],)).fetchone()==(following,)


def test_subtitle_decisions_survive_temporary_ids_without_rewriting_progress(store):
    video = {'video_id':'tt1234567:2:1', 'fingerprint':'edition-a'}
    token = 'a' * 40
    store.save_subtitle_verdict(token, video, 's' * 32, 'rejected', 'episode_mismatch')
    assert store.subtitle_verdict(token) == {'status':'rejected','reason':'episode_mismatch'}
    assert store.subtitle_verdict('b' * 40) is None
    store.save_subtitle_verdict(token, video, 's' * 32, 'verified')
    assert store.subtitle_verdict(token) == {'status':'verified','reason':''}
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM playback_progress').fetchone()[0] == 0


def test_tv_catalog_refreshes_once_per_calendar_day_and_keeps_offline_copy(store):
    """Comprueba la caducidad con timestamps PostgreSQL reales, sin usar la videoteca."""
    import asyncio
    from datetime import datetime, timedelta
    import httpx
    from app.catalog import Catalog

    requests = []
    unavailable = False

    def respond(request):
        requests.append(request.url.path)
        if unavailable:
            return httpx.Response(503)
        return httpx.Response(200, json={'metas': [
            {'id': 'tt1234567', 'type': 'movie', 'name': 'Actualizada'},
        ]})

    async def exercise():
        nonlocal unavailable
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            catalog = Catalog(store, client)
            key = 'daily:/catalog/movie/top/skip=0.json'
            old = {'items': [{'id': 'tt1234567', 'type': 'movie', 'name': 'Anterior'}], 'next_skip': 50}
            catalog.remember(key, old, [])
            midnight = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
            with store.connect() as conn:
                conn.execute('UPDATE catalog_page SET updated_at=%s WHERE cache_key=%s', (midnight-timedelta(seconds=1), key))
            first = await catalog.page('movie', daily=True)
            assert first['items'][0]['name'] == 'Actualizada' and not first['stale']
            assert len(requests) == 1
            assert await catalog.page('movie', daily=True) == first
            assert len(requests) == 1
            with store.connect() as conn:
                conn.execute('UPDATE catalog_page SET updated_at=%s WHERE cache_key=%s', (midnight-timedelta(seconds=1), key))
            unavailable = True
            offline = await catalog.page('movie', daily=True)
            assert offline['stale'] and offline['items'] == first['items']
            with store.connect() as conn:
                assert conn.execute('SELECT count(*) FROM catalog_page WHERE cache_key=%s', (key,)).fetchone()[0] == 1
                conn.execute('DELETE FROM catalog_page WHERE cache_key=%s', (key,))
            catalog.remember('/catalog/movie/top/skip=0.json', old, [])
            # Primera apertura tras actualizar la app, todavía sin caché diaria.
            first_offline = await catalog.page('movie', daily=True)
            assert first_offline['stale'] and first_offline['items'] == old['items']

    asyncio.run(exercise())


def test_rewind_out_of_order_and_old_sessions(store):
    library=Library(store)
    first=library.session('tt1234567','tt1234567','phone-a')['session_id']
    assert library.progress(first,1,5000,100000,False,'phone-a')['accepted']
    assert library.progress(first,2,2000,100000,False,'phone-a')['accepted']
    assert not library.progress(first,1,9000,100000,False,'phone-a')['accepted']
    assert not library.progress(first,3,9000,100000,False,'phone-b')['accepted']
    second=library.session('tt1234567','tt1234567','phone-a')
    assert second['position_ms']==2000
    assert not library.progress(first,99,9000,100000,False,'phone-a')['accepted']
    assert library.progress(second['session_id'],1,100000,100000,True,'phone-a')['accepted']
    assert library.continuing()==[]


def test_resume_preferences_survive_new_links_and_sessions_without_losing_progress(store):
    """Dos TV comparten la edición y los ajustes, aunque se renueve el enlace firmado."""
    playback=Playback(store,Settings(),None)
    def catalog(signature):
        return playback.save_catalog('tt1234567','tt1234567',[{'provider':'penguplay','streams':[
            {'url':'https://cdn.example/movie.mp4?signature='+signature,
             'name':'1080p English','behaviorHints':{'filename':'Movie.1080p.mkv'}}]}])
    first_source=catalog('first')[0]
    library=Library(store)
    first=library.session('tt1234567','tt1234567','tv-a')['session_id']
    preferences={'source_id':first_source['source_id'],'audio_stream_index':2,
                 'subtitle_key':'a'*32,'subtitle_sync_token':'b'*40,'subtitle_offset_ms':-2500,
                 'subtitle_disabled':False,'subtitle_manual':True,'subtitle_style':'yellow-outline'}
    assert library.progress(first,1,600000,3600000,False,'tv-a',30000,preferences)['accepted']
    saved=library.preferences('tt1234567')
    assert saved['source_fingerprint']==first_source['fingerprint']
    assert 'source_id' not in saved and 'signature' not in json.dumps(saved)
    second_source=catalog('second')[0]
    assert second_source['source_id']!=first_source['source_id']
    assert second_source['fingerprint']==saved['source_fingerprint']
    second=library.session('tt1234567','tt1234567','tv-b')
    assert second['position_ms']==600000 and second['preferences']==saved
    assert library.detail_progress('tt1234567')['videos']['tt1234567']['preferences']==saved
    # Sesión/actor/orden siguen protegiendo tanto el minuto como las preferencias.
    assert not library.progress(first,999,900000,3600000,False,'tv-a',30000,preferences)['accepted']
    preferences['source_id']=second_source['source_id']
    preferences['subtitle_offset_ms']=0
    assert not library.progress(second['session_id'],1,800000,3600000,False,'tv-a',30000,preferences)['accepted']
    assert library.progress(second['session_id'],2,700000,3600000,False,'tv-b',30000,preferences)['accepted']
    assert not library.progress(second['session_id'],1,900000,3600000,False,'tv-b',30000,saved | {'source_id':second_source['source_id']})['accepted']
    # Un Android anterior que no envía preferencias tampoco las borra.
    assert library.progress(second['session_id'],3,720000,3600000,False,'tv-b',30000)['accepted']
    assert library.preferences('tt1234567')['subtitle_offset_ms']==0


def test_manual_preferences_save_during_short_resume_without_overwriting_watched_position(store):
    playback=Playback(store,Settings(),None)
    source=playback.save_catalog('tt1234567','tt1234567',[{'provider':'penguplay','streams':[
        {'url':'https://cdn.example/movie.mp4','name':'1080p English'}]}])[0]
    library=Library(store)
    first=library.session('tt1234567','tt1234567','tv')['session_id']
    assert library.progress(first,1,600000,3600000,False,'tv',30000)['accepted']
    second=library.session('tt1234567','tt1234567','tv')['session_id']
    assert library.progress(second,1,10000,3600000,False,'tv',5000,
        {'source_id':source['source_id'],'subtitle_disabled':True})['accepted']
    assert library.position('tt1234567')==600000
    assert library.preferences('tt1234567')['subtitle_disabled']
    with store.connect() as conn:
        conn.execute("UPDATE media_source SET video_id='tt7654321' WHERE id=%s",(source['source_id'],))
    assert not library.progress(second,2,700000,3600000,False,'tv',30000,
        {'source_id':source['source_id'],'subtitle_disabled':False})['accepted']
    assert library.preferences('tt1234567')['subtitle_disabled']


def test_pairing_and_admin_ticket_are_single_use(store):
    devices=Devices(store);code=devices.pairing()
    key=devices.exchange(code)['api_key']
    assert devices.identify(key)
    with pytest.raises(ProviderError):devices.exchange(code)
    assert not devices.identify('wrong-key')
    admin=AdminSession(store);ticket=admin.issue()
    session=admin.exchange(ticket)['api_key']
    assert admin.valid(session) and not admin.valid(ticket)
    with pytest.raises(ProviderError):admin.exchange(ticket)


def test_hls_child_does_not_leak_auth_to_another_origin(store):
    playback=Playback(store,Settings(),None)
    stream={'url':'https://origin.example/master.m3u8','behaviorHints':{'proxyHeaders':{'request':{'Authorization':'private-test-token','User-Agent':'Test'}}}}
    parent=playback.save_sources('tt1234567','tt1234567',[stream])[0]['source_id']
    original=playback.source(parent)
    url=playback.child_source(parent,original,'https://cdn.example/segment.ts')
    child=playback.source(url.rsplit('/',1)[1])['stream']
    assert 'Authorization' not in child['behaviorHints']['proxyHeaders']['request']
    assert original['stream']['behaviorHints']['proxyHeaders']['request']['Authorization']=='private-test-token'


def test_migration_keeps_old_mediafusion_sources_and_adds_ranking(store):
    """Una fila escrita como antes de la migración 005 sigue siendo una fuente MediaFusion válida."""
    playback=Playback(store,Settings(),None)
    encrypted=store.cipher.encrypt(json.dumps({'url':'https://cdn.example/antigua.mp4'}).encode()).decode()
    with store.connect() as conn:
        conn.execute('INSERT INTO media_source(id,content_id,video_id,encrypted_data) VALUES (%s,%s,%s,%s)',
            ('a'*32,'tt1234567','tt1234567',encrypted))
        assert conn.execute("SELECT provider,rank_score FROM media_source WHERE id=%s",('a'*32,)).fetchone()==('mediafusion',0)
    old=playback.source('a'*32)
    assert old['provider']=='mediafusion' and old['stream']['url'].endswith('antigua.mp4')


def test_sources_of_several_providers_are_stored_ranked_and_labelled(store):
    playback=Playback(store,Settings(),None)
    items=playback.save_catalog('tt1234567','tt1234567',[
        {'provider':'penguplay','streams':[{'infoHash':'a'*40,'name':'2160p REMUX HEVC Blu-ray 45 GB 👤 80'}]},
        {'provider':'flixstreams','streams':[{'url':'https://cdn.example/master.m3u8','name':'1080p H264 AAC Español HLS 6 Mbps'}]},
        {'provider':'mediafusion','streams':[{'url':'https://cdn.example/pelicula.mp4','name':'1080p H264 AAC Español 5 Mbps'}]}])
    # El REMUX de 45 GB supera la caché P2P: no se ofrece aunque venga del proveedor preferido.
    assert [row['provider'] for row in items]==['flixstreams','mediafusion']
    assert items[0]['score']>items[1]['score']
    assert items[0]['provider_name']=='Flix-Streams' and items[0]['reasons']
    assert items[0]['origin']=='original' and items[0]['languages']==['spanish']
    assert items[0]['ranking_tier']==2 and items[0]['speed_scope']==''
    with store.connect() as conn:
        saved=dict(conn.execute('SELECT provider,rank_score FROM media_source WHERE content_id=%s',('tt1234567',)).fetchall())
    assert saved['flixstreams']==items[0]['score'] and set(saved)=={'flixstreams','mediafusion'}


def test_unplayable_sources_are_hidden_but_never_leave_an_empty_list(store):
    """Un torrent enorme o sin pares agota 25 s para nada; no debe ofrecerse si hay algo mejor.

    Se comprueba con la regla de solo alojador apagada, que es cuando el P2P puede
    llegar a ofrecerse; con la regla encendida no llega ninguno.
    """
    playback=Playback(store,Settings(direct_only=False),None)
    huge={'infoHash':'a'*40,'name':'1080p H264 AAC 👤 40','behaviorHints':{'videoSize':28*1024**3}}
    orphan={'infoHash':'b'*40,'name':'1080p H264 AAC Español'}
    usable={'infoHash':'c'*40,'name':'1080p H264 AAC Español 👤 25','behaviorHints':{'videoSize':2*1024**3}}
    items=playback.save_catalog('tt1234567','tt1234567',[{'provider':'mediafusion','streams':[huge,orphan,usable]}])
    assert len(items)==1 and items[0]['seeders']==25 and items[0]['size_gb']==2
    # Sin ninguna reproducible se muestran igualmente, con el motivo a la vista.
    only_bad=playback.save_catalog('tt1234567','tt1234567',[{'provider':'mediafusion','streams':[huge,orphan]}])
    assert len(only_bad)==2 and all(not row['playable'] for row in only_bad)
    assert any('máximo utilizable' in ' '.join(row['blockers']) for row in only_bad)
    assert any('sin pares' in ' '.join(row['blockers']) for row in only_bad)


def test_an_unidentified_hdhub_video_is_never_offered_as_the_only_fallback(store):
    """Los bloqueos técnicos admiten respaldo; una posible publicidad no."""
    playback=Playback(store,Settings(),None)
    advert={'url':'https://cdn.example/shared-ad.mp4','name':'VS Sunny 360p',
            'description':'MovieBox/Sunny Native App Stream'}
    items=playback.save_catalog('tt1234567','tt1234567',
                                [{'provider':'hdhub','streams':[advert]}])
    assert items==[]


def test_a_saved_source_carries_the_catalog_runtime_to_the_real_video_check(store):
    with store.connect() as conn:
        conn.execute("UPDATE content SET data=data || '{\"runtime\":\"2h\"}'::jsonb WHERE id='tt1234567'")
    playback=Playback(store,Settings(),None)
    item=playback.save_catalog('tt1234567','tt1234567',[{'provider':'penguplay','streams':[
        {'url':'https://cdn.example/movie.mp4','name':'Movie 1080p H264 AAC'}]}])[0]
    assert playback.source(item['source_id'])['expected_duration_ms']==7200000


def test_only_hoster_sources_are_offered(store):
    """Solo alojador: enlace HTTP que empieza al instante, sin pares ni cuenta de pago.

    Un torrent depende de que haya quien lo comparta, y aunque el orden ya lo pone
    detrás, ofrecerlo gasta la espera de quien lo elige en un fallo probable. La regla
    es por fuente, no por proveedor: vale para el que se añada manana.
    """
    playback=Playback(store,Settings(),None)
    directo={'url':'https://alojador.example/pelicula.mp4','name':'1080p H264 AAC Espanol',
             'behaviorHints':{'videoSize':2*1024**3}}
    torrent={'infoHash':'c'*40,'name':'1080p H264 AAC Espanol 25','behaviorHints':{'videoSize':2*1024**3}}
    externo={'externalUrl':'https://sitio.example/ver','name':'Ver en el sitio'}
    items=playback.save_catalog('tt7654321','tt7654321',
                                [{'provider':'penguplay','streams':[torrent,directo,externo]}])
    assert [row['transport'] for row in items]==['http']

    # Un proveedor que solo trae P2P deja de aportar: no hay nada que ofrecer.
    solo_p2p=playback.save_catalog('tt7654322','tt7654322',
                                   [{'provider':'mediafusion','streams':[torrent]}])
    assert solo_p2p==[]


def test_provider_health_is_neutral_until_there_are_enough_attempts(store):
    assert store.provider_health()=={'penguplay':50,'hdhub':50,'flixstreams':50,'mediafusion':50}
    for _ in range(4):store.record_metric('penguplay','error')
    assert store.provider_health()['penguplay']==50
    for _ in range(16):store.record_metric('penguplay','prepared',3200)
    health=store.provider_health()
    assert 50<health['penguplay']<=100 and health['mediafusion']==50
    store.record_metric('inexistente','prepared')
    assert 'inexistente' not in store.provider_health()


def test_provider_speed_needs_several_samples_and_tracks_the_recent_average(store):
    store.record_speed('uno.cdn',1_000_000,'hdhub')
    store.record_speed('dos.cdn',3_000_000,'hdhub')
    assert store.provider_speeds()=={}, 'un único pico no puede recomendar al proveedor'
    store.record_speed('tres.cdn',3_000_000,'hdhub')
    learned=store.provider_speeds()
    assert set(learned)=={'hdhub'} and 1_000_000<learned['hdhub']<3_000_000
    assert store.host_speeds()['tres.cdn']==3_000_000


def test_playback_journal_survives_expired_sources_and_deduplicates_server_and_client(store):
    from app.playback_events import PlaybackEvents
    playback=Playback(store,Settings(),None)
    candidate=playback.save_catalog('tt1234567','tt1234567',[{'provider':'penguplay','streams':[
        {'url':'https://cdn.example/private-token.mp4','name':'1080p H264 AAC'}]}])[0]
    source=playback.source(candidate['source_id']);journal=PlaybackEvents(store)
    attempt='attempt-deduplication-0001';journal.begin('tv-a',source,attempt,'playback-chain-001','tv')
    journal.begin('tv-a',source,attempt,'playback-chain-001','tv')
    assert journal.record('tv-a',attempt,'client-first','first_frame',elapsed_ms=2300,position_ms=1000)
    assert not journal.record('tv-a',attempt,'client-first','first_frame',elapsed_ms=2300)
    assert journal.learn_failure('tv-a',attempt,'video_probe_failed')
    assert not journal.learn_failure('tv-a',attempt,'player_error')
    with pytest.raises(ProviderError):journal.context('tv-b',attempt)
    with pytest.raises(ProviderError):journal.begin('tv-b',source,attempt,'another-chain-001','tv')
    with store.connect() as conn:
        conn.execute('DELETE FROM media_source WHERE id=%s',(candidate['source_id'],))
        assert conn.execute('SELECT temporary_failures FROM source_reputation').fetchone()[0]==1
        persisted=conn.execute('SELECT row_to_json(a)::text FROM playback_attempt a').fetchone()[0]
        assert 'private-token' not in persisted and candidate['source_id'] not in persisted
        assert conn.execute('SELECT rank_position FROM playback_attempt').fetchone()[0]==1
    assert journal.record('tv-a',attempt,'switch','source_switch',switch_reason='automatic')
    assert journal.context('tv-a',attempt)['fingerprint']==source['fingerprint']
    summary=journal.summary()['providers'][0]
    assert summary['attempts']==1 and summary['first_frames']==1 and summary['automatic_switches']==1
    assert summary['average_startup_ms']==2300


def test_journal_failure_claim_is_atomic_and_stability_does_not_clear_audio_rejection(store):
    from concurrent.futures import ThreadPoolExecutor
    from app.playback_events import PlaybackEvents
    source={'content_id':'tt1234567','video_id':'tt1234567','provider':'penguplay','fingerprint':'a'*32}
    journal=PlaybackEvents(store);attempt='concurrent-attempt-00001'
    journal.begin('tv',source,attempt,'concurrent-playback-001','tv')
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _:journal.learn_failure('tv',attempt,'network_timeout'),range(2))).count(True)==1
    assert not journal.stable('tv',attempt)
    journal.record('tv',attempt,'first','first_frame')
    assert not journal.stable('tv',attempt), 'un fotograma no prueba estabilidad'
    assert journal.learn_failure('tv',attempt,'audio_language_mismatch'), 'una contradicción comprobada debe superar el fallo temporal'
    assert not journal.learn_failure('tv',attempt,'audio_language_mismatch')
    with store.connect() as conn:
        conn.execute("UPDATE playback_event SET created_at=now()-interval '61 seconds' WHERE event='first_frame'")
    journal.record('tv',attempt,'recent-error','error',error_code='network_timeout')
    assert not journal.stable('tv',attempt), 'un error reciente invalida el minuto estable'
    with store.connect() as conn:
        conn.execute("UPDATE playback_event SET created_at=now()-interval '61 seconds' WHERE event='error'")
    assert journal.stable('tv',attempt)
    assert store.source_reputation(source['video_id'])[source['fingerprint']]=='audio_rejected'
    with store.connect() as conn:
        assert conn.execute('SELECT temporary_until,temporary_failures FROM source_reputation').fetchone()==(None,0)


def test_temporary_penalty_expires_and_local_operations_do_not_extend_it(store):
    source={'video_id':'tt1234567','provider':'penguplay','fingerprint':'b'*32}
    for code in ('playback_stopped','cast_busy','cast_replaced','audio_track_not_found','disk_full'):
        assert not store.record_source_failure(source,code)
    for _ in range(8):assert store.record_source_failure(source,'video_probe_failed')
    with store.connect() as conn:
        minutes=conn.execute('SELECT extract(epoch FROM temporary_until-now())/60 FROM source_reputation').fetchone()[0]
        assert 59<minutes<=60
        conn.execute("UPDATE source_reputation SET temporary_until=now()-interval '1 second'")
    assert store.source_reputation('tt1234567')=={}


def test_observed_attempt_health_overrides_preparation_metrics_and_speed_expires(store):
    from app.playback_events import PlaybackEvents
    for _ in range(20):store.record_metric('penguplay','prepared')
    journal=PlaybackEvents(store)
    for i in range(5):
        attempt='observed-attempt-%016d'%i
        source={'content_id':'tt1234567','video_id':'tt1234567','provider':'penguplay','fingerprint':'c'*32}
        journal.begin('tv',source,attempt,attempt,'tv')
        journal.record('tv',attempt,'prepared','prepared','prepare')
        journal.record('tv',attempt,'failed','error',error_code='video_probe_failed')
    assert store.provider_health()['penguplay']<50
    store.record_speed('old.cdn',1000000,'penguplay')
    with store.connect() as conn:
        conn.execute("UPDATE source_speed SET updated_at=now()-interval '25 hours'")
        conn.execute("UPDATE provider_speed SET samples=3,updated_at=now()-interval '7 hours'")
    assert store.host_speeds()=={} and store.provider_speeds()=={}


def test_playback_event_api_authenticates_validates_and_records_preparation_failure(store):
    from test_api import client,_fake_playback,KEY
    with client(store) as connection:
        source_id='journal-api-source-00001';_fake_playback(connection.app,source_id)
        attempt='journal-api-attempt-00001'
        started=connection.post('/api/playback/start',json={'source_id':source_id,'attempt_id':attempt,
            'playback_id':'journal-api-playback-001','client':'tv'},headers={'X-API-Key':KEY})
        assert started.status_code==200 and started.json()['attempt_id']==attempt
        async def failed(*args):raise ProviderError('video_probe_failed','No entregó video.',504)
        connection.app.state.media_tools.cast_path=failed
        assert connection.post('/api/playback/cast',json={'source_id':source_id},headers={'X-API-Key':KEY}).status_code==504
        event={'attempt_id':attempt,'event_id':'automatic-switch','event':'source_switch','switch_reason':'automatic',
               'error_code':'video_probe_failed','position_ms':0}
        assert connection.post('/api/playback/event',json=event).status_code==401
        for expected in (True,False):
            response=connection.post('/api/playback/event',json=event,headers={'X-API-Key':KEY})
            assert response.status_code==200 and response.json()['accepted'] is expected
        invalid=dict(event,error_code='https://secret.example/token')
        assert connection.post('/api/playback/event',json=invalid,headers={'X-API-Key':KEY}).status_code==422
        assert connection.get('/api/playback/analysis',headers={'X-API-Key':KEY}).json()['providers'][0]['failed_attempts']==1
    with store.connect() as conn:
        assert conn.execute('SELECT temporary_failures FROM source_reputation').fetchone()[0]==1
        assert conn.execute("SELECT count(*) FROM playback_event WHERE event='source_switch'").fetchone()[0]==1


def test_source_availability_is_temporary_but_repeated_file_errors_keep_their_policy(store):
    playback=Playback(store,Settings(),None)
    preferred={'url':'https://cdn.example/token-uno','name':'Prueba.2026.1080p.H264.AAC',
               'behaviorHints':{'videoSize':2*1024**3}}
    fallback={'url':'https://otro.example/video','name':'Prueba.2026.720p.H264.AAC',
              'behaviorHints':{'videoSize':1*1024**3}}
    first=playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[preferred,fallback]}])
    source=playback.source(first[0]['source_id'])
    assert 'fingerprint' in source and first[0]['delivered']==1080

    # La disponibilidad ahora baja prioridad temporalmente, sin excluir la edición.
    store.record_source_failure(source,'preparation_timeout')
    store.record_source_failure(source,'video_probe_failed')
    store.record_source_failure(source,'playback_stopped')
    unchanged=playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[preferred,fallback]}])
    assert unchanged[0]['delivered']==720 and unchanged[1]['reputation']=='temporary'
    assert len(unchanged)==2
    store.record_source_success(source)
    assert store.source_reputation('tt1234567')=={}

    # Un fallo real necesita repetirse; la URL firmada puede cambiar y sigue siendo la
    # misma edición por su identidad y tamaño, no por su token temporal.
    store.record_source_failure(source,'cast_conversion_failed')
    assert store.source_reputation('tt1234567')=={}
    store.record_source_failure(source,'cast_conversion_failed')
    repeated={**preferred,'url':'https://cdn.example/token-dos'}
    lowered=playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[repeated,fallback]}])
    assert lowered[0]['delivered']==720 and lowered[1]['reputation']=='repeated'


def test_wrong_content_is_lowered_immediately_for_thirty_days(store):
    playback=Playback(store,Settings(),None)
    wrong={'url':'https://cdn.example/wrong','name':'Prueba.2026.1080p.H264.AAC',
           'behaviorHints':{'videoSize':2*1024**3}}
    fallback={'url':'https://otro.example/fine','name':'Prueba.2026.720p.H264.AAC',
              'behaviorHints':{'videoSize':1*1024**3}}
    item=playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[wrong,fallback]}])[0]
    store.record_source_failure(playback.source(item['source_id']),'content_mismatch')
    status=store.source_reputation('tt1234567')
    assert list(status.values())==['hard']
    reordered=playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[wrong,fallback]}])
    assert reordered[0]['delivered']==720 and reordered[1]['reputation']=='hard'
    with store.connect() as conn:
        remaining=conn.execute("SELECT extract(day FROM hard_until-now()) FROM source_reputation").fetchone()[0]
    assert 29<=remaining<=30


def test_rejected_real_audio_stays_out_of_catalog_after_url_renewal(store):
    """The Offer: la etiqueta bilingüe no reintroduce un archivo confirmado solo Hindi."""
    playback=Playback(store,Settings(),None)
    wrong={'url':'https://cdn.example/wrong.mkv?token=first','name':'Prueba.1080p.Hindi.English',
           'behaviorHints':{'filename':'Prueba.1080p.Hindi.English.mkv','videoSize':400000000}}
    valid={'url':'https://cdn.example/good.mkv','name':'Prueba.720p.English',
           'behaviorHints':{'filename':'Prueba.720p.English.mkv','videoSize':900000000}}
    groups=[{'provider':'penguplay','streams':[wrong,valid]}]
    items=playback.save_catalog('tt1234567','tt1234567',groups,original_languages={'english'})
    source=next(playback.source(row['source_id']) for row in items if row['delivered']==1080)
    assert store.record_source_failure(source,'audio_language_mismatch')
    assert store.source_reputation('tt1234567')[source['fingerprint']]=='audio_rejected'
    from app.providers.stremio import ProviderError
    with pytest.raises(ProviderError) as error:playback.source(items[0]['source_id'])
    assert error.value.code=='audio_language_mismatch'
    # Un timeout/red no borra esta comprobación ni condena a otra edición.
    assert store.record_source_failure(source,'video_probe_failed')
    wrong['url']='https://cdn.example/wrong.mkv?token=second'
    renewed=playback.save_catalog('tt1234567','tt1234567',groups,original_languages={'english'})
    assert len(renewed)==1 and renewed[0]['delivered']==720
    assert playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[wrong]}],original_languages={'english'})==[]
    with store.connect() as conn:
        conn.execute("UPDATE source_reputation SET audio_rejected_until=now()-interval '1 second'")
    assert len(playback.save_catalog('tt1234567','tt1234567',groups,original_languages={'english'}))==2


def test_archive_rejection_survives_new_urls_without_losing_progress_or_valid_alternatives(store):
    playback=Playback(store,Settings(),None)
    archive={'url':'https://cdn.example/opaque?token=first','name':'1080p English package'}
    valid={'url':'https://cdn.example/video.mkv','name':'720p English episode'}
    groups=[{'provider':'penguplay','streams':[archive,valid]}]
    items=playback.save_catalog('tt1234567','tt1234567',groups)
    bad=next(item for item in items if item['delivered']==1080)
    source=playback.source(bad['source_id'])
    library=Library(store)
    session=library.session('tt1234567','tt1234567','tv')['session_id']
    assert library.progress(session,1,600000,3600000,False,'tv',30000)['accepted']
    assert store.record_source_failure(source,'archive_source')
    # Red y estabilidad nunca eliminan un rechazo de archivo confirmado.
    store.record_source_failure(source,'network_timeout')
    store.record_source_success(source)
    assert store.source_reputation('tt1234567')[source['fingerprint']]=='archive_rejected'
    with pytest.raises(ProviderError) as caught:playback.source(bad['source_id'])
    assert caught.value.code=='archive_source'
    archive['url']='https://cdn.example/opaque?token=renewed'
    renewed=playback.save_catalog('tt1234567','tt1234567',groups)
    assert len(renewed)==1 and renewed[0]['delivered']==720
    assert playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[archive]}])==[]
    assert library.position('tt1234567')==600000
    with store.connect() as conn:
        conn.execute("UPDATE source_reputation SET archive_rejected_until=now()-interval '1 second'")
    assert len(playback.save_catalog('tt1234567','tt1234567',groups))==2


def test_declared_archives_never_reappear_as_fallback_or_through_old_sources(store):
    playback=Playback(store,Settings(),None)
    archive={'url':'https://cdn.example/file.zip','name':'1080p English'}
    assert playback.save_catalog('tt1234567','tt1234567',
        [{'provider':'penguplay','streams':[archive]}])==[]
    # Simula un enlace guardado por una versión anterior del backend.
    encrypted=store.cipher.encrypt(json.dumps(archive).encode()).decode()
    with store.connect() as conn:
        conn.execute('INSERT INTO media_source(id,content_id,video_id,encrypted_data,provider) VALUES (%s,%s,%s,%s,%s)',
            ('old-archive-source','tt1234567','tt1234567',encrypted,'penguplay'))
    with pytest.raises(ProviderError) as caught:playback.source('old-archive-source')
    assert caught.value.code=='archive_source'


def test_private_manifests_are_encrypted_and_never_returned_in_the_summary(store):
    secret='https://pengu.uk/private-pengu-token/manifest.json'
    store.save_provider(secret,{'provider':'PenguPlay','provider_key':'penguplay'},'admin-local','penguplay')
    with store.connect() as conn:
        row=conn.execute("SELECT encrypted_manifest,summary::text FROM provider_config WHERE provider='penguplay'").fetchone()
    assert 'private-pengu-token' not in row[0] and 'private-pengu-token' not in row[1]
    assert store.provider('penguplay')['manifest_url']==secret
    assert [row['provider'] for row in store.configured_providers()]==['penguplay']
    assert store.forget_provider('penguplay','admin-local') and store.provider('penguplay') is None


def test_tuning_a_channel_creates_a_playable_source_without_leaking_the_link(store):
    """Un canal se comporta como cualquier fuente: el enlace del emisor queda cifrado."""
    from app.television import Television
    channel={'id':'TVN.cl','name':'TVN','logo':'https://cdn.example/tvn.png','categories':['general'],
             'url':'https://cdn.example/senal-privada/master.m3u8'}
    result=Television(store,None)._save(channel)
    assert result['content']['id']=='tv:TVN.cl' and result['content']['type']=='tv'
    source=result['items'][0]
    assert source['provider']=='tv' and source['playable'] and 'senal-privada' not in json.dumps(result)
    with store.connect() as conn:
        row=conn.execute('SELECT encrypted_data,provider FROM media_source WHERE id=%s',(source['source_id'],)).fetchone()
    assert 'senal-privada' not in row[0] and row[1]=='tv'
    playback=Playback(store,Settings(),None)
    assert playback.source(source['source_id'])['stream']['url']==channel['url']


def test_tuning_keeps_verified_channel_alternatives_in_fallback_order(store):
    from app.television import Television
    channel={'id':'TVN.cl','name':'TVN','logo':'','categories':['general'],
             'url':'https://cdn.example/uno/master.m3u8',
             'urls':['https://cdn.example/uno/master.m3u8','https://cdn.example/dos/master.m3u8']}
    result=Television(store,None)._save(channel)
    assert len(result['items'])==2
    playback=Playback(store,Settings(),None)
    saved=[playback.source(item['source_id'])['stream']['url'] for item in result['items']]
    assert saved==channel['urls']
    assert all('cdn.example' not in json.dumps(item) for item in result['items'])


def test_live_channels_never_appear_in_continue_watching(store):
    from app.television import Television
    from app.library import Library
    channel={'id':'Mega.cl','name':'Mega','logo':'','categories':[],'url':'https://cdn.example/mega/master.m3u8'}
    source=Television(store,None)._save(channel)['items'][0]
    library=Library(store)
    session=library.session('tv:Mega.cl','tv:Mega.cl','phone-a')
    assert library.progress(session['session_id'],1,60000,0,False,'phone-a')['accepted']
    # La película sí debe aparecer; la señal en directo no.
    movie=library.session('tt1234567','tt1234567','phone-a')
    assert library.progress(movie['session_id'],1,30000,100000,False,'phone-a')['accepted']
    assert [row['id'] for row in library.continuing()]==['tt1234567']


def test_saved_position_is_offered_to_prepare_ahead(store):
    """El trabajo adelantado se hace desde donde se va a retomar, no desde el principio."""
    library=Library(store)
    session=library.session('tt1234567','tt1234567','phone')
    assert library.position('tt1234567')==0
    assert library.progress(session['session_id'],1,600000,7200000,False,'phone')['accepted']
    assert library.position('tt1234567')==600000
    assert library.progress(session['session_id'],2,7190000,7200000,True,'phone')['accepted']
    assert library.position('tt1234567')==0, 'lo terminado se vuelve a ver desde el inicio'


def test_accidental_opening_preserves_history_and_92_percent_completes(store):
    """Los 30 s son reproducción real, no la posición alcanzada mediante un salto."""
    library=Library(store)
    first=library.session('tt1234567','tt1234567','tv')['session_id']
    assert library.progress(first,1,600_000,1_000_000,False,'tv',30_000)['accepted']

    restarted=library.session('tt1234567','tt1234567','tv')
    assert restarted['position_ms']==600_000
    # Ver desde el inicio durante veinte segundos conserva el historial anterior.
    assert library.progress(restarted['session_id'],1,20_000,1_000_000,False,'tv',20_000)['accepted']
    assert library.position('tt1234567')==600_000
    # A partir del umbral ya es una reproducción nueva efectiva.
    assert library.progress(restarted['session_id'],2,30_000,1_000_000,False,'tv',30_000)['accepted']
    assert library.position('tt1234567')==30_000
    assert library.progress(restarted['session_id'],3,920_000,1_000_000,False,'tv',900_000)['accepted']
    detail=library.detail_progress('tt1234567')['videos']['tt1234567']
    assert detail['completed'] and detail['progress']==1.0
    assert library.position('tt1234567')==0

"""Pruebas API sin depender de servicios externos ni de credenciales reales."""
import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from urllib.parse import urlsplit

from app.main import create_app
from app.providers.stremio import PROVIDER_ORDER
from app.settings import Settings

KEY = "test-api-key-only-" * 3
MANIFESTS = {"mediafusion": {"id": "mediafusion", "resources": ["stream"], "catalogs": []},
             "penguplay": {"id": "com.penguplay", "resources": [{"name": "stream"}], "catalogs": []},
             "hdhub": {"id": "com.stremio.HdHub", "resources": ["stream", "catalog"], "catalogs": []},
             "flixstreams": {"id": "org.flickystream.addon", "resources": ["stream"], "catalogs": []}}


class MemoryStore:
    def __init__(self):
        self.saved = {}
        self.events = []
        self.metrics = []
        self.source_failures = []
        self.karaoke = {}
    def health(self): return True
    def provider(self, provider="mediafusion"): return self.saved.get(provider)
    def configured_providers(self): return [self.saved[key] for key in PROVIDER_ORDER if key in self.saved]
    def save_provider(self, url, summary, actor, provider="mediafusion"):
        self.saved[provider] = {"provider": provider, "manifest_url": url, "summary": summary}
    def forget_provider(self, provider, actor): return self.saved.pop(provider, None) is not None
    def record(self, actor, action, result="ok"): self.events.append((actor, action))
    def record_metric(self, provider, event, start_ms=None, bitrate_kbps=None): self.metrics.append((provider, event))
    def provider_health(self): return {key: 50 for key in PROVIDER_ORDER}
    def source_reputation(self, video_id): return {}
    def record_source_failure(self, source, code):
        self.source_failures.append((source.get('fingerprint'),code));return True
    def karaoke_preferences(self): return dict(self.karaoke)
    def set_karaoke_preference(self, song_id, video_id, status, actor):
        if status == 'favorite':
            for key, value in list(self.karaoke.items()):
                if key[0] == song_id and value == 'favorite' and key[1] != video_id:
                    del self.karaoke[key]
        if status == 'neutral': self.karaoke.pop((song_id, video_id), None)
        else: self.karaoke[(song_id, video_id)] = status
        self.events.append((actor, 'karaoke.version.' + status))


async def no_dns(host): pass


class MemoryJournal:
    """Doble de la persistencia; las transacciones reales se prueban en PostgreSQL."""
    def __init__(self,store):self.store=store;self.attempts={};self.events={};self.learned=set()
    def begin(self,actor,source,attempt_id,playback_id,client):
        self.attempts[attempt_id]=(actor,source)
    def context(self,actor,attempt_id):
        from app.providers.stremio import ProviderError
        owner,source=self.attempts.get(attempt_id,(None,None))
        if owner!=actor:raise ProviderError('attempt_not_found','No existe.',404)
        return source
    def record(self,actor,attempt_id,event_id,event,stage='player',**fields):
        self.context(actor,attempt_id)
        key=(attempt_id,event_id)
        if key in self.events:return False
        self.events[key]=(event,stage,fields);return True
    def learn_failure(self,actor,attempt_id,code):
        if attempt_id in self.learned:return False
        self.learned.add(attempt_id)
        return self.store.record_source_failure(self.context(actor,attempt_id),code)
    def stable(self,actor,attempt_id):return False


def responder(request):
    """Cada host declara la identidad real del addon que sirve."""
    host = request.url.host
    key = ("penguplay" if "pengu" in host else "hdhub" if "hdhub" in host
           else "flixstreams" if "flixnest" in host else "mediafusion")
    return httpx.Response(200, json=MANIFESTS[key])


def client(store=None, karaoke_root=None, openai_key=None):
    settings=Settings(api_key=KEY,encryption_key=Fernet.generate_key().decode(),database_url="postgresql://unused",
                      karaoke_root=karaoke_root or "Z:/sergioflix-test-karaoke-missing",
                      openai_api_key=openai_key,openai_model="test-model",_env_file=None)
    store=store or MemoryStore()
    app=create_app(settings,store,httpx.MockTransport(responder),no_dns)
    if isinstance(store,MemoryStore):app.state.playback_events=MemoryJournal(store)
    return TestClient(app)


def test_api_requires_key_but_panel_can_load():
    with client() as browser:
        assert browser.get("/").status_code == 200
        assert browser.get("/api/providers/mediafusion").status_code == 401
        assert browser.get("/api/providers/mediafusion",headers={"X-API-Key":KEY}).status_code == 200


def test_seek_confirmation_audits_validated_player_state_and_accepts_older_clients(monkeypatch):
    recorded = []
    monkeypatch.setattr('app.main.audit', lambda event, actor, **fields: recorded.append((event, actor, fields)))
    old = {'event': 'playback_seek_completed', 'operation_id': 21, 'target_ms': 1498496}
    observed = {**old, 'position_ms': 1500000, 'elapsed_ms': 1500, 'ready_state': 4,
                'paused': False, 'seeking': True, 'waiting': True, 'reason': 'clock',
                'url': 'https://private.invalid/do-not-record'}
    with client() as browser:
        assert browser.post('/api/events', json=observed).status_code == 401
        headers = {'X-API-Key': KEY}
        assert browser.post('/api/events', headers=headers, json=old).status_code == 204
        assert browser.post('/api/events', headers=headers, json=observed).status_code == 204
        for invalid in ({'ready_state': 5}, {'elapsed_ms': -1}, {'position_ms': -1},
                        {'reason': 'https://private.invalid/free-text'}):
            assert browser.post('/api/events', headers=headers, json={**observed, **invalid}).status_code == 422
    seek_events = [fields for event, actor, fields in recorded if event == 'playback_seek_completed']
    assert seek_events == [
        {'operation_id': 21, 'target_ms': 1498496},
        {key: value for key, value in observed.items() if key not in ('event', 'url')}]


def test_seek_audit_keeps_player_evidence_without_urls_or_secrets(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.observability import audit
    recorded = []
    monkeypatch.setattr('app.observability.make_logger', lambda: SimpleNamespace(info=recorded.append))
    audit('playback_seek_completed', 'device:test', position_ms=1500000, ready_state=4,
          paused=False, seeking=True, waiting=True, reason='clock',
          url='https://private.invalid', api_key='private-key')
    row = json.loads(recorded[0])
    assert row['position_ms'] == 1500000 and row['ready_state'] == 4
    assert row['seeking'] is True and row['waiting'] is True and row['paused'] is False
    assert 'private' not in recorded[0] and 'api_key' not in row and 'url' not in row


def test_synced_subtitle_route_passes_and_validates_seek_offset():
    from fastapi.responses import Response
    calls = []
    with client() as browser:
        async def deliver(token, offset):
            calls.append((token, offset))
            return Response("WEBVTT\n", media_type="text/vtt")
        browser.app.state.subtitles.deliver_synced = deliver
        assert browser.get('/subtitles/synced/' + 'a' * 40 + '.vtt?offset=900000').status_code == 200
        assert browser.get('/subtitles/synced/' + 'a' * 40 + '.vtt?offset=-500').status_code == 200
        assert browser.get('/subtitles/synced/' + 'a' * 40 + '.vtt?offset=99999999').status_code == 422
    assert calls == [('a' * 40, 900000), ('a' * 40, -500)]


def test_manual_subtitle_delivery_requires_the_explicit_flag_and_keeps_offset_validation():
    from fastapi.responses import Response
    calls=[]
    with client() as browser:
        async def deliver(source, offset=0, manual=False):
            calls.append((offset,manual));return Response('WEBVTT\n',media_type='text/vtt')
        browser.app.state.subtitles.deliver=deliver
        path='/subtitles/'+'a'*32+'.vtt'
        assert browser.get(path).status_code==200
        assert browser.get(path+'?manual=true&offset=500').status_code==200
        assert browser.get(path+'?manual=true&offset=99999999').status_code==422
    assert calls==[(0,False),(500,True)]


def test_daily_catalog_option_requires_auth_and_reaches_catalog(monkeypatch):
    calls = []

    async def page(self, media_type, skip, genre, search, daily=False):
        calls.append((media_type, skip, daily))
        return {'items': [], 'next_skip': None, 'stale': False}

    monkeypatch.setattr('app.catalog.Catalog.page', page)
    with client() as browser:
        assert browser.get('/api/catalog/movie?daily=true').status_code == 401
        assert browser.get('/api/catalog/movie?daily=true', headers={'X-API-Key': KEY}).status_code == 200
        assert browser.get('/api/catalog/series', headers={'X-API-Key': KEY}).status_code == 200
    assert calls == [('movie', 0, True), ('series', 0, False)]


def test_movie_sources_filter_identity_before_ranking(monkeypatch):
    """HDHub mezcla el reality y la película pese al IMDb correcto: se separan aquí."""
    from app.catalog import Catalog
    from tests.test_source_identity import MOVIE, REALITY

    async def detail(*args): return MOVIE
    async def sources(*args):
        return [{'provider': 'hdhub', 'health': 50, 'streams': [
            REALITY, {'description': 'Rules.of.the.Game.Manhunt.2021.1080p.mkv'},
            {'description': 'The.Game.1997.1080p.BluRay.mkv'},
        ]}], []

    monkeypatch.setattr(Catalog, 'detail', detail)
    monkeypatch.setattr('app.main.gather_streams', sources)
    monkeypatch.setattr('app.library.Library.preferences', lambda self,video_id: {})
    store = MemoryStore()
    store.save_provider('https://hdhub.example/manifest.json', {}, 'test', 'hdhub')
    store.host_speeds = lambda: {}
    store.provider_speeds = lambda: {}
    with client(store) as browser:
        captured = []
        def save_catalog(content_id, video_id, groups, *args):
            captured.extend(groups[0]['streams'])
            return [{'source_id': 'correct-movie'}]
        browser.app.state.playback.save_catalog = save_catalog
        response = browser.get('/api/playback/sources/movie/tt0119174', headers={'X-API-Key': KEY})
    assert response.status_code == 200
    assert captured == [{'description': 'The.Game.1997.1080p.BluRay.mkv'}]
    assert response.json()['found'] == 3 and response.json()['discarded'] == 2


def karaoke_files(tmp_path):
    """Colección mínima que también comprueba categorías y copias por resolución."""
    for artist in ('Queen', 'Shakira', 'Ricardo Montaner'):
        (tmp_path / artist).mkdir()
    (tmp_path / 'Queen' / 'Queen - Somebody to Love (Piano Karaoke) [PsJsweDb5GQ] 720p.mp4').write_bytes(b'0123456789')
    (tmp_path / 'Queen' / 'Queen - Somebody to Love (Piano Karaoke) [PsJsweDb5GQ] 1080p.mp4').write_bytes(b'abcdefghij')
    (tmp_path / 'Shakira' / 'Shakira - Antología (Karaoke) [FV8nes2mEUQ] 1080p.mp4').write_bytes(b'shakira')
    (tmp_path / 'Ricardo Montaner' / 'Cima del cielo.mp4').write_bytes(b'montaner')
    return tmp_path


def test_karaoke_scans_local_folders_and_builds_editorial_shelves(tmp_path):
    from app.karaoke import Karaoke
    root = karaoke_files(tmp_path)
    karaoke = Karaoke(MemoryStore(), Settings(
        api_key=KEY, encryption_key=Fernet.generate_key().decode(),
        database_url='postgresql://unused', karaoke_root=root, _env_file=None,
    ))
    catalog = karaoke.catalog()
    rows = {row['title']: row['items'] for row in catalog['items']}
    assert catalog['artist_count'] == 3
    assert [item['name'] for item in rows['Favoritos']] == ['Queen']
    assert [item['name'] for item in rows['Mujer Latina']] == ['Shakira']
    assert [item['name'] for item in rows['Ídolos que avivan la depresión']] == ['Ricardo Montaner']
    assert 'Otros artistas' not in rows
    artist = karaoke.artist('queen')
    assert artist['type'] == 'karaoke_artist'
    assert artist['releaseInfo'] == '1 canción'
    assert len(artist['episodes']) == 1
    assert artist['episodes'][0]['name'] == 'Somebody to Love'
    assert [item['delivered'] for item in artist['episodes'][0]['versions']] == [1080, 720]


def test_karaoke_recognizes_el_bloque_depresivo_aliases_and_quality(tmp_path):
    from app.karaoke import Karaoke
    folder = tmp_path / 'El Bloque Depresivo'
    folder.mkdir()
    (folder / 'KARAOKE BLOQUE DEPRESIVO - ＂REGRESA＂ [qnHYDiSk644] 360p.mp4').write_bytes(b'a')
    (folder / 'Sabor a mi - Macha y El Bloque Depresivo ( En vivo ) [oHrud2edL2Q] 960p.mp4').write_bytes(b'b')
    karaoke = Karaoke(MemoryStore(), Settings(
        api_key=KEY, encryption_key=Fernet.generate_key().decode(),
        database_url='postgresql://unused', karaoke_root=tmp_path, _env_file=None,
    ))

    rows = {row['title']: row['items'] for row in karaoke.catalog()['items']}
    assert [item['name'] for item in rows['Favoritos']] == ['El Bloque Depresivo']
    assert [item['name'] for item in rows['Ídolos que avivan la depresión']] == ['El Bloque Depresivo']
    assert 'Otros artistas' not in rows
    songs = karaoke.artist('el-bloque-depresivo')['episodes']
    assert [song['name'] for song in songs] == ['Regresa', 'Sabor a mi (En vivo)']
    assert songs[1]['versions'][0]['delivered'] == 960
    assert karaoke.artist('el-bloque-depresivo')['releaseInfo'] == '2 canciones'


def test_karaoke_assigns_known_local_artists_to_editorial_shelves(tmp_path):
    from app.karaoke import Karaoke
    expected = {
        'Arjona': 'Ídolos que avivan la depresión',
        'Chayanne': 'Ídolos que avivan la depresión',
        'Joaquin Sabina': 'Ídolos que avivan la depresión',
        'Los Angeles Negros': 'Ídolos que avivan la depresión',
        'Ricardo Montaner': 'Ídolos que avivan la depresión',
        'Ricky Martin': 'Ídolos que avivan la depresión',
        'Babasonicos': 'Rock en español',
        'La Oreja de Van Gogh': 'Rock en español',
        'Sui Generis': 'Rock en español',
        'Elton Jhon': 'Rock Clásico',
    }
    for artist in expected:
        folder = tmp_path / artist
        folder.mkdir()
        (folder / 'Canción.mp4').write_bytes(b'video')
    karaoke = Karaoke(MemoryStore(), Settings(
        api_key=KEY, encryption_key=Fernet.generate_key().decode(),
        database_url='postgresql://unused', karaoke_root=tmp_path, _env_file=None,
    ))

    rows = {row['title']: {item['name'] for item in row['items']}
            for row in karaoke.catalog()['items']}
    assert 'Otros artistas' not in rows
    for artist, shelf in expected.items():
        assert artist in rows[shelf]


def test_karaoke_assigns_new_artists_and_collections_without_fallback(tmp_path):
    from app.karaoke import Karaoke
    expected = {
        'La Sociedad': 'Ídolos que avivan la depresión',
        'Para 30ñeras': 'Rock en español',
        'Para 30ñeras - 2': 'Rock en español',
        'Elvis Presley': 'Rock Clásico',
        'Ex-Beatles': 'Rock Clásico',
        'Música de los 80s': 'Rock Clásico',
        'Rock Progresivo': 'Rock Clásico',
        'Toto': 'Rock Clásico',
        'Anglo Hot': 'Anglo alternativo',
        'Canciones de viejo Comunista': 'Canción social',
        'Clásicos de Disney': 'Especiales',
        'Novelas de los 90s': 'Especiales',
    }
    for artist in expected:
        folder = tmp_path / artist
        folder.mkdir()
        (folder / 'Canción.mp4').write_bytes(b'video')
    karaoke = Karaoke(MemoryStore(), Settings(
        api_key=KEY, encryption_key=Fernet.generate_key().decode(),
        database_url='postgresql://unused', karaoke_root=tmp_path, _env_file=None,
    ))

    rows = {row['title']: {item['name'] for item in row['items']}
            for row in karaoke.catalog()['items']}
    assert 'Otros artistas' not in rows
    for artist, shelf in expected.items():
        assert artist in rows[shelf]


def test_karaoke_is_authenticated_streams_ranges_and_remembers_versions(tmp_path):
    store = MemoryStore()
    root = karaoke_files(tmp_path)
    with client(store, root) as browser:
        assert browser.get('/api/karaoke').status_code == 401
        catalog = browser.get('/api/karaoke', headers={'X-API-Key': KEY}).json()
        assert catalog['artist_count'] == 3

        artist = browser.get('/api/karaoke/artists/queen', headers={'X-API-Key': KEY}).json()
        song = artist['episodes'][0]
        first, second = song['versions']
        marked = browser.put(
            f"/api/karaoke/songs/{song['id']}/versions/{second['version_id']}",
            headers={'X-API-Key': KEY}, json={'status': 'favorite'},
        )
        changed = marked.json()['episodes'][0]
        assert marked.status_code == 200
        assert changed['default_version_id'] == second['version_id']
        assert changed['versions'][0]['status'] == 'favorite'

        rejected = browser.put(
            f"/api/karaoke/songs/{song['id']}/versions/{first['version_id']}",
            headers={'X-API-Key': KEY}, json={'status': 'bad'},
        )
        assert rejected.status_code == 200
        statuses = {item['version_id']: item['status'] for item in rejected.json()['episodes'][0]['versions']}
        assert statuses == {first['version_id']: 'bad', second['version_id']: 'favorite'}
        assert [event[1] for event in store.events[-2:]] == [
            'karaoke.version.favorite', 'karaoke.version.bad'
        ]

        started = browser.post(
            f"/api/karaoke/songs/{song['id']}/play", headers={'X-API-Key': KEY},
            json={'version_id': second['version_id']},
        )
        assert started.status_code == 200
        url = urlsplit(started.json()['stream_url'])
        ranged = browser.get(url.path + '?' + url.query, headers={'Range': 'bytes=0-3'})
        assert ranged.status_code == 206
        assert ranged.content == b'0123'
        assert ranged.headers['accept-ranges'] == 'bytes'
        tampered = browser.get((url.path + '?' + url.query).replace('signature=', 'signature=0'))
        assert tampered.status_code == 422

        poster = browser.get('/karaoke/artists/queen/poster')
        assert poster.status_code == 200
        assert poster.headers['content-type'].startswith('image/svg+xml')


def test_pages_do_not_allow_external_frames_or_send_referrers():
    with client() as browser:
        panel = browser.get('/')
        television = browser.get('/tv/')
        assert panel.headers['referrer-policy'] == 'no-referrer'
        assert 'youtube.com' not in panel.headers['content-security-policy']
        assert television.headers['referrer-policy'] == 'no-referrer'
        assert 'youtube.com' not in television.headers['content-security-policy']
        assert "media-src 'self'" in television.headers['content-security-policy']


def test_assistant_fails_cleanly_when_its_private_key_is_not_configured():
    with client() as browser:
        response = browser.post(
            "/api/assistant/search", headers={"X-API-Key": KEY}, json={"prompt": "Busca Batman"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "assistant_not_configured"


def test_assistant_accepts_long_dictation_without_truncation_and_rejects_overflow(monkeypatch):
    interpreted = []

    async def interpret(self, prompt):
        interpreted.append(prompt)
        return {"message": "No encontré una coincidencia.", "searches": []}

    monkeypatch.setattr('app.assistant.Assistant.interpret', interpret)
    # Una descripción larga con acentos llega completa; el exceso no llama a la IA.
    prompt = ("Busco una película de ciencia ficción situada en Chile. " * 16)[:800]
    assert len(prompt) == 800 and not prompt.endswith(' ')
    store = MemoryStore()
    with client(store, openai_key="private-test-key") as browser:
        data = {"prompt": prompt}
        assert browser.post('/api/assistant/search', json=data).status_code == 401
        response = browser.post('/api/assistant/search', headers={"X-API-Key": KEY}, json=data)
        assert response.status_code == 200
        assert interpreted == [prompt]
        overflow = browser.post('/api/assistant/search', headers={"X-API-Key": KEY},
                                json={"prompt": prompt + "x"})
        assert overflow.status_code == 422
    assert interpreted == [prompt]
    assert [action for actor, action in store.events] == ['assistant.search']


def test_assistant_returns_twenty_ai_choices_with_authentication_and_audit(monkeypatch):
    interpreted = []

    async def interpret(self, prompt):
        interpreted.append(prompt)
        return {"message": "Te propongo estas películas.", "searches": [
            {"term": "Película " + str(index), "kind": "title", "media_type": "movie", "year": 2000,
             "reason": "La interpretación de Robert De Niro es el motivo de la elección " + str(index) + "."}
            for index in range(20)
        ]}

    async def page(self, media_type, skip, genre, search):
        return {"items": [
            {"id": "tt-secondary", "type": "movie", "name": "Resultado secundario"},
            {"id": "tt" + search.split()[-1], "type": "movie", "name": search, "releaseInfo": "2000"},
        ]}

    monkeypatch.setattr('app.assistant.Assistant.interpret', interpret)
    monkeypatch.setattr('app.catalog.Catalog.page', page)
    store = MemoryStore()
    with client(store, openai_key="private-test-key") as browser:
        data = {"prompt": "Muéstrame películas de Robert De Niro"}
        assert browser.post('/api/assistant/search', json=data).status_code == 401
        assert interpreted == []
        response = browser.post('/api/assistant/search', headers={"X-API-Key": KEY}, json=data)
    assert response.status_code == 200
    assert [item['id'] for item in response.json()['items']] == ['tt' + str(index) for index in range(20)]
    assert [item['assistant_reason'] for item in response.json()['items']] == [
        "La interpretación de Robert De Niro es el motivo de la elección " + str(index) + "."
        for index in range(20)]
    assert response.json()['episode'] is None
    assert interpreted == [data['prompt']]
    assert any(action == 'assistant.search' for actor, action in store.events)
    assert 'private-test-key' not in response.text


def test_configuration_is_never_echoed():
    secret="https://mediafusion.elfhosted.com/secret-provider-config/manifest.json"
    with client() as browser:
        saved=browser.put("/api/providers/mediafusion",headers={"X-API-Key":KEY},json={"manifest_url":secret})
        assert saved.status_code == 200
        status=browser.get("/api/providers/mediafusion",headers={"X-API-Key":KEY})
        assert status.json()["configured"] is True
        assert "secret-provider-config" not in saved.text + status.text


def test_validation_does_not_echo_input():
    with client() as browser:
        response=browser.put("/api/providers/mediafusion",headers={"X-API-Key":KEY},json={"manifest_url":{"token":"test-secret"}})
        assert response.status_code == 422
        assert "test-secret" not in response.text


def test_each_provider_has_its_own_configuration_slot():
    store=MemoryStore()
    with client(store) as browser:
        for provider,url in (("penguplay","https://pengu.uk/private-pengu-config/manifest.json"),
                             ("hdhub","https://hdhub.thevolecitor.qzz.io/private-hdhub-config/manifest.json"),
                             ("flixstreams","https://flixnest.app/flix-streams/private-flix-config/manifest.json"),
                             ("mediafusion","https://mediafusion.elfhosted.com/private-mf-config/manifest.json")):
            saved=browser.put(f"/api/providers/{provider}",headers={"X-API-Key":KEY},json={"manifest_url":url})
            assert saved.status_code == 200, saved.text
            assert "private-" not in saved.text
        listing=browser.get("/api/providers",headers={"X-API-Key":KEY}).json()
        assert listing["order"] == ["penguplay","hdhub","flixstreams","mediafusion"]
        assert all(item["configured"] for item in listing["items"])
        assert [item["priority"] for item in listing["items"]] == [12,10,6,0]
        assert "private-" not in str(listing)


def test_provider_rejects_a_manifest_from_another_addon():
    with client() as browser:
        # El host es de PenguPlay pero la ruta se declara como Flix-Streams: cada uno valida el suyo.
        wrong_host=browser.put("/api/providers/flixstreams",headers={"X-API-Key":KEY},
                               json={"manifest_url":"https://pengu.uk/config/manifest.json"})
        assert wrong_host.status_code == 422 and wrong_host.json()["code"] == "invalid_manifest"
        wrong_identity=browser.put("/api/providers/penguplay",headers={"X-API-Key":KEY},
                                   json={"manifest_url":"https://www.pengu.uk/config/manifest.json"})
        assert wrong_identity.status_code == 200
        unknown=browser.put("/api/providers/otro",headers={"X-API-Key":KEY},json={"manifest_url":"https://pengu.uk/manifest.json"})
        assert unknown.status_code == 422


def test_forgetting_a_provider_keeps_the_others():
    store=MemoryStore()
    with client(store) as browser:
        browser.put("/api/providers/penguplay",headers={"X-API-Key":KEY},json={"manifest_url":"https://pengu.uk/a/manifest.json"})
        browser.put("/api/providers/mediafusion",headers={"X-API-Key":KEY},json={"manifest_url":"https://mediafusion.elfhosted.com/b/manifest.json"})
        assert browser.delete("/api/providers/penguplay",headers={"X-API-Key":KEY}).json()["removed"] is True
        assert [row["provider"] for row in store.configured_providers()] == ["mediafusion"]


def test_provider_list_shows_the_manifest_summary_but_never_the_url():
    with client() as browser:
        browser.put("/api/providers/penguplay",headers={"X-API-Key":KEY},
                    json={"manifest_url":"https://pengu.uk/private-pengu-token/manifest.json"})
        listing=browser.get("/api/providers",headers={"X-API-Key":KEY})
        pengu=next(item for item in listing.json()["items"] if item["provider"]=="penguplay")
        assert pengu["summary"]["provider_key"]=="penguplay" and pengu["summary"]["catalog_count"]==0
        assert "private-pengu-token" not in listing.text and "manifest_url" not in listing.text


def _fake_playback(app,source_id):
    """Deja el arranque de reproducción listo para probar sin proveedores ni FFmpeg."""
    source={'content_id':'tt1234567','video_id':'tt1234567','stream':{'url':'https://cdn.example/v.mp4'},
            'provider':'penguplay','fingerprint':'f'*32}
    app.state.playback.source=lambda key:source
    async def ready(key):return {'content_type':'video/mp4'}
    app.state.playback.ready=ready
    app.state.playback.begin=lambda key,actor:{'session_id':'s1','position_ms':0,'video_id':'tt1234567',
        'source_id':key,'stream_url':'http://local/media/'+key,'content_type':'video/mp4'}


def test_preparation_is_shared_between_start_and_later_subtitle_request():
    import asyncio,time
    with client() as connection:
        _fake_playback(connection.app,'a'*32)
        subs=connection.app.state.subtitles; calls=[]
        async def tracks(key):
            calls.append(key);await asyncio.sleep(0.1)
            return [{'sync_token':'b'*40,'verified':True,'verification':{'revision':4}}]
        subs.tracks=tracks
        original=subs.prepared
        async def short_budget(key,budget=0.1):return await original(key,0.001)
        subs.prepared=short_budget
        started=connection.post('/api/playback/start',json={'source_id':'a'*32},headers={'X-API-Key':KEY}).json()
        assert started['subtitles_pending']
        ready=connection.get('/api/playback/subtitles/'+'a'*32,headers={'X-API-Key':KEY}).json()
        for _ in range(10):
            if not ready['pending']:break
            time.sleep(.03)
            ready=connection.get('/api/playback/subtitles/'+'a'*32,headers={'X-API-Key':KEY}).json()
        assert ready['items'][0]['sync_token']=='b'*40 and not ready['pending'] and calls==['a'*32]


def test_subtitle_requests_cannot_access_another_actors_active_source():
    with client() as connection:
        _fake_playback(connection.app,'a'*32)
        async def empty(_):return []
        connection.app.state.subtitles.tracks=empty
        connection.post('/api/playback/start',json={'source_id':'a'*32},headers={'X-API-Key':KEY})
        connection.app.state.control.sources['a'*32]['actor']='another-phone'
        assert connection.get('/api/playback/subtitles/'+'a'*32,headers={'X-API-Key':KEY}).status_code==403
        assert connection.post('/api/playback/subtitles/'+'a'*32+'/retry',headers={'X-API-Key':KEY}).status_code==403
        assert connection.post('/api/playback/subtitles/next',json={'source_id':'a'*32},headers={'X-API-Key':KEY}).status_code==403


def test_subtitle_options_do_not_turn_into_verified_tracks_in_api_or_legacy_recovery():
    with client() as connection:
        source='a'*32;headers={'X-API-Key':KEY}
        _fake_playback(connection.app,source)
        async def prepared(*args):return {'subtitles':[], 'subtitles_pending':False,
            'subtitle_state':'available','subtitle_reason':'audio_read_timeout','subtitle_message':'Elige una alternativa.',
            'subtitle_alternatives':[{'manual_only':True,'verified':False,'source_id':'b'*32}]}
        connection.app.state.subtitles.prepared=prepared
        started=connection.post('/api/playback/start',json={'source_id':source},headers=headers).json()
        assert not started['subtitles'] and started['subtitle_alternatives'][0]['manual_only']
        assert connection.get('/api/playback/subtitles/'+source).status_code==401
        found=connection.get('/api/playback/subtitles/'+source,headers=headers).json()
        assert not found['items'] and found['alternatives'][0]['manual_only']
        recovered=connection.post('/api/playback/subtitles/sync',headers=headers,
            json={'source_id':source,'subtitle_source_id':'b'*32,'recover':True}).json()
        assert not recovered['verified'] and not recovered['changed'] and 'item' not in recovered


def test_status_is_quick_and_manual_retry_is_the_only_way_to_start_again(monkeypatch):
    import asyncio,time
    monkeypatch.setattr('app.subtitles.PREPARATION_SECONDS',.3)
    store=MemoryStore()
    with client(store) as connection:
        source='q'*32
        _fake_playback(connection.app,source)
        subs=connection.app.state.subtitles;queries=[]
        async def slow(key):queries.append(key);await asyncio.Event().wait()
        subs.tracks=slow
        headers={'X-API-Key':KEY}
        start=connection.post('/api/playback/start',json={'source_id':source},headers=headers)
        assert start.json()['subtitles_pending']
        before=time.monotonic()
        response=connection.get('/api/playback/subtitles/'+source,headers=headers)
        assert time.monotonic()-before<.2 and response.json()['pending']
        time.sleep(.35)
        final=connection.get('/api/playback/subtitles/'+source,headers=headers).json()
        assert final['state']=='failed' and not final['pending'] and final['items']==[]
        previous=len(queries)
        for _ in range(3):
            assert connection.get('/api/playback/subtitles/'+source,headers=headers).json()==final
        assert len(queries)==previous
        previous_job=subs.preparations[(source,None)]
        retry=connection.post('/api/playback/subtitles/'+source+'/retry',headers=headers)
        assert retry.status_code==200 and retry.json()['pending']
        # Un trabajo admite dos rondas: bajo carga la segunda puede comenzar
        # antes de que el hilo de TestClient vuelva a ejecutar esta aserción.
        assert previous < len(queries) <= previous+2
        assert subs.start_prepare(source) is not previous_job
        assert ('admin-local','playback.subtitle_search_retry') in store.events


def test_next_subtitle_preparation_is_idempotent_and_does_not_activate_another_video():
    with client() as connection:
        _fake_playback(connection.app,'a'*32)
        async def empty(_):return []
        connection.app.state.subtitles.tracks=empty
        connection.app.state.playback.next_video=lambda _:None
        connection.post('/api/playback/start',json={'source_id':'a'*32},headers={'X-API-Key':KEY})
        actors=dict(connection.app.state.control.actors)
        first=connection.post('/api/playback/subtitles/next',json={'source_id':'a'*32},headers={'X-API-Key':KEY})
        second=connection.post('/api/playback/subtitles/next',json={'source_id':'a'*32},headers={'X-API-Key':KEY})
        assert first.status_code==202 and first.json()['scheduled']
        assert not second.json()['scheduled'] and connection.app.state.control.actors==actors


def test_caption_telemetry_rejects_incomplete_or_impossible_measurements():
    from pydantic import ValidationError
    from app.main import PlaybackEventRequest
    import pytest
    base={'attempt_id':'caption-attempt-0001','event_id':'1','event':'subtitle_render'}
    with pytest.raises(ValidationError):PlaybackEventRequest(**base)
    fields={'render_context':'seek','render_samples':1,'render_lag_ms':10,'render_total_lag_ms':100}
    with pytest.raises(ValidationError):PlaybackEventRequest(**base,**fields)
    fields['render_total_lag_ms']=10
    assert PlaybackEventRequest(**base,**fields).render_context=='seek'


def test_playback_start_delivers_subtitles_when_the_provider_answers():
    with client() as connection:
        _fake_playback(connection.app,'a'*32)
        async def tracks(key):return [{'url':'http://local/subtitles/x.vtt','language':'es'}]
        connection.app.state.subtitles.tracks=tracks
        response=connection.post('/api/playback/start',json={'source_id':'a'*32},headers={'X-API-Key':KEY})
        assert response.status_code==200
        assert response.json()['subtitles'][0]['language']=='es'


def test_a_slow_subtitle_provider_never_holds_back_the_video():
    """Se buscan en paralelo con la fuente: si tardan, la película arranca igual."""
    import asyncio,time
    with client() as connection:
        _fake_playback(connection.app,'b'*32)
        async def slow(key):
            await asyncio.sleep(30);return [{'url':'tarde'}]
        connection.app.state.subtitles.tracks=slow
        started=time.monotonic()
        response=connection.post('/api/playback/start',json={'source_id':'b'*32},headers={'X-API-Key':KEY})
        assert response.status_code==200 and response.json()['subtitles']==[]
        assert time.monotonic()-started<10, 'la espera de los subtítulos no se suma a la del video'


def test_a_broken_subtitle_provider_does_not_break_the_start():
    with client() as connection:
        _fake_playback(connection.app,'c'*32)
        async def broken(key):raise RuntimeError('proveedor caído')
        connection.app.state.subtitles.tracks=broken
        response=connection.post('/api/playback/start',json={'source_id':'c'*32},headers={'X-API-Key':KEY})
        assert response.status_code==200 and response.json()['subtitles']==[]


def test_audio_tracks_can_be_listed_and_changed_at_the_same_position():
    store=MemoryStore()
    with client(store) as connection:
        source_id='z'*32
        _fake_playback(connection.app,source_id)
        assert connection.post('/api/playback/start',json={'source_id':source_id},
                               headers={'X-API-Key':KEY}).status_code==200
        async def tracks(key):
            return {'items':[{'stream_index':1,'label':'Hindi','selected':False},
                             {'stream_index':2,'label':'Inglés','selected':True}],
                    'audio_stream_index':2}
        calls=[]
        async def cast_path(*args):
            calls.append(args)
            return {'stream_url':'http://local/cast/index.m3u8','content_type':'application/vnd.apple.mpegurl',
                    'adapted':True,'offset_ms':123000,'audio_stream_index':1,'audio_tracks':[]}
        connection.app.state.media_tools.audio_tracks=tracks
        connection.app.state.media_tools.cast_path=cast_path
        listed=connection.get('/api/playback/audio/'+source_id,headers={'X-API-Key':KEY})
        assert listed.status_code==200 and listed.json()['audio_stream_index']==2
        assert ('admin-local','playback.audio.list') in store.events
        changed=connection.post('/api/playback/cast',json={'source_id':source_id,'position_ms':123000,
                                                           'audio_stream_index':1},
                                headers={'X-API-Key':KEY})
        assert changed.status_code==200 and calls[0][-1]==1 and calls[0][1]==123000
        assert ('admin-local','playback.audio.change') in store.events


def test_audio_inspection_rejection_is_learned_once_for_the_actual_source():
    from app.providers.stremio import ProviderError
    store=MemoryStore()
    with client(store) as connection:
        source_id='a'*32;_fake_playback(connection.app,source_id)
        assert connection.post('/api/playback/start',json={'source_id':source_id},headers={'X-API-Key':KEY}).status_code==200
        async def rejected(key):raise ProviderError('audio_language_mismatch','Solo Hindi.',422)
        connection.app.state.media_tools.audio_tracks=rejected
        for _ in range(2):
            result=connection.get('/api/playback/audio/'+source_id,headers={'X-API-Key':KEY})
            assert result.status_code==422 and result.json()['code']=='audio_language_mismatch'
        assert store.source_failures==[('f'*32,'audio_language_mismatch')]


def test_a_real_cast_failure_is_assigned_to_the_exact_source():
    from app.providers.stremio import ProviderError
    store=MemoryStore()
    with client(store) as connection:
        source_id='d'*32
        _fake_playback(connection.app,source_id)
        started=connection.post('/api/playback/start',json={'source_id':source_id},headers={'X-API-Key':KEY})
        assert started.status_code==200
        async def wrong(*args):
            raise ProviderError('content_mismatch','No corresponde.',422)
        connection.app.state.control.require(source_id)['upstream_failure']={'code':'upstream_missing','status':404}
        connection.app.state.media_tools.cast_path=wrong
        failed=connection.post('/api/playback/cast',json={'source_id':source_id},headers={'X-API-Key':KEY})
        assert failed.status_code==422
        assert store.source_failures==[('f'*32,'content_mismatch')]
        errors=[entry for entry in connection.app.state.playback_events.events.values() if entry[0]=='error']
        assert errors[0][2]['error_code']=='content_mismatch' and errors[0][2]['upstream_status'] is None


def test_proxy_cause_is_journaled_once_and_not_replaced_by_ffprobe_failure():
    from app.providers.stremio import ProviderError
    store=MemoryStore()
    with client(store) as connection:
        source_id='u'*32
        _fake_playback(connection.app,source_id)
        assert connection.post('/api/playback/start',json={'source_id':source_id},
                               headers={'X-API-Key':KEY}).status_code==200
        async def unavailable(key,request):
            state=connection.app.state.control.require(key)
            state['upstream_failure']={'code':'upstream_missing','status':404}
            raise ProviderError('upstream_missing','No entrega el video.',502)
        connection.app.state.playback.media=unavailable
        for _ in range(2):
            assert connection.get('/media/'+source_id).status_code==502
        async def failed_probe(*args):raise ProviderError('video_probe_failed','Inspección fallida.',504)
        connection.app.state.media_tools.cast_path=failed_probe
        assert connection.post('/api/playback/cast',json={'source_id':source_id},
                               headers={'X-API-Key':KEY}).status_code==504
        assert store.source_failures==[('f'*32,'upstream_missing')]
        errors=[entry for entry in connection.app.state.playback_events.events.values() if entry[0]=='error']
        assert len(errors)==1 and errors[0][2]['error_code']=='upstream_missing'
        assert errors[0][2]['upstream_status']==404


def test_failed_seek_keeps_active_session_and_reports_recovery(tmp_path):
    from app.providers.stremio import ProviderError
    with client() as connection:
        source_id='seek-api-regression-source'
        _fake_playback(connection.app,source_id)
        assert connection.post('/api/playback/start',json={'source_id':source_id},
                               headers={'X-API-Key':KEY}).status_code==200
        connection.app.state.control.sources[source_id]['stage']='streaming'
        tools=connection.app.state.media_tools
        tools.root=tmp_path
        folder=tmp_path/source_id;folder.mkdir()
        (folder/'offset').write_text('1800000')
        async def failed_seek(*args):
            (folder/'offset').write_text('1790000')
            raise ProviderError('cast_buffer_timeout','No entregó el punto.',504)
        tools.cast_path=failed_seek
        result=connection.post('/api/playback/cast',json={'source_id':source_id,'position_ms':1790000},
                               headers={'X-API-Key':KEY})
        assert result.status_code==504 and result.json()['recovery_required']
        assert connection.app.state.control.status('admin-local')['active']
        assert connection.app.state.control.sources[source_id]['stage']=='streaming'
        # Rechazar una pista inexistente conserva el material, no exige reiniciarlo.
        async def missing_audio(*args):raise ProviderError('audio_track_not_found','No existe.',422)
        tools.cast_path=missing_audio
        result=connection.post('/api/playback/cast',json={'source_id':source_id,'audio_stream_index':99},
                               headers={'X-API-Key':KEY})
        assert result.status_code==422 and not result.json()['recovery_required']
        assert connection.app.state.control.status('admin-local')['active']


def test_retired_cast_versions_do_not_serve_new_bytes_or_damage_source_reputation(tmp_path):
    store=MemoryStore()
    with client(store) as connection:
        source_id='retired-fragment-test-source'
        _fake_playback(connection.app,source_id)
        assert connection.post('/api/playback/start',json={'source_id':source_id},
                               headers={'X-API-Key':KEY}).status_code==200
        tools=connection.app.state.media_tools;tools.root=tmp_path
        folder=tmp_path/source_id;folder.mkdir()
        (folder/'offset').write_text('1700000')
        (folder/'conversion').write_text(tools.conversion_identity(None))
        (folder/'part000000.ts').write_bytes(b'new-video')
        (folder/'index.m3u8').write_text('#EXTM3U\n#EXTINF:4,\npart000000.ts\n#EXT-X-ENDLIST\n')
        assert connection.get('/cast/'+source_id+'/part000000.ts?v=1800000').status_code==410
        assert connection.get('/cast/'+source_id+'/index.m3u8?generation=1800000').status_code==410
        assert connection.get('/cast/'+source_id+'/part000000.ts?v=1700000').content==b'new-video'
        url='/cast/'+source_id+'/part000000.ts?v=1700000'
        partial=connection.get(url,headers={'Range':'bytes=0-2'})
        assert partial.status_code==206 and partial.content==b'new'
        assert partial.headers['content-range']=='bytes 0-2/9'
        assert connection.head(url).headers['content-length']=='9'
        assert connection.head(url).content==b''
        multipart=connection.get(url,headers={'Range':'bytes=0-2,4-8'})
        assert multipart.status_code==206 and b'new' in multipart.content and b'video' in multipart.content
        assert len(multipart.content)==int(multipart.headers['content-length'])
        assert connection.get(url,headers={'Range':'bytes=99-100'}).status_code==416
        assert store.source_failures==[]


def test_repeated_segment_requests_count_as_one_playback_failure():
    from app.providers.stremio import ProviderError
    store=MemoryStore()
    with client(store) as connection:
        source_id='e'*32
        _fake_playback(connection.app,source_id)
        assert connection.post('/api/playback/start',json={'source_id':source_id},headers={'X-API-Key':KEY}).status_code==200
        async def broken(*args):
            raise ProviderError('cast_conversion_failed','Se interrumpió.',502)
        connection.app.state.media_tools.segment=broken
        first=connection.get('/cast/'+source_id+'/index.m3u8')
        second=connection.get('/cast/'+source_id+'/index.m3u8')
        assert first.status_code==502 and second.status_code==502
        assert store.source_failures==[('f'*32,'cast_conversion_failed')]


def test_progress_uses_the_catalog_runtime_when_the_file_duration_is_missing():
    """Sin denominador no hay barra: la duración del archivo llega en cero al principio."""
    from app.library import watched_fraction
    ficha={'runtime':'1h 40min'}
    assert watched_fraction(ficha,30*60*1000,0)==0.3
    # La del archivo manda cuando existe: un montaje del director dura más que el nominal.
    assert watched_fraction({'runtime':'160 min'},90*60*1000,180*60*1000)==0.5
    # Lo terminado se marca entero aunque la posición se quedara corta.
    assert watched_fraction(ficha,10,0,completed=True)==1.0
    assert watched_fraction({},60*60*1000,0)==0.0 and watched_fraction(ficha,0,0)==0.0
    # Una apertura breve no existe para el historial y los créditos ya cuentan como final.
    assert watched_fraction(ficha,29_999,100_000)==0.0
    assert watched_fraction(ficha,91_999,100_000)<1.0
    assert watched_fraction(ficha,92_000,100_000)==1.0


def test_progress_preferences_are_authenticated_validated_and_optional(monkeypatch):
    calls=[]
    def progress(self,*args):calls.append(args);return {'accepted':True}
    monkeypatch.setattr('app.library.Library.progress',progress)
    body={'session_id':'test-session','sequence':1,'position_ms':600000,'duration_ms':3600000,
          'watched_ms':30000,'preferences':{'source_id':'a'*32,'subtitle_offset_ms':-2500,
                                           'subtitle_manual':True,'subtitle_key':'b'*32}}
    with client() as browser:
        assert browser.put('/api/playback/progress',json=body).status_code==401
        headers={'X-API-Key':KEY}
        assert browser.put('/api/playback/progress',json=body,headers=headers).json()['accepted']
        assert calls[-1][-1]['subtitle_offset_ms']==-2500
        assert browser.put('/api/playback/progress',json=body | {'preferences':body['preferences'] |
            {'subtitle_sync_token':'../private/file'}},headers=headers).status_code==422
        assert browser.put('/api/playback/progress',json=body | {'preferences':body['preferences'] |
            {'audio_stream_index':256}},headers=headers).status_code==422
        del body['preferences']
        assert browser.put('/api/playback/progress',json=body,headers=headers).json()['accepted']
        assert calls[-1][-1] is None

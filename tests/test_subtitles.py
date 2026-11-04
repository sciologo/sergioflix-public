"""Pruebas de la búsqueda ampliada y del resguardo del subtítulo original."""
import asyncio
import io
import json
import zipfile
import pytest
from pathlib import Path
from types import SimpleNamespace

from pydantic import SecretStr

from app.subtitles import Subtitles, release_score, subtitle_payload
from app.subtitle_identity import identity_reason, filename
from app.providers.stremio import ProviderError


class PlaybackStub:
    def __init__(self, sources=None, key=""):
        self.sources = sources or {}
        self.saved = []
        self.settings = SimpleNamespace(
            subdl_api_key=SecretStr(key) if key else None,
            lan_ip="192.168.50.10",
            media_port=8765,
        )

    def source(self, source_id):
        return self.sources[source_id]

    def save_sources(self, content_id, video_id, streams):
        self.saved.extend(streams)
        return [{"source_id": ("s%031d" % len(self.saved))}]


class SubDLClient:
    def __init__(self, data):
        self.data = data
        self.params = None

    async def get(self, url, params=None):
        self.params = params
        return SimpleNamespace(status_code=200, json=lambda: self.data)


class SubtitleCatClient:
    async def get(self, url, params=None):
        if "index.php" in url:
            return SimpleNamespace(status_code=200, text='''
                <a href="subs/1/Show.S02E03.1080p.WEB.html">Show.S02E03.1080p.WEB</a>
                <a href="subs/2/Show.S02E04.1080p.WEB.html">Show.S02E04.1080p.WEB</a>
            ''')
        return SimpleNamespace(status_code=200, text='''
            <a href="subs/3/Show.S02E03.1080p.WEB-es.srt">Español</a>
            <a href="subs/4/Show.S02E03.1080p.WEB-es-419.srt">Español latino</a>
            <a href="subs/5/Show.S02E03.1080p.WEB-en.srt">English</a>
        ''')


def test_release_matching_prefers_the_same_edition():
    video = "The.Movie.2024.1080p.WEB-DL.DDP5.1.H264-GROUP.mkv"
    exact = "The.Movie.2024.1080p.WEB-DL.H264-GROUP.es.srt"
    wrong = "The.Movie.2024.720p.BluRay.x265-OTHER.es.srt"
    assert release_score(video, exact) > release_score(video, wrong)


def test_zip_chooses_the_member_matching_the_video_and_converts_to_vtt():
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Movie.720p.OTHER.srt", "1\n00:00:01,000 --> 00:00:02,000\nIncorrecto\n")
        archive.writestr("Movie.1080p.WEB-DL.GROUP.srt", "1\n00:00:03,000 --> 00:00:04,000\nCorrecto\n")
    result = subtitle_payload(package.getvalue(), "Movie.1080p.WEB-DL.GROUP.mkv")
    assert result.startswith("WEBVTT") and "Correcto" in result and "Incorrecto" not in result


def test_subdl_search_is_limited_to_the_exact_episode():
    client = SubDLClient({
        "status": True,
        "subtitles": [{"unpack_files": [
            {"url": "/subtitle/a/1", "name": "Show.S02E03.srt", "language": "ES", "season": 2, "episode": 3},
            {"url": "/subtitle/a/2", "name": "Show.S02E04.srt", "language": "ES", "season": 2, "episode": 4},
        ]}],
    })
    playback = PlaybackStub(key="clave-subdl")
    subtitles = Subtitles(client, playback)
    found = asyncio.run(subtitles._subdl({"video_id": "tt1234567:2:3"}))
    assert [row["name"] for row in found] == ["Show.S02E03.srt"]
    assert client.params["imdb_id"] == "tt1234567"
    assert client.params["season_number"] == 2 and client.params["episode_number"] == 3
    assert (found[0]['season'], found[0]['episode']) == (2, 3)


def test_subdl_keeps_regular_and_hearing_impaired_subtitles():
    class Client(SubDLClient):
        async def get(self, url, params=None):
            rows = [{'name': 'Movie.2002.srt', 'url': '/subtitle/regular', 'hi': False},
                    {'name': 'Movie.2002.SDH.srt', 'url': '/subtitle/sdh', 'hi': True}]
            if 'hi' in params: rows = [row for row in rows if row['hi'] == bool(params['hi'])]
            self.data = {'status': True, 'subtitles': rows}
            return await super().get(url, params)
    client = Client({})
    subtitles = Subtitles(client, PlaybackStub(key='private-key'))
    found = asyncio.run(subtitles._subdl({'video_id': 'tt0217505'}))
    assert len(found) == 2 and 'hi' not in client.params
    assert [row['hearing_impaired'] for row in found] == [False, True]


@pytest.mark.parametrize('status,reason', [(401,'subtitle_provider_auth'), (403,'subtitle_provider_auth'),
    (429,'subtitle_provider_limited'), (503,'subtitle_provider_response')])
def test_subdl_errors_are_not_reported_as_missing_subtitles(status, reason):
    class Client:
        async def get(self, *args, **kwargs): return SimpleNamespace(status_code=status)
    subtitles = Subtitles(Client(), PlaybackStub(key='private-key'))
    with pytest.raises(ProviderError, match=reason):
        asyncio.run(subtitles._subdl({'video_id': 'tt0217505'}))


def test_subdl_invalid_payload_is_a_provider_failure():
    subtitles = Subtitles(SubDLClient({'status': False, 'error': 'private details'}), PlaybackStub(key='private-key'))
    with pytest.raises(ProviderError, match='subtitle_provider_response') as caught:
        asyncio.run(subtitles._subdl({'video_id': 'tt0217505'}))
    assert 'private' not in caught.value.message


def test_subtitlecat_keeps_spanish_alternatives_for_the_exact_episode():
    subtitles = Subtitles(SubtitleCatClient(), PlaybackStub())
    found = asyncio.run(subtitles._subtitlecat({
        "video_id": "tt1234567:2:3", "content_name": "Show",
    }))
    assert [row["name"] for row in found] == [
        "Show.S02E03.1080p.WEB.es.srt", "Show.S02E03.1080p.WEB.es-419.srt",
    ]
    assert all(row["url"].startswith("https://www.subtitlecat.com/subs/") for row in found)


def test_subtitlecat_movies_require_title_and_year_and_keep_downloads_on_known_host():
    import httpx
    requests = []
    def respond(request):
        requests.append(request.url.path)
        if request.url.path == '/index.php':
            return httpx.Response(200, text='''
                <a href="/subs/1/Gangs.of.New.York.2002.html">Gangs.of.New.York.2002</a>
                <a href="/subs/2/Gangs.of.New.York.2003.html">Gangs.of.New.York.2003</a>
                <a href="/subs/3/Gangs.of.New.York.html">Gangs.of.New.York</a>
                <a href="/subs/4/Other.Movie.2002.html">Other.Movie.2002</a>
                <a href="https://foreign.example/subs/5/movie.html">Gangs.of.New.York.2002</a>
            ''')
        return httpx.Response(200, text='''
            <a href="/subs/1/Gangs.of.New.York.2002-es.srt">Español</a>
            <a href="https://foreign.example/subs/movie-es.srt">Español</a>
            <a href="/subs/1/Gangs.of.New.York.2002-en.srt">English</a>
        ''')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            subs = Subtitles(client, PlaybackStub())
            return await subs._subtitlecat({'video_id':'tt0217505', 'content_name':'Gangs of New York',
                'stream':{}, 'subtitle_context':{'releaseInfo':'2002'}})
    found = asyncio.run(run())
    assert len(found) == 1 and found[0]['name'] == 'Gangs.of.New.York.2002.es.srt'
    assert requests == ['/index.php', '/subs/1/Gangs.of.New.York.2002.html']


def test_subtitlecat_does_not_guess_movie_year_or_hide_provider_errors():
    class Client:
        async def get(self, *args, **kwargs): return SimpleNamespace(status_code=503)
    subs = Subtitles(Client(), PlaybackStub())
    source = {'video_id':'tt0217505','content_name':'Gangs of New York','stream':{}}
    assert asyncio.run(subs._subtitlecat(source)) == []
    source['stream'] = {'name':'Gangs.of.New.York.2002.mp4'}
    with pytest.raises(ProviderError, match='subtitle_provider_response'):
        asyncio.run(subs._subtitlecat(source))


def test_automatic_sync_creates_a_copy_and_keeps_the_original(tmp_path):
    video_id, subtitle_id = "v" * 32, "s" * 32
    common = {"content_id": "tt1234567", "video_id": "tt1234567"}
    playback = PlaybackStub({
        video_id: {**common, "provider": "penguplay", "stream": {"url": "https://video.example/movie.mp4"}},
        subtitle_id: {**common, "provider": "subtitulos", "stream": {"url": "https://subs.example/movie.srt"}},
    })
    subtitles = Subtitles(SimpleNamespace(), playback)
    subtitles.sync_root = Path(tmp_path)

    async def download(_):
        return "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola\n"

    async def align(_, input_path, output_path, audio_index=None):
        original = input_path.read_text(encoding="utf-8")
        output_path.write_text(original.replace("00:00:01,000", "00:00:03,000").replace("00:00:02,000", "00:00:04,000"), encoding="utf-8")

    subtitles._download_vtt, subtitles._run_sync = download, align
    result = asyncio.run(subtitles.sync(video_id, subtitle_id))
    assert result["changed"] is True and result["item"]["automatic"] is True
    assert playback.sources[subtitle_id]["stream"]["url"] == "https://subs.example/movie.srt"
    assert list(tmp_path.glob("*.vtt")) and not list(tmp_path.glob("*.srt"))


def test_opensubtitles_includes_latin_spanish(monkeypatch):
    async def search(self, url):
        return {"subtitles": [
            {"url": "https://subs.example/latino", "lang": "spl", "subtitleFileName": "Show.S01E02.srt"},
            {"url": "https://subs.example/es", "lang": "spa"},
            {"url": "https://subs.example/en", "lang": "eng"},
        ]}
    monkeypatch.setattr("app.subtitles.OpenSubtitles._json", search)
    rows = asyncio.run(Subtitles(None, PlaybackStub())._opensubtitles({"video_id": "tt1234567:1:2"}))
    assert len(rows) == 2 and rows[0]["language_label"] == "Español latino"


def test_tracks_use_release_description_and_keep_distinct_downloads_with_same_name():
    playback = PlaybackStub({"video": {
        "content_id": "tt1234567", "video_id": "tt1234567:1:2",
        "stream": {"name": "HdHub 720p", "description": "Show.S01E02.1080p.WEB-DL.GROUP.mkv"},
    }})
    subtitles = Subtitles(None, playback)
    async def found(_):
        return [{"url": "https://subs.example/" + url, "name": name,
                 "provider": "OpenSubtitles", "hearing_impaired": False}
                for url, name in (("bluray", "Show.S01E02.720p.BluRay.OTHER.srt"),
                                  ("web", "Show.S01E02.1080p.WEB-DL.GROUP.srt"),
                                  ("one", "subtitulo.srt"), ("two", "subtitulo.srt"),
                                  ("one", "subtitulo.srt"))]
    async def empty(_): return []
    subtitles._opensubtitles, subtitles._subdl, subtitles._subtitlecat = found, empty, empty
    tracks = asyncio.run(subtitles.tracks("video"))
    assert len(tracks) == 4
    assert playback.saved[0]["url"].endswith("/web")
    assert "mejor coincidencia" not in tracks[0]["label"]


@pytest.mark.parametrize("offset,expected", [(600000, "00:00:03.000"), (-1000, "00:10:04.000")])
def test_synced_subtitles_keep_seek_and_manual_offset(tmp_path, offset, expected):
    subtitles = Subtitles(None, PlaybackStub())
    subtitles.sync_root = tmp_path
    token = "a" * 40
    original = "WEBVTT\n\n00:10:03.000 --> 00:10:04.000\nHola\n"
    (tmp_path / (token + ".vtt")).write_text(original, encoding="utf-8")
    (tmp_path / (token + '.json')).write_text('{"verification_revision":4}', encoding='utf-8')
    response = asyncio.run(subtitles.deliver_synced(token, offset))
    assert expected in response.body.decode()
    assert (tmp_path / (token + ".vtt")).read_text(encoding="utf-8") == original


def test_unreliable_alignment_does_not_publish_a_synced_track(tmp_path):
    common = {"content_id": "tt1234567", "video_id": "tt1234567"}
    playback = PlaybackStub({"v": {**common, "provider": "penguplay", "stream": {}},
                             "s": {**common, "provider": "subtitulos", "stream": {}}})
    subtitles = Subtitles(None, playback)
    subtitles.sync_root = tmp_path
    async def download(_): return "WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nHola\n"
    async def reject(*_): return {"accepted": False, "reason": "validation_failed"}
    subtitles._download_vtt, subtitles._run_sync = download, reject
    assert asyncio.run(subtitles.sync("v", "s"))["changed"] is False
    assert not list(tmp_path.glob("*.vtt")) and not list(tmp_path.glob("*.srt"))


def test_calibration_cache_survives_temporary_ids_but_separates_audio(tmp_path):
    common = {"content_id": "tt1234567", "video_id": "tt1234567"}
    video = {**common, "provider": "penguplay", "fingerprint": "same-edition",
             "stream": {'infoHash': 'a' * 40, 'fileIdx': 0}}
    subtitle = {**common, "provider": "subtitulos", "stream": {}}
    playback = PlaybackStub({"v1": video, "v2": video, "s1": subtitle, "s2": subtitle})
    media = SimpleNamespace(audio_selections={"v1": 2, "v2": 2})
    subtitles = Subtitles(None, playback, media)
    subtitles.sync_root = tmp_path
    calls = []
    async def download(_): return "WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nHola\n"
    async def align(video_id, input_path, output_path, audio_index=None):
        calls.append(video_id)
        output_path.write_text("1\n00:00:04,000 --> 00:00:05,000\nHola\n", encoding="utf-8")
        return {"accepted": True, "scale": 1.0, "offset_seconds": 1.0}
    subtitles._download_vtt, subtitles._run_sync = download, align
    async def run():
        first = await subtitles.sync("v1", "s1")
        second = await subtitles.sync("v2", "s2")
        assert first["item"]["url"] == second["item"]["url"] and calls == ["v1"]
        media.audio_selections["v2"] = 3
        third = await subtitles.sync("v2", "s2")
        assert third["item"]["url"] != first["item"]["url"] and calls == ["v1", "v2"]
    asyncio.run(run())


def office_source(extended=False):
    return {'content_id': 'tt0386676', 'video_id': 'tt0386676:2:1', 'fingerprint': 'office-edition',
            'provider': 'penguplay', 'content_name': 'The Office',
            'stream': {'behaviorHints': {'filename': 'The.Office.' + ('Extended.Cut.' if extended else '') +
                       'S02E01.The.Dundies.720p.WEBRip.x265.mkv|^|720pHEVC'}},
            'subtitle_context': {'name': 'The Office', 'country': 'United States',
                'releaseInfo': '2005–2013', 'episode_name': 'The Dundies', 'ambiguous': True}}


@pytest.mark.parametrize('name,reason', [
    ('The Office UK S02E01 Merger.srt', 'series_country_mismatch'),
    ('The.Office.2001.S02E01.Merger.srt', 'series_year_mismatch'),
    ('The_Office_0203.srt', 'episode_mismatch'),
    ('The.Office.S02E02.srt', 'episode_mismatch'),
    ('The Office - S02E01 - Merger WEBRip-1080p-x265-AAC.es.srt', 'identity_unverified'),
    ('The Office - S02E01.srt', 'identity_unverified'),
    ('The.Office.S02E01.The.Dundies.srt', ''),
    ('The Office (US) S02E01 Los Dundies.srt', ''),
    ('The.Office.Extended.Cut.S02E01.The.Dundies.srt', 'edition_mismatch'),
])
def test_office_identity_precedes_codec_matching(name, reason):
    assert identity_reason(office_source(), {'name': name}) == reason


def test_provider_filename_excludes_display_card():
    assert filename(office_source()['stream']).endswith('.mkv')


def test_an_identical_but_validated_timeline_is_published(tmp_path):
    common = {'content_id': 'tt1234567', 'video_id': 'tt1234567'}
    playback = PlaybackStub({'v': {**common, 'provider': 'penguplay', 'stream': {}},
                             's': {**common, 'provider': 'subtitulos', 'stream': {}}})
    subtitles = Subtitles(None, playback); subtitles.sync_root = tmp_path
    async def download(_): return 'WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nHola\n'
    async def already_correct(_, source, output, audio_index=None):
        output.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
        return {'accepted': True, 'scale': 1, 'offset_seconds': 0}
    subtitles._download_vtt, subtitles._run_sync = download, already_correct
    result = asyncio.run(subtitles.sync('v', 's'))
    assert result['verified'] and result['item']['verified']
    assert list(tmp_path.glob('*.vtt'))


def test_short_subtitles_for_extended_office_are_rejected_before_voice_analysis(tmp_path):
    video = office_source(extended=True)
    sub = {'content_id': video['content_id'], 'video_id': video['video_id'], 'provider': 'subtitulos',
           'stream': {'name': 'The Office (US) S02E01 The Dundies.srt'}}
    playback = PlaybackStub({'v': video, 's': sub})
    class Media:
        audio_selections = {}
        async def inspect(self, _): return {'format': {'duration': '1921'}}
    subtitles = Subtitles(None, playback, Media()); subtitles.sync_root = tmp_path
    async def download(_): return 'WEBVTT\n\n00:20:03.000 --> 00:20:04.935\nFin\n'
    async def must_not_run(*_): pytest.fail('No se debe analizar audio para una pista de otro montaje')
    subtitles._download_vtt, subtitles._run_sync = download, must_not_run
    result = asyncio.run(subtitles.sync('v', 's'))
    assert not result['verified'] and result['reason'] == 'subtitle_duration_mismatch'


def test_resolver_recovers_from_a_missing_first_download():
    subtitles = Subtitles(None, PlaybackStub())
    calls = []
    async def sync(_, subtitle):
        calls.append(subtitle)
        if subtitle == 'missing': raise ProviderError('subtitle_unavailable', 'No disponible')
        return {'verified': True, 'item': {'key': 'correct'}}
    async def tracks(_): return [{'source_id': 'correct'}]
    subtitles.sync, subtitles.tracks = sync, tracks
    assert asyncio.run(subtitles.resolve('video', 'missing'))['verified']
    assert calls == ['missing', 'correct']


def test_confirmed_mismatch_is_remembered_and_separated_by_audio(tmp_path):
    video = office_source()
    sub = {'content_id': video['content_id'], 'video_id': video['video_id'], 'provider': 'subtitulos',
           'stream': {'name': 'The Office (US) S02E01 The Dundies.srt'}}
    playback = PlaybackStub({'v': video, 's': sub}); verdicts = {}; calls = []
    class Store:
        def subtitle_verdict(self, token): return verdicts.get(token)
        def save_subtitle_verdict(self, token, video, key, status, reason):
            verdicts[token] = {'status': status, 'reason': reason}
    playback.store = Store()
    subtitles = Subtitles(None, playback, SimpleNamespace(audio_selections={'v': 1})); subtitles.sync_root = tmp_path
    async def download(_): return 'WEBVTT\n\n00:20:03.000 --> 00:20:04.000\nFin\n'
    async def reject(*_): calls.append(1); return {'accepted': False, 'reason': 'subtitle_duration_mismatch'}
    subtitles._download_vtt, subtitles._run_sync = download, reject
    async def exercise():
        await subtitles.sync('v', 's'); await subtitles.sync('v', 's')
        assert len(calls) == 1
        subtitles.media_tools.audio_selections['v'] = 2
        await subtitles.sync('v', 's')
        assert len(calls) == 2
    asyncio.run(exercise())


def test_old_calibration_is_not_treated_as_verified(tmp_path):
    subtitles = Subtitles(None, PlaybackStub()); subtitles.sync_root = tmp_path
    token = 'a' * 40
    (tmp_path / (token + '.vtt')).write_text('WEBVTT\n', encoding='utf-8')
    with pytest.raises(ProviderError, match='subtitle_revalidation_required'):
        asyncio.run(subtitles.deliver_synced(token))


def test_opensubtitles_receives_file_identity(monkeypatch):
    requests = []
    async def search(_, url): requests.append(url); return {'subtitles': []}
    monkeypatch.setattr('app.subtitles.OpenSubtitles._json', search)
    source = office_source()
    source['stream']['behaviorHints'].update(videoHash='0123456789abcdef', videoSize=1234567)
    asyncio.run(Subtitles(None, PlaybackStub())._opensubtitles(source))
    assert '/videoHash=0123456789abcdef&videoSize=1234567&filename=' in requests[0]
    assert '%7C%5E%7C' not in requests[0]


def test_editions_prefer_normal_only_when_extended_has_no_candidates():
    subtitles = Subtitles(None, PlaybackStub())
    normal = office_source()['stream']; cut = office_source(extended=True)['stream']
    async def search(_): return [{'name': 'The.Office.S02E01.The.Dundies.srt'}]
    subtitles._search = search
    asyncio.run(subtitles.prefer_editions([{'streams': [cut, normal]}], 'tt0386676:2:1', {'name':'The Office'}))
    assert cut['subtitleEditionUnavailable'] and not normal.get('subtitleEditionUnavailable')


def test_raw_legacy_subtitle_cannot_bypass_validation():
    subtitles = Subtitles(None, PlaybackStub({'s': {'provider':'subtitulos','stream':{}}}))
    with pytest.raises(ProviderError, match='subtitle_verification_required'):
        asyncio.run(subtitles.deliver('s'))


def test_spanish_embedded_track_is_extracted_from_its_own_file(tmp_path):
    import subprocess
    import sys
    from app.media_tools import executable
    try:
        ffmpeg = executable('ffmpeg')
    except ProviderError:
        pytest.skip('FFmpeg no está disponible')
    original = tmp_path / 'spanish.srt'
    original.write_text('1\n00:00:01,000 --> 00:00:02,000\nTexto del archivo\n', encoding='utf-8')
    movie = tmp_path / 'video.mkv'
    subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','color=size=160x90:duration=3',
        '-i',str(original),'-c:v','libx264','-preset','ultrafast','-c:s','srt',
        '-metadata:s:s:0','language=spa',str(movie)], check=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    video = office_source()
    embedded = {'embeddedVideo':'v', 'embeddedIndex':1, 'videoFingerprint':video['fingerprint']}
    class Media:
        audio_selections = {}
        def source_url(self, _): return str(movie)
    subtitles = Subtitles(None, PlaybackStub({'v':video}), Media()); subtitles.sync_root = tmp_path
    text = asyncio.run(subtitles._download_embedded(embedded))
    assert text.startswith('WEBVTT') and 'Texto del archivo' in text
    from app.subtitles import shift
    assert '00:00:00.000 --> 00:00:01.000' in shift(text, 1000)


def test_changing_audio_during_analysis_cannot_publish_or_remember_a_calibration(tmp_path):
    common = {'content_id':'tt1234567','video_id':'tt1234567'}
    playback = PlaybackStub({'v':{**common,'provider':'penguplay','stream':{}},
                             's':{**common,'provider':'subtitulos','stream':{}}})
    media = SimpleNamespace(audio_selections={'v':2})
    subtitles = Subtitles(None,playback,media); subtitles.sync_root=tmp_path
    async def download(_): return 'WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nHola\n'
    async def change_audio(_, source, output, audio_index):
        assert audio_index == 2
        media.audio_selections['v']=3
        output.write_text(source.read_text(encoding='utf-8'),encoding='utf-8')
        return {'accepted':True}
    subtitles._download_vtt, subtitles._run_sync = download, change_audio
    result=asyncio.run(subtitles.sync('v','s'))
    assert not result['verified'] and result['reason']=='reference_changed'
    assert not list(tmp_path.glob('*.vtt')) and not list(tmp_path.glob('*.json'))

"""El IMDb correcto no garantiza que un proveedor devuelva el archivo correcto."""
import json
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.playback import Playback
from app.providers.stremio import ProviderError
from app.source_identity import movie_source_mismatch

MOVIE = {'type': 'movie', 'name': 'The Game', 'releaseInfo': '1997'}
REALITY = {'description': '[PixelDrain] [3.78 GB] '
           'Squid.Game.The.Challenge.S02E01.Six.Legs.1080p.NF.WEB-DL.H.264.mkv'}


def test_reality_episodes_cannot_be_offered_as_a_movie():
    assert movie_source_mismatch(REALITY, MOVIE) == 'episode_for_movie'
    assert movie_source_mismatch({'behaviorHints': {'filename': 'The.Game.2x01.720p.mkv'}}, MOVIE)


@pytest.mark.parametrize('filename', [
    'Squid.Game.The.Challenge.S01.1080p.x264.Hindi.English.zip',
    'Squid.Game.The.Challenge.Season.1.720p.zip',
])
def test_complete_reality_seasons_cannot_be_offered_as_a_movie(filename):
    assert movie_source_mismatch({'description': filename}, MOVIE) == 'episode_for_movie'


def test_a_different_release_year_is_a_contradiction():
    stream = {'description': 'Rules.of.the.Game.Manhunt.2021.1080p.WEB-DL.mkv'}
    assert movie_source_mismatch(stream, MOVIE) == 'wrong_release_year'


@pytest.mark.parametrize('stream', [
    {'description': 'The.Game.1997.1080p.BluRay.Hindi.English.x264.mkv'},
    {'description': 'The Game (1997)\n1080p MP4 BluRay'},
    {'behaviorHints': {'filename': 'The.Game.1997.1080p.BluRay.x264.YIFY.mp4'}},
    {'description': 'El juego.1998.720p.BluRay.mkv'},
    {'name': '1080p', 'url': 'https://cdn.example/video.mp4'},
])
def test_valid_translated_and_unidentified_sources_are_preserved(stream):
    assert movie_source_mismatch(stream, MOVIE) == ''


def test_series_and_numbers_in_movie_titles_do_not_get_rejected():
    assert movie_source_mismatch(REALITY, {'type': 'series', 'releaseInfo': '2021'}) == ''
    assert movie_source_mismatch({'title': '1917.1080p.mkv'},
                                 {'type': 'movie', 'name': '1917', 'releaseInfo': '2019'}) == ''
    assert movie_source_mismatch({'title': 'Blade.Runner.2049.2017.1080p.mkv'},
                                 {'type': 'movie', 'name': 'Blade Runner 2049', 'releaseInfo': '2017'}) == ''
    assert movie_source_mismatch(REALITY, {}) == ''


def test_previously_saved_wrong_sources_are_blocked_but_subtitles_are_preserved():
    cipher = Fernet(Fernet.generate_key())
    encrypted = cipher.encrypt(json.dumps(REALITY).encode()).decode()
    # Columnas reales del SELECT, incluidas reputación y posición del ranking.
    row = ['tt0119174', 'tt0119174', encrypted, 'hdhub', MOVIE, 'fingerprint', 'The Game', None, 1, {}, None, False]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, *args): return self
        def fetchone(self): return row

    store = SimpleNamespace(cipher=cipher, connect=Connection)
    playback = Playback(store, SimpleNamespace(), None)
    with pytest.raises(ProviderError) as error:
        playback.source('saved-source')
    assert error.value.code == 'content_mismatch'
    row[3] = 'subtitulos'
    assert playback.source('saved-source')['stream'] == REALITY

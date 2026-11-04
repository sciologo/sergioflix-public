"""La lista de canales solo admite señales chilenas utilizables y nunca publica sus enlaces."""
import asyncio

import pytest

from app.collections import COLLECTIONS, entries
from app.showcase import AWARD_WINNERS, IMDB_TOP_MOVIES, IMDB_TOP_SERIES
from app.television import (FAVORITE_CHANNELS, FEATURED, SPORTS_FIRST, Television, normalize,
                            pluto_route, pluto_sports)

CHANNELS = [
    {'id': 'TVN.cl', 'name': 'TVN', 'country': 'CL', 'categories': ['general']},
    {'id': 'Mega.cl', 'name': 'Mega', 'country': 'CL', 'categories': ['general']},
    {'id': 'Zzz.cl', 'name': 'Zzz Local', 'country': 'CL', 'categories': ['general']},
    {'id': 'Vieja.cl', 'name': 'Vieja', 'country': 'CL', 'closed': '2020-01-01'},
    {'id': 'Telemundo.us', 'name': 'Telemundo', 'country': 'US', 'categories': ['general']},
]
LOGOS = [{'channel': 'TVN.cl', 'in_use': True, 'url': 'https://cdn.example/tvn.png'},
         {'channel': 'Mega.cl', 'in_use': False, 'url': 'https://cdn.example/viejo.png'}]


def streams(*pairs):
    return [{'channel': channel, 'url': url} for channel, url in pairs]


def test_only_chilean_open_channels_with_a_usable_link_are_offered():
    rows = normalize(CHANNELS, streams(
        ('TVN.cl', 'https://cdn.example/tvn/master.m3u8'),
        ('Mega.cl', 'https://cdn.example/mega/index.m3u8'),
        ('Zzz.cl', 'http://cdn.example/inseguro/index.m3u8'),
        ('Vieja.cl', 'https://cdn.example/vieja/index.m3u8'),
        ('Telemundo.us', 'https://cdn.example/us/index.m3u8')), LOGOS)
    assert [row['name'] for row in rows] == ['TVN', 'Mega']
    # Un canal cerrado, uno extranjero y uno sin HTTPS quedan fuera.
    assert all(row['url'].startswith('https://') and '.m3u8' in row['url'] for row in rows)


def test_known_channels_come_first_and_the_rest_go_in_alphabetical_order():
    channels = CHANNELS + [{'id': 'Aaa.cl', 'name': 'Aaa TV', 'country': 'CL', 'categories': []}]
    rows = normalize(channels, streams(
        ('Aaa.cl', 'https://cdn.example/a/index.m3u8'),
        ('Zzz.cl', 'https://cdn.example/z/index.m3u8'),
        ('Mega.cl', 'https://cdn.example/mega/index.m3u8'),
        ('TVN.cl', 'https://cdn.example/tvn/index.m3u8')), LOGOS)
    assert [row['name'] for row in rows] == ['TVN', 'Mega', 'Aaa TV', 'Zzz Local']
    assert FEATURED[0] == 'TVN'


def test_only_logos_in_use_and_served_por_https_are_kept():
    rows = normalize(CHANNELS, streams(('TVN.cl', 'https://cdn.example/tvn/index.m3u8'),
                                       ('Mega.cl', 'https://cdn.example/mega/index.m3u8')),
                     LOGOS + [{'channel': 'Zzz.cl', 'in_use': True, 'url': 'http://cdn.example/inseguro.png'}])
    art = {row['name']: row['logo'] for row in rows}
    assert art['TVN'] == 'https://cdn.example/tvn.png'
    assert art['Mega'] == ''


def test_a_channel_keeps_all_usable_streams_in_fallback_order():
    rows = normalize(CHANNELS, streams(('TVN.cl', 'https://cdn.example/uno/index.m3u8'),
                                       ('TVN.cl', 'https://cdn.example/dos/index.m3u8')), LOGOS)
    assert len(rows) == 1 and rows[0]['url'].endswith('uno/index.m3u8')
    assert [url.rsplit('/', 2)[-2] for url in rows[0]['urls']] == ['uno', 'dos']


def test_requested_tv_favorites_have_a_stable_order():
    assert FAVORITE_CHANNELS == ('TVN.cl', 'ChileVision.cl', 'Canal13.cl', 'Mega.cl', 'CNNenEspanol.us')


def test_favorites_keep_only_working_manifests_and_are_marked_for_the_tv_shelf():
    class CheckedTelevision(Television):
        async def _official_url(self,channel_id):
            return f'https://official.example/{channel_id}/master.m3u8'

        async def _playlist_works(self,url):
            return 'intersurtv' not in url

    existing=normalize(CHANNELS,streams(
        ('TVN.cl','https://iptv2.intersurtv.cl/TVN/index.m3u8'),
        ('Mega.cl','https://cdn.example/mega/index.m3u8')),LOGOS)
    rows=asyncio.run(CheckedTelevision(None,None)._favorites(existing,'nacional'))
    favorites=[row for row in rows if row.get('favorite')]
    assert [row['id'] for row in favorites]==list(FAVORITE_CHANNELS[:4])
    assert all(row['favorite_rank']==index for index,row in enumerate(favorites))
    assert all('intersurtv' not in url for row in favorites for url in row['urls'])


@pytest.mark.parametrize('url', ['https://cdn.example/video.mp4', 'ftp://cdn.example/x.m3u8',
                                 'https://user:key@cdn.example/x.m3u8', 'https://cdn.example/' + 'a' * 4100 + '.m3u8'])
def test_links_that_are_not_a_plain_https_playlist_are_rejected(url):
    assert normalize(CHANNELS, streams(('TVN.cl', url)), LOGOS) == []


def test_the_new_shelves_lead_the_home_screen():
    titles = [title for title, _, __ in COLLECTIONS]
    assert titles[:3] == ['Mejores películas · premiadas', 'Mejores películas según IMDb', 'Mejores series según IMDb']
    assert len(IMDB_TOP_MOVIES) == 50 and len(IMDB_TOP_SERIES) == 50 and len(AWARD_WINNERS) == 84
    # Sin repetidos dentro de cada lista y con identificadores IMDb válidos.
    for shelf in (AWARD_WINNERS, IMDB_TOP_MOVIES, IMDB_TOP_SERIES):
        assert len(set(shelf)) == len(shelf)
        assert all(item.startswith('tt') and item[2:].isdigit() for item in shelf)


def test_the_themed_shelves_close_the_home_screen():
    titles = [title for title, _, __ in COLLECTIONS]
    assert titles[3:] == ['Para que no te gane la depresión', 'Me estremezco compadre',
                          'Para niños rata', 'Para ñoños']
    for title, media_type, items in COLLECTIONS:
        pairs = entries(media_type, items)
        # Sin repetidos dentro de la lista y cada ficha se pedirá con su propio tipo.
        assert len({identifier for identifier, __ in pairs}) == len(pairs), title
        assert all(identifier.startswith('tt') and identifier[2:].isdigit()
                   and item_type in ('movie', 'series') for identifier, item_type in pairs), title


FEEDS = [{'channel': 'TVN.cl', 'languages': ['spa']}, {'channel': 'Telemundo.us', 'languages': ['spa']},
         {'channel': 'BBC.uk', 'languages': ['eng']}]
WORLD = CHANNELS + [{'id': 'BBC.uk', 'name': 'BBC News', 'country': 'UK', 'categories': ['news']}]


def test_cable_keeps_spanish_channels_from_outside_chile():
    rows = normalize(WORLD, streams(
        ('TVN.cl', 'https://cdn.example/tvn/index.m3u8'),
        ('Telemundo.us', 'https://cdn.example/tlmd/index.m3u8'),
        ('BBC.uk', 'https://cdn.example/bbc/index.m3u8')), LOGOS, FEEDS, 'cable')
    # TVN es chileno (va en nacional) y la BBC no emite en español.
    assert [row['name'] for row in rows] == ['Telemundo']
    assert rows[0]['country'] == 'US'


def test_the_national_list_ignores_the_language_index():
    rows = normalize(WORLD, streams(('TVN.cl', 'https://cdn.example/tvn/index.m3u8'),
                                    ('Telemundo.us', 'https://cdn.example/tlmd/index.m3u8')), LOGOS, FEEDS, 'nacional')
    assert [row['name'] for row in rows] == ['TVN']


def test_recognisable_cable_names_come_first_even_with_a_region_suffix():
    world = [{'id': f'c{index}.us', 'name': name, 'country': 'US', 'categories': []}
             for index, name in enumerate(['Zeta Local', 'AMC Latin America', 'Aaa Local', 'Telemundo Al Dia'])]
    feeds = [{'channel': channel['id'], 'languages': ['spa']} for channel in world]
    rows = normalize(world, streams(*[(channel['id'], f"https://cdn.example/{channel['id']}/index.m3u8") for channel in world]),
                     (), feeds, 'cable')
    assert [row['name'] for row in rows] == ['Telemundo Al Dia', 'AMC Latin America', 'Aaa Local', 'Zeta Local']


SPORTS = [
    {'id': 'TNTSports.cl', 'name': 'TNT Sports', 'country': 'CL', 'categories': ['sports']},
    {'id': 'ESPN.us', 'name': 'ESPN Latin America', 'country': 'US', 'categories': ['sports']},
    {'id': 'DSports.us', 'name': 'DSports', 'country': 'US', 'categories': ['sports']},
    {'id': 'Aaa.ar', 'name': 'Aaa Deportes', 'country': 'AR', 'categories': ['sports']},
    {'id': 'TyCSports.ar', 'name': 'TyC Sports', 'country': 'AR', 'categories': ['sports']},
    {'id': 'FIFAPlus.uk', 'name': 'FIFA+', 'country': 'UK', 'categories': ['sports']},
    {'id': 'SkyUK.uk', 'name': 'Sky Sports', 'country': 'UK', 'categories': ['sports']},
    {'id': 'Teletrak.cl', 'name': 'Teletrak TV', 'country': 'CL', 'categories': ['sports']},
    {'id': 'Noticias.ar', 'name': 'Noticias', 'country': 'AR', 'categories': ['news']},
]
SPORT_FEEDS = [{'channel': channel['id'], 'id': 'SD', 'languages': ['spa']}
               for channel in SPORTS if channel['id'] not in ('SkyUK.uk', 'FIFAPlus.uk')] + [
    {'channel': 'FIFAPlus.uk', 'id': 'English', 'languages': ['eng']},
    {'channel': 'FIFAPlus.uk', 'id': 'Spain', 'languages': ['spa']},
    {'channel': 'SkyUK.uk', 'id': 'SD', 'languages': ['eng']}]


def sport_streams():
    return [{'channel': channel['id'], 'feed': 'SD', 'url': f"https://cdn.example/{channel['id']}/index.m3u8"}
            for channel in SPORTS if channel['id'] != 'FIFAPlus.uk'] + [
        {'channel': 'FIFAPlus.uk', 'feed': 'English', 'url': 'https://cdn.example/fifa-en/index.m3u8'},
        {'channel': 'FIFAPlus.uk', 'feed': 'Spain', 'url': 'https://cdn.example/fifa-es/index.m3u8'}]


def test_sports_lead_with_tnt_espn_and_dsports_and_keep_only_spanish():
    rows = normalize(SPORTS, sport_streams(), (), SPORT_FEEDS, 'deportes')
    names = [row['name'] for row in rows]
    assert names[:3] == ['TNT Sports', 'ESPN Latin America', 'DSports']
    assert names[3:] == ['TyC Sports', 'FIFA+', 'Teletrak TV', 'Aaa Deportes']
    # Sky no emite en español y Noticias no es deportivo.
    assert SPORTS_FIRST[:3] == ['tnt sports', 'espn', 'dsports']


def test_sports_drop_each_stream_announced_in_another_language():
    rows = normalize(SPORTS, sport_streams(), (), SPORT_FEEDS, 'deportes')
    fifa = next(row for row in rows if row['id'] == 'FIFAPlus.uk')
    assert fifa['urls'] == ['https://cdn.example/fifa-es/index.m3u8']


def test_only_working_sports_signals_are_published():
    class CheckedTelevision(Television):
        async def _playlist_works(self, url):
            return 'TNTSports' not in url and 'muerta' not in url

    rows = normalize(SPORTS, sport_streams() + [
        {'channel': 'ESPN.us', 'feed': 'SD', 'url': 'https://muerta.example/espn/index.m3u8'}], (), SPORT_FEEDS, 'deportes')
    kept = asyncio.run(CheckedTelevision(None, None)._verified(rows))
    # Un canal sin ninguna ruta viva no aparece; uno con una ruta viva conserva solo esa.
    assert 'TNTSports.cl' not in [row['id'] for row in kept]
    espn = next(row for row in kept if row['id'] == 'ESPN.us')
    assert espn['urls'] == ['https://cdn.example/ESPN.us/index.m3u8']


PLUTO = {'63c6e37e636b7e0007ccb635': {'name': 'Pluto TV Fútbol', 'logo': 'https://images.example/futbol.png',
                                      'url': 'https://stitcher.example/futbol/master.m3u8?jwt=x'},
         '5dcde0cc2efd2700090b7ff4': {'name': 'Pluto TV Novelas', 'logo': '',
                                      'url': 'https://stitcher.example/novelas/master.m3u8?jwt=x'}}


def test_a_reused_pluto_id_does_not_pass_for_the_channel_it_used_to_be():
    assert pluto_route(PLUTO, '5dcde0cc2efd2700090b7ff4', 'cnn') is None
    assert pluto_route(PLUTO, '63c6e37e636b7e0007ccb635', 'Pluto TV Fútbol')['url'].startswith('https://stitcher')


def test_pluto_sports_join_the_index_row_instead_of_duplicating_it():
    rows = pluto_sports([{'id': 'PlutoTVFutbol.us', 'name': 'Pluto TV Futbol', 'logo': '', 'country': 'US',
                          'categories': ['sports'], 'url': 'https://cdn.example/viejo.m3u8',
                          'urls': ['https://cdn.example/viejo.m3u8']}], PLUTO)
    assert len(rows) == 1
    assert rows[0]['urls'] == ['https://stitcher.example/futbol/master.m3u8?jwt=x', 'https://cdn.example/viejo.m3u8']
    assert rows[0]['logo'] == 'https://images.example/futbol.png'


def test_a_takedown_slate_is_not_a_working_signal():
    class SlateTelevision(Television):
        async def _read_text(self, url, limit=500_000):
            if url.endswith('master.m3u8'):
                return '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nmedia.m3u8\n'
            return '#EXTM3U\n#EXTINF:5,\nhttps://cdn.example/clip/x_ptv_takedownslates_a/1.ts\n'

        async def _segment_works(self, url, media):
            return True

    assert asyncio.run(SlateTelevision(None, None)._playlist_works('https://cdn.example/master.m3u8')) is False


def test_tuning_prefers_the_verified_sports_routes_over_cable():
    rows = {'deportes': [{'id': 'TyCSports.ar', 'urls': ['https://verificada.example/index.m3u8']}],
            'nacional': [], 'cable': [{'id': 'TyCSports.ar', 'urls': ['https://caida.example/index.m3u8']}]}

    class CachedTelevision(Television):
        def cached(self, section='nacional'):
            return ({'items': rows[section]}, True)

        def _save(self, channel):
            return channel

    assert asyncio.run(CachedTelevision(None, None).source('TyCSports.ar'))['urls'] == ['https://verificada.example/index.m3u8']

"""La calidad efectiva de streaming pesa más que tamaño o resolución máxima."""
from app.stream_ranking import (declared_audio_languages,describe,original_audio_languages,
                                rank_streams,url_key)


def test_http_1080_h264_beats_4k_remux_torrent_and_720_fallback():
    streams=[
        {'infoHash':'a'*40,'name':'4K 2160p REMUX HEVC TrueHD 45 Mbps 50 GB 👤 60'},
        {'url':'https://video.example/movie-720.mp4','name':'720p H264 AAC 4 Mbps Español'},
        {'url':'https://video.example/master.m3u8','name':'1080p H264 AAC 6 Mbps Español HLS'},
    ]
    ranked=rank_streams(streams)
    assert ranked[0][0]['url'].endswith('master.m3u8')
    assert ranked[1][0]['url'].endswith('movie-720.mp4')
    assert ranked[2][0]['infoHash']


def test_size_is_secondary_and_does_not_reject_a_stream():
    small=describe({'infoHash':'a'*40,'name':'1080p H264 AAC 8 GB 👤 1'})
    large=describe({'url':'https://cdn.example/video.mp4','name':'1080p H264 AAC 32 GB'})
    assert large['score']>small['score'] and large['size_gb']==32


def test_provider_history_is_bounded():
    weak=describe({'url':'https://cdn.example/video.mp4','name':'1080p H264 AAC'},health=0)
    strong=describe({'url':'https://cdn.example/video.mp4','name':'1080p H264 AAC'},health=100)
    assert strong['score']-weak['score']==30


def test_spanish_and_reasonable_bitrate_improve_equivalent_source():
    base=describe({'url':'https://cdn.example/video.mp4','name':'1080p H264 AAC 18 Mbps'})
    preferred=describe({'url':'https://cdn.example/video.mp4','name':'1080p H264 AAC Español 6 Mbps'})
    assert preferred['score']>base['score']


def test_origin_only_distinguishes_cinema_recordings_from_original_sources():
    cinema=describe({'url':'https://cdn.example/video.mp4','name':'Movie 1080p HDCAM Español'})
    official=describe({'url':'https://cdn.example/video.mp4','name':'Movie 1080p WEB-DL MovieBox 2Peckle Español'})
    assert cinema['origin']=='cine'
    assert official['origin']=='original'


def test_foreign_first_audio_is_strongly_demoted_without_changing_other_ranking_rules():
    original={'english'}
    foreign={'url':'https://rapido.example/video.mp4','name':'1080p H264 AAC 6 Mbps',
             'behaviorHints':{'filename':'Movie.2026.1080p.WEB-DL.Hindi-English.x264.mkv'}}
    preferred={'infoHash':'a'*40,'name':'1080p H264 AAC English 👤 20',
               'behaviorHints':{'filename':'Movie.2026.1080p.BluRay.English.x264.mkv'}}
    ranked=rank_streams([foreign,preferred],original_languages=original)
    assert ranked[0][0] is preferred  # hasta un torrent válido adelanta al doblaje ajeno
    neutral={**foreign,'behaviorHints':{'filename':'Movie.2026.1080p.WEB-DL.English.x264.mkv'}}
    assert ranked[1][1]['foreign_audio']
    assert describe(foreign,original_languages=original)['score']==describe(neutral,original_languages=original)['score']-120
    assert declared_audio_languages(foreign)==['hindi','english']


def test_original_language_and_spanish_are_never_penalized():
    assert original_audio_languages({'country':'Colombia'})=={'spanish'}
    korean=describe({'url':'https://cdn.example/v.mp4','name':'1080p H264 AAC',
                     'behaviorHints':{'filename':'Parasite.2019.1080p.Korean.x264.mkv'}},
                    original_languages={'korean'})
    spanish=describe({'url':'https://cdn.example/es.mp4','description':'Audio: Español, Inglés'},
                     original_languages={'english'})
    assert not korean['foreign_audio'] and not spanish['foreign_audio'] and spanish['spanish']


def test_editions_too_large_for_this_setup_are_marked_unplayable():
    """El tope de un torrent es el de la caché; el de un HTTP, lo que mueve un Chromecast."""
    from app.stream_ranking import MAX_CACHED_GB,MAX_STREAMED_GB
    torrent=describe({'infoHash':'a'*40,'name':'1080p H264 AAC 👤 50','behaviorHints':{'videoSize':28*1024**3}})
    assert not torrent['playable'] and f'{MAX_CACHED_GB:g} GB' in ' '.join(torrent['blockers'])
    streamed=describe({'url':'https://cdn.example/v.mkv','name':'4K REMUX','behaviorHints':{'videoSize':28*1024**3}})
    assert not streamed['playable'] and f'{MAX_STREAMED_GB:g} GB' in ' '.join(streamed['blockers'])
    fine=describe({'url':'https://cdn.example/v.mp4','name':'1080p H264 AAC','behaviorHints':{'videoSize':3*1024**3}})
    assert fine['playable'] and fine['size_gb']==3


def test_a_torrent_without_declared_peers_is_not_offered():
    orphan=describe({'infoHash':'b'*40,'name':'1080p H264 AAC Español'})
    assert not orphan['playable'] and 'sin pares' in ' '.join(orphan['blockers'])
    # El tamaño exacto del proveedor manda sobre lo que diga la etiqueta.
    mismatch=describe({'infoHash':'c'*40,'name':'1080p 👤 9 📦 2 GB','behaviorHints':{'videoSize':30*1024**3}})
    assert mismatch['size_gb']==30 and not mismatch['playable']


def test_hdhub_sources_without_any_size_are_treated_as_unverified_content():
    """Una publicidad también es un video válido: HDHub debe identificar el archivo."""
    from app.stream_ranking import describe
    generic=describe({'url':'https://cdn.example/instant','name':'HdHub HubCDN',
                      'description':'[HLS Stream] Instant'},provider='hdhub')
    known=describe({'url':'https://cdn.example/movie.mkv','name':'Movie 1080p',
                    'behaviorHints':{'videoSize':2*1024**3}},provider='hdhub')
    identified=describe({'url':'https://cdn.example/movie.mkv','name':'Movie.2026.1080p'},provider='hdhub')
    assert generic['suspected_ad'] and not generic['playable']
    assert not known['suspected_ad'] and known['playable']
    assert not identified['suspected_ad'] and identified['playable']


def test_a_server_measured_fast_wins_over_an_identical_slow_one():
    """Dos ediciones idénticas no son iguales: lo que cambia es a qué velocidad llegan."""
    from app.stream_ranking import url_key
    speeds={url_key('https://rapido.example/v.mp4'):3*1024*1024,
            url_key('https://lento.example/v.mp4'):300*1024}
    ranked=rank_streams([{'url':'https://lento.example/v.mp4','name':'1080p H264 AAC Español'},
                         {'url':'https://rapido.example/v.mp4','name':'1080p H264 AAC Español'}],speeds=speeds)
    assert ranked[0][0]['url'].startswith('https://rapido')
    assert 'servidor rápido' in ' '.join(ranked[0][1]['reasons'])
    assert ranked[0][1]['measured_bps']==3*1024*1024


def test_provider_speed_is_the_fallback_but_the_final_server_wins():
    source={'url':'https://cdn.example/v.mkv','name':'1080p H264 6 Mbps'}
    provider=describe(source,provider='hdhub',provider_speeds={'hdhub':2*1024*1024})
    assert provider['measured_bps']==2*1024*1024 and provider['speed_scope']=='provider'
    server=describe(source,provider='hdhub',speeds={url_key(source['url']):4*1024*1024},
                    provider_speeds={'hdhub':2*1024*1024})
    assert server['measured_bps']==4*1024*1024 and server['speed_scope']=='server'


def test_a_large_http_file_is_not_penalized_twice_when_bitrate_already_represents_it():
    small=describe({'url':'https://cdn.example/a.mkv','name':'1080p H264',
                    'behaviorHints':{'videoSize':10*1024**3}},duration=7200)
    large=describe({'url':'https://cdn.example/b.mkv','name':'1080p H264',
                    'behaviorHints':{'videoSize':17*1024**3}},duration=7200)
    assert small['score']==large['score']
    assert 'archivo grande' not in ' '.join(large['reasons'])


def test_an_unmeasured_server_is_not_punished():
    sin=describe({'url':'https://nuevo.example/v.mp4','name':'1080p H264 AAC'})
    from app.stream_ranking import url_key
    medio=describe({'url':'https://medio.example/v.mp4','name':'1080p H264 AAC'},
                   speeds={url_key('https://medio.example/v.mp4'):700*1024})
    assert sin['score']==medio['score'] and sin['measured_bps'] is None


def test_speed_never_rescues_a_torrent_over_a_direct_link():
    from app.stream_ranking import url_key
    speeds={url_key('https://lento.example/v.mp4'):200*1024}
    directo=describe({'url':'https://lento.example/v.mp4','name':'1080p H264 AAC'},speeds=speeds)
    torrent=describe({'infoHash':'a'*40,'name':'1080p H264 AAC 👤 200'},speeds=speeds)
    assert directo['score']>torrent['score']
    # Un torrent no tiene servidor que medir.
    assert torrent['measured_bps'] is None


def _fuente(nombre,url,gigas):
    return {'name':nombre,'url':url,'behaviorHints':{'videoSize':int(gigas*1024**3)}}


def test_the_measured_margin_decides_over_the_classic_score():
    """Medido en casa: una edición de 4,5 GB en un capítulo de 51 min pide 11,9 Mbps y
    este enlace da 5. Por puntaje clásico ganaba igual, y se cortaba cada dos minutos."""
    duracion=51*60
    grande=_fuente('Silo S01E01 1080p WEB-DL H.264','https://uno.example/a.mkv',4.54)
    chica=_fuente('Silo S01E01 1080p HEVC','https://dos.example/b.mkv',0.49)
    medido={url_key(grande['url']):640_000,url_key(chica['url']):5_000_000}
    orden=rank_streams([grande,chica],speeds=medido,duration=duracion)
    assert orden[0][0] is chica
    assert orden[1][1]['margin']<1 and 'el servidor no da el caudal que pide' in orden[1][1]['reasons']
    # Sin duración no hay bitrate que estimar y manda el criterio de siempre.
    assert rank_streams([grande,chica],speeds=medido)[0][0] is grande


def test_within_the_same_margin_class_the_better_image_wins_up_to_the_useful_ceiling():
    duracion=110*60
    ligera=_fuente('1080p WEB-DL H.264','https://uno.example/a.mkv',2.0)
    equilibrada=_fuente('1080p WEB-DL H.264','https://dos.example/b.mkv',4.0)
    excesiva=_fuente('1080p WEB-DL H.264','https://tres.example/c.mkv',8.0)
    medido={url_key(f['url']):5_000_000 for f in (ligera,equilibrada,excesiva)}
    orden=[stream for stream,_ in rank_streams([ligera,equilibrada,excesiva],speeds=medido,duration=duracion)]
    assert orden==[equilibrada,ligera,excesiva],'más bitrate solo hasta donde se nota'


def test_a_source_that_forces_a_full_re_encode_never_wins_on_paper_quality():
    """Al recodificar la salida baja a 720p: la fuente de más calidad entrega la peor."""
    duracion=110*60
    hevc=_fuente('1080p HEVC x265','https://uno.example/a.mkv',2.0)
    avc=_fuente('720p WEB-DL H.264','https://dos.example/b.mkv',2.0)
    medido={url_key(f['url']):5_000_000 for f in (hevc,avc)}
    orden=[stream for stream,_ in rank_streams([hevc,avc],speeds=medido,duration=duracion)]
    assert orden==[avc,hevc]


def test_an_unmeasured_giant_is_not_the_first_choice_by_default():
    """Pedir el doble de lo útil ya es evidencia: sin una medición que lo respalde,
    un capítulo de 4 GB en 49 minutos no puede ganar por resolución."""
    duracion=49*60
    enorme=_fuente('1080p WEB-DL H.264','https://uno.example/a.mkv',4.10)
    sensata=_fuente('1080p HEVC','https://dos.example/b.mkv',2.20)
    orden=[stream for stream,_ in rank_streams([enorme,sensata],duration=duracion)]
    assert orden==[sensata,enorme]
    # Con un caudal que sostenga a las dos con holgura quedan en la misma clase, y ahí
    # gana la que no hay que recodificar: entrega 1080p en vez de 720p.
    medido={url_key(enorme['url']):6_000_000,url_key(sensata['url']):6_000_000}
    assert rank_streams([enorme,sensata],speeds=medido,duration=duracion)[0][0] is enorme


def test_what_this_setup_cannot_open_never_leads_the_list():
    """El caudal ordena entre lo utilizable; un torrent sin pares no es utilizable."""
    duracion=110*60
    sin_pares={'name':'1080p H.264 pack','infoHash':'a'*40,'behaviorHints':{'videoSize':int(1.5*1024**3)}}
    directa=_fuente('1080p WEB-DL H.264','https://uno.example/a.mkv',3.0)
    orden=[stream for stream,_ in rank_streams([sin_pares,directa],duration=duracion)]
    assert orden==[directa,sin_pares]


def _directa(resolucion,bitrate,host):
    return {'url':f'https://{host}.example/v.mp4',
            'name':f'{resolucion}p H264 AAC Español {bitrate} Mbps'}


def test_safe_1080_always_leads_safe_720_even_when_720_has_more_margin():
    full=_directa(1080,6,'full')
    hd=_directa(720,3,'hd')
    speeds={url_key(full['url']):1_600_000,   # 12,8 Mbps: margen 2,13
            url_key(hd['url']):4_000_000}     # 32 Mbps: margen 10,67
    ranked=rank_streams([hd,full],speeds=speeds)
    assert ranked[0][0] is full
    assert ranked[0][1]['ranking_tier']==0 and ranked[1][1]['ranking_tier']==1


def test_within_safe_1080_the_highest_useful_bitrate_wins_not_the_largest_margin():
    quality=_directa(1080,6,'quality')
    light=_directa(1080,3,'light')
    speeds={url_key(quality['url']):2_000_000, # margen 2,67
            url_key(light['url']):4_000_000}   # margen 10,67
    assert rank_streams([light,quality],speeds=speeds)[0][0] is quality


def test_known_720_then_unknown_then_known_480_then_sources_below_threshold():
    safe720=_directa(720,4,'safe720')
    unknown1080=_directa(1080,5,'unknown1080')
    safe480=_directa(480,2,'safe480')
    weak1080=_directa(1080,6,'weak1080')
    speeds={url_key(safe720['url']):2_000_000,
            url_key(safe480['url']):1_000_000,
            url_key(weak1080['url']):500_000}
    ranked=rank_streams([weak1080,safe480,unknown1080,safe720],speeds=speeds)
    assert [stream for stream,_ in ranked]==[safe720,unknown1080,safe480,weak1080]
    assert [analysis['ranking_tier'] for _,analysis in ranked]==[1,2,3,5]


def test_language_and_failure_guards_still_precede_the_new_quality_tiers():
    foreign=_directa(1080,6,'foreign')
    foreign['name']='1080p H264 AAC 6 Mbps'
    foreign['behaviorHints']={'filename':'Movie.2026.1080p.Hindi.x264.mkv'}
    valid=_directa(720,3,'valid')
    speeds={url_key(foreign['url']):4_000_000,url_key(valid['url']):2_000_000}
    ranked=rank_streams([foreign,valid],speeds=speeds,original_languages={'english'})
    assert ranked[0][0] is valid and not ranked[1][1]['playable']

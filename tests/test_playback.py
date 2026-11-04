"""Regresiones del orden de episodios, rangos y compatibilidad de los receptores reales."""
import pytest
from app.catalog import regular_episodes
from app.torrents import parse_range
from app.media_tools import cast_compatible
from app.providers.mediafusion import ProviderError


@pytest.mark.parametrize('header,expected',[(None,(0,99,200)),('bytes=0-9',(0,9,206)),('bytes=90-',(90,99,206)),('bytes=-10',(90,99,206)),('bytes=10-999',(10,99,206))])
def test_range_preserves_requested_bytes(header,expected):assert parse_range(header,100)==expected


@pytest.mark.parametrize('header',['bytes=100-','bytes=-0','bytes=10-3','bytes=0-4,6-8','bytes=-','bad'])
def test_bad_range_is_416(header):
    with pytest.raises(ProviderError) as error:parse_range(header,100)
    assert error.value.status==416


def test_episode_order_excludes_specials_future_and_duplicates():
    rows=[{'id':'b','season':2,'episode':1},{'id':'a10','season':1,'episode':10},{'id':'a2','season':1,'episode':2},
          {'id':'special','season':0,'episode':1},{'id':'duplicate','season':1,'episode':2},
          {'id':'future','season':3,'episode':1,'released':'2999-01-01T00:00:00Z'}]
    assert [row['id'] for row in regular_episodes(rows)]==['a2','a10','b']


def test_cast_2_profile_requires_1080p30_avc_aac():
    video={'codec_type':'video','codec_name':'h264','width':1920,'height':1080,'level':41,'avg_frame_rate':'30000/1001'}
    assert cast_compatible({'streams':[video,{'codec_type':'audio','codec_name':'aac'}]})
    for wrong in ({'codec_name':'hevc'},{'width':3840},{'avg_frame_rate':'60/1'},{'level':42}):
        assert not cast_compatible({'streams':[{**video,**wrong}]})
    assert not cast_compatible({'streams':[video,{'codec_type':'audio','codec_name':'ac3'}]})


def test_subtitle_conversion_preserves_spanish_text_and_timing():
    from app.subtitles import webvtt
    text=webvtt('1\r\n00:00:23,000 --> 00:00:24,500\r\nEspañol: acción.\r\n')
    assert text.startswith('WEBVTT\n\n')
    assert '00:00:23.000 --> 00:00:24.500' in text and 'Español: acción.' in text
    with pytest.raises(ProviderError):webvtt('<html>Error del proveedor</html>')


def test_cache_cleanup_preserves_active_and_unmanaged_paths(tmp_path):
    from app.cache_cleanup import cleanup
    active=tmp_path/('a'*40);old=tmp_path/('b'*40);other=tmp_path/'archivos-personales'
    for path in (active,old,other):path.mkdir();(path/'video').write_bytes(b'x'*100)
    cleanup(tmp_path,{active.name},budget=0)
    assert active.exists() and other.exists() and not old.exists()


def test_native_hls_skips_ffmpeg_and_uses_the_local_proxy():
    """Un HLS del proveedor ya es adaptativo: reconvertirlo solo añade retraso y CPU."""
    import asyncio
    from app.media_tools import MediaTools,native_hls
    from app.settings import Settings
    assert native_hls({'url':'https://cdn.example/master.m3u8'})
    assert native_hls({'url':'https://cdn.example/play?x=1','behaviorHints':{'videoMimeType':'application/vnd.apple.mpegurl'}})
    assert not native_hls({'url':'https://cdn.example/video.mp4'}) and not native_hls({'infoHash':'a'*40})
    source_id='a'*32
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/master.m3u8'},'provider':'penguplay'})
    def refuse(key):raise AssertionError('FFprobe no debe ejecutarse para un HLS válido')
    tools.inspect=refuse
    result=asyncio.run(tools.cast_path(source_id))
    assert result['content_type']=='application/vnd.apple.mpegurl'
    assert result['stream_url'].endswith('/media/'+source_id) and result['adapted'] is False


def test_a_compatible_mp4_is_cast_directly_without_conversion():
    """Sin un HLS original se inspecciona el video real antes de decidir si transcodificar."""
    import asyncio
    from app.media_tools import MediaTools
    from app.settings import Settings
    video={'codec_type':'video','codec_name':'h264','width':1920,'height':1080,'level':41,'avg_frame_rate':'24/1'}
    info={'streams':[video,{'codec_type':'audio','codec_name':'aac'}],'format':{'format_name':'mov,mp4,m4a'}}
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/video.mp4'},'provider':'flixstreams'})
    async def inspect(key):return info
    def refuse(key,name):raise AssertionError('un MP4 compatible no necesita conversión HLS')
    tools.inspect,tools.segment=inspect,refuse
    result=asyncio.run(tools.cast_path('c'*32))
    assert result['content_type']=='video/mp4' and result['adapted'] is False


def test_a_seek_ignored_by_the_tv_forces_even_a_direct_mp4_through_an_offset_playlist():
    """Reenviar el MP4 completo repite exactamente el fallo: la TV vuelve a cero."""
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    video={'codec_type':'video','codec_name':'h264','width':1920,'height':1080,
           'level':41,'avg_frame_rate':'24/1','pix_fmt':'yuv420p'}
    info={'streams':[video,{'codec_type':'audio','codec_name':'aac'}],
          'format':{'format_name':'mov,mp4,m4a','duration':'6000'}}
    async def inspect(_):return info
    tools.inspect=inspect
    source='j'*32
    try:
        result=asyncio.run(tools.cast_path(source,1200000,force_adapted=True))
        assert result['adapted'] and result['offset_ms']==1200000
        assert started==[1200000]
    finally:shutil.rmtree(tools.root/source,ignore_errors=True)


def test_a_seek_ignored_by_the_tv_also_adapts_a_native_hls():
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    tools.lookup=lambda _:{'stream':{'url':'https://cdn.example/master.m3u8'}}
    source='k'*32
    try:
        result=asyncio.run(tools.cast_path(source,900000,force_adapted=True))
        assert result['adapted'] and result['offset_ms']==900000
        assert started==[900000]
    finally:shutil.rmtree(tools.root/source,ignore_errors=True)


def _tools(duration_seconds,started):
    """MediaTools con inspección y conversión simuladas: no hace falta FFmpeg."""
    import asyncio
    from app.media_tools import MediaTools
    from app.settings import Settings
    video={'codec_type':'video','codec_name':'hevc','width':1920,'height':1080,'avg_frame_rate':'24/1'}
    info={'streams':[video,{'codec_type':'audio','codec_name':'ac3'}],
          'format':{'format_name':'matroska','duration':str(duration_seconds)}}
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/pelicula.mkv'},'provider':'penguplay'})
    async def inspect(key):return info
    async def segment(key,name):
        started.append(tools.offsets.get(key,0))
        folder=tools.root/key;folder.mkdir(parents=True,exist_ok=True)
        (folder/'conversion').write_text(tools.conversion_identity(tools.audio_selections.get(key)))
        (folder/'index.m3u8').write_text('#EXTM3U\n#EXTINF:4.0,\npart000000.ts\n#EXTINF:4.0,\npart000001.ts\n#EXTINF:4.0,\npart000002.ts\n')
    tools.inspect,tools.segment=inspect,segment
    return tools


@pytest.mark.parametrize('suffix',['mp4','m3u8'])
@pytest.mark.parametrize('default_language',['hin','eng'])
def test_original_audio_is_really_selected_in_direct_mp4_and_hls(tmp_path,suffix,default_language):
    """Informar English no cambia la pista Hindi de una URL entregada tal cual."""
    import asyncio
    started=[];tools=_tools(3600,started);tools.root=tmp_path
    tools.lookup=lambda key:{'stream':{'url':'https://cdn.example/video.'+suffix},'original_languages':['english']}
    info={'streams':[{'codec_type':'video','index':0,'codec_name':'h264','width':1920,'height':1080,
                      'level':41,'avg_frame_rate':'24/1','pix_fmt':'yuv420p'},
                     {'codec_type':'audio','index':1,'codec_name':'aac','tags':{'language':'hin'},
                      'disposition':{'default':int(default_language=='hin')}},
                     {'codec_type':'audio','index':2,'codec_name':'aac','tags':{'language':'eng'},
                      'disposition':{'default':int(default_language=='eng')}}],
          'format':{'format_name':'mov,mp4','duration':'3600'}}
    async def inspect(key):return info
    tools.inspect=inspect
    result=asyncio.run(tools.cast_path('a'*32))
    assert result['audio_stream_index']==2
    assert result['adapted']==(default_language=='hin')
    assert started==([0] if default_language=='hin' else [])


def test_a_converted_source_can_start_at_the_point_the_viewer_left_it():
    """Sin esto una fuente que se convierte solo se puede ver desde el segundo cero."""
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    try:
        first=asyncio.run(tools.cast_path('a'*32,1800000))
        assert first['adapted'] and first['offset_ms']==1800000
        assert first['duration_ms']==6000000, 'la barra necesita la duración real del video'
        # `start` en la direccion ya no repite el desplazamiento: dice cuanto hay que
        # recortar de la lista. Aqui la conversion arranca justo donde se pidio, asi
        # que no sobra nada que recortar.
        assert 'start=0' in first['stream_url']
        assert started==[1800000], 'FFmpeg debe empezar en el punto pedido, no en cero'
    finally:shutil.rmtree(tools.root/('a'*32),ignore_errors=True)


def test_seeking_inside_converted_material_trims_instead_of_reconverting():
    """Adelantar dentro de lo ya convertido no puede reiniciar el video.

    Antes se devolvia la misma lista con el mismo desplazamiento y se le pedia al
    televisor que se colocase solo, cosa que su reproductor ignora: el video volvia al
    principio. Ahora se le entrega la lista empezando en el fragmento que toca, y el
    desplazamiento devuelto ya cuenta el recorte.
    """
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    clave='c'*32
    try:
        asyncio.run(tools.cast_path(clave,0))
        # Diez fragmentos de cuatro segundos: cuarenta segundos ya convertidos.
        lineas=['#EXTM3U','#EXT-X-MEDIA-SEQUENCE:0']
        for i in range(10):lineas+=['#EXTINF:4.000000,','part%06d.ts'%i]
        (tools.root/clave/'index.m3u8').write_text('\n'.join(lineas)+'\n')
        # La conversion simulada deja de reescribir la lista: aqui ya esta hecha.
        async def quieta(key,name):started.append(tools.offsets.get(key,0))
        tools.segment=quieta
        adelante=asyncio.run(tools.cast_path(clave,20000))
        assert tools.offsets[clave]==0, 'no se puede reconvertir lo que ya esta convertido'
        assert adelante['offset_ms']==20000, 'la barra tiene que saber donde empieza la lista'
        assert 'start=20000' in adelante['stream_url']
    finally:shutil.rmtree(tools.root/clave,ignore_errors=True)


def test_seeking_again_restarts_the_conversion_only_when_the_point_changes():
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    try:
        asyncio.run(tools.cast_path('b'*32,0))
        asyncio.run(tools.cast_path('b'*32,0))
        assert started==[0,0] and tools.offsets['b'*32]==0
        asyncio.run(tools.cast_path('b'*32,3000000))
        assert started[-1]==3000000 and tools.offsets['b'*32]==3000000
    finally:shutil.rmtree(tools.root/('b'*32),ignore_errors=True)


def test_the_start_point_never_lands_past_the_end_of_the_video():
    import asyncio,shutil
    started=[]
    tools=_tools(600,started)
    try:
        result=asyncio.run(tools.cast_path('c'*32,10_000_000))
        # 600 s de video: se recorta a 585 s para que siempre quede algo por ver.
        assert result['offset_ms']==585000 and started==[585000]
    finally:shutil.rmtree(tools.root/('c'*32),ignore_errors=True)


def test_a_direct_source_reports_its_duration_and_needs_no_offset():
    import asyncio
    from app.media_tools import MediaTools
    from app.settings import Settings
    video={'codec_type':'video','codec_name':'h264','width':1920,'height':1080,'level':41,'avg_frame_rate':'24/1'}
    info={'streams':[video,{'codec_type':'audio','codec_name':'aac'}],
          'format':{'format_name':'mov,mp4,m4a','duration':'5400.0'}}
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/v.mp4'},'provider':'penguplay'})
    async def inspect(key):return info
    tools.inspect=inspect
    tools.segment=lambda key,name:(_ for _ in ()).throw(AssertionError('un MP4 compatible no se convierte'))
    result=asyncio.run(tools.cast_path('d'*32,1800000))
    assert not result['adapted'] and result['offset_ms']==0 and result['duration_ms']==5400000


def test_a_short_valid_video_is_rejected_when_a_full_movie_was_expected():
    """FFprobe debe distinguir una publicidad reproducible de la película solicitada."""
    import asyncio
    import pytest
    from app.media_tools import MediaTools
    from app.providers.stremio import ProviderError
    from app.settings import Settings
    video={'codec_type':'video','codec_name':'h264','width':1280,'height':720,
           'level':31,'avg_frame_rate':'24/1'}
    info={'streams':[video,{'codec_type':'audio','codec_name':'aac'}],
          'format':{'format_name':'mov,mp4,m4a','duration':'90.0'}}
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/ad.mp4'},'provider':'hdhub'})
    async def inspect(key):return info
    tools.inspect=inspect
    with pytest.raises(ProviderError) as error:
        asyncio.run(tools.cast_path('e'*32,0,expected_duration_ms=7200000))
    assert error.value.code=='content_mismatch'


def test_subtitles_follow_a_conversion_that_does_not_start_at_zero():
    """Tras retomar o saltar, el texto debe moverse con la imagen o queda desfasado."""
    from app.subtitles import prune,shift
    vtt=('WEBVTT\n\n1\n00:00:05.000 --> 00:00:07.500\nAntes del salto\n\n'
         '2\n00:30:05.000 --> 00:30:07.500\nJusto después\n\n'
         '3\n00:35:00.000 --> 00:35:02.000\nMás tarde\n')
    moved=prune(shift(vtt,1800000))
    assert '00:00:05.000 --> 00:00:07.500\nJusto después' in moved
    assert '00:05:00.000 --> 00:05:02.000\nMás tarde' in moved
    # El bloque que terminaba antes del punto de partida ya no tiene sentido.
    assert 'Antes del salto' not in moved and moved.startswith('WEBVTT')


def test_a_manual_nudge_moves_every_cue_in_both_directions():
    from app.subtitles import shift
    vtt='WEBVTT\n\n1\n00:10:00.000 --> 00:10:02.000\nHola\n'
    assert '00:09:58.500 --> 00:10:00.500' in shift(vtt,1500)
    assert '00:10:01.500 --> 00:10:03.500' in shift(vtt,-1500)
    assert shift(vtt,0)==vtt


def test_shifting_never_produces_a_negative_timestamp():
    from app.subtitles import shift
    vtt='WEBVTT\n\n1\n00:00:01.000 --> 00:00:09.000\nA caballo del corte\n'
    moved=shift(vtt,5000)
    assert '00:00:00.000 --> 00:00:04.000' in moved


def _probe(video_codec='h264',audio_codec='eac3',width=1920,height=1080,level=40,fps='24000/1001'):
    return {'streams':[{'codec_type':'video','codec_name':video_codec,'width':width,'height':height,
                        'level':level,'avg_frame_rate':fps,'pix_fmt':'yuv420p'},
                       {'codec_type':'audio','codec_name':audio_codec}],
            'format':{'format_name':'matroska','duration':'2866.8'}}


def test_a_compatible_picture_is_copied_even_when_the_sound_is_not():
    """El audio EAC3 no es motivo para re-codificar un video que el Chromecast ya acepta."""
    from app.media_tools import video_arguments
    assert video_arguments(_probe())==['-c:v','copy']
    # Y con el audio ya en AAC, igual.
    assert video_arguments(_probe(audio_codec='aac'))==['-c:v','copy']


def test_a_picture_the_chromecast_cannot_show_is_still_re_encoded():
    from app.media_tools import video_arguments
    for wrong in ({'video_codec':'hevc'},{'width':3840,'height':2160},{'fps':'60/1'},{'level':51}):
        arguments=video_arguments(_probe(**wrong))
        assert arguments[:2]==['-c:v','libx264'] and 'scale=1280:720' in ' '.join(arguments)


def test_copying_never_adds_a_filter_that_would_force_decoding():
    from app.media_tools import video_arguments
    # `-vf` obligaría a decodificar y anularía la ventaja de copiar.
    assert '-vf' not in video_arguments(_probe())


def test_casting_what_is_already_playing_reuses_the_conversion():
    """Enviar a la TV lo que ya se ve en el teléfono debe ser inmediato, no rehacerlo."""
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    source='e'*32
    try:
        first=asyncio.run(tools.cast_path(source,0))
        assert started==[0] and first['offset_ms']==0
        # La lista simulada trae 12 s convertidos; pedir el segundo 8 cae dentro.
        tools.jobs[source]={'process':None,'access':0}
        again=asyncio.run(tools.cast_path(source,8000))
        assert again['offset_ms']==0, 'debe devolver la lista existente, no una nueva'
        assert started==[0,0], 'FFmpeg no debe reiniciarse por un punto ya convertido'
    finally:shutil.rmtree(tools.root/source,ignore_errors=True)


def test_a_point_outside_what_is_converted_does_restart_the_conversion():
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    source='f'*32
    stopped=[]
    async def fake_stop(key):stopped.append(key);tools.jobs.pop(key,None)
    tools.stop_conversion=fake_stop
    try:
        asyncio.run(tools.cast_path(source,0))
        tools.jobs[source]={'process':None,'access':0}
        far=asyncio.run(tools.cast_path(source,1800000))
        assert far['offset_ms']==1800000 and stopped==[source]
        assert started[-1]==1800000
    finally:shutil.rmtree(tools.root/source,ignore_errors=True)


def test_a_short_step_back_before_resume_contains_the_requested_scene():
    """Retomar en 30:00 y pedir 29:55 nunca puede devolver una lista desde 30:00."""
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    try:
        asyncio.run(tools.cast_path('d'*32,1800000))
        near=asyncio.run(tools.cast_path('d'*32,1795000))
        assert near['offset_ms']==1795000 and tools.offsets['d'*32]==1795000
        assert started==[1800000,1795000], 'el destino anterior requiere material que lo contenga'
        far=asyncio.run(tools.cast_path('d'*32,1700000))
        assert far['offset_ms']==1700000 and started[-1]==1700000, 'un salto de verdad sí reconvierte'
    finally:shutil.rmtree(tools.root/('d'*32),ignore_errors=True)


def _converted(source_id,playlist):
    """Deja en disco una conversión ya hecha, sin proceso vivo detrás."""
    from app.media_tools import MediaTools
    from app.settings import Settings
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/pelicula.mkv'}})
    folder=tools.root/source_id;folder.mkdir(parents=True,exist_ok=True)
    (folder/'part000000.ts').write_bytes(b'video')
    (folder/'index.m3u8').write_text(playlist)
    (folder/'conversion').write_text(tools.conversion_identity(None))
    return tools,folder


def test_what_is_already_converted_survives_without_a_live_process():
    """Una pausa larga suelta el proceso; lo convertido tiene que seguir sirviéndose igual."""
    import asyncio,shutil
    tools,folder=_converted('e'*32,'#EXTM3U\n#EXTINF:4.0,\npart000000.ts\n#EXT-X-ENDLIST\n')
    async def refuse(*args):raise AssertionError('no hay que reconvertir lo que ya está en disco')
    tools.launch=refuse
    try:
        playlist=asyncio.run(tools.segment('e'*32,'index.m3u8'))
        assert b'#EXT-X-ENDLIST' in playlist.body
        assert b'part000000.ts?v=0' in playlist.body
        piece=asyncio.run(tools.segment('e'*32,'part000000.ts'))
        assert str(piece.path).endswith('part000000.ts')
        with pytest.raises(ProviderError) as error:asyncio.run(tools.segment('e'*32,'part000009.ts'))
        assert error.value.code=='cast_expired', 'lo que no está sí hay que volver a pedirlo'
    finally:shutil.rmtree(folder,ignore_errors=True)


def test_serving_a_fragment_does_not_wait_for_another_video_being_prepared():
    """El candado de preparación bloqueaba la entrega y la TV se quedaba sin fragmentos."""
    import asyncio,shutil
    tools,folder=_converted('f'*32,'#EXTM3U\n#EXTINF:4.0,\npart000000.ts\n#EXT-X-ENDLIST\n')
    async def scenario():
        await tools.lock.acquire()   # otro video está arrancando su conversión
        try:return await tools.segment('f'*32,'part000000.ts')
        finally:tools.lock.release()
    try:
        assert str(asyncio.run(scenario()).path).endswith('part000000.ts')
    finally:shutil.rmtree(folder,ignore_errors=True)


def test_a_half_written_playlist_is_never_announced_as_ready():
    """Una lista parcial de un trabajo soltado hay que continuarla, no darla por buena."""
    import asyncio,shutil
    tools,folder=_converted('g'*32,'#EXTM3U\n#EXTINF:4.0,\npart000000.ts\n')
    async def inspect(_):raise ProviderError('sentinela','se continuó la conversión')
    tools.inspect=inspect
    try:
        with pytest.raises(ProviderError) as error:asyncio.run(tools.segment('g'*32,'index.m3u8'))
        assert error.value.code=='sentinela'
    finally:shutil.rmtree(folder,ignore_errors=True)


def test_a_conversion_left_on_disk_is_reused_from_its_own_starting_point():
    """Volver a abrir la misma edición no puede servir lo convertido como si empezara
    en el punto pedido: la película arrancaría desde el principio con la barra mintiendo."""
    import asyncio,shutil
    started=[]
    tools=_tools(6000,started)
    source_id='h'*32
    folder=tools.root/source_id
    try:
        asyncio.run(tools.cast_path(source_id,1800000))
        folder.mkdir(parents=True,exist_ok=True);(folder/'offset').write_text('1800000')
        # La sesión termina: se suelta el proceso y se olvida el punto, pero quedan los datos.
        asyncio.run(tools.stop(source_id))
        assert source_id not in tools.offsets
        again=asyncio.run(tools.cast_path(source_id,1804000))
        assert again['offset_ms']==1800000, 'se reutiliza desde donde de verdad empieza'
        far=asyncio.run(tools.cast_path(source_id,4000000))
        assert far['offset_ms']==4000000 and started[-1]==4000000
    finally:shutil.rmtree(folder,ignore_errors=True)


def test_the_conversion_writes_down_where_it_started(monkeypatch):
    """Ese apunte es lo que permite reutilizar después lo que quedó convertido."""
    import asyncio,shutil
    from app.media_tools import MediaTools
    from app.settings import Settings
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/pelicula.mkv'}})
    source_id='i'*32;folder=tools.root/source_id
    info={'streams':[{'codec_type':'video','codec_name':'h264','width':1280,'height':720,'level':31,'avg_frame_rate':'24/1'},
                     {'codec_type':'audio','codec_name':'ac3','index':1}],'format':{'duration':'6000'}}
    class Fake:
        returncode=None
        stdout=None
        def terminate(self):pass
        async def wait(self):return 0
    async def spawn(*args,**kwargs):return Fake()
    async def monitor(job,data):return None
    monkeypatch.setattr('app.media_tools.asyncio.create_subprocess_exec',spawn)
    tools.monitor=monitor
    tools.offsets[source_id]=90000
    async def scenario():
        await tools.launch(source_id,folder,info)
        await asyncio.sleep(0)
    try:
        asyncio.run(scenario())
        assert (folder/'offset').read_text()=='90000'
        assert (folder/'conversion').read_text()=='audio-v1:1'
        assert tools.started_at(source_id)==90000
    finally:shutil.rmtree(folder,ignore_errors=True)


def test_changing_audio_invalidates_only_the_old_conversion():
    """Un HLS anterior no puede reaparecer con la pista que tenía antes."""
    import asyncio,shutil
    from app.media_tools import MediaTools
    from app.settings import Settings
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/pelicula.mkv'}})
    source_id='j'*32;folder=tools.root/source_id
    folder.mkdir(parents=True,exist_ok=True)
    (folder/'index.m3u8').write_text('#EXTM3U\n#EXT-X-ENDLIST\n')
    (folder/'conversion').write_text('audio-v1:1')
    tools.audio_selections[source_id]=1
    try:
        asyncio.run(tools.select_audio(source_id,2))
        assert not folder.exists()
        assert tools.audio_selections[source_id]==2
    finally:shutil.rmtree(folder,ignore_errors=True)


def test_reopening_the_same_edition_does_not_probe_it_again():
    """Volver a abrir lo mismo costaba otros veinte segundos de FFprobe sobre el mismo archivo."""
    import asyncio
    from types import SimpleNamespace
    from app.media_tools import MediaTools,INSPECTION_MEMORY
    async def scenario():
        tools=MediaTools(SimpleNamespace());sondeos=[]
        async def probe(key):sondeos.append(key);return {'streams':[{'codec_type':'video'}]}
        tools._inspect=probe
        assert await tools.inspect('k'*32)
        await tools.stop('k'*32)
        assert await tools.inspect('k'*32)
        assert sondeos==['k'*32], 'la segunda vez se responde con lo ya inspeccionado'
        for index in range(INSPECTION_MEMORY+4):await tools.inspect(f'{index:032d}')
        assert len(tools.inspections)<=INSPECTION_MEMORY, 'la memoria de inspecciones va acotada'
    asyncio.run(scenario())

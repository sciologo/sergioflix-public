"""Que no se cuele una película doblada a un idioma que en esta casa nadie entiende.

Tres cosas distintas, y hacen falta las tres. Ver el idioma cuando está escrito, apartar
lo que no trae ninguna pista aceptable, y al convertir quedarse con la pista buena
cuando el archivo trae el original y el doblaje juntos.
"""
from app.media_tools import audio_choices, pick_audio, verify_audio_languages
import pytest
from app.providers.stremio import ProviderError
from app.stream_ranking import declared_audio_languages, describe, original_audio_languages

INGLES = original_audio_languages({"country": "United States"})

# Formato real de un alojador de publicaciones indias: el idioma va detrás de la marca
# de calidad y la palabra «audio» no aparece por ninguna parte.
DOBLE = {"url": "https://alojador.example/a.mkv",
         "description": "[PixelDrain] [💾 2.2 GB] The.Movie.2019.1080p.BluRay.Hindi.DDP2.0-English.5.1.HEVC",
         "behaviorHints": {"videoSize": 2 * 1024 ** 3}}
SOLO_DOBLAJE = {"url": "https://alojador.example/b.mkv",
                "description": "[PixelDrain] [💾 2.2 GB] The.Movie.2019.1080p.BluRay.Hindi.DDP5.1.HEVC",
                "behaviorHints": {"videoSize": 2 * 1024 ** 3}}
LIMPIA = {"url": "https://alojador.example/c.mkv",
          "description": "[PixelDrain] [💾 2.2 GB] The.Movie.2019.1080p.BluRay.English.DDP5.1.x264",
          "behaviorHints": {"videoSize": 2 * 1024 ** 3}}


def test_el_idioma_se_lee_aunque_la_etiqueta_no_diga_audio():
    """Antes solo se miraban las líneas con la palabra «audio», y así no se veía ninguno."""
    assert declared_audio_languages(DOBLE) == ["hindi", "english"]
    assert declared_audio_languages(SOLO_DOBLAJE) == ["hindi"]
    assert declared_audio_languages(LIMPIA) == ["english"]


def test_el_titulo_de_la_pelicula_no_se_confunde_con_su_idioma():
    """Solo se mira detrás de la marca de calidad: delante está el nombre."""
    ingles = {"url": "https://alojador.example/d.mkv",
              "description": "The.English.Patient.1996.1080p.BluRay.Spanish.DDP5.1"}
    assert declared_audio_languages(ingles) == ["spanish"]


def test_sin_pista_aceptable_no_se_ofrece():
    solo = describe(SOLO_DOBLAJE, "hdhub", 50, None, 7200, INGLES)
    assert solo["sin_audio_util"] and not solo["playable"]
    assert "solo hindi" in " ".join(solo["blockers"])


def test_la_adaptacion_no_castiga_el_doblaje_si_puede_elegir_el_original():
    """Un MKV adaptado no reproduce la pista cero: el servidor escogerá el inglés."""
    doble = describe(DOBLE, "hdhub", 50, None, 7200, INGLES)
    limpia = describe(LIMPIA, "hdhub", 50, None, 7200, INGLES)
    assert not doble["sin_audio_util"] and doble["playable"]
    assert doble["foreign_audio"] and not limpia["foreign_audio"]
    assert not doble["audio_risk"]
    assert "elegirá el audio original" in " ".join(doble["reasons"])
    # La limpia todavía puede ganar por copiar H.264 en vez de recodificar HEVC,
    # pero no por una pista Hindi que nunca llegará a la TV.
    assert limpia["order"] < doble["order"]


def test_sin_saber_el_idioma_original_no_se_castiga_a_nadie():
    """Ante metadatos incompletos no se inventa: apartar por adivinación sería peor."""
    doble = describe(DOBLE, "hdhub", 50, None, 7200, set())
    assert not doble["sin_audio_util"] and not doble["foreign_audio"]


def _pistas(*idiomas):
    salida = [{"codec_type": "video", "index": 0}]
    for posicion, idioma in enumerate(idiomas):
        salida.append({"codec_type": "audio", "index": posicion + 1, "tags": {"language": idioma}})
    return {"streams": salida}


def test_al_convertir_manda_el_espanol():
    assert pick_audio(_pistas("hin", "eng", "spa"))["tags"]["language"] == "spa"


def test_al_convertir_se_salta_el_doblaje_de_la_pista_cero():
    """El caso real: la publicación pone el hindi delante y el inglés detrás."""
    assert pick_audio(_pistas("hin", "eng"))["tags"]["language"] == "eng"


def test_el_usuario_puede_elegir_otra_pista_por_su_indice_real():
    info=_pistas("hin","eng","spa")
    assert pick_audio(info,1)["tags"]["language"]=="hin"
    choices=audio_choices(info,2)
    assert [item['label'] for item in choices]==['Hindi','Inglés','Español']
    assert [item['stream_index'] for item in choices if item['selected']]==[2]
    assert pick_audio(info,99) is None


def test_si_todo_es_doblaje_se_reproduce_igual():
    """Una película india de verdad: quedarse sin sonido sería peor que el idioma."""
    assert pick_audio(_pistas("hin", "tam"))["tags"]["language"] == "hin"


def test_sin_etiqueta_de_idioma_se_respeta_el_orden_del_archivo():
    assert pick_audio(_pistas("", ""))["index"] == 1


def test_un_archivo_sin_audio_no_revienta():
    assert pick_audio({"streams": [{"codec_type": "video", "index": 0}]}) is None


def test_el_audio_real_desmiente_una_publicacion_que_promete_ingles():
    """The Offer T1E3 de 0,37 GB: el proveedor dice Hindi/English, solo existe hin."""
    assert not describe(DOBLE,original_languages=INGLES)['sin_audio_util']
    with pytest.raises(ProviderError) as error:
        verify_audio_languages(_pistas('hin'),INGLES)
    assert error.value.code=='audio_language_mismatch' and error.value.status==422


def test_la_comprobacion_real_conserva_original_espanol_y_metadatos_desconocidos():
    for tracks,originals in [(_pistas('hin','eng'),INGLES),(_pistas('spa'),INGLES),
                            (_pistas('hin'),{'hindi'}),(_pistas('hin','und'),INGLES),
                            (_pistas(''),INGLES),(_pistas('hin'),set())]:
        verify_audio_languages(tracks,originals)


@pytest.mark.parametrize('suffix',['mkv','m3u8'])
def test_cast_y_selector_rechazan_audio_confirmado_antes_de_convertir(suffix):
    import asyncio
    from app.media_tools import MediaTools
    from app.settings import Settings
    tools=MediaTools(Settings(),lambda key:{'stream':{'url':'https://cdn.example/movie.'+suffix},'original_languages':['english']})
    async def inspect(key):return _pistas('hin')
    async def select(*args):raise AssertionError('No debe convertir una edición rechazada')
    tools.inspect=inspect;tools.select_audio=select
    for action in (tools.cast_path,tools.audio_tracks):
        with pytest.raises(ProviderError) as error:asyncio.run(action('a'*32))
        assert error.value.code=='audio_language_mismatch'

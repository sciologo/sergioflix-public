"""Adelantar sin reconvertir: la lista se sirve empezando por otro fragmento.

El reproductor de un televisor Samsung ignora la orden de saltar mientras la lista de
fragmentos no lleva marca de final, porque la trata como emisión en directo. Reproducir
desde el principio de lo que recibe sí sabe hacerlo siempre. Así que en vez de pedirle
que se coloque en el minuto veinte, se le entrega una lista que empieza ahí.
"""
from app.media_tools import MediaTools

CABECERA = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:5\n"
            "#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-INDEPENDENT-SEGMENTS\n")


def lista(fragmentos=10, duracion=4.0, terminada=False):
    cuerpo = "".join("#EXTINF:%f,\npart%06d.ts\n" % (duracion, i) for i in range(fragmentos))
    return CABECERA + cuerpo + ("#EXT-X-ENDLIST\n" if terminada else "")


def nombres(playlist):
    return [line for line in playlist.split("\n") if line.endswith(".ts")]


def test_recorta_en_el_fragmento_que_cubre_el_punto_pedido():
    recorte, fuera = MediaTools.cut_playlist(lista(), 20000)
    assert fuera == 20000
    assert nombres(recorte)[0] == "part000005.ts"
    assert len(nombres(recorte)) == 5


def test_el_numero_de_secuencia_acompana_al_recorte():
    """Sin esto el reproductor cree que el primer fragmento es el cero de la película."""
    recorte, _ = MediaTools.cut_playlist(lista(), 20000)
    assert "#EXT-X-MEDIA-SEQUENCE:5" in recorte
    assert "#EXT-X-MEDIA-SEQUENCE:0" not in recorte


def test_conserva_la_marca_de_final_si_la_habia():
    recorte, _ = MediaTools.cut_playlist(lista(terminada=True), 8000)
    assert recorte.strip().endswith("#EXT-X-ENDLIST")
    assert nombres(recorte)[0] == "part000002.ts"


def test_una_lista_sin_terminar_no_gana_marca_de_final():
    recorte, _ = MediaTools.cut_playlist(lista(), 8000)
    assert "#EXT-X-ENDLIST" not in recorte


def test_pedir_el_principio_devuelve_la_lista_intacta():
    original = lista()
    recorte, fuera = MediaTools.cut_playlist(original, 0)
    assert fuera == 0 and recorte == original


def test_no_deja_la_lista_sin_material():
    """Pedir el filo de lo convertido tiene que dejar algo que reproducir."""
    recorte, fuera = MediaTools.cut_playlist(lista(), 40000)
    assert len(nombres(recorte)) >= 3
    assert fuera < 40000


def test_el_desplazamiento_cae_en_el_borde_del_fragmento():
    """Se recorta por fragmentos enteros: pedir el segundo 22 deja fuera 20, no 22."""
    _, fuera = MediaTools.cut_playlist(lista(), 22000)
    assert fuera == 20000


def test_una_lista_todavia_vacia_no_se_recorta():
    recorte, fuera = MediaTools.cut_playlist(CABECERA, 20000)
    assert fuera == 0 and recorte == CABECERA


def test_los_fragmentos_rehechos_llevan_el_punto_de_conversion_en_la_url():
    versionada=MediaTools.version_segments(lista(3),932982)
    assert 'part000000.ts?v=932982' in versionada
    assert 'part000001.ts?v=932982' in versionada

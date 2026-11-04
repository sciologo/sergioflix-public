"""Descarta contradicciones explícitas entre una película y su archivo de video."""
import re

YEAR = re.compile(r'(?<!\d)((?:19|20)\d{2})(?!\d)')
EPISODE = re.compile(r'(?<![a-z0-9])(?:s\d{1,3}(?:[ ._-]*e\d{1,4})?|\d{1,3}x\d{1,4}|season[ ._-]+\d{1,3})(?![a-z0-9])', re.I)
QUALITY = re.compile(r'\b(?:480p|720p|1080p|2160p|4k|uhd)\b', re.I)


def movie_source_mismatch(stream, meta, media_type=None):
    """Rechaza episodios, temporadas y años incompatibles; sin evidencia conserva la fuente.

    No compara títulos por similitud: traducciones y nombres alternativos válidos
    no deben perderse. Un año que forma parte del título tampoco prueba un error.
    """
    meta = meta or {}
    if (media_type or meta.get('type')) != 'movie':
        return ''
    hints = stream.get('behaviorHints')
    hints = hints if isinstance(hints, dict) else {}
    texts = [str(hints.get('filename') or '')]
    for key in ('description', 'title'):
        texts.extend(str(stream.get(key) or '').splitlines())
    if any(EPISODE.search(text) for text in texts):
        return 'episode_for_movie'
    expected = YEAR.search(str(meta.get('releaseInfo') or ''))
    if not expected:
        return ''
    title_years = set(YEAR.findall(str(meta.get('name') or '')))
    for text in texts:
        # Solo el encabezado de publicación aporta el año, antes de calidad/audio.
        quality = QUALITY.search(text)
        if not quality and not re.search(r'\.(?:mkv|mp4|avi|m4v)\b|\((?:19|20)\d{2}\)', text, re.I):
            continue
        prefix = text[:quality.start()] if quality else text
        years = YEAR.findall(prefix)
        if not years or years[-1] in title_years:
            continue
        # Un año de diferencia puede ser estreno en otro país; no es contradicción.
        if abs(int(years[-1]) - int(expected.group(1))) > 1:
            return 'wrong_release_year'
    return ''

"""Identidad antes de calidad: una coincidencia técnica no acredita el contenido."""
import re
import unicodedata

EPISODE = re.compile(r"(?:s(\d{1,3})[ ._-]*e(\d{1,4})|(?<!\d)(\d{1,3})x(\d{1,4}))(?!\d)", re.I)
EXTENDED = re.compile(r"\b(?:extended(?:[ ._-]+cut)?|superfan|unrated|director.?s[ ._-]+cut)\b", re.I)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
DISC = re.compile(r"\b(?:cd|disc|disk)[ ._-]*0*([1-9])\b", re.I)


def words(value):
    text = unicodedata.normalize('NFKD', str(value or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower()
    return set(re.findall(r'[a-z0-9]+', text)) - {'the', 'a', 'an', 'srt', 'vtt', 'es', 'spa'}


def filename(stream):
    """Algunos proveedores añaden la tarjeta técnica después de |^|."""
    hints = stream.get('behaviorHints') or {}
    lines = str(hints.get('filename') or stream.get('description') or stream.get('title')
                or stream.get('name') or '').split('|^|')[0].splitlines()
    return lines[0] if lines else ''


def extended(value):
    return bool(EXTENDED.search(str(value or '').replace('.', ' ').replace('_', ' ')))


def identity_reason(source, row):
    """Contradicciones firmes se rechazan; un homónimo ambiguo necesita más evidencia.

    El título del episodio puede estar traducido. Su ausencia no se interpreta como
    error; en series homónimas tampoco alcanza para activar una pista automáticamente.
    La comprobación temporal se hace después y nunca sustituye esta identidad.
    """
    if row.get('embedded'):
        return ''
    name = str(row.get('name') or '')
    # Una parte CD1/CD2 no cubre una película publicada en un único archivo.
    subtitle_disc = DISC.search(name.replace('_', ' ').replace('.', ' '))
    video_disc = DISC.search(filename(source['stream']).replace('_', ' ').replace('.', ' '))
    if subtitle_disc and (not video_disc or subtitle_disc.group(1) != video_disc.group(1)):
        return 'subtitle_part_mismatch'
    parts = source['video_id'].split(':')
    if len(parts) == 3:
        wanted = tuple(map(int, parts[1:]))
        for field, expected in zip(('season', 'episode'), wanted):
            if row.get(field) not in (None, '', 0, expected, str(expected)):
                return 'episode_mismatch'
        found = EPISODE.search(name)
        if found:
            actual = tuple(int(n) for n in (found.groups()[:2] if found.group(1) else found.groups()[2:]))
            if actual != wanted:
                return 'episode_mismatch'
        else:
            compact = re.search(r'[_ .-](\d{2})(\d{2})\.(?:srt|vtt|ass|ssa)$', name, re.I)
            if compact and tuple(map(int, compact.groups())) != wanted:
                return 'episode_mismatch'
    metadata = source.get('subtitle_context') or {}
    title_words = words(metadata.get('name') or source.get('content_name'))
    marker = EPISODE.search(name)
    year_text = name[:marker.start()] if marker else name
    years = [year for year in YEAR.findall(year_text) if year not in title_words]
    expected_year = YEAR.search(str(metadata.get('releaseInfo') or ''))
    if years and expected_year:
        if abs(int(years[0]) - int(expected_year.group())) > 1:
            return 'series_year_mismatch'
    country = str(metadata.get('country') or '').lower()
    tokens = words(name)
    region_tokens = tokens - title_words
    us, uk = bool(region_tokens & {'us', 'usa', 'nbc'}), bool(region_tokens & {'uk', 'bbc'})
    if ('united states' in country and uk) or ('united kingdom' in country and us):
        return 'series_country_mismatch'
    if not extended(filename(source['stream'])) and extended(name):
        return 'edition_mismatch'
    if metadata.get('ambiguous'):
        episode_words = words(metadata.get('episode_name'))
        episode_matches = bool(episode_words and episode_words <= tokens)
        country_matches = ('united states' in country and us) or ('united kingdom' in country and uk)
        year_matches = bool(expected_year and expected_year.group() in years)
        if not (episode_matches or country_matches or year_matches or row.get('hash_match')):
            return 'identity_unverified'
    return ''

"""Ranking explicable orientado a inicio rápido, estabilidad y Chromecast 2/3."""
import hashlib
import re
from urllib.parse import urlsplit

from app.providers.stremio import PROVIDERS

# Tope real de un archivo servido desde la caché P2P; app/torrents.py lo aplica al abrirlo.
# Ofrecer algo mayor solo gasta 25 s de espera en un rechazo seguro.
MAX_CACHED_BYTES=12*1024**3
MAX_CACHED_GB=MAX_CACHED_BYTES/1024**3
# Un HTTP no ocupa caché, pero una edición de decenas de GB no la puede mover
# un Chromecast 2/3 por Wi-Fi doméstico: tampoco se ofrece.
MAX_STREAMED_GB=20
# Caudal medido del servidor, en bytes por segundo. Una edición idéntica servida a
# 2 MB/s arranca en segundos y a 0,4 MB/s tarda medio minuto: pesa tanto como la
# resolución, pero nunca tanto como la diferencia entre HTTP y P2P.
FAST_HOST=2*1024*1024
GOOD_HOST=1024*1024
SLOW_HOST=600*1024
# Clases de margen: el caudal que entrega el servidor frente al que exige el archivo
# (su tamaño dividido por su duración). Medido en la casa, es lo que de verdad decide
# si algo se ve entero o se corta: una edición de 4 GB en un capítulo de 50 minutos pide
# 11 Mbps, y este enlace no los da. Una muestra corta sobreestima el caudal sostenido,
# así que «cómodo» empieza en 2 y no en 1,5.
MARGIN_PLENTY=3.0
MARGIN_COMFORTABLE=2.0
# Por encima de esto la imagen ya no mejora en un Chromecast 2/3, y cada mega de más
# es disco, espera y una apuesta a que el servidor lo sostenga durante dos horas.
USEFUL_MBPS=6.0
# Orden de la clase de conversión. No es velocidad —recodificar va a 3,8x tiempo real en
# este equipo— sino calidad: al recodificar, la salida baja a 720p.
TRANSCODE_ORDER={'nativo':0,'copia':1,'recodifica':2}

# Idiomas que los proveedores suelen declarar en nombres de archivo. Se miran solo
# después de la marca de calidad o en una línea «Audio:», para no confundir títulos
# como «The French Dispatch» con una pista francesa.
LANGUAGE_PATTERNS=(
    ('spanish',r'\b(?:spanish|español|castellano|latino)\b'),
    ('english',r'\b(?:english|inglés)\b'),('hindi',r'\bhindi\b'),
    ('tamil',r'\btamil\b'),('telugu',r'\btelugu\b'),('malayalam',r'\bmalayalam\b'),
    ('bengali',r'\b(?:bengali|bangla)\b'),('french',r'\b(?:french|français)\b'),
    ('italian',r'\b(?:italian|italiano)\b'),('german',r'\b(?:german|deutsch)\b'),
    ('portuguese',r'\b(?:portuguese|português)\b'),('russian',r'\b(?:russian|русский)\b'),
    ('ukrainian',r'\b(?:ukrainian|українська)\b'),('polish',r'\b(?:polish|polski)\b'),
    ('turkish',r'\b(?:turkish|türkçe)\b'),('korean',r'\b(?:korean|한국어)\b'),
    ('japanese',r'\b(?:japanese|日本語)\b'),('chinese',r'\b(?:chinese|mandarin|cantonese)\b'),
    ('arabic',r'\b(?:arabic|العربية)\b'),('dutch',r'\b(?:dutch|nederlands)\b'),
    ('swedish',r'\b(?:swedish|svenska)\b'),('danish',r'\b(?:danish|dansk)\b'),
    ('norwegian',r'\b(?:norwegian|norsk)\b'),('finnish',r'\b(?:finnish|suomi)\b'),
    ('persian',r'\b(?:persian|farsi)\b'),('thai',r'\bthai\b'),
)

# Cinemeta no declara el idioma original, pero sí el país. Esta tabla deliberadamente
# conservadora solo decide cuando la relación es suficientemente clara; si no, el
# idioma queda neutral en vez de castigar una edición original por adivinación.
COUNTRY_LANGUAGES=(
    (('spain','mexico','argentina','chile','colombia','peru','uruguay','venezuela','cuba'),('spanish',)),
    (('united states','united kingdom','australia','new zealand','ireland'),('english',)),
    (('canada',),('english','french')),(('france',),('french',)),(('italy',),('italian',)),
    (('germany','austria'),('german',)),(('brazil','portugal'),('portuguese',)),
    (('south korea',),('korean',)),(('japan',),('japanese',)),
    (('china','hong kong','taiwan'),('chinese',)),(('russia','soviet union'),('russian',)),
    (('turkey',),('turkish',)),(('poland',),('polish',)),
    (('sweden',),('swedish',)),(('denmark',),('danish',)),(('norway',),('norwegian',)),
    (('finland',),('finnish',)),(('iran',),('persian',)),(('thailand',),('thai',)),
    (('india',),('hindi','tamil','telugu','malayalam','bengali')),
)


def original_audio_languages(meta):
    """Idiomas originales probables solo cuando el país permite inferirlos sin riesgo."""
    country=str((meta or {}).get('country') or '').lower()
    found=set()
    for names,languages in COUNTRY_LANGUAGES:
        if any(name in country for name in names):found.update(languages)
    return found


QUALITY_MARK=re.compile(r'\b(?:720p|1080p|2160p|4k|uhd)\b',re.I)


def declared_audio_languages(stream):
    """Idiomas en orden probable de pistas, usando solo zonas explícitas de la etiqueta.

    Se miran dos sitios y ninguno más, para no confundir el título de la película con
    su idioma: la parte del nombre de publicación que va **después** de la marca de
    calidad, que es donde por convención se escribe el audio, y las líneas que nombran
    el audio de forma explícita.

    Lo segundo solo no bastaba. Un alojador escribe
    `...BluRay.Hindi.DDP2.0-English.5.1.HEVC` sin usar nunca la palabra «audio», y así
    ninguna de sus fuentes quedaba marcada aunque el idioma estuviera a la vista.
    """
    parts=[]
    hints=stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'),dict) else {}
    textos=[str(hints.get('filename') or '')]
    for key in ('description','title','name'):
        textos.extend(str(stream.get(key) or '').splitlines())
    for texto in textos:
        quality=QUALITY_MARK.search(texto)
        if quality:parts.append(texto[quality.end():])
    for key in ('description','title','name'):
        for line in str(stream.get(key) or '').splitlines():
            if re.search(r'\b(?:audio|idioma|language)\b',line,re.I):parts.append(line)
    text=' '.join(parts).lower()
    matches=[]
    for language,pattern in LANGUAGE_PATTERNS:
        for match in re.finditer(pattern,text,re.I):matches.append((match.start(),language))
    ordered=[]
    for _,language in sorted(matches):
        if language not in ordered:ordered.append(language)
    return ordered


def runtime_seconds(meta):
    """Duración declarada del título. Sin ella no hay bitrate que estimar."""
    text=str((meta or {}).get('runtime') or '')
    hours=re.search(r'(\d+)\s*h',text)
    minutes=re.search(r'(\d+)\s*m',text)
    total=(int(hours.group(1))*60 if hours else 0)+(int(minutes.group(1)) if minutes else 0)
    if not total:
        alone=re.fullmatch(r'\s*(\d+)\s*',text)
        total=int(alone.group(1)) if alone else 0
    return total*60


def margin_class(margin,demand=None):
    """El caudal degrada, nunca promueve.

    Solo se mide a las que van primeras, así que premiar al que tiene medición hundía
    por sorteo a las demás: la elegida terminaba siendo la que alguien midió alguna vez,
    no la mejor. Sin medición se entra por arriba, y solo baja lo que se sabe apretado.

    La excepción es pedir el doble de lo útil: eso ya es evidencia por sí sola. Este
    enlace da entre 0,5 y 4,7 MB/s según el servidor, y un archivo de 12 Mbps exige
    1,5 MB/s sostenidos durante dos horas.
    """
    if margin is None:return 1 if demand and demand>USEFUL_MBPS*1.5 else 0
    if margin>=MARGIN_COMFORTABLE:return 0
    if margin>=1:return 1
    return 2


def ranking_tier(measured,margin,delivered,demand=None):
    """Jerarquía de calidad acordada, después de filtros, idioma y fallos.

    Una vez que la red entrega al menos el doble de lo exigido, más margen ya no
    compra mejor imagen: 1080p manda sobre 720p y dentro de cada resolución manda el
    bitrate útil. Lo desconocido queda detrás de 1080/720 comprobados, pero delante de
    480p. Una demanda extrema sin medición no es realmente «desconocida»: ya sabemos
    que excede ampliamente lo razonable para este receptor.
    """
    if margin is not None and margin>=MARGIN_COMFORTABLE:
        if delivered>=1080:return 0
        if delivered>=720:return 1
        return 3
    if margin is None:
        return 5 if demand and demand>USEFUL_MBPS*1.5 else 2
    return 4 if margin>=1 else 5


def url_key(url):
    """Identifica un enlace sin guardarlo: puede llevar el token privado del usuario."""
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def source_fingerprint(provider,stream):
    """Reconoce una edición entre URLs firmadas distintas sin conservar datos privados."""
    hints=stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'),dict) else {}
    def stable(value):
        value=re.sub(r'https?://\S+','[enlace]',str(value or '').lower())
        return re.sub(r'\s+',' ',value).strip()
    fields=[provider,stable(stream.get('infoHash')),str(stream.get('fileIdx') or ''),
            stable(hints.get('filename')),stable(stream.get('name')),stable(stream.get('title')),
            stable(stream.get('description')),str(declared_bytes(stream) or '')]
    # Una fuente sin etiqueta estable es rara; su enlace cifrado sirve como último recurso.
    if not any(fields[1:]):fields.append(str(stream.get('url') or ''))
    return hashlib.sha256('\x1f'.join(fields).encode()).hexdigest()[:32]


def _number(pattern,text):
    match=re.search(pattern,text,re.I)
    return float(match.group(1)) if match else None


def declared_bytes(stream):
    """`behaviorHints.videoSize` es el tamaño exacto; la etiqueta solo es el respaldo."""
    hints=stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'),dict) else {}
    size=hints.get('videoSize')
    return size if isinstance(size,int) and 0<size<2**53 else None


def describe(stream,provider='mediafusion',health=50,speeds=None,duration=0,original_languages=None,
             reputation='',provider_speeds=None):
    label=' '.join(str(stream.get(key) or '') for key in ('name','title','description'))
    lower=label.lower()
    url=str(stream.get('url') or '')
    path=urlsplit(url).path.lower()
    transport='http' if urlsplit(url).scheme in ('http','https') else 'torrent' if stream.get('infoHash') else 'unsupported'
    hls='.m3u8' in path or 'hls' in lower
    mp4='.mp4' in path or re.search(r'\bmp4\b',lower) is not None
    resolution=2160 if re.search(r'\b(?:2160p?|4k|uhd)\b',lower) else 1080 if re.search(r'\b1080p?\b',lower) else 720 if re.search(r'\b720p?\b',lower) else 480 if re.search(r'\b480p?\b',lower) else 0
    h264=bool(re.search(r'\b(?:h[ .]?264|avc|x264)\b',lower))
    incompatible_video=bool(re.search(r'\b(?:hevc|h[ .]?265|x265|av1)\b',lower))
    aac=bool(re.search(r'\baac\b',lower))
    incompatible_audio=bool(re.search(r'\b(?:truehd|dts(?:-hd)?|e-?ac-?3|ddp|atmos)\b',lower))
    languages=declared_audio_languages(stream)
    spanish=('spanish' in languages or
             bool(re.search(r'\b(?:spanish|español|castellano|latino|dual[ ._-]?lat)\b',lower)))
    originals=set(original_languages or ())
    accepted=originals|{'spanish'}
    # El primer idioma declarado suele ser la pista predeterminada. Solo se castiga
    # cuando conocemos el original; ante metadatos incompletos no se inventa un idioma.
    foreign_audio=bool(languages and originals and languages[0] not in accepted)
    # Distinto de lo anterior, y la diferencia importa: que la primera pista sea un
    # doblaje solo significa que el archivo lo pone delante, y el original suele venir
    # dentro —«Hindi DDP2.0-English 5.1» trae las dos—. Al convertir se elige la buena.
    # Lo que no se puede ver es lo que no trae ninguna pista aceptable.
    sin_audio_util=bool(languages and originals and not set(languages)&accepted)
    remux='remux' in lower
    bluray=bool(re.search(r'\b(?:blu[ .-]?ray|bdremux|bdmv)\b',lower))
    # Para elegir desde el sofá solo importa distinguir una grabación de sala de
    # una copia procedente de una fuente oficial. El release group o la plataforma
    # concreta no mejoran la decisión y se mantienen fuera de la interfaz.
    cinema_recording=bool(re.search(r'\b(?:cam(?:rip)?|hdcam|hdts|telesync|telecine)\b',lower) or
                          re.search(r'(?:^|[ ._-])(?:ts|tc)(?:$|[ ._-])',lower))
    origin='cine' if cinema_recording else 'original'
    bitrate=_number(r'(\d+(?:\.\d+)?)\s*(?:mbps|mb/s)',lower)
    exact=declared_bytes(stream)
    size_gb=round(exact/1024**3,2) if exact else _number(r'(\d+(?:\.\d+)?)\s*(?:gb|gib)',lower)
    # HDHub mezcla ocasionalmente un MP4/HLS genérico entre sus películas. En los casos
    # observados no identifica el archivo ni declara tamaño; como una publicidad también
    # es técnicamente un video válido, debe apartarse antes de llegar al reproductor.
    # La regla se limita al proveedor que exhibió el problema para no castigar HLS
    # legítimos de otros catálogos, donde omitir el tamaño es normal.
    filename=str((stream.get('behaviorHints') or {}).get('filename') or '')
    identified=bool(filename or re.search(r'\b(?:19|20)\d{2}\b|\bs\d{1,2}e\d{1,3}\b|\btt\d{5,12}\b',lower))
    suspected_ad=provider=='hdhub' and size_gb is None and not identified
    seeders=int(_number(r'(?:👤|seed(?:er)?s?)\s*[:=]?\s*(\d+)',lower) or 0)
    score=0
    reasons=[]
    def add(points,reason):
        nonlocal score
        score+=points
        if points:reasons.append(reason)
    add(65 if transport=='http' else -35 if transport=='torrent' else -100,'HTTP directo' if transport=='http' else 'P2P' if transport=='torrent' else 'No compatible')
    add(30 if hls else 18 if mp4 else 0,'HLS adaptativo' if hls else 'MP4 directo' if mp4 else '')
    # Una edición que el Chromecast acepta tal cual empieza en un par de segundos;
    # una que el PC debe convertir tarda más de veinte. Es una inferencia: las etiquetas
    # casi nunca declaran el audio, pero un MP4 con H.264 y sin audio incompatible
    # anunciado suele llevar AAC y reproducirse sin tocar nada.
    direct=transport=='http' and (mp4 or hls) and h264 and not incompatible_audio and not incompatible_video
    # Una lista HLS se entrega tal cual. El resto pasa por FFmpeg si no es directo, y
    # allí se puede escoger la pista original aunque el archivo traiga Hindi primero.
    selects_audio=transport=='http' and not hls and not direct
    selectable_original=bool(languages and accepted and set(languages)&accepted)
    audio_risk=foreign_audio and not (selects_audio and selectable_original)
    add(30 if direct else 0,'probablemente sin conversión' if direct else '')
    add(22 if resolution==1080 else 14 if resolution==720 else -38 if resolution>=2160 else 0,'1080p' if resolution==1080 else '720p' if resolution==720 else '4K innecesario' if resolution>=2160 else '')
    add(14 if h264 else -32 if incompatible_video else 0,'H.264/AVC' if h264 else 'video requiere recodificación completa' if incompatible_video else '')
    add(8 if aac else -9 if incompatible_audio else 0,'AAC' if aac else 'audio requiere adaptación' if incompatible_audio else '')
    add(16 if spanish else 0,'audio/etiqueta en español' if spanish else '')
    add(-120 if audio_risk else 0,
        'audio principal ajeno al original o español' if audio_risk else '')
    if foreign_audio and not audio_risk:reasons.append('la adaptación elegirá el audio original')
    add(-34 if remux else 0,'REMUX de alto caudal' if remux else '')
    add(-18 if bluray else 0,'Blu-ray' if bluray else '')
    if bitrate:
        add(12 if 2.5<=bitrate<=8 else -30 if bitrate>15 else -12 if bitrate>8 else 0,
            'bitrate adecuado' if 2.5<=bitrate<=8 else 'bitrate excesivo' if bitrate>8 else '')
    # En HTTP el archivo no se guarda completo: el tamaño ya participa como bitrate
    # estimado y volver a restarlo castigaría dos veces a un CDN que sí da el caudal.
    if size_gb and transport=='torrent':
        add(-25 if size_gb>30 else -12 if size_gb>20 else -5 if size_gb>12 else 0,'archivo muy grande' if size_gb>20 else 'archivo grande' if size_gb>12 else '')
    if transport=='torrent':add(min(18,seeders//3) if seeders else -10,'pares disponibles' if seeders else 'sin pares declarados')
    # La reputación aporta como máximo 15 puntos y no domina el transporte/formato.
    add(round((max(0,min(100,health))-50)*.3),'historial del proveedor')
    # La preferencia por proveedor es mucho menor que la diferencia HTTP/torrent (100 puntos):
    # un torrent de PenguPlay nunca adelanta a un HTTP/HLS de MediaFusion.
    spec=PROVIDERS.get(provider)
    add(spec.priority if spec else 0,f'proveedor preferido: {spec.display}' if spec and spec.priority else '')
    # Lo medido sobre este mismo servidor en reproducciones anteriores.
    server_measured=(speeds or {}).get(url_key(url)) if transport=='http' and url else None
    provider_measured=(provider_speeds or {}).get(provider) if transport=='http' else None
    measured=server_measured or provider_measured
    speed_scope='server' if server_measured else 'provider' if provider_measured else ''
    if measured:
        add(25 if measured>=FAST_HOST else 12 if measured>=GOOD_HOST else 0 if measured>=SLOW_HOST else -22,
            f'{"servidor" if speed_scope=="server" else "proveedor"} rápido ({measured/1048576:.1f} MB/s)'
            if measured>=GOOD_HOST else
            f'{"servidor" if speed_scope=="server" else "proveedor"} lento ({measured/1048576:.1f} MB/s)'
            if measured<SLOW_HOST else '')
    # Lo que decide de verdad: cuánto caudal exige el archivo frente al que da su
    # servidor. El tamaño es la mejor estimación del bitrate cuando la etiqueta calla.
    exact=declared_bytes(stream) or (int(size_gb*1024**3) if size_gb else None)
    needed=exact/duration if exact and duration else bitrate*1_000_000/8 if bitrate else None
    demand=needed*8/1_000_000 if needed else bitrate
    margin=measured/needed if measured and needed else None
    if margin is not None:
        reasons.append('caudal de sobra' if margin>=MARGIN_PLENTY else
                       'caudal cómodo' if margin>=MARGIN_COMFORTABLE else
                       'caudal justo' if margin>=1 else 'el servidor no da el caudal que pide')
    # Al recodificar la salida baja a 720p: la mejor fuente puede entregar la peor imagen.
    transcode='nativo' if hls else 'recodifica' if incompatible_video else 'copia'
    delivered=720 if transcode=='recodifica' else min(resolution or 720,1080)
    if demand and demand>USEFUL_MBPS:
        reasons.append('bitrate sin medir por encima de lo sostenible' if margin is None and demand>USEFUL_MBPS*1.5
                       else 'bitrate por encima de lo útil')
    # Motivos por los que esta fuente no sirve en este equipo, no por los que puntúa mal.
    # Un torrent se guarda en caché, así que su tope es el de la caché; un HTTP se
    # retransmite, pero una edición de decenas de GB no cabe en un Chromecast 2/3.
    limit=MAX_CACHED_GB if transport=='torrent' else MAX_STREAMED_GB
    blockers=[]
    # Un doblaje que no es ni el original ni el español no es una alternativa peor:
    # es una película que no se puede ver. Se aparta, no se ordena más abajo.
    if sin_audio_util:
        blockers.append('sin audio original ni español: solo '+', '.join(languages))
    if suspected_ad:
        blockers.append('HDHub no identifica el archivo ni informa su tamaño')
    if size_gb and size_gb>limit:
        blockers.append(f'{size_gb:g} GB: supera el máximo utilizable de {limit:g} GB')
    if transport=='torrent' and not seeders:
        blockers.append('sin pares: no se puede abrir')
    # El orden final. Antes que nada, lo utilizable y el idioma: una pista principal
    # ajena no adelanta al original o al español. Entre fuentes del mismo grupo de
    # idioma, el P2P nunca adelanta a un enlace directo. Dentro de eso manda el caudal,
    # luego la imagen sin pasarse de lo útil, y el puntaje clásico solo desempata.
    hard=reputation=='hard';repeated=reputation=='repeated';temporary=reputation=='temporary'
    if hard:score-=100
    elif repeated:score-=50
    elif temporary:
        score-=35
        reasons.append('fallo reciente; prioridad reducida temporalmente')
    # Un promedio de proveedor no comprueba este CDN. Puede advertir poco margen,
    # pero no promover una fuente a la clase de caudal confirmado.
    confirmed_margin=margin if speed_scope!='provider' or margin is None or margin<MARGIN_COMFORTABLE else None
    tier=ranking_tier(measured,confirmed_margin,delivered,demand)
    # El bitrate de entrada no describe la imagen entregada cuando FFmpeg recodifica a
    # 720p/4 Mbps. Se compara lo que realmente llega a la pantalla, con techo donde el
    # Chromecast deja de aportar mejora visible.
    useful_bitrate=min(demand or 0,4.0 if transcode=='recodifica' else USEFUL_MBPS)
    if tier in (0,1,3):
        # Red ya aprobada: calidad primero, sin premiar caudal que el receptor ya no
        # transforma en mejor imagen. El margen y la velocidad solo desempatan.
        tier_order=(1 if demand and demand>USEFUL_MBPS else 0,-useful_bitrate,
                    -(margin or 0),-(measured or 0),TRANSCODE_ORDER[transcode],-delivered)
    elif tier==2:
        # Sin caudal suficiente para calcular margen: resolución, prudencia y proveedor.
        tier_order=(-delivered,1 if demand and demand>USEFUL_MBPS else 0,
                    -useful_bitrate,TRANSCODE_ORDER[transcode],-(measured or 0))
    else:
        # Bajo el umbral manda la probabilidad de no cortar, no la resolución de portada.
        tier_order=(-(margin or 0),-delivered,-useful_bitrate,
                    TRANSCODE_ORDER[transcode],-(measured or 0))
    order=(0 if not blockers else 1,1 if audio_risk else 0,
           1 if hard else 0,0 if transport=='http' else 1,1 if repeated or temporary else 0,
           1 if tier>=4 else 0,
           1 if stream.get('subtitleEditionUnavailable') and not spanish else 0,tier,*tier_order,-int(score))
    return {'score':int(score),'transport':transport,'hls':hls,'resolution':resolution,'h264':h264,'aac':aac,
        'measured_bps':measured,'speed_scope':speed_scope,'margin':round(margin,2) if margin else None,
        'demand_mbps':round(demand,1) if demand else None,'transcode':transcode,
        'delivered':delivered,'ranking_tier':tier,'order':order,
        'spanish':spanish,'languages':languages,'foreign_audio':foreign_audio,'audio_risk':audio_risk,
        'bitrate_mbps':bitrate,'size_gb':size_gb,'seeders':seeders,'origin':origin,
        'provider':provider,'playable':not blockers,'blockers':blockers,'foreign_audio':foreign_audio,'sin_audio_util':sin_audio_util,
        'suspected_ad':suspected_ad,'reputation':reputation,
        'reasons':[reason for reason in reasons if reason]}


def rank_streams(streams,provider='mediafusion',health=50,speeds=None,duration=0,original_languages=None,
                 reputations=None,provider_speeds=None):
    return rank_catalog([{'provider':provider,'streams':streams,'health':health}],speeds,duration,
                        original_languages,reputations,provider_speeds)


def rank_catalog(groups,speeds=None,duration=0,original_languages=None,reputations=None,provider_speeds=None):
    """Ordena las fuentes de varios proveedores en una sola lista comparable."""
    rows,position=[],0
    for group in groups:
        provider,health=group.get('provider','mediafusion'),group.get('health',50)
        for stream in group.get('streams') or []:
            position+=1
            if not isinstance(stream,dict):continue
            fingerprint=source_fingerprint(provider,stream)
            analysis=describe(stream,provider,health,speeds,duration,original_languages,
                              (reputations or {}).get(fingerprint,''),provider_speeds)
            analysis['fingerprint']=fingerprint
            if analysis['transport']=='unsupported':continue
            rows.append((analysis['order'],position,stream,analysis))
    return [(stream,analysis) for _,__,stream,analysis in sorted(rows,key=lambda row:(row[0],row[1]))]

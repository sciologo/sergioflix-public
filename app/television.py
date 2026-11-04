"""Canales de TV abierta de Chile.

La lista viene del proyecto público iptv-org, que publica un índice de emisiones
abiertas y sus enlaces. Aquí solo se aceptan enlaces HTTPS con lista HLS y se
resuelven, como cualquier otra fuente, a través del proxy local: el Chromecast y
el teléfono nunca reciben la URL del emisor.
"""
import asyncio
import json
import re
import secrets
from urllib.parse import urljoin, urlsplit

from app.providers.stremio import ProviderError, public_dns

CHANNELS_URL='https://iptv-org.github.io/api/channels.json'
STREAMS_URL='https://iptv-org.github.io/api/streams.json'
# Los logotipos viven en su propio índice desde que la API se separó por recursos.
LOGOS_URL='https://iptv-org.github.io/api/logos.json'
# Las emisiones declaran su idioma en el índice de señales, no en el de canales.
FEEDS_URL='https://iptv-org.github.io/api/feeds.json'
COUNTRY='CL'
SPANISH='spa'
SECTIONS=('nacional','cable','deportes')
# v2 invalida la caché que guardaba una sola URL y podía perpetuar una señal caída.
CACHE_KEY='tv:cl:v2'
CACHE_SECONDS=21600
MAX_CHANNELS=400

# Nombres tal como los publica el índice; ordenan primero los canales conocidos.
FEATURED=['TVN','Mega','Canal 13','ChileVision','La Red','TV+','TV Chile','T13 En Vivo',
          '24 Horas','Telecanal','UCV TV','TVU','Bio Bio TV','ADN TV','Cooperativa','Zona Latina']
# Cable internacional en español. Se comparan como fragmentos porque el índice
# publica los canales con sufijos de región: «AMC Latin America», «DW Español».
FEATURED_CABLE=['cnn','telesur','dw','france 24','euronews','tve internacional','telemundo','univision',
                'star channel','amc','axn','sony','studio universal','paramount','comedy central',
                'discovery','history','national geographic','animal planet','cartoon network','nickelodeon',
                'disney','tnt','cinemax','espn','fox sports','tyc sports','directv sports','la 1','antena 3']
MAX_CABLE=800

# Deportes reúne las señales deportivas en español de Chile y del extranjero, sin el
# tope de Cable, y solo publica las que respondieron en la última comprobación.
# Estos tres encabezan la fila en cuanto exista una señal verificada de ellos.
SPORTS_FIRST=['tnt sports','espn','dsports','directv sports']
FEATURED_SPORTS=['tyc sports','bein sports','claro sports','win sports','tudn','fox deportes',
                 'fifa+','pluto tv fútbol','teledeporte','real madrid tv','aym sports','teletrak']
MAX_SPORTS=200
# Cuántas rutas por canal se prueban al renovar y cuánto puede tardar cada una.
SPORTS_URLS_CHECKED=3
CHECK_SECONDS=10
# Pluto TV entrega un cartel de retirada si la señal se pide sin sesión, con 200 y
# HLS válido. Se abre una sesión al renovar y solo se aceptan canales de su parrilla
# cuyo nombre siga siendo el esperado: Pluto reutiliza identificadores, y el que era
# CNN en Español hoy emite «Pluto TV Novelas».
PLUTO_BOOT_URL='https://boot.pluto.tv/v4/start'
# Las claves son los identificadores de iptv-org: si el índice ya trae el canal, la
# ruta de Pluto se suma como primera alternativa en vez de duplicarlo.
PLUTO_SPORTS={
    'PlutoTVFutbol.us':{'pluto':'63c6e37e636b7e0007ccb635','name':'Pluto TV Fútbol'},
    'ITVDeportes.mx':{'pluto':'63f7e3f9dff38e00082a57af','name':'AYM Sports 3'},
    'Motorvision.de':{'pluto':'60e5ea9a9bb4200008376f76','name':'Motorvision TV'},
    'PlutoTVESports.us':{'pluto':'5ff3934600d4c7000733ff49','name':'Pluto TV E-Sports'},
}

# Estos cinco accesos encabezan TV y forman la estantería «Favoritos». Las señales
# abiertas conservan más de una ruta para que una caída puntual del operador no deje
# el canal inutilizable. Los enlaces se validan al renovar la caché; no se publican al TV.
FAVORITE_CHANNELS=('TVN.cl','ChileVision.cl','Canal13.cl','Mega.cl','CNNenEspanol.us')
FAVORITE_RANK={channel:index for index,channel in enumerate(FAVORITE_CHANNELS)}
CURATED_CHANNELS={
    'TVN.cl': {'section':'nacional','name':'TVN','country':'CL','categories':['general'],
               'urls':['https://pantera1-100gb-cl-movistar.dps.live/tvn/tvn.smil/playlist.m3u8',
                       'https://iptv.bitred.cl/tvn/index.m3u8']},
    'ChileVision.cl': {'section':'nacional','name':'ChileVision','country':'CL','categories':['general'],
                       'urls':['https://origin.dpsgo.com/ssai/event/'
                               'R6cwOFyHQXaW6-kMcfWYKA/master.m3u8']},
    'Canal13.cl': {'section':'nacional','name':'Canal 13','country':'CL','categories':['general'],
                   'urls':['https://origin.dpsgo.com/ssai/event/'
                           'nthipJzGQY-A-N0t6xCZhA/master.m3u8']},
    'Mega.cl': {'section':'nacional','name':'Mega','country':'CL','categories':['general'],
                'urls':['https://pantera1-100gb-cl-movistar.dps.live/mega/mega.smil/playlist.m3u8',
                        'https://iptv.bitred.cl/mega/index.m3u8']},
    # CNN ofrecía gratuitamente esta señal lineal mediante Pluto TV. No es un enlace
    # insertado en el cliente: el PC lo valida y lo sirve por el mismo proxy local.
    # Desde 2026 ese identificador emite otra cosa; el favorito se oculta solo hasta
    # que la parrilla vuelva a nombrarlo CNN.
    'CNNenEspanol.us': {'section':'cable','name':'CNN en Español','country':'US','categories':['news'],
        'logo':'https://images.pluto.tv/channels/5dcde0cc2efd2700090b7ff4/colorLogoPNG.png',
        'pluto':'5dcde0cc2efd2700090b7ff4','pluto_name':'cnn',
        'urls':['https://service-stitcher.clusters.pluto.tv/stitch/hls/channel/'
                '5dcde0cc2efd2700090b7ff4/master.m3u8?deviceType=web&deviceMake=unknown&'
                'deviceModel=web&sid=sergioflix&deviceId=sergioflix&deviceVersion=1&'
                'appVersion=1&deviceDNT=0']},
}
# Rudo es el reproductor embebido por los sitios oficiales de estas señales. Leer su
# página evita fijar en código tokens o eventos que los canales pueden rotar.
RUDO_PAGES={
    'TVN.cl':'https://rudo.video/live/tvn',
    'ChileVision.cl':'https://rudo.video/live/chv',
    'Canal13.cl':'https://rudo.video/live/13cl',
    'Mega.cl':'https://rudo.video/live/mega',
}


def _usable(url):
    if not isinstance(url,str):return False
    parts=urlsplit(url)
    return (parts.scheme=='https' and bool(parts.hostname) and not parts.username
            and not parts.password and '.m3u8' in parts.path.lower() and len(url)<4000)


def _clean(text,limit=90):
    value=' '.join(str(text or '').split())
    return value[:limit]


def spanish_channels(feeds):
    """Canales con al menos una señal declarada en español."""
    return {feed.get('channel') for feed in feeds
            if isinstance(feed,dict) and SPANISH in (feed.get('languages') or [])}


_REFRESHING={}


def _refreshed(section):
    def done(task):
        _REFRESHING.pop(section,None)
        # Un fallo de renovación conserva la copia anterior; se lee para no dejarlo suelto.
        if not task.cancelled():task.exception()
    return done


def _withdrawn(playlist):
    """Pluto responde 200 con un cartel en bucle cuando la señal no está disponible."""
    return 'takedownslate' in playlist.lower()


def pluto_route(pluto,channel_id,expected):
    """Ruta de sesión de un canal de Pluto, solo si su nombre en la parrilla coincide."""
    found=pluto.get(channel_id) if channel_id else None
    if not found or expected.casefold() not in found.get('name','').casefold():return None
    return found if _usable(found['url']) else None


def pluto_sports(rows,pluto):
    """Suma los canales deportivos de Pluto TV que figuran en la parrilla de la sesión."""
    by_id={row['id']:row for row in rows}
    for channel_id,spec in PLUTO_SPORTS.items():
        found=pluto_route(pluto,spec['pluto'],spec['name'])
        if not found:continue
        row=by_id.get(channel_id)
        if row:
            row['urls']=[found['url']]+[url for url in row.get('urls',[]) if url!=found['url']]
            row['url']=found['url'];row['logo']=row.get('logo') or found['logo']
            continue
        by_id[channel_id]={'id':channel_id,'name':spec['name'],'logo':found['logo'],'country':'',
                           'categories':['sports'],'url':found['url'],'urls':[found['url']]}
    return list(by_id.values())


def sort_sports(rows):
    """TNT Sports, ESPN y DSports primero; después los reconocibles; el resto alfabético."""
    tokens=SPORTS_FIRST+FEATURED_SPORTS
    def rank(row):
        lower=row['name'].lower()
        return next((index for index,token in enumerate(tokens) if token in lower),len(tokens))
    return sorted(rows,key=lambda row:(rank(row),row['name'].lower()))[:MAX_SPORTS]


def normalize(channels,streams,logos=(),feeds=(),section='nacional'):
    """Une índice y emisiones y deja un enlace utilizable por canal.

    `nacional` son las señales abiertas de Chile. `cable` son canales de otros
    países cuya emisión está declarada en español, que es lo que se puede seguir
    sin depender de subtítulos que el proveedor no entrega. `deportes` son los de
    categoría deportiva de ambos orígenes; como un mismo canal publica emisiones en
    varios idiomas (FIFA+, Red Bull TV), ahí se descarta cada emisión en otro idioma.
    """
    spanish=spanish_channels(feeds) if section in ('cable','deportes') else set()
    feed_languages={(feed.get('channel'),feed.get('id')):feed.get('languages') or []
                    for feed in feeds if isinstance(feed,dict)}
    art={}
    for logo in logos:
        if not isinstance(logo,dict) or not logo.get('in_use'):continue
        url=logo.get('url')
        if isinstance(url,str) and url.startswith('https://') and len(url)<600:
            art.setdefault(logo.get('channel'),url)
    catalog={}
    for channel in channels:
        if not isinstance(channel,dict):continue
        if channel.get('closed') or not isinstance(channel.get('id'),str) or not channel.get('name'):continue
        if section=='deportes':
            if 'sports' not in (channel.get('categories') or []):continue
            if channel.get('country')!=COUNTRY and channel['id'] not in spanish:continue
        elif section=='cable':
            if channel.get('country')==COUNTRY or channel['id'] not in spanish:continue
        elif channel.get('country')!=COUNTRY:continue
        catalog[channel['id']]={'id':channel['id'],'name':_clean(channel['name']),
            'logo':art.get(channel['id'],''),'country':_clean(channel.get('country'),4),
            'categories':[_clean(c,24) for c in (channel.get('categories') or []) if isinstance(c,str)][:3]}
    rows=[]
    for stream in streams:
        if not isinstance(stream,dict):continue
        channel=catalog.get(stream.get('channel'))
        url=stream.get('url')
        if not channel or not _usable(url):continue
        if section=='deportes':
            languages=feed_languages.get((stream.get('channel'),stream.get('feed')))
            if languages and SPANISH not in languages:continue
        urls=channel.setdefault('urls',[])
        if url in urls:continue
        urls.append(url)
        if 'url' not in channel:
            channel['url']=url
            rows.append(channel)
    if section=='deportes':
        return sort_sports(rows)
    if section=='cable':
        def rank(row):
            lower=row['name'].lower()
            return next((index for index,token in enumerate(FEATURED_CABLE) if token in lower),len(FEATURED_CABLE))
        rows.sort(key=lambda row:(rank(row),row['name'].lower()))
        return rows[:MAX_CABLE]
    order={name.lower():index for index,name in enumerate(FEATURED)}
    rows.sort(key=lambda row:(order.get(row['name'].lower(),len(order)),row['name'].lower()))
    return rows[:MAX_CHANNELS]


class Television:
    def __init__(self,store,client):
        self.store,self.client=store,client

    def cached(self,section='nacional'):
        with self.store.connect() as conn:
            row=conn.execute("SELECT data, updated_at > now() - %s * interval '1 second' FROM catalog_page WHERE cache_key=%s",
                             (CACHE_SECONDS,CACHE_KEY+':'+section)).fetchone()
        return row

    def remember(self,rows,section='nacional'):
        with self.store.connect() as conn:
            conn.execute("""INSERT INTO catalog_page(cache_key,data) VALUES (%s,%s::jsonb)
                ON CONFLICT(cache_key) DO UPDATE SET data=EXCLUDED.data,updated_at=now()""",
                (CACHE_KEY+':'+section,json.dumps({'items':rows})))

    async def _fetch(self,url):
        try:
            async with self.client.stream('GET',url,follow_redirects=False) as response:
                if response.status_code!=200:
                    raise ProviderError('tv_unavailable','El índice de canales no respondió. Intenta más tarde.')
                chunks,size=[],0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>25_000_000:raise ProviderError('tv_too_large','El índice de canales es demasiado grande.')
                    chunks.append(chunk)
            data=json.loads(b''.join(chunks))
            if not isinstance(data,list):raise ValueError('lista requerida')
            return data
        except ProviderError:raise
        except Exception:
            raise ProviderError('tv_unavailable','No se pudo leer el índice de canales.') from None

    async def channels(self,section='nacional'):
        """Los enlaces no salen de aquí: el teléfono recibe nombre, logo, país y categoría."""
        from starlette.concurrency import run_in_threadpool
        cached=await run_in_threadpool(self.cached,section)
        if cached and cached[1]:
            rows=cached[0]['items']
        elif cached and section=='deportes':
            # Verificar cada señal tarda segundos: se sirve la copia anterior y se renueva aparte.
            rows=cached[0]['items']
            if section not in _REFRESHING:
                # Guardar la tarea la protege del recolector hasta que termine.
                _REFRESHING[section]=asyncio.create_task(self._rebuild(section))
                _REFRESHING[section].add_done_callback(_refreshed(section))
        else:
            try:rows=await self._rebuild(section)
            except ProviderError:
                if not cached:raise
                rows=cached[0]['items']
        return [{key:row.get(key,'') for key in
                 ('id','name','logo','country','categories','favorite','favorite_rank')} for row in rows]

    async def source(self,channel_id):
        """Convierte un canal en una fuente reproducible, igual que una película."""
        from starlette.concurrency import run_in_threadpool
        # Deportes va primero: sus rutas están verificadas, mientras que el mismo canal en
        # Cable o TV nacional conserva las del índice tal cual (TyC Sports abría en un 403).
        for section in ('deportes',)+tuple(section for section in SECTIONS if section!='deportes'):
            cached=await run_in_threadpool(self.cached,section)
            rows=cached[0]['items'] if cached else await self._rebuild(section)
            channel=next((row for row in rows if row['id']==channel_id),None)
            if channel:return await run_in_threadpool(self._save,channel)
        raise ProviderError('channel_not_found','Ese canal ya no está disponible.',404)

    async def _rebuild(self,section):
        from starlette.concurrency import run_in_threadpool
        channels,streams,logos=await self._fetch(CHANNELS_URL),await self._fetch(STREAMS_URL),await self._fetch(LOGOS_URL)
        feeds=await self._fetch(FEEDS_URL) if section in ('cable','deportes') else ()
        pluto=await self._pluto() if section in ('cable','deportes') else {}
        rows=normalize(channels,streams,logos,feeds,section)
        if section=='deportes':
            rows=sort_sports(await self._verified(pluto_sports(rows,pluto)))
        else:
            rows=await self._favorites(rows,section,pluto)
        await run_in_threadpool(self.remember,rows,section)
        return rows

    async def _pluto(self):
        """Abre una sesión de Pluto TV y devuelve ruta y logo de cada canal de su parrilla.

        Sin la sesión el servidor responde con un cartel de retirada; un canal que no
        figura en la parrilla de este país tampoco emite lo que su nombre promete.
        """
        try:
            boot=(await self.client.get(PLUTO_BOOT_URL,headers={'User-Agent':'Mozilla/5.0'},params={
                'appName':'web','appVersion':'9.0.0','deviceVersion':'120.0.0','deviceModel':'web',
                'deviceMake':'chrome','deviceType':'web','clientID':secrets.token_hex(16),
                'clientModelNumber':'1.0.0','channelSlug':'','serverSideAds':'false'})).json()
            token,params=boot['sessionToken'],boot['stitcherParams']
            stitcher,guide=boot['servers']['stitcher'],boot['servers']['channels']
            if not all(isinstance(value,str) for value in (token,params,stitcher,guide)):return {}
            if not (stitcher.startswith('https://') and guide.startswith('https://')):return {}
            listing=(await self.client.get(guide+'/v2/guide/channels',
                params={'channelIds':'','offset':0,'limit':1000,'sort':'number:asc'},
                headers={'User-Agent':'Mozilla/5.0','Authorization':'Bearer '+token})).json()
        except Exception:
            return {}
        result={}
        for channel in listing.get('data') or []:
            if not isinstance(channel,dict) or not re.fullmatch(r'[0-9a-f]{24}',str(channel.get('id'))):continue
            logo=next((image.get('url') for image in channel.get('images') or []
                       if isinstance(image,dict) and image.get('type')=='colorLogoPNG'),'')
            result[channel['id']]={'name':_clean(channel.get('name')),
                'url':f"{stitcher}/v2/stitch/hls/channel/{channel['id']}/master.m3u8?{params}"
                      f'&jwt={token}&masterJWTPassthrough=true',
                'logo':logo if isinstance(logo,str) and logo.startswith('https://') else ''}
        return result

    async def _verified(self,rows):
        """Deja en cada canal solo las rutas que respondieron ahora y descarta los mudos."""
        gate=asyncio.Semaphore(16)

        async def check(url):
            # Un segundo intento evita perder un canal seis horas por una lentitud puntual.
            for _ in range(2):
                async with gate:
                    try:
                        if await asyncio.wait_for(self._playlist_works(url),CHECK_SECONDS):return True
                    except asyncio.TimeoutError:pass
            return False

        candidates=[(row,(row.get('urls') or [row['url']])[:SPORTS_URLS_CHECKED]) for row in rows]
        checks=iter(await asyncio.gather(*(check(url) for _,urls in candidates for url in urls)))
        result=[]
        for row,urls in candidates:
            working=[url for url in urls if next(checks)]
            if not working:continue
            row['urls']=working;row['url']=working[0]
            result.append(row)
        return result

    async def _read_text(self,url,limit=500_000):
        """Lee una página pública acotada; se usa solo para descubrir/validar HLS."""
        try:
            current=url
            for _ in range(5):
                parts=urlsplit(current)
                if parts.scheme!='https' or not parts.hostname:return ''
                await public_dns(parts.hostname)
                async with self.client.stream('GET',current,follow_redirects=False,
                                              headers={'User-Agent':'Mozilla/5.0'}) as response:
                    if response.status_code in (301,302,303,307,308):
                        current=urljoin(current,response.headers.get('location',''))
                        continue
                    if response.status_code!=200:return ''
                    chunks,size=[],0
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>limit:return ''
                        chunks.append(chunk)
                    return b''.join(chunks).decode('utf-8-sig','replace')
            return ''
        except Exception:
            return ''

    async def _official_url(self,channel_id):
        page=await self._read_text(RUDO_PAGES[channel_id])
        candidates=re.findall(r'https://[^"\'\s]+\.m3u8(?:\?[^"\'\s<]*)?',page,re.I)
        # `rudo.m3u8` es el placeholder del elemento <video>; la URL del evento es la real.
        return next((url for url in candidates if _usable(url) and 'rudo.video/rudo.m3u8' not in url),'')

    async def _playlist_works(self,url):
        """Comprueba manifiesto maestro y variante; un 200 HTML no cuenta como señal."""
        master=await self._read_text(url,1_000_000)
        if not master.lstrip().startswith('#EXTM3U'):return False
        if '#EXT-X-STREAM-INF' not in master:
            return not _withdrawn(master) and await self._segment_works(url,master)
        child=next((line.strip() for line in master.splitlines()
                    if line.strip() and not line.lstrip().startswith('#')), '')
        if not child:return False
        child=urljoin(url,child)
        if not _usable(child):return False
        media=await self._read_text(child,1_000_000)
        return (media.lstrip().startswith('#EXTM3U') and not _withdrawn(media)
                and await self._segment_works(child,media))

    async def _segment_works(self,url,media):
        """Pide el comienzo del último fragmento: hay listas que responden y videos que no."""
        segment=next((line.strip() for line in reversed(media.splitlines())
                      if line.strip() and not line.lstrip().startswith('#')),'')
        if not segment:return False
        target=urljoin(url,segment)
        parts=urlsplit(target)
        if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:return False
        try:
            await public_dns(parts.hostname)
            async with self.client.stream('GET',target,follow_redirects=True,
                    headers={'User-Agent':'Mozilla/5.0','Range':'bytes=0-4095'}) as response:
                if response.status_code not in (200,206):return False
                async for chunk in response.aiter_bytes():
                    return len(chunk)>0
            return False
        except Exception:
            return False

    async def _favorites(self,rows,section,pluto=None):
        """Añade los favoritos y deja primero solo sus rutas que respondieron ahora."""
        pluto=pluto or {}
        by_id={row['id']:row for row in rows}
        official_ids=[channel for channel in RUDO_PAGES
                      if CURATED_CHANNELS[channel]['section']==section]
        official=await asyncio.gather(*(self._official_url(channel) for channel in official_ids))
        official_by_id=dict(zip(official_ids,official))
        targets=[]
        for channel_id in FAVORITE_CHANNELS:
            spec=CURATED_CHANNELS[channel_id]
            if spec['section']!=section:continue
            row=by_id.get(channel_id)
            if not row:
                row={'id':channel_id,'name':spec['name'],'logo':spec.get('logo',''),
                     'country':spec['country'],'categories':spec['categories']}
            urls=[]
            session=(pluto_route(pluto,spec.get('pluto'),spec.get('pluto_name',''))or{}).get('url','')
            for candidate in [official_by_id.get(channel_id,''),session]+spec['urls']+row.get('urls',[row.get('url','')]):
                if candidate and candidate not in urls and _usable(candidate):urls.append(candidate)
            targets.append((channel_id,row,urls))
        checks=await asyncio.gather(*(
            self._playlist_works(url) for _,_,urls in targets for url in urls
        ))
        cursor=0
        for channel_id,row,urls in targets:
            working=[]
            for url in urls:
                if checks[cursor]:working.append(url)
                cursor+=1
            if not working:continue
            row['urls']=working;row['url']=working[0]
            row['favorite']=True;row['favorite_rank']=FAVORITE_RANK[channel_id]
            by_id[channel_id]=row
        result=list(by_id.values())
        if section=='cable':
            result.sort(key=lambda row:(0 if row.get('favorite') else 1,row.get('favorite_rank',999),row['name'].lower()))
            return result[:MAX_CABLE]
        order={name.lower():index for index,name in enumerate(FEATURED)}
        result.sort(key=lambda row:(0 if row.get('favorite') else 1,row.get('favorite_rank',999),
                                    order.get(row['name'].lower(),len(order)),row['name'].lower()))
        return result[:MAX_CHANNELS]

    def _save(self,channel):
        content_id='tv:'+channel['id']
        urls=channel.get('urls') or [channel['url']]
        tags=[t for t in [channel.get('country','')]+list(channel.get('categories') or []) if t]
        payload={'id':content_id,'type':'tv','name':channel['name'],'poster':channel['logo'],
                 'background':channel['logo'],'description':'Señal en directo · '+', '.join(tags or ['general']),
                 'genres':channel['categories'],'releaseInfo':'En directo','episodes':[]}
        with self.store.connect() as conn:
            conn.execute("""INSERT INTO content(id,media_type,name,data) VALUES (%s,'tv',%s,%s::jsonb)
                ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,data=EXCLUDED.data,updated_at=now()""",
                (content_id,channel['name'],json.dumps(payload)))
            conn.execute('DELETE FROM media_source WHERE expires_at<now()')
            items=[]
            for index,url in enumerate(urls):
                source_id=secrets.token_urlsafe(32)
                stream={'url':url,'name':channel['name']}
                data=self.store.cipher.encrypt(json.dumps(stream).encode()).decode()
                conn.execute("""INSERT INTO media_source(id,content_id,video_id,encrypted_data,provider,rank_score)
                    VALUES (%s,%s,%s,%s,'tv',%s)""",
                    (source_id,content_id,content_id,data,100-index))
                items.append({'source_id':source_id,
                    'label':channel['name']+('' if index==0 else ' · alternativa '+str(index+1)),
                    'transport':'http','seeders':0,'provider':'tv','provider_name':'TV en directo',
                    'score':100-index,'reasons':['señal en directo'],'size_gb':None,
                    'playable':True,'blockers':[],'spanish_verified':False,'cast_verified':False})
        return {'content':payload,'items':items}

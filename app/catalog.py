"""Catálogo Cinemeta con caché persistente y orden explícito de episodios."""
import json
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.providers.stremio import Cinemeta, ProviderError


def regular_episodes(videos: list) -> list:
    """No incorporar especiales, futuros ni duplicados en la cola ordinaria."""
    episodes = {}
    now = datetime.now(timezone.utc)
    for video in videos:
        try:
            season, episode = int(video.get("season", 0)), int(video.get("episode", 0))
            if season < 1 or episode < 1:
                continue
            released = video.get("released")
            if released:
                aired=datetime.fromisoformat(released.replace("Z", "+00:00"))
                if aired.tzinfo is None:aired=aired.replace(tzinfo=timezone.utc)
                if aired>now:continue
            if not video.get("id"):
                continue
            episodes.setdefault((season,episode), {**video,"season":season,"episode":episode})
        except (ValueError, TypeError, AttributeError):
            continue
    return [episodes[key] for key in sorted(episodes)]


class Catalog:
    def __init__(self, store, client: httpx.AsyncClient):
        self.store, self.client = store, client
        self.fetcher = Cinemeta(client, "https://v3-cinemeta.strem.io/manifest.json", {"v3-cinemeta.strem.io"})

    def cached(self, key, max_age=14400, since=None):
        with self.store.connect() as conn:
            if since is not None:
                return conn.execute("SELECT data, updated_at >= %s FROM catalog_page WHERE cache_key=%s",(since,key)).fetchone()
            row=conn.execute("SELECT data, updated_at > now() - %s * interval '1 second' FROM catalog_page WHERE cache_key=%s",(max_age,key)).fetchone()
        return row

    def remember(self, key, data, items):
        with self.store.connect() as conn:
            for item in items:
                if item.get("type") not in ("movie","series") or not item.get("id") or not item.get("name"):
                    continue
                conn.execute("""INSERT INTO content(id, media_type, name, data) VALUES (%s,%s,%s,%s::jsonb)
                    ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name,data=content.data || EXCLUDED.data,updated_at=now()""",
                    (item["id"],item["type"],item["name"],json.dumps(item)))
            conn.execute("""INSERT INTO catalog_page(cache_key,data) VALUES (%s,%s::jsonb)
                ON CONFLICT(cache_key) DO UPDATE SET data=EXCLUDED.data,updated_at=now()""",(key,json.dumps(data)))

    async def page(self, media_type, skip=0, genre=None, search=None, daily=False):
        from starlette.concurrency import run_in_threadpool
        extras = {"skip":str(skip)}
        if genre: extras["genre"]=genre
        if search: extras["search"]=search
        args="&".join(quote(k,safe="")+"="+quote(v,safe="") for k,v in extras.items())
        path=f"/catalog/{media_type}/top/{args}.json"
        cache_key=('search-v2:' if search else '')+path
        # La TV renueva populares en la primera consulta del día, sin alterar
        # la caché de búsqueda ni acumular una clave nueva por cada fecha.
        since=None
        if daily and not search:
            cache_key='daily:'+cache_key
            since=datetime.now().astimezone().replace(hour=0,minute=0,second=0,microsecond=0)
        cached=await run_in_threadpool(self.cached,cache_key,since=since)
        if since is not None and not cached:
            # Al activar la renovación diaria también sirve la copia anterior
            # si la primera consulta coincide con una caída del proveedor.
            previous=await run_in_threadpool(self.cached,path)
            if previous: cached=(previous[0],False)
        if cached and cached[1]: return {**cached[0],"stale":False}
        try:
            # Cinemeta publica actualmente sus páginas en este subdominio propio.
            # Se evita seguir redirecciones arbitrarias del conector de proveedores.
            data=await self.fetcher._json(("https://v3-cinemeta.strem.io" if search else "https://cinemeta-catalogs.strem.io/top")+path)
            metas=data.get("metas")
            if not isinstance(metas,list): raise ProviderError("invalid_catalog","El catálogo no respondió correctamente.")
            items=[{k:item[k] for k in ("id","type","name","poster","background","description","genres","releaseInfo","imdbRating") if k in item}
                   for item in metas if isinstance(item,dict) and item.get("id") and item.get("name")]
            if search:
                def local_matches():
                    with self.store.connect() as conn:
                        return [r[0] for r in conn.execute('SELECT data FROM content WHERE media_type=%s AND name ILIKE %s ORDER BY name LIMIT 50',(media_type,'%'+search+'%')).fetchall()]
                combined=await run_in_threadpool(local_matches)+items
                items=list({item['id']:item for item in reversed(combined)}.values())[::-1]
            result={"items":items,"next_skip":skip+50 if metas else None,"source":"Cinemeta"}
            await run_in_threadpool(self.remember,cache_key,result,items)
            return {**result,"stale":False}
        except ProviderError:
            if cached: return {**cached[0],"stale":True}
            raise

    async def detail(self, media_type, content_id):
        from starlette.concurrency import run_in_threadpool
        path=f"/meta/{media_type}/{quote(content_id,safe='')}.json"
        # v2 incorpora `country`, necesario para no confundir un doblaje ajeno con el
        # idioma original. Separar la clave activa el dato sin borrar la caché anterior.
        cache_key='detail-country-v2:'+path
        cached=await run_in_threadpool(self.cached,cache_key)
        if cached and cached[1]: return cached[0]
        try:
            data=await self.fetcher._json("https://v3-cinemeta.strem.io"+path)
            meta=data.get("meta")
            if not isinstance(meta,dict) or meta.get("id")!=content_id or meta.get("type")!=media_type:
                raise ProviderError("not_found","No se encontró la ficha del título.",404)
            result={k:meta[k] for k in ("id","type","name","poster","background","description","genres","releaseInfo","imdbRating","runtime","director","cast","country") if k in meta}
            result["episodes"]=regular_episodes(meta.get("videos",[])) if media_type=="series" else []
            await run_in_threadpool(self.remember,cache_key,result,[result])
            return result
        except ProviderError:
            if cached: return cached[0]
            raise

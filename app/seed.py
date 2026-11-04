"""Carga inicial paginada de fichas reales, sin descargar imágenes ni videos."""
import asyncio
import json

import httpx

from app.catalog import Catalog
from app.settings import Settings
from app.storage import Store


async def seed(pages=30):
    store=Store(Settings())
    async with httpx.AsyncClient(timeout=25,trust_env=False) as client:
        catalog=Catalog(store,client)
        for media_type in ('movie','series'):
            for page in range(pages):
                data=await catalog.page(media_type,page*50)
                if not data['items']: break
                if page%5==0: print(media_type,'páginas:',page+1,flush=True)
                await asyncio.sleep(.3)
    with store.connect() as conn:
        counts=conn.execute('SELECT media_type,count(*) FROM content GROUP BY media_type ORDER BY media_type').fetchall()
    print(json.dumps(dict(counts)))


if __name__=='__main__': asyncio.run(seed())

"""La ficha de Catedrales sigue el traslado público sin abrir proveedores privados."""
import asyncio
import time

import httpx
import pytest

from app.catalog import Catalog
from app.providers.stremio import Cinemeta, MAX_RESPONSE, MediaFusion, ProviderError

ORIGIN = 'https://v3-cinemeta.strem.io'
LIVE = 'https://cinemeta-live.strem.io'
PATH = '/meta/series/tt44600168.json'
META = {'id':'tt44600168','type':'series','name':'Catedrales','releaseInfo':'2026',
        'videos':[{'id':f'tt44600168:1:{i}','season':1,'episode':i,'title':str(i),
                   'released':'2026-09-16T00:00:00.000Z'} for i in range(1,7)]}


async def no_dns(host):
    pass


class MemoryCatalog(Catalog):
    def __init__(self, client):
        super().__init__(None, client)
        self.fetcher.dns_check = no_dns
        self.pages, self.writes = {}, []

    def cached(self, key, *args, **kwargs):
        return self.pages.get(key)

    def remember(self, key, data, items):
        self.pages[key] = (data, True)
        self.writes.append((key, data))


def adapter(client, dns=no_dns):
    return Cinemeta(client, ORIGIN+'/manifest.json', {'v3-cinemeta.strem.io'}, dns_check=dns)


@pytest.mark.parametrize('status', [301,302,303,307,308])
def test_redirected_series_loads_all_episodes_and_reuses_the_valid_cache(status):
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        if request.url.host=='v3-cinemeta.strem.io':
            return httpx.Response(status,headers={'Location':LIVE+PATH})
        return httpx.Response(200,json={'meta':META})
    async def run():
        # Incluso si el cliente compartido tiene follow_redirects=True, el
        # adaptador conserva el control de cada salto y de sus dominios.
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
            catalog=MemoryCatalog(client)
            first=await catalog.detail('series','tt44600168')
            assert [e['id'] for e in first['episodes']]==[f'tt44600168:1:{i}' for i in range(1,7)]
            assert await catalog.detail('series','tt44600168')==first
            assert len(catalog.writes)==1
        assert requests==[ORIGIN+PATH,LIVE+PATH]
    asyncio.run(run())


@pytest.mark.parametrize('location', [
    'http://cinemeta-live.strem.io'+PATH,
    'https://cinemeta-live.strem.io.evil.test'+PATH,
    'https://127.0.0.1'+PATH,
    'https://localhost'+PATH,
    'https://secret:password@cinemeta-live.strem.io'+PATH,
    'https://cinemeta-live.strem.io:444'+PATH,
    'https://cinemeta-live.strem.io:bad'+PATH,
    LIVE+PATH+'#secret',
    LIVE+'/\\secret',
    LIVE+PATH+'\n',
    'https://[broken',
    '',
])
def test_invalid_redirect_is_rejected_before_requesting_its_destination(location):
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(307,headers={'Location':location})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError) as caught:await adapter(client)._json(ORIGIN+PATH)
            # HTTPX descarta ciertos Location malformados antes de entregar la
            # respuesta al adaptador; ambos errores son seguros y no siguen el salto.
            assert caught.value.code in ('provider_response','unreachable')
            assert 'secret' not in caught.value.message and 'password' not in caught.value.message
        assert requests==[ORIGIN+PATH]
    asyncio.run(run())


def test_redirect_loop_stops_after_two_hops():
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        target=LIVE if request.url.host=='v3-cinemeta.strem.io' else ORIGIN
        return httpx.Response(307,headers={'Location':target+PATH})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError,match='provider_response'):await adapter(client)._json(ORIGIN+PATH)
        assert len(requests)==3
    asyncio.run(run())


def test_relative_redirect_closes_its_body_before_requesting_the_destination():
    class RedirectBody(httpx.AsyncByteStream):
        closed=False
        async def __aiter__(self):
            pytest.fail('No se debe descargar el cuerpo de una redirección')
            yield b''
        async def aclose(self):self.closed=True
    body=RedirectBody()
    def handler(request):
        if not request.url.query:
            return httpx.Response(307,headers={'Location':PATH+'?fresh=1'},stream=body)
        assert body.closed
        return httpx.Response(200,json={'meta':META})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert (await adapter(client)._json(ORIGIN+PATH))['meta']['id']=='tt44600168'
        assert body.closed
    asyncio.run(run())


def test_two_authorized_redirects_can_complete_successfully():
    requests=[]
    def handler(request):
        requests.append(request.url.host)
        if request.url.host=='v3-cinemeta.strem.io':
            return httpx.Response(307,headers={'Location':'https://cinemeta-catalogs.strem.io'+PATH})
        if request.url.host=='cinemeta-catalogs.strem.io':
            return httpx.Response(308,headers={'Location':LIVE+PATH})
        return httpx.Response(200,json={'meta':META})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert (await adapter(client)._json(ORIGIN+PATH))['meta']['id']=='tt44600168'
        assert requests==['v3-cinemeta.strem.io','cinemeta-catalogs.strem.io','cinemeta-live.strem.io']
    asyncio.run(run())


def test_unavailable_destination_preserves_the_previous_cached_detail():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request:httpx.Response(307,headers={'Location':'https://unrelated.example/'}))) as client:
            catalog=MemoryCatalog(client)
            old={'id':'tt44600168','type':'series','name':'Catedrales','episodes':[]}
            catalog.pages['detail-country-v2:'+PATH]=(old,False)
            assert await catalog.detail('series','tt44600168')==old and not catalog.writes
    asyncio.run(run())


def test_every_destination_is_dns_checked_and_a_private_address_stops_the_chain():
    requests,dns_checks=[],[]
    async def dns(host):
        dns_checks.append(host)
        if host=='cinemeta-live.strem.io':raise ProviderError('invalid_host','Destino no público.')
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(307,headers={'Location':LIVE+PATH})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError,match='invalid_host'):await adapter(client,dns)._json(ORIGIN+PATH)
        assert requests==[ORIGIN+PATH] and dns_checks==['v3-cinemeta.strem.io','cinemeta-live.strem.io']
    asyncio.run(run())


@pytest.mark.parametrize('change', [{'id':'tt4944694'}, {'type':'movie'}, {'type':None}])
def test_redirect_cannot_replace_the_requested_title_or_type(change):
    def handler(request):
        if request.url.host=='v3-cinemeta.strem.io':return httpx.Response(307,headers={'Location':LIVE+PATH})
        return httpx.Response(200,json={'meta':{**META,**change}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            catalog=MemoryCatalog(client)
            with pytest.raises(ProviderError,match='not_found'):await catalog.detail('series','tt44600168')
            assert not catalog.writes
    asyncio.run(run())


def test_private_provider_still_rejects_redirects_even_to_a_cinemeta_host():
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(307,headers={'Location':LIVE+PATH})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
            addon=MediaFusion(client,'https://mediafusion.elfhosted.com/private-test-token/manifest.json',
                              {'mediafusion.elfhosted.com'},dns_check=no_dns)
            with pytest.raises(ProviderError) as caught:await addon.manifest()
            assert caught.value.code=='provider_response' and 'private-test-token' not in caught.value.message
        assert len(requests)==1
    asyncio.run(run())


def test_response_size_limit_also_applies_after_a_redirect():
    def handler(request):
        if request.url.host=='v3-cinemeta.strem.io':return httpx.Response(307,headers={'Location':LIVE+PATH})
        return httpx.Response(200,content=b' '*(MAX_RESPONSE+1))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError,match='response_too_large'):await adapter(client)._json(ORIGIN+PATH)
    asyncio.run(run())


def test_redirects_share_one_total_timeout(monkeypatch):
    original=asyncio.timeout
    monkeypatch.setattr('app.providers.stremio.asyncio.timeout',lambda seconds:original(.12))
    requests=[]
    async def handler(request):
        requests.append(str(request.url));await asyncio.sleep(.08)
        if request.url.host=='v3-cinemeta.strem.io':return httpx.Response(307,headers={'Location':LIVE+PATH})
        return httpx.Response(200,json={'meta':META})
    async def run():
        start=time.monotonic()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderError,match='timeout'):await adapter(client)._json(ORIGIN+PATH)
        assert len(requests)==2 and time.monotonic()-start<.4
    asyncio.run(run())

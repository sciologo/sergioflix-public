"""Resolver el destino real de cada enlace antes de elegirlo."""
import asyncio

import httpx

from app.probing import final_host, resolve_speeds
from app.stream_ranking import url_key

FAST = 'https://pengu.uk/direct/uno'
SLOW = 'https://pengu.uk/direct/dos'
SPEEDS = {'rapido.cdn': 3 * 1024 * 1024, 'lento.cdn': 300 * 1024}


def transport(hops=1, broken=None):
    def handler(request):
        path = request.url.path
        if request.url.host == 'pengu.uk':
            if path == '/direct/roto':
                return httpx.Response(500)
            target = 'https://rapido.cdn/f' if path.endswith('uno') else 'https://lento.cdn/f'
            if hops > 1:
                target = f'https://salto.cdn/a?to={target}'
            return httpx.Response(307, headers={'Location': target})
        if request.url.host == 'salto.cdn':
            return httpx.Response(302, headers={'Location': request.url.params['to']})
        return httpx.Response(206, content=b'v', headers={'Content-Range': 'bytes 0-0/100'})
    return httpx.MockTransport(handler)


def run(coroutine):
    async def go():
        async with httpx.AsyncClient(transport=transport()) as client:
            return await coroutine(client)
    return asyncio.run(go())


def test_the_final_server_is_found_behind_the_provider_link():
    assert run(lambda client: final_host(client, FAST)) == 'rapido.cdn'
    assert run(lambda client: final_host(client, SLOW)) == 'lento.cdn'


def test_several_redirects_are_followed():
    async def go():
        async with httpx.AsyncClient(transport=transport(hops=2)) as client:
            return await final_host(client, FAST)
    assert asyncio.run(go()) == 'rapido.cdn'


def test_speeds_come_back_keyed_by_the_link_the_ranking_will_see():
    speeds = run(lambda client: resolve_speeds(client, [FAST, SLOW], SPEEDS))
    assert speeds == {url_key(FAST): 3 * 1024 * 1024, url_key(SLOW): 300 * 1024}


def test_an_unknown_or_broken_link_simply_does_not_score():
    speeds = run(lambda client: resolve_speeds(client, [FAST, 'https://pengu.uk/direct/roto'], SPEEDS))
    # El roto se resuelve a un servidor que no está medido: no suma ni resta.
    assert list(speeds) == [url_key(FAST)]
    assert run(lambda client: resolve_speeds(client, [FAST], {})) == {}


def test_only_a_handful_of_candidates_are_probed():
    from app.probing import PROBE_CANDIDATES
    asked = []
    def handler(request):
        if request.url.host == 'rapido.cdn':
            return httpx.Response(206, content=b'v')
        asked.append(str(request.url))
        return httpx.Response(307, headers={'Location': 'https://rapido.cdn/f'})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resolve_speeds(client, [f'https://pengu.uk/d/{i}' for i in range(20)], SPEEDS)
    asyncio.run(go())
    assert len(asked) == PROBE_CANDIDATES, 'el usuario espera esta consulta: no puede crecer sin límite'


def test_every_directly_known_cdn_is_recognized_without_spending_probe_slots():
    known=[f'https://rapido.cdn/video-{i}.mkv' for i in range(12)]
    asked=[]
    def handler(request):
        asked.append(str(request.url));return httpx.Response(500)
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await resolve_speeds(client,known,SPEEDS)
    speeds=asyncio.run(go())
    assert len(speeds)==len(known) and not asked

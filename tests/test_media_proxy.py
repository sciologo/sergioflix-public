"""El proxy debe sobrevivir a un CDN que cierra antes de entregar su propio Content-Length."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.playback import Playback
from app.providers.stremio import ProviderError

BODY=b'v'*4000


class Chunks(httpx.AsyncByteStream):
    """MockTransport marca `content=` como ya consumido; el proxy necesita un flujo real."""
    def __init__(self,data):self.data=data
    async def __aiter__(self):
        for start in range(0,len(self.data),512):yield self.data[start:start+512]
SETTINGS=SimpleNamespace(lan_ip='192.168.50.10',media_port=8765)


def request(headers=None):
    return SimpleNamespace(method='GET',headers=headers or {},client=SimpleNamespace(host='192.168.8.21'))


async def collect(response):
    chunks=[]
    async for chunk in response.body_iterator:chunks.append(chunk)
    return b''.join(chunks)


def serve(cut_after,attempts):
    """Primera respuesta truncada; las reanudaciones entregan el resto desde el byte pedido."""
    def slice_from(header):
        return int(header.split('=')[1].split('-')[0]) if header else 0
    def handler(incoming):
        header=incoming.headers.get('range','')
        attempts.append(header)
        start=slice_from(header)
        rest=BODY[start:]
        payload=rest[:cut_after-start] if len(attempts)==1 else rest
        headers={'Content-Length':str(len(rest)),'Content-Type':'video/mp4'}
        if header:headers['Content-Range']=f'bytes {start}-{len(BODY)-1}/{len(BODY)}'
        return httpx.Response(206 if header else 200,stream=Chunks(payload),headers=headers)
    return httpx.MockTransport(handler)


async def no_dns(host):pass


def play(transport,monkeypatch,headers=None):
    monkeypatch.setattr('app.playback.public_dns',no_dns)
    playback=Playback(None,SETTINGS,None,transport=transport)
    stream={'url':'https://cdn.example/video.mp4'}
    source={'content_id':'tt1','video_id':'tt1','stream':stream,'provider':'penguplay'}
    return playback,stream,source


def test_a_cdn_that_closes_early_is_resumed_without_truncating_the_player(monkeypatch):
    attempts=[]
    playback,stream,source=play(serve(1500,attempts),monkeypatch)
    async def run():
        response=await playback.http_media(stream,request(),'a'*32,source)
        return await collect(response)
    data=asyncio.run(run())
    assert data==BODY, 'el reproductor debe recibir el archivo completo'
    assert attempts[0]=='' and attempts[1]=='bytes=1500-'
    assert len(attempts)==2


def test_a_resumed_range_request_continues_from_the_right_byte(monkeypatch):
    attempts=[]
    playback,stream,source=play(serve(2500,attempts),monkeypatch)
    async def run():
        response=await playback.http_media(stream,request({'range':'bytes=1000-'}),'a'*32,source)
        return await collect(response)
    data=asyncio.run(run())
    assert data==BODY[1000:], 'el rango pedido debe llegar completo'
    # El CDN entregó 1000..2499; se reanuda en el byte absoluto donde cortó, no en 0.
    assert attempts[0]=='bytes=1000-' and attempts[1]=='bytes=2500-'


def test_a_cdn_that_never_completes_fails_instead_of_sending_a_short_body(monkeypatch):
    attempts=[]
    def handler(incoming):
        attempts.append(incoming.headers.get('range',''))
        return httpx.Response(200,stream=Chunks(BODY[:10]),headers={'Content-Length':str(len(BODY))})
    playback,stream,source=play(httpx.MockTransport(handler),monkeypatch)
    async def run():
        response=await playback.http_media(stream,request(),'a'*32,source)
        return await collect(response)
    with pytest.raises(ProviderError) as caught:asyncio.run(run())
    # Cortar con error es correcto: una respuesta corta con Content-Length rompe el protocolo.
    assert caught.value.code=='video_truncated'
    assert len(attempts)==5

"""Regresión: archivos ZIP/RAR ofrecidos por HDHub como episodios de The Office."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.playback import Playback
from app.providers.stremio import ProviderError
from app.source_validation import ARCHIVE_SIGNATURES, archive_signature, declared_archive


@pytest.mark.parametrize('stream', [
    {'url': 'https://cdn.example/Season.ZIP?signature=private'},
    {'url': 'https://cdn.example/Season%2Erar'},
    {'behaviorHints': {'filename': 'The.Office.S01.7z'}},
    {'description': '[R2] The.Office.S01.1080p.zip\n13.9 GB'},
    {'title': 'The.Office.S01.rar'},
])
def test_explicit_archive_filenames_are_rejected(stream):
    assert declared_archive(stream)


@pytest.mark.parametrize('stream', [
    {'url': 'https://cdn.example/zip/movie.mp4?token=archive.zip'},
    {'name': 'Zip and Rar', 'url': 'https://cdn.example/watch'},
    {'behaviorHints': {'filename': 'Movie.rar.mp4'}},
    {'description': 'Movie.zip.2026.1080p.mkv'},
    {'url': 'https://cdn.example/master.m3u8'},
])
def test_names_and_unknown_formats_do_not_condemn_a_video(stream):
    assert not declared_archive(stream)


@pytest.mark.parametrize('signature', ARCHIVE_SIGNATURES)
def test_binary_signatures_identify_archives(signature):
    assert archive_signature(signature+b'payload')
    assert not archive_signature(b'video'+signature)


class Pieces(httpx.AsyncByteStream):
    def __init__(self, pieces):
        self.pieces = pieces
        self.closed = False

    async def __aiter__(self):
        for piece in self.pieces:
            yield piece

    async def aclose(self):
        self.closed = True


async def no_dns(host):
    pass


def proxy(monkeypatch, handler):
    monkeypatch.setattr('app.playback.public_dns', no_dns)
    playback = Playback(None, SimpleNamespace(), None, transport=httpx.MockTransport(handler))
    state = {}
    playback.control = SimpleNamespace(require=lambda key: state)
    stream = {'url': 'https://cdn.example/opaque-signed-link'}
    request = SimpleNamespace(method='GET', headers={}, client=None)
    return playback, stream, request, state


@pytest.mark.parametrize('signature', ARCHIVE_SIGNATURES)
@pytest.mark.parametrize('mime', ['application/octet-stream', 'application/vnd.apple.mpegurl'])
def test_hidden_archives_are_rejected_before_sending_video_headers(monkeypatch, signature, mime):
    # Firmas fragmentadas entre lecturas: no basta mirar el primer bloque aislado.
    pieces = Pieces([signature[:2], signature[2:], b'payload'])
    playback, stream, request, state = proxy(monkeypatch,
        lambda req: httpx.Response(200, stream=pieces, headers={'Content-Type': mime}))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(playback.http_media(stream, request, 'a'*32, {'stream': stream}))
    assert caught.value.code == 'archive_source' and caught.value.status == 422
    assert state['upstream_failure'] == {'code': 'archive_source', 'status': None}
    assert pieces.closed


@pytest.mark.parametrize('body', [
    b'\x00\x00\x00\x18ftypisom'+b'movie-data'*30,
    bytes.fromhex('1a45dfa3')+b'matroska-data'*30,
    b'unknown-video-container'+b'data'*30,
])
def test_octet_stream_video_keeps_every_byte_without_an_extra_request(monkeypatch, body):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(200, stream=Pieces([body[:2], body[2:5], body[5:]]),
                              headers={'Content-Type': 'application/octet-stream', 'Content-Length': str(len(body))})
    playback, stream, request, state = proxy(monkeypatch, handler)
    async def run():
        response = await playback.http_media(stream, request, 'a'*32, {'stream': stream})
        return b''.join([chunk async for chunk in response.body_iterator])
    assert asyncio.run(run()) == body
    assert len(calls) == 1 and 'upstream_failure' not in state


def test_an_archive_signature_inside_a_video_range_does_not_reject_the_video(monkeypatch):
    body = b'PK\x03\x04'+b'video-data'*10
    playback, stream, request, state = proxy(monkeypatch,
        lambda req: httpx.Response(206, stream=Pieces([body]), headers={
            'Content-Range': f'bytes 1000-{999+len(body)}/2000', 'Content-Length': str(len(body))}))
    request.headers = {'range': 'bytes=1000-'}
    async def run():
        response = await playback.http_media(stream, request, 'a'*32, {'stream': stream})
        return b''.join([chunk async for chunk in response.body_iterator])
    assert asyncio.run(run()) == body and 'upstream_failure' not in state


@pytest.mark.parametrize(('status', 'code'), [(403, 'upstream_rejected'), (404, 'upstream_missing'),
                                            (410, 'upstream_missing'), (400, 'video_unavailable')])
def test_remote_http_status_survives_the_local_proxy(monkeypatch, status, code):
    playback, stream, request, state = proxy(monkeypatch, lambda req: httpx.Response(status))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(playback.http_media(stream, request, 'a'*32, {'stream': stream}))
    assert caught.value.code == code and caught.value.upstream_status == status
    assert state['upstream_failure'] == {'code': code, 'status': status}


@pytest.mark.parametrize(('failure', 'code'), [(httpx.ConnectTimeout, 'network_timeout'),
                                              (httpx.ConnectError, 'network_error')])
def test_network_failures_keep_their_cause_without_marking_a_file_invalid(monkeypatch, failure, code):
    def handler(req):
        raise failure('private URL must never be recorded', request=req)
    playback, stream, request, state = proxy(monkeypatch, handler)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(playback.http_media(stream, request, 'a'*32, {'stream': stream}))
    assert caught.value.code == code and 'private URL' not in str(caught.value)
    assert state['upstream_failure']['code'] == code

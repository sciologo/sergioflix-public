"""Pruebas de límites y contratos que afectan seguridad o reproducción."""
import asyncio
import json

import httpx
import pytest

from app.providers.mediafusion import MAX_RESPONSE, MediaFusion, ProviderError, classify_stream, normalize_manifest

HOSTS = {"mediafusion.elfhosted.com"}
PRIVATE = "https://mediafusion.elfhosted.com/private-test-token/manifest.json"


async def no_dns(host):
    pass


def run_probe(handler, operation="manifest", **kwargs):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = MediaFusion(client, PRIVATE, HOSTS, dns_check=no_dns)
            return await getattr(adapter, operation)(**kwargs)
    return asyncio.run(run())


@pytest.mark.parametrize("url", ["http://mediafusion.elfhosted.com/manifest.json", "https://localhost/manifest.json",
    "https://mediafusion.elfhosted.com.evil.test/manifest.json", "https://secret@mediafusion.elfhosted.com/manifest.json",
    "https://mediafusion.elfhosted.com/manifest.json?secret=test", "https://mediafusion.elfhosted.com:444/manifest.json"])
def test_invalid_manifest_url(url):
    with pytest.raises(ProviderError) as caught:
        normalize_manifest(url, HOSTS)
    assert url not in str(caught.value)


def test_stremio_scheme_keeps_private_path():
    assert normalize_manifest(PRIVATE.replace("https:", "stremio:"), HOSTS) == PRIVATE


def test_empty_catalog_manifest_still_supports_streams():
    result = run_probe(lambda req: httpx.Response(200, json={"id":"mediafusion", "resources":[{"name":"stream"}], "catalogs":[]}))
    assert result["catalog_count"] == 0
    assert result["resources"] == ["stream"]


def test_episode_path_and_empty_streams_are_valid():
    def handler(request):
        assert request.url.path == "/private-test-token/stream/series/tt14688458:2:10.json"
        return httpx.Response(200,json={"streams":[]})
    result = run_probe(handler, "streams", media_type="series", video_id="tt14688458:2:10")
    assert result["returned_count"] == 0


def test_does_not_follow_redirect_or_leak_token():
    with pytest.raises(ProviderError) as caught:
        run_probe(lambda req: httpx.Response(302, headers={"Location":"https://127.0.0.1/private-test-token"}))
    assert caught.value.code == "provider_response"
    assert "private-test-token" not in caught.value.message


def test_response_size_is_bounded():
    with pytest.raises(ProviderError) as caught:
        run_probe(lambda req: httpx.Response(200, content=b" " * (MAX_RESPONSE+1)))
    assert caught.value.code == "response_too_large"


def test_classification_does_not_treat_torrent_or_labels_as_cast_proof():
    stream={"infoHash":"abc", "name":"Español 1080p Chromecast", "behaviorHints":{"bingeGroup":"same"}}
    assert classify_stream(stream) == {"kind":"torrent", "requires_headers":False,"subtitle_entries":0,"spanish_verified":False,"cast_verified":False}


@pytest.mark.parametrize("status,code", [(429,"rate_limited"),(401,"provider_auth"),(503,"provider_response")])
def test_safe_provider_errors(status,code):
    with pytest.raises(ProviderError) as caught:
        run_probe(lambda req:httpx.Response(status,text=PRIVATE))
    assert caught.value.code == code
    assert PRIVATE not in caught.value.message


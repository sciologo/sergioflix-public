"""Orden PenguPlay → Flix-Streams → MediaFusion, aislamiento de fallos y secretos fuera de las respuestas."""
import asyncio
import json

import httpx
import pytest

from app.providers.stremio import (ADAPTERS, PROVIDER_ORDER, ProviderError, gather_streams,
                                   normalize_manifest)
from app.stream_ranking import rank_catalog

PENGU = "https://pengu.uk/private-pengu-token/manifest.json"
FLIX = "https://flixnest.app/flix-streams/private-flix-token/manifest.json"
FUSION = "https://mediafusion.elfhosted.com/private-fusion-token/manifest.json"
HOSTS = {"penguplay": {"pengu.uk", "www.pengu.uk"}, "flixstreams": {"flixnest.app", "free.flixnest.app"},
         "mediafusion": {"mediafusion.elfhosted.com"}}
CONFIGURED = [{"provider": "penguplay", "manifest_url": PENGU},
              {"provider": "flixstreams", "manifest_url": FLIX},
              {"provider": "mediafusion", "manifest_url": FUSION}]


async def no_dns(host):
    pass


def build(handler):
    """Adaptador real sobre un transporte simulado: valida host, identidad y rutas."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client, lambda manifest, provider: ADAPTERS[provider](client, manifest, HOSTS[provider], dns_check=no_dns)


def collect(handler, media_type="movie", video_id="tt1254207"):
    async def run():
        client, adapter = build(handler)
        async with client:
            return await gather_streams(CONFIGURED, media_type, video_id, adapter)
    return asyncio.run(run())


def stream_for(host):
    return {"url": f"https://cdn.{host}/video.mp4", "name": "1080p H264 AAC Español 6 Mbps"}


def test_providers_are_queried_in_the_requested_order():
    asked = []

    def handler(request):
        asked.append(request.url.host)
        return httpx.Response(200, json={"streams": [stream_for(request.url.host)]})

    groups, failures = collect(handler)
    assert [group["provider"] for group in groups] == ["penguplay", "flixstreams", "mediafusion"]
    assert asked == ["pengu.uk", "flixnest.app", "mediafusion.elfhosted.com"]
    assert failures == []


def test_a_failing_provider_does_not_stop_the_others():
    def handler(request):
        if request.url.host == "pengu.uk":
            return httpx.Response(503, text=PENGU)
        if request.url.host == "flixnest.app":
            raise httpx.ConnectError("simulated")
        return httpx.Response(200, json={"streams": [stream_for("mediafusion.elfhosted.com")]})

    groups, failures = collect(handler)
    assert [group["provider"] for group in groups] == ["mediafusion"]
    assert [failure["provider"] for failure in failures] == ["penguplay", "flixstreams"]
    assert [failure["code"] for failure in failures] == ["provider_response", "unreachable"]
    # Ni la URL privada ni su token pueden viajar en el error mostrado al teléfono.
    assert "private-pengu-token" not in json.dumps(failures)
    assert "private-flix-token" not in json.dumps(failures)


def test_equivalent_sources_follow_the_provider_priority():
    groups = [{"provider": provider, "streams": [{"url": "https://cdn.example/video.mp4", "name": "1080p H264 AAC Español 6 Mbps"}]}
              for provider in ("mediafusion", "flixstreams", "penguplay")]
    ranked = rank_catalog(groups)
    assert [analysis["provider"] for _, analysis in ranked] == ["penguplay", "flixstreams", "mediafusion"]


def test_http_hls_wins_even_against_the_preferred_provider_torrent():
    ranked = rank_catalog([
        {"provider": "penguplay", "streams": [{"infoHash": "a" * 40, "name": "2160p REMUX HEVC TrueHD Blu-ray 48 GB 👤 90"}]},
        {"provider": "mediafusion", "streams": [{"url": "https://cdn.example/master.m3u8", "name": "1080p H264 AAC Español HLS 6 Mbps"}]},
    ])
    first, analysis = ranked[0]
    assert analysis["provider"] == "mediafusion" and first["url"].endswith("master.m3u8")
    assert ranked[1][1]["provider"] == "penguplay"


def test_priority_never_outweighs_the_transport_gap():
    torrent = rank_catalog([{"provider": "penguplay", "streams": [{"infoHash": "b" * 40, "name": "1080p H264 AAC Español 👤 200"}]}])
    direct = rank_catalog([{"provider": "mediafusion", "streams": [{"url": "https://cdn.example/v.mp4", "name": "1080p H264 AAC Español"}]}])
    assert direct[0][1]["score"] > torrent[0][1]["score"]


@pytest.mark.parametrize("provider,url", [
    ("penguplay", FLIX), ("flixstreams", PENGU), ("mediafusion", PENGU),
    ("penguplay", "http://pengu.uk/manifest.json"), ("flixstreams", "https://flixnest.app.evil.test/manifest.json")])
def test_each_provider_only_accepts_its_own_hosts(provider, url):
    with pytest.raises(ProviderError) as caught:
        normalize_manifest(url, HOSTS[provider])
    assert caught.value.code == "invalid_manifest" and "token" not in caught.value.message


@pytest.mark.parametrize("provider,identity", [
    ("penguplay", "com.penguplay"), ("flixstreams", "org.flickystream.addon"),
    ("flixstreams", "org.flixstreams.free"), ("mediafusion", "mediafusion")])
def test_real_manifest_identities_are_accepted(provider, identity):
    async def run():
        manifest = {"penguplay": PENGU, "flixstreams": FLIX, "mediafusion": FUSION}[provider]
        client, adapter = build(lambda request: httpx.Response(200, json={
            "id": identity, "version": "1.0.0", "resources": [{"name": "stream"}], "catalogs": []}))
        async with client:
            return await adapter(manifest, provider).manifest()
    summary = asyncio.run(run())
    assert summary["provider_key"] == provider and summary["resources"] == ["stream"]


def test_a_manifest_from_another_addon_is_rejected():
    async def run():
        client, adapter = build(lambda request: httpx.Response(200, json={"id": "mediafusion", "resources": ["stream"], "catalogs": []}))
        async with client:
            return await adapter(PENGU, "penguplay").manifest()
    with pytest.raises(ProviderError) as caught:
        asyncio.run(run())
    assert caught.value.code == "wrong_provider"


def test_provider_order_matches_the_requested_preference():
    assert PROVIDER_ORDER == ("penguplay", "hdhub", "flixstreams", "mediafusion")


def test_audit_log_never_receives_a_manifest_url():
    """La auditoría admite una lista fija de campos: una URL privada no puede colarse."""
    import logging
    from app.observability import audit, make_logger

    written = []

    class Capture(logging.Handler):
        def emit(self, record):
            written.append(record.getMessage())

    logger = make_logger()
    handler = Capture()
    logger.addHandler(handler)
    try:
        audit("provider_unavailable", "admin-local", error_code="unreachable",
              manifest_url=PENGU, message=f"fallo al leer {FLIX}")
    finally:
        logger.removeHandler(handler)
    assert written and "private-pengu-token" not in written[0] and "private-flix-token" not in written[0]
    assert "unreachable" in written[0]

"""Clasificación privada y prudencia al recomendar desde un promedio de proveedor."""
from app.playback_events import probe_cause, failure_code
from app.stream_ranking import describe, rank_streams, url_key


def test_probe_keeps_only_a_normalized_cause():
    for stderr,code in [
        (b'https://private.example/token: HTTP error 403 Forbidden','upstream_rejected'),
        (b'https://private.example/token: HTTP error 404','upstream_missing'),
        (b'Connection timed out: https://private.example/token','network_timeout'),
        (b'Invalid data found when processing input','probe_format_error')]:
        assert probe_cause(stderr)==code
    assert failure_code(RuntimeError('https://private.example/token'))=='internal_error'


def test_provider_average_cannot_claim_confirmed_capacity_and_temporary_failure_is_a_fallback():
    first={'url':'https://cdn.example/first.mp4','name':'1080p H264 AAC 4 Mbps'}
    second={'url':'https://cdn.example/second.mp4','name':'720p H264 AAC 3 Mbps'}
    average=describe(first,provider='penguplay',provider_speeds={'penguplay':4_000_000})
    precise=describe(first,provider='penguplay',speeds={url_key(first['url']):4_000_000})
    assert average['ranking_tier']==2 and precise['ranking_tier']==0
    from app.stream_ranking import source_fingerprint
    ranked=rank_streams([first,second],reputations={source_fingerprint('mediafusion',first):'temporary'})
    assert ranked[0][0] is second and ranked[1][0] is first

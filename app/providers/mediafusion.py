"""Compatibilidad: MediaFusion es ahora un adaptador Stremio más, sin motor propio.

Los módulos existentes importan `ProviderError`, `classify_stream` y `public_dns` desde aquí;
mantener las reexportaciones evita tocar reproducción, control y pruebas ya verificadas.
"""
from app.providers.stremio import (  # noqa: F401
    ADAPTERS,
    MAX_RESPONSE,
    PROVIDER_ORDER,
    PROVIDERS,
    VIDEO_ID,
    FlixStreams,
    MediaFusion,
    PenguPlay,
    ProviderError,
    ProviderSpec,
    StremioAddon,
    classify_stream,
    normalize_manifest,
    public_dns,
)

__all__ = ["ADAPTERS", "MAX_RESPONSE", "PROVIDERS", "PROVIDER_ORDER", "VIDEO_ID", "FlixStreams", "MediaFusion",
           "PenguPlay", "ProviderError", "ProviderSpec", "StremioAddon", "classify_stream", "normalize_manifest",
           "public_dns"]

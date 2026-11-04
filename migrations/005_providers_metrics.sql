-- Varios proveedores Stremio y métricas de reproducción.
-- Las filas antiguas se conservan: media_source.provider adopta 'mediafusion' por defecto.
ALTER TABLE media_source ADD COLUMN provider text NOT NULL DEFAULT 'mediafusion';
ALTER TABLE media_source ADD COLUMN rank_score integer NOT NULL DEFAULT 0;
CREATE INDEX media_source_provider_idx ON media_source(provider);

CREATE TABLE playback_metric (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider text NOT NULL,
    event text NOT NULL CHECK (event IN ('prepared','first_frame','buffering','error','cast_started')),
    start_ms integer CHECK (start_ms IS NULL OR start_ms >= 0),
    bitrate_kbps integer CHECK (bitrate_kbps IS NULL OR bitrate_kbps >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX playback_metric_provider_idx ON playback_metric(provider, created_at DESC);

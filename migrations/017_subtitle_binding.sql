-- Copia aprobada durable: no depende de URLs firmadas ni de la caché de disco.
CREATE TABLE subtitle_binding (
    token text PRIMARY KEY CHECK (token ~ '^[a-f0-9]{40}$'),
    video_id text NOT NULL,
    asset_key text NOT NULL,
    audio_key text NOT NULL,
    subtitle_sha256 text NOT NULL,
    subtitle_key text NOT NULL,
    revision integer NOT NULL,
    verification jsonb NOT NULL,
    body text NOT NULL CHECK (octet_length(body) <= 2000000),
    body_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subtitle_binding_asset_idx ON subtitle_binding(video_id,asset_key,audio_key,revision);

-- Métricas agregadas de presentación: sin texto, enlaces ni datos del diálogo.
ALTER TABLE playback_event ADD COLUMN subtitle_key text CHECK (subtitle_key ~ '^[a-f0-9]{32}$'),
    ADD COLUMN subtitle_token text CHECK (subtitle_token ~ '^[a-f0-9]{40}$'),
    ADD COLUMN render_context text CHECK (render_context IN ('start','resume','seek','audio','continuous')),
    ADD COLUMN render_samples integer CHECK (render_samples BETWEEN 1 AND 10000),
    ADD COLUMN render_lag_ms integer CHECK (render_lag_ms BETWEEN 0 AND 60000),
    ADD COLUMN render_total_lag_ms integer CHECK (render_total_lag_ms BETWEEN 0 AND 600000000);
ALTER TABLE playback_event DROP CONSTRAINT playback_event_event_check;
ALTER TABLE playback_event ADD CHECK (event IN ('attempt_started','prepared','cast_ready',
    'first_frame','buffering','buffering_end','stable','error','source_switch',
    'recovery_started','recovered','cancelled','ended','subtitle_render'));

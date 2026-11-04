-- Decisiones sobre el contenido descargado, separadas de enlaces y preferencias.
CREATE TABLE subtitle_verdict (
    token text PRIMARY KEY,
    video_id text NOT NULL,
    source_fingerprint text NOT NULL,
    subtitle_key text NOT NULL,
    status text NOT NULL CHECK (status IN ('verified','rejected')),
    reason text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subtitle_verdict_video_idx ON subtitle_verdict(video_id, source_fingerprint);

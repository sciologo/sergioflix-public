-- Memoria acotada de una edición concreta. Solo se guarda una huella irreversible:
-- nunca la URL firmada, el nombre del archivo ni el título que la originó.
ALTER TABLE media_source ADD COLUMN source_fingerprint text;
CREATE INDEX media_source_fingerprint_idx ON media_source(video_id, source_fingerprint);

CREATE TABLE source_reputation (
    video_id text NOT NULL,
    source_fingerprint text NOT NULL,
    provider text NOT NULL,
    failures smallint NOT NULL DEFAULT 0 CHECK (failures >= 0),
    last_failure_at timestamptz NOT NULL DEFAULT now(),
    hard_until timestamptz,
    PRIMARY KEY(video_id, source_fingerprint)
);
CREATE INDEX source_reputation_recent_idx ON source_reputation(last_failure_at DESC);

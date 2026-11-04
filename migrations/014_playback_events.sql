-- Historial por intento, separado de los enlaces temporales y de la reputación.
ALTER TABLE media_source ADD COLUMN rank_position integer;
ALTER TABLE media_source ADD COLUMN ranking_snapshot jsonb NOT NULL DEFAULT '{}';
ALTER TABLE source_reputation ADD COLUMN temporary_until timestamptz;
ALTER TABLE source_reputation ADD COLUMN temporary_failures smallint NOT NULL DEFAULT 0;
ALTER TABLE source_reputation ADD COLUMN last_success_at timestamptz;

CREATE TABLE playback_attempt (
    attempt_id text PRIMARY KEY,
    playback_id text NOT NULL,
    actor text NOT NULL,
    client text NOT NULL,
    content_id text NOT NULL,
    video_id text NOT NULL,
    provider text NOT NULL,
    source_fingerprint text,
    rank_position integer,
    ranking_snapshot jsonb NOT NULL DEFAULT '{}',
    failure_learned boolean NOT NULL DEFAULT false,
    learned_error_code text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX playback_attempt_video_idx ON playback_attempt(video_id, created_at DESC);
CREATE INDEX playback_attempt_chain_idx ON playback_attempt(actor, playback_id, created_at);

CREATE TABLE playback_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id text NOT NULL REFERENCES playback_attempt(attempt_id),
    event_id text NOT NULL,
    event text NOT NULL CHECK (event IN ('attempt_started','prepared','cast_ready',
        'first_frame','buffering','buffering_end','stable','error','source_switch',
        'recovery_started','recovered','cancelled','ended')),
    stage text NOT NULL,
    error_code text,
    upstream_status integer CHECK (upstream_status BETWEEN 100 AND 599),
    position_ms bigint CHECK (position_ms >= 0),
    elapsed_ms integer CHECK (elapsed_ms >= 0),
    duration_ms integer CHECK (duration_ms >= 0),
    buffer_ms integer CHECK (buffer_ms >= 0),
    switch_reason text CHECK (switch_reason IN ('automatic','manual')),
    next_fingerprint text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(attempt_id, event_id)
);
CREATE INDEX playback_event_attempt_idx ON playback_event(attempt_id, created_at);
CREATE INDEX playback_event_created_idx ON playback_event(created_at DESC);

CREATE TABLE content (
    id text PRIMARY KEY,
    media_type text NOT NULL CHECK (media_type IN ('movie','series')),
    name text NOT NULL,
    data jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX content_name_idx ON content (lower(name));
CREATE INDEX content_data_idx ON content USING gin(data jsonb_path_ops);
CREATE TABLE catalog_page (
    cache_key text PRIMARY KEY,
    data jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE library (
    content_id text PRIMARY KEY REFERENCES content(id),
    favorite boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE playback_progress (
    video_id text PRIMARY KEY,
    content_id text NOT NULL REFERENCES content(id),
    position_ms bigint NOT NULL CHECK(position_ms >= 0),
    duration_ms bigint NOT NULL CHECK(duration_ms >= 0),
    completed boolean NOT NULL DEFAULT false,
    session_id text NOT NULL,
    sequence bigint NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE playback_session (
    id text PRIMARY KEY,
    video_id text NOT NULL,
    content_id text NOT NULL REFERENCES content(id),
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '12 hours'
);


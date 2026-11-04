CREATE TABLE paired_device (
    id text PRIMARY KEY,
    key_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE pairing_code (
    code_hash text PRIMARY KEY,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '15 minutes',
    used boolean NOT NULL DEFAULT false
);
CREATE TABLE media_source (
    id text PRIMARY KEY,
    content_id text NOT NULL,
    video_id text NOT NULL,
    encrypted_data text NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT now() + interval '12 hours'
);


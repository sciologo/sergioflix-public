CREATE TABLE provider_config (
    provider text PRIMARY KEY,
    encrypted_manifest text NOT NULL,
    summary jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE audit_event (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    action text NOT NULL,
    result text NOT NULL DEFAULT 'ok'
);
CREATE INDEX audit_event_created_idx ON audit_event(created_at);


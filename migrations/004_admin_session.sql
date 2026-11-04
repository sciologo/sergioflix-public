CREATE TABLE admin_ticket (
    token_hash text PRIMARY KEY,
    purpose text NOT NULL CHECK(purpose IN ('login','session')),
    expires_at timestamptz NOT NULL
);

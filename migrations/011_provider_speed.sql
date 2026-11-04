-- Caudal aprendido por proveedor. El servidor final manda cuando se conoce; esta
-- media conservadora sirve de respaldo para enlaces firmados o CDN todavía sin ruta.
CREATE TABLE provider_speed (
    provider text PRIMARY KEY,
    samples integer NOT NULL DEFAULT 0 CHECK (samples >= 0),
    bytes_per_second bigint NOT NULL DEFAULT 0 CHECK (bytes_per_second >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Caudal medido de cada servidor de video. Un origen rápido arranca en segundos
-- y uno lento tarda medio minuto, aunque la ficha se vea idéntica.
CREATE TABLE source_speed (
    host text PRIMARY KEY,
    samples integer NOT NULL DEFAULT 0 CHECK (samples >= 0),
    bytes_per_second bigint NOT NULL DEFAULT 0 CHECK (bytes_per_second >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

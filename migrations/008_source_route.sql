-- A qué servidor lleva realmente cada enlace. Los proveedores entregan direcciones
-- propias que redirigen a distintos CDN, así que el enlace por sí solo no dice nada
-- sobre la velocidad: hay que recordar dónde terminó la última vez.
CREATE TABLE source_route (
    url_hash text PRIMARY KEY,
    host text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Un comprimido confirmado no vuelve como respaldo al renovarse su enlace.
ALTER TABLE source_reputation ADD COLUMN archive_rejected_until timestamptz;

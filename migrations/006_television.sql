-- Los canales en directo se guardan como fichas propias para reutilizar sesiones y proxy.
ALTER TABLE content DROP CONSTRAINT content_media_type_check;
ALTER TABLE content ADD CONSTRAINT content_media_type_check CHECK (media_type IN ('movie','series','tv'));

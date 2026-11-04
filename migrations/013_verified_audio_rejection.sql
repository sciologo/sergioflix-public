-- Un archivo cuyo audio real contradice el catálogo no debe reaparecer al renovar URL.
ALTER TABLE source_reputation ADD COLUMN audio_rejected_until timestamptz;

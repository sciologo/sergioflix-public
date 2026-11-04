-- La fuente y los subtítulos pertenecen al episodio/edición, no a enlaces temporales.
ALTER TABLE playback_progress ADD COLUMN preferences jsonb NOT NULL DEFAULT '{}'::jsonb;

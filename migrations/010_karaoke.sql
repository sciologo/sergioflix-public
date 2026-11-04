-- Curaduría doméstica de las versiones de karaoke. El catálogo editorial vive en
-- código; aquí solo queda la decisión humana que mejora la próxima reproducción.
CREATE TABLE karaoke_preference (
    song_id text NOT NULL,
    video_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('favorite', 'bad')),
    actor text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (song_id, video_id)
);

-- Una canción puede tener una única versión favorita, pero varias descartadas.
CREATE UNIQUE INDEX karaoke_one_favorite_idx
    ON karaoke_preference(song_id) WHERE status = 'favorite';
CREATE INDEX karaoke_preference_song_idx ON karaoke_preference(song_id, updated_at DESC);

UPDATE library_albums
SET played_at = :played_at
WHERE id = (SELECT album_id FROM library_tracks WHERE id = :track_id)
    AND (played_at IS NULL OR played_at < :played_at);

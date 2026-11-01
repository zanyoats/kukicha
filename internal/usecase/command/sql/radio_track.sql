SELECT tracks.id, roots.config_id, tracks.title, tracks.artists,
    tracks.duration_seconds, tracks.disc_number, tracks.track_number,
    albums.id, albums.album
FROM %s AS members
JOIN library_tracks AS tracks ON tracks.id = members.track_id
JOIN library_roots AS roots ON roots.id = tracks.root_id
JOIN library_albums AS albums ON albums.id = tracks.album_id
WHERE members.%s = :entity_id AND members.position = :position;

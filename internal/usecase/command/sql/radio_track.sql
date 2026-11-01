SELECT tracks.id, track_ids.path, tracks.title, tracks.artists,
    tracks.duration_seconds, tracks.disc_number, tracks.track_number,
    albums.id, albums.album
FROM %s AS members
JOIN available_library_tracks AS tracks ON tracks.id = members.track_id
JOIN library_track_ids AS track_ids ON track_ids.id = tracks.id
JOIN available_library_albums AS albums ON albums.id = tracks.album_id
WHERE members.%s = :entity_id AND members.position = :position;

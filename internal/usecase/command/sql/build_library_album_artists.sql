INSERT OR IGNORE INTO library_album_artists (album_id, artist_id)
SELECT DISTINCT
    albums.id,
    artist_ids.id
FROM scan_tracks AS s
JOIN scan_track_normalized_metadata AS normalized
    ON normalized.root_pos = s.root_pos AND normalized.path = s.path
JOIN library_album_ids AS album_ids
    ON album_ids.album_key = s.album_key
JOIN library_albums AS albums
    ON albums.id = album_ids.id
JOIN json_each(normalized.normalized_metadata, '$.mapped_album_artist_displays') AS artist_displays
JOIN library_artist_ids AS artist_ids
    ON artist_ids.artist = artist_displays.value;

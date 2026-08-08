INSERT OR IGNORE INTO library_album_artists (album_id, artist_key, artist)
SELECT DISTINCT
    albums.id,
    artist_keys.value,
    artist_displays.value
FROM scan_tracks AS s
JOIN library_albums AS albums
    ON albums.album_key = json_extract(s.normalized_metadata, '$.album_key')
JOIN json_each(s.normalized_metadata, '$.mapped_album_artist_keys') AS artist_keys
JOIN json_each(s.normalized_metadata, '$.mapped_album_artist_displays') AS artist_displays
    ON artist_displays.key = artist_keys.key
WHERE s.file_type = :audio_file_type;

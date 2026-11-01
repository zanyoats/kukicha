WITH artist_albums AS (
    SELECT albums.id, albums.album, album_ids.album_key,
        (
            SELECT json_group_array(artist)
            FROM (
                SELECT DISTINCT originals.value AS artist
                FROM library_tracks AS tracks
                JOIN json_each(CASE
                    WHEN json_array_length(tracks.track_metadata, '$.album_artists') > 0
                        THEN json_extract(tracks.track_metadata, '$.album_artists')
                    WHEN json_array_length(tracks.track_metadata, '$.artists') > 0
                        THEN json_extract(tracks.track_metadata, '$.artists')
                    ELSE json_array(:unknown_artist)
                END) AS originals
                WHERE tracks.album_id = albums.id
                ORDER BY artist COLLATE NOCASE, artist
            )
        ) AS original_artists
    FROM available_library_albums AS albums
    JOIN library_album_ids AS album_ids ON album_ids.id = albums.id
    WHERE EXISTS (
        SELECT 1 FROM library_album_artists AS matching_artists
        WHERE matching_artists.album_id = albums.id AND matching_artists.artist_id = :artist_id
    )
)
SELECT
    albums.album_key,
    COALESCE(mappings.mapped_artists, albums.original_artists),
    albums.id,
    albums.album,
    mappings.album_key IS NOT NULL,
    albums.original_artists
FROM artist_albums AS albums
LEFT JOIN album_artist_mappings AS mappings ON mappings.album_key = albums.album_key
ORDER BY albums.album, albums.id;

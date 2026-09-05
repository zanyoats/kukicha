SELECT
    albums.id,
    albums.album,
    COALESCE(small_artwork.width_px, :artwork_width_small),
    COALESCE(small_artwork.height_px, :artwork_width_small),
    COALESCE(large_artwork.width_px, :artwork_width_large),
    COALESCE(large_artwork.height_px, :artwork_width_large),
    json_group_array(
        json_object('id', artists.id, 'name', artists.artist)
            ORDER BY
                artists.artist COLLATE NOCASE,
                artists.id
    ) FILTER (
        WHERE artists.id IS NOT NULL
    )
FROM library_albums AS albums
-- Keep artwork selection aligned with album_artwork.sql.
LEFT JOIN thumbnail_cache AS small_artwork
    ON small_artwork.id = (
        SELECT thumbnail.id
        FROM library_tracks AS tracks
        JOIN library_track_artwork AS artwork
            ON artwork.track_id = tracks.id
        JOIN thumbnail_cache AS thumbnail
            ON thumbnail.id = artwork.thumbnail_cache_id
        WHERE tracks.album_id = albums.id
            AND thumbnail.width_px = :artwork_width_small
            AND thumbnail.artwork_content_size > 0
        ORDER BY
            COALESCE(tracks.disc_number, 1),
            CASE
                WHEN tracks.track_number IS NULL THEN 1
                ELSE 0
            END,
            tracks.track_number,
            tracks.path COLLATE BINARY,
            tracks.id,
            artwork.id,
            thumbnail.id
        LIMIT 1
    )
LEFT JOIN thumbnail_cache AS large_artwork
    ON large_artwork.id = (
        SELECT thumbnail.id
        FROM library_tracks AS tracks
        JOIN library_track_artwork AS artwork
            ON artwork.track_id = tracks.id
        JOIN thumbnail_cache AS thumbnail
            ON thumbnail.id = artwork.thumbnail_cache_id
        WHERE tracks.album_id = albums.id
            AND thumbnail.width_px = :artwork_width_large
            AND thumbnail.artwork_content_size > 0
        ORDER BY
            COALESCE(tracks.disc_number, 1),
            CASE
                WHEN tracks.track_number IS NULL THEN 1
                ELSE 0
            END,
            tracks.track_number,
            tracks.path COLLATE BINARY,
            tracks.id,
            artwork.id,
            thumbnail.id
        LIMIT 1
    )
LEFT JOIN library_album_artists AS album_artists
    ON album_artists.album_id = albums.id
LEFT JOIN library_artist_ids AS artists
    ON artists.id = album_artists.artist_id
GROUP BY
    albums.id,
    albums.album
ORDER BY
    albums.album COLLATE NOCASE,
    albums.id;

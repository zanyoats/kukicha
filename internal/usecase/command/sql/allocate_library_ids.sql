INSERT INTO library_root_ids (config_id)
SELECT config_id FROM scan_roots WHERE true
ORDER BY config_id COLLATE BINARY
ON CONFLICT (config_id) DO NOTHING;

INSERT INTO library_album_ids (album_key)
SELECT candidates.album_key
FROM (
    SELECT album_key
    FROM scan_tracks
    GROUP BY album_key
) AS candidates
WHERE NOT EXISTS (
    SELECT 1
    FROM library_album_ids AS existing
    WHERE existing.album_key = candidates.album_key
)
ORDER BY candidates.album_key COLLATE BINARY;

INSERT INTO library_artist_ids (artist)
SELECT candidates.artist
FROM (
    SELECT MIN(artist_displays.value COLLATE BINARY) AS artist
    FROM scan_track_normalized_metadata AS tracks
    JOIN json_each(
        tracks.normalized_metadata,
        '$.mapped_album_artist_displays'
    ) AS artist_displays
    GROUP BY artist_displays.value COLLATE NOCASE
) AS candidates
WHERE NOT EXISTS (
    SELECT 1
    FROM library_artist_ids AS existing
    WHERE existing.artist = candidates.artist
)
ORDER BY
    candidates.artist COLLATE NOCASE,
    candidates.artist COLLATE BINARY;

INSERT INTO library_track_ids (path)
SELECT path FROM scan_tracks WHERE true
GROUP BY path
ORDER BY path COLLATE BINARY
ON CONFLICT (path) DO NOTHING;

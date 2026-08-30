INSERT INTO library_root_ids (root_path)
SELECT candidates.root_path
FROM (
    SELECT root_path
    FROM scan_tracks
    GROUP BY root_path
) AS candidates
WHERE NOT EXISTS (
    SELECT 1
    FROM library_root_ids AS existing
    WHERE existing.root_path = candidates.root_path
)
ORDER BY candidates.root_path COLLATE BINARY;

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
    FROM scan_tracks AS tracks
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

INSERT INTO library_track_ids (root_path, path)
SELECT candidates.root_path, candidates.path
FROM (
    SELECT root_path, path
    FROM scan_tracks
    GROUP BY root_path, path
) AS candidates
WHERE NOT EXISTS (
    SELECT 1
    FROM library_track_ids AS existing
    WHERE existing.root_path = candidates.root_path
        AND existing.path = candidates.path
)
ORDER BY candidates.root_path COLLATE BINARY, candidates.path COLLATE BINARY;

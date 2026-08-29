INSERT INTO library_track_embedded_artwork (
    track_id,
    picture_type,
    artwork_content_type,
    artwork_content_size,
    artwork_bytes
)
SELECT
    tracks.id,
    artwork.picture_type,
    artwork.artwork_content_type,
    artwork.artwork_content_size,
    artwork.artwork_bytes
FROM scan_artwork AS artwork
JOIN scan_tracks AS scanned_tracks
    ON scanned_tracks.root_pos = artwork.root_pos
    AND scanned_tracks.path = artwork.path
JOIN library_roots AS roots
    ON roots.pos = scanned_tracks.root_pos
JOIN library_tracks AS tracks
    ON tracks.root_id = roots.id
    AND tracks.path = scanned_tracks.path
ORDER BY tracks.id, artwork.artwork_pos;

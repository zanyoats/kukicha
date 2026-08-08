INSERT INTO library_track_artwork (
    track_id,
    width_px,
    artwork_content_type,
    artwork_content_size,
    artwork_bytes
)
VALUES (
    :track_id,
    :width_px,
    :artwork_content_type,
    :artwork_content_size,
    :artwork_bytes
);

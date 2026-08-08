INSERT INTO scan_artwork (
    root_pos,
    path,
    artwork_pos,
    picture_type,
    artwork_content_type,
    artwork_content_size,
    artwork_bytes
)
VALUES (
    :root_pos,
    :path,
    :artwork_pos,
    :picture_type,
    :artwork_content_type,
    :artwork_content_size,
    :artwork_bytes
);

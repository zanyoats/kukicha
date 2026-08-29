INSERT INTO scan_artwork_cover (
    root_pos,
    path,
    artwork_pos,
    picture_type,
    thumbnail_cache_name
)
VALUES (
    :root_pos,
    :path,
    :artwork_pos,
    :picture_type,
    :thumbnail_cache_name
);

INSERT INTO thumbnail_cache (
    name,
    width_px,
    height_px,
    artwork_content_type,
    artwork_content_size,
    artwork_bytes
)
VALUES (
    :name,
    :width_px,
    :height_px,
    :artwork_content_type,
    :artwork_content_size,
    :artwork_bytes
)
ON CONFLICT (name, width_px) DO NOTHING;

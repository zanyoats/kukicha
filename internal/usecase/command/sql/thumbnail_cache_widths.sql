SELECT width_px
FROM thumbnail_cache
WHERE name = :name
    AND width_px IN (:artwork_width_small, :artwork_width_large);

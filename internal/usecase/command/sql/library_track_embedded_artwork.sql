SELECT artwork_bytes
FROM library_track_embedded_artwork
WHERE track_id = :track_id
    AND picture_type IN ('front_cover', 'other')
ORDER BY
    CASE picture_type
        WHEN 'front_cover' THEN 0
        WHEN 'other' THEN 1
    END,
    id;

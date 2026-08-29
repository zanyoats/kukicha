SELECT
    lookup_kind,
    lookup_id,
    result_kind,
    lookup_url,
    artwork_url,
    artwork_content_type,
    artwork_content_size,
    artwork_bytes
FROM itunes_lookup_image_cache
WHERE cache_key = :cache_key;

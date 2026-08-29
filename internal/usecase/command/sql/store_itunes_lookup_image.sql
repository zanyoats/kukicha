INSERT INTO itunes_lookup_image_cache (
    cache_key,
    lookup_kind,
    lookup_id,
    result_kind,
    fetched_at,
    lookup_url,
    artwork_url,
    artwork_content_type,
    artwork_content_size,
    artwork_bytes,
    thumbnail_cache_name
) VALUES (
    :cache_key,
    :lookup_kind,
    :lookup_id,
    :result_kind,
    :fetched_at,
    :lookup_url,
    :artwork_url,
    :artwork_content_type,
    :artwork_content_size,
    :artwork_bytes,
    :thumbnail_cache_name
)
ON CONFLICT (cache_key) DO UPDATE SET
    lookup_kind = excluded.lookup_kind,
    lookup_id = excluded.lookup_id,
    result_kind = excluded.result_kind,
    fetched_at = excluded.fetched_at,
    lookup_url = excluded.lookup_url,
    artwork_url = excluded.artwork_url,
    artwork_content_type = excluded.artwork_content_type,
    artwork_content_size = excluded.artwork_content_size,
    artwork_bytes = excluded.artwork_bytes,
    thumbnail_cache_name = excluded.thumbnail_cache_name;

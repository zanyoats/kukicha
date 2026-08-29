UPDATE itunes_lookup_image_cache
SET thumbnail_cache_name = :thumbnail_cache_name
WHERE cache_key = :cache_key
    AND result_kind = 'hit';

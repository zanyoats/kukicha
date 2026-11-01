DELETE FROM library_album_overrides;

INSERT INTO library_album_overrides (
    album_key, provider, source_url, genres_json, thumbnail_cache_name
)
SELECT
    overrides.album_key, overrides.provider, overrides.source_url,
    overrides.genres_json, overrides.thumbnail_cache_name
FROM scan_album_overrides AS overrides
JOIN library_album_ids AS ids ON ids.album_key = overrides.album_key
JOIN library_albums AS albums ON albums.id = ids.id;

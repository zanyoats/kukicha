DELETE FROM album_artist_split_mappings;
DELETE FROM library_root_ids;
DELETE FROM library_album_ids;
DELETE FROM library_artist_ids;
DELETE FROM library_track_ids;
DELETE FROM itunes_lookup_image_cache;
DELETE FROM thumbnail_cache;
DELETE FROM library_job_logs;
DELETE FROM jobs WHERE id != :job_id;

-- The old library, radio membership, and successful job no longer exist.
-- Sync restores these markers as membership and artwork are rebuilt.
DELETE FROM app_metadata
WHERE key IN (:generated_at_key, :generated_job_id_key, :radio_membership_version_key);

-- Preserve library and job ID counters so old links cannot resolve to new rows.
-- Preserve log ID counters so pagination cursors work across recovered resets.
-- Taxonomy sequences belong to the preserved catalog.
DELETE FROM sqlite_sequence WHERE name = 'thumbnail_cache';

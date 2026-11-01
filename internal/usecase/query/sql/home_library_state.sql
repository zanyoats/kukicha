SELECT
    EXISTS (SELECT 1 FROM app_metadata WHERE key = :generated_at_key),
    EXISTS (SELECT 1 FROM available_library_tracks),
    COALESCE((SELECT state FROM jobs WHERE type = 'build' ORDER BY id DESC LIMIT 1), '');

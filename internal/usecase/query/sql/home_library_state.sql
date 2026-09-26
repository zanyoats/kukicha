SELECT
    EXISTS (SELECT 1 FROM app_metadata WHERE key = :generated_at_key),
    EXISTS (SELECT 1 FROM library_roots);

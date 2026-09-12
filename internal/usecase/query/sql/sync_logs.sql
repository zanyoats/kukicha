SELECT rowid, level, timestamp, message, root_path, path
FROM library_sync_logs
WHERE rowid < :before
ORDER BY rowid DESC
LIMIT :limit;

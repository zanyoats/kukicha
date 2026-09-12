SELECT
    COUNT(*) FILTER (WHERE level = 'error'),
    COUNT(*) FILTER (WHERE level = 'warn'),
    COUNT(*) FILTER (WHERE level = 'info')
FROM library_sync_logs;

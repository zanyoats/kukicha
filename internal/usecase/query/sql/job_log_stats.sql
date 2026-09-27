SELECT
    COUNT(*) FILTER (WHERE level = 'error'),
    COUNT(*) FILTER (WHERE level = 'warn'),
    COUNT(*) FILTER (WHERE level = 'info'),
    COUNT(*) FILTER (WHERE level = 'debug')
FROM library_job_logs
WHERE job_id = :job_id;

SELECT id, level, timestamp, message, data
FROM library_job_logs
WHERE job_id = :job_id AND id < :before
ORDER BY id DESC
LIMIT :limit;

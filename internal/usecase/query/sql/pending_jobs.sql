SELECT id, type, state, cancel_requested_at IS NOT NULL
FROM jobs
WHERE state IN ('running', 'queued')
ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, id;

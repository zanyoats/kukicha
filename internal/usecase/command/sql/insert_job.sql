INSERT INTO jobs (type, payload)
SELECT :type, :payload
WHERE NOT EXISTS (
    SELECT 1
    FROM jobs
    WHERE type = :type AND state IN ('queued', 'running')
);

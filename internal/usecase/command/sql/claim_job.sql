-- Select and claim the oldest queued job in one atomic write.
-- Keep the writer as the transaction's first statement or use BEGIN IMMEDIATE.
UPDATE jobs
SET state = 'running',
    attempts = attempts + 1,
    started_at = CURRENT_TIMESTAMP,
    finished_at = NULL,
    last_error = NULL
WHERE id = (
    SELECT id
    FROM jobs
    WHERE state = 'queued' AND type != 'add' -- Disabled until both add modes, root marker checks, and standalone sync removal exist.
    ORDER BY id
    LIMIT 1
) AND state = 'queued'
RETURNING id;

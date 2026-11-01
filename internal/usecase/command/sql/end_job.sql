UPDATE jobs
SET state = CASE
        WHEN cancel_requested_at IS NOT NULL THEN 'canceled'
        WHEN :interrupted THEN 'queued'
        WHEN :failure IS NOT NULL THEN 'failed'
        ELSE 'succeeded'
    END,
    payload = CASE
        WHEN cancel_requested_at IS NULL AND :interrupted THEN payload
        ELSE json_remove(payload, '$.source.remote.credentials')
    END,
    finished_at = CASE
        WHEN cancel_requested_at IS NULL AND :interrupted
            THEN NULL
        ELSE CURRENT_TIMESTAMP
    END,
    last_error = CASE
        WHEN cancel_requested_at IS NULL AND NOT :interrupted
            THEN :failure
        ELSE NULL
    END
WHERE
        id = :job_id
    AND attempts = :attempt
    AND state = 'running'
RETURNING state;

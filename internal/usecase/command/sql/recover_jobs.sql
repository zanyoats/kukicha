UPDATE jobs
SET state = CASE
        WHEN cancel_requested_at IS NOT NULL THEN 'canceled'
        ELSE 'queued'
    END,
    payload = CASE WHEN cancel_requested_at IS NOT NULL
        THEN json_remove(payload, '$.source.remote.credentials') ELSE payload END,
    started_at = CASE
        WHEN cancel_requested_at IS NOT NULL THEN started_at
        ELSE NULL
    END,
    finished_at = CASE
        WHEN cancel_requested_at IS NOT NULL THEN CURRENT_TIMESTAMP
        ELSE NULL
    END,
    last_error = NULL
WHERE state = 'running';

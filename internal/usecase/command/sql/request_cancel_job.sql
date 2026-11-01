UPDATE jobs
    SET state =
            CASE
            WHEN state = 'queued'
            THEN 'canceled'
            ELSE state
            END,
        payload = CASE WHEN state = 'queued'
            THEN json_remove(payload, '$.source.remote.credentials') ELSE payload END,
        finished_at =
            CASE
            WHEN state = 'queued'
            THEN CURRENT_TIMESTAMP
            ELSE finished_at
            END,
        cancel_requested_at = COALESCE(
            cancel_requested_at, CURRENT_TIMESTAMP
        )
WHERE
        id = :job_id
    AND state IN ('queued', 'running');

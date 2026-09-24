SELECT
    id,
    type,
    payload,
    state,
    attempts,
    created_at,
    started_at,
    finished_at,
    last_error,
    cancel_requested_at
FROM jobs
WHERE id = :job_id;

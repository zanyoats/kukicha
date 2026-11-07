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
    cancel_requested_at,
    library_published, artwork_completed_at, cleaned_at
FROM jobs
WHERE id = :job_id;

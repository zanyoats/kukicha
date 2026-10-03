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
WHERE :before = 0 OR (created_at, id) < (
    SELECT created_at, id FROM jobs WHERE id = :before
)
ORDER BY created_at DESC, id DESC
LIMIT :limit;

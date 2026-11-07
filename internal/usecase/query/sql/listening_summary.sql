SELECT
    COALESCE(SUM(CASE WHEN s.played_at >= @start THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN s.played_at >= @start THEN COALESCE(t.duration_seconds, 0) ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN s.played_at < @previous_end THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN s.played_at < @previous_end THEN COALESCE(t.duration_seconds, 0) ELSE 0 END), 0)
FROM scrobbles AS s
LEFT JOIN library_tracks AS t ON t.id = s.track_id
WHERE (s.played_at >= @start AND s.played_at < @end)
   OR (s.played_at >= @previous_start AND s.played_at < @previous_end)

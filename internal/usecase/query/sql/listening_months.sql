WITH months AS (
    SELECT json_extract(value, '$.year') AS year,
           json_extract(value, '$.month') AS month,
           json_extract(value, '$.start') AS start,
           json_extract(value, '$.end') AS end
    FROM json_each(?)
)
SELECT m.year, m.month, COUNT(s.track_id)
FROM months AS m
LEFT JOIN scrobbles AS s ON s.played_at >= m.start AND s.played_at < m.end
GROUP BY m.year, m.month

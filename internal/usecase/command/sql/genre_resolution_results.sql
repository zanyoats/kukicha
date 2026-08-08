WITH summaries AS (
    SELECT
        track_id,
        MAX(path) AS path,
        COUNT(input_genre) AS input_count,
        SUM(CASE
            WHEN input_genre IS NOT NULL
                AND (
                    resolved_genre IS NOT NULL
                    OR resolved_style IS NOT NULL
                )
                THEN 1
            ELSE 0
        END) AS resolved_count,
        SUM(CASE
            WHEN input_genre IS NOT NULL
                AND resolved_genre IS NULL
                AND resolved_style IS NULL
                THEN 1
            ELSE 0
        END) AS unresolved_count,
        json_group_array(input_genre)
            FILTER (
                WHERE input_genre IS NOT NULL
            ) AS input_genres_json,
        json_group_array(input_genre)
            FILTER (
                WHERE input_genre IS NOT NULL
                    AND resolved_genre IS NULL
                    AND resolved_style IS NULL
            ) AS unresolved_genres_json,
        group_concat(quote(input_genre), ', ')
            FILTER (
                WHERE input_genre IS NOT NULL
                    AND resolved_genre IS NULL
                    AND resolved_style IS NULL
            ) AS unresolved_display
    FROM scan_track_genre_resolution
    GROUP BY track_id
)
SELECT
    track_id,
    path,
    CASE
        WHEN input_count = 0 THEN 'empty'
        WHEN resolved_count = 0 THEN 'unresolved'
        WHEN unresolved_count > 0 THEN 'partial'
    END AS status,
    CASE
        WHEN input_count = 0
            THEN 'set to unknown since audio file genres was empty'
        WHEN resolved_count = 0
            THEN 'set to unknown since the following audio file genres could not resolve to either a canonical style or genre: ' || unresolved_display
        WHEN unresolved_count > 0
            THEN 'the following audio file genres could not resolve to either a canonical style or genre: ' || unresolved_display
    END AS log_message
FROM summaries
WHERE input_count = 0
    OR resolved_count = 0
    OR unresolved_count > 0
ORDER BY track_id;

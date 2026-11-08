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
INSERT INTO library_job_logs (job_id, level, message, data)
SELECT
    :job_id,
    'warn',
    CASE
        WHEN input_count = 0
            THEN 'Track imported. No genre tags were present in the audio metadata; the library genre was set to unknown.'
        WHEN resolved_count = 0
            THEN 'Track imported. None of the audio genre tags could be recognized as a genre or style; the library genre was set to unknown. Unrecognized tags: ' || unresolved_display
        WHEN unresolved_count > 0
            THEN 'Track imported. Recognized genres and styles were kept, but some audio genre tags could not be recognized. Unrecognized tags: ' || unresolved_display
    END,
    json_object('root', roots.config_id, 'path', summaries.path)
FROM summaries
JOIN library_tracks AS tracks ON tracks.id = summaries.track_id
JOIN library_roots AS roots ON roots.id = tracks.root_id
WHERE (input_count = 0
    OR resolved_count = 0
    OR unresolved_count > 0)
    AND :warn_enabled
ORDER BY summaries.track_id;

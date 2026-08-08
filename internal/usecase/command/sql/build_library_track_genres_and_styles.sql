DROP TABLE IF EXISTS scan_taxonomy_style_resolution;

CREATE TEMP TABLE scan_taxonomy_style_resolution AS
WITH ranked AS (
    SELECT
        style_key,
        canonical_style,
        ROW_NUMBER() OVER (
            PARTITION BY style_key COLLATE NOCASE
            ORDER BY taxonomy_id, pos
        ) AS rank
    FROM (
        SELECT
            name AS style_key,
            name AS canonical_style,
            id AS taxonomy_id,
            0 AS pos
        FROM taxonomy
        WHERE kind = 'style'

        UNION ALL

        SELECT
            alias AS style_key,
            name AS canonical_style,
            id AS taxonomy_id,
            1 AS pos
        FROM taxonomy
        WHERE
                kind = 'style'
            AND alias COLLATE NOCASE <> name COLLATE NOCASE
    )
)
SELECT
    ranked.style_key,
    ranked.canonical_style
FROM ranked
JOIN taxonomy_styles AS canonical_styles
    ON canonical_styles.style = ranked.canonical_style
WHERE ranked.rank = 1;

DROP TABLE IF EXISTS scan_track_genre_resolution;

CREATE TEMP TABLE scan_track_genre_resolution AS
WITH RECURSIVE
split_delimiters(pos, delimiter) AS (
    SELECT
        CAST(tokens.key AS INTEGER) + 1,
        tokens.value
    FROM json_each(:split_delimiters) AS tokens
),
raw_genres AS (
    SELECT
        tracks.id AS track_id,
        s.path,
        genres.key AS genre_pos,
        genres.value AS input_genre
    FROM scan_tracks AS s
    JOIN library_roots AS roots
        ON roots.pos = s.root_pos
    JOIN library_tracks AS tracks
        ON tracks.root_id = roots.id
        AND tracks.path = s.path
    LEFT JOIN json_each(
        s.normalized_metadata,
        '$.genres'
    ) AS genres
    WHERE s.file_type = :audio_file_type
),
raw_resolutions AS (
    SELECT
        raw.track_id,
        raw.path,
        raw.genre_pos,
        raw.input_genre,
        tg.genre AS resolved_genre,
        tsr.canonical_style AS resolved_style
    FROM raw_genres AS raw
    LEFT JOIN taxonomy_genres AS tg
        ON tg.genre = raw.input_genre
    LEFT JOIN scan_taxonomy_style_resolution AS tsr
        ON tsr.style_key COLLATE NOCASE
            = raw.input_genre COLLATE NOCASE
        AND tg.genre IS NULL
),
normalized_genres(
    track_id,
    path,
    genre_pos,
    original_genre,
    delimiter_pos,
    normalized_genre
) AS (
    SELECT
        raw.track_id,
        raw.path,
        raw.genre_pos,
        raw.input_genre,
        0,
        raw.input_genre
    FROM raw_resolutions AS raw
    WHERE raw.input_genre IS NOT NULL
        AND raw.resolved_genre IS NULL
        AND raw.resolved_style IS NULL
        AND EXISTS (
            SELECT 1
            FROM split_delimiters AS delimiters
            WHERE instr(
                raw.input_genre,
                delimiters.delimiter
            ) > 0
        )

    UNION ALL

    SELECT
        normalized.track_id,
        normalized.path,
        normalized.genre_pos,
        normalized.original_genre,
        delimiters.pos,
        replace(
            normalized.normalized_genre,
            delimiters.delimiter,
            char(31)
        )
    FROM normalized_genres AS normalized
    JOIN split_delimiters AS delimiters
        ON delimiters.pos
            = normalized.delimiter_pos + 1
),
split_sources AS (
    SELECT
        track_id,
        path,
        genre_pos,
        original_genre,
        normalized_genre
    FROM normalized_genres
    WHERE delimiter_pos = (
        SELECT MAX(pos)
        FROM split_delimiters
    )
),
split_parts(
    track_id,
    path,
    genre_pos,
    original_genre,
    part_pos,
    part,
    rest
) AS (
    SELECT
        track_id,
        path,
        genre_pos,
        original_genre,
        0,
        trim(substr(
            normalized_genre,
            1,
            instr(
                normalized_genre || char(31),
                char(31)
            ) - 1
        )),
        substr(
            normalized_genre || char(31),
            instr(
                normalized_genre || char(31),
                char(31)
            ) + 1
        )
    FROM split_sources

    UNION ALL

    SELECT
        track_id,
        path,
        genre_pos,
        original_genre,
        part_pos + 1,
        trim(substr(
            rest,
            1,
            instr(rest, char(31)) - 1
        )),
        substr(
            rest,
            instr(rest, char(31)) + 1
        )
    FROM split_parts
    WHERE rest <> ''
),
split_resolutions AS (
    SELECT
        parts.track_id,
        parts.path,
        parts.genre_pos,
        parts.original_genre,
        parts.part_pos,
        parts.part AS input_genre,
        tg.genre AS resolved_genre,
        tsr.canonical_style AS resolved_style
    FROM split_parts AS parts
    LEFT JOIN taxonomy_genres AS tg
        ON tg.genre = parts.part
    LEFT JOIN scan_taxonomy_style_resolution AS tsr
        ON tsr.style_key COLLATE NOCASE
            = parts.part COLLATE NOCASE
        AND tg.genre IS NULL
    WHERE parts.part <> ''
),
expandable_genres AS (
    SELECT
        track_id,
        genre_pos
    FROM split_resolutions
    GROUP BY
        track_id,
        genre_pos
    HAVING SUM(
        CASE
            WHEN resolved_genre IS NOT NULL
                OR resolved_style IS NOT NULL
                THEN 1
            ELSE 0
        END
    ) > 0
)
SELECT
    raw.track_id,
    raw.path,
    raw.genre_pos,
    raw.input_genre,
    raw.resolved_genre,
    raw.resolved_style
FROM raw_resolutions AS raw
WHERE raw.input_genre IS NULL
    OR raw.resolved_genre IS NOT NULL
    OR raw.resolved_style IS NOT NULL
    OR NOT EXISTS (
        SELECT 1
        FROM expandable_genres AS expandable
        WHERE expandable.track_id = raw.track_id
            AND expandable.genre_pos = raw.genre_pos
    )

UNION ALL

SELECT
    split.track_id,
    split.path,
    split.genre_pos,
    split.input_genre,
    split.resolved_genre,
    split.resolved_style
FROM split_resolutions AS split
JOIN expandable_genres AS expandable
    ON expandable.track_id = split.track_id
    AND expandable.genre_pos = split.genre_pos;

INSERT OR IGNORE INTO library_track_genres (
    track_id,
    genre
)
SELECT DISTINCT
    track_id,
    resolved_genre
FROM scan_track_genre_resolution
WHERE resolved_genre IS NOT NULL;

INSERT OR IGNORE INTO library_track_genres (
    track_id,
    genre
)
SELECT
    track_id,
    :unknown_item
FROM scan_track_genre_resolution
GROUP BY track_id
HAVING SUM(
    CASE
        WHEN resolved_genre IS NOT NULL
            OR resolved_style IS NOT NULL
            THEN 1
        ELSE 0
    END
) = 0;

INSERT OR IGNORE INTO library_track_styles (
    track_id,
    style
)
SELECT DISTINCT
    track_id,
    resolved_style
FROM scan_track_genre_resolution
WHERE resolved_style IS NOT NULL;

-- Resolve normalized genre values against the taxonomy. Only values that
-- do not match as a whole are tried again using secondary splitting.

-- Build a lookup from style names and taxonomy aliases to canonical styles.
-- If a lookup name occurs more than once, the lowest taxonomy ID wins;
-- for the same ID, prefer its name over its alias.
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
-- Read the genres produced by NormalizeScanTracks, keeping their positions.
-- LEFT JOIN also retains tracks with no genres so they can receive unknown.
raw_genres AS (
    SELECT
        tracks.id AS track_id,
        s.path,
        genres.key AS genre_pos,
        genres.value AS input_genre
    FROM scan_tracks AS s
    JOIN scan_track_normalized_metadata AS normalized
        ON normalized.root_pos = s.root_pos AND normalized.path = s.path
    JOIN library_roots AS roots
        ON roots.config_id = s.config_id
    JOIN library_tracks AS tracks
        ON tracks.root_id = roots.id
        AND tracks.path = s.path
    LEFT JOIN json_each(
        normalized.normalized_metadata,
        '$.genres'
    ) AS genres
),
-- Try the whole value first. A genre match takes precedence over a style match.
-- input_id lets the splitter carry each result back to its original value.
raw_resolutions AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY raw.track_id, raw.genre_pos) AS input_id,
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
-- Only unresolved values reach the secondary splitter. For example, if
-- "Rock/Pop" itself is a taxonomy term, keep it even when '/' is a delimiter.
split_sources AS (
    SELECT input_id, input_genre AS input_value
    FROM raw_resolutions
    WHERE input_genre IS NOT NULL
        AND resolved_genre IS NULL AND resolved_style IS NULL
),
-- Go inserts the same splitter used for primary genres, with secondary delimiters.
/* split_genre_values */,
-- Look up each nonblank part directly in the taxonomy. Do not reapply the
-- configured genre aliases; those were applied once during primary normalization.
split_resolutions AS (
    SELECT
        raw.track_id,
        raw.path,
        raw.genre_pos,
        raw.input_genre AS original_genre,
        parts.part_pos,
        parts.part AS input_genre,
        tg.genre AS resolved_genre,
        tsr.canonical_style AS resolved_style
    FROM split_parts AS parts
    JOIN raw_resolutions AS raw USING (input_id)
    LEFT JOIN taxonomy_genres AS tg
        ON tg.genre = parts.part
    LEFT JOIN scan_taxonomy_style_resolution AS tsr
        ON tsr.style_key COLLATE NOCASE
            = parts.part COLLATE NOCASE
        AND tg.genre IS NULL
    WHERE parts.part <> ''
),
-- Replace an original value only if at least one of its parts is recognized.
-- "Rock/Mystery" can expand when Rock is known; "Mystery/Other" stays intact
-- when neither part is known. Group by position so each original is decided alone.
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
-- Keep whole-value matches and unresolved originals whose splitting did not help.
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

-- For originals whose splitting helped, keep all parts, including unresolved ones.
-- The inserts below link only recognized parts to library genres and styles.
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

-- Link each track to its recognized genres without duplicate relationships.
INSERT OR IGNORE INTO library_track_genres (
    track_id,
    genre_id
)
SELECT DISTINCT
    resolutions.track_id,
    genres.id
FROM scan_track_genre_resolution AS resolutions
JOIN taxonomy_genres AS genres
    ON genres.genre = resolutions.resolved_genre;

-- Assign unknown when the track has no recognized genre, even if it has styles.
-- One unresolved value does not make a track with a recognized genre unknown.
INSERT OR IGNORE INTO library_track_genres (
    track_id,
    genre_id
)
SELECT
    resolutions.track_id,
    genres.id
FROM scan_track_genre_resolution AS resolutions
JOIN taxonomy_genres AS genres
    ON genres.genre = :unknown_item
GROUP BY resolutions.track_id, genres.id
HAVING SUM(
    CASE
        WHEN resolved_genre IS NOT NULL
            THEN 1
        ELSE 0
    END
) = 0;

-- Link recognized styles separately from genres.
INSERT OR IGNORE INTO library_track_styles (
    track_id,
    style_id
)
SELECT DISTINCT
    resolutions.track_id,
    styles.id
FROM scan_track_genre_resolution AS resolutions
JOIN taxonomy_styles AS styles
    ON styles.style = resolutions.resolved_style;

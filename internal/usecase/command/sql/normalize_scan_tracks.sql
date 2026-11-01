-- Build one normalized metadata object per track. Keep the original tags,
-- provider values, and album keys unchanged in their source tables.
-- Artist selection and genre normalization run independently below.
WITH RECURSIVE
-- Try saved mappings first, then album artists, track artists, and unknown.
-- Saved mappings have already been validated when written.
artist_candidates AS (
    SELECT tracks.root_pos, tracks.path, 0 AS priority, mappings.mapped_artists AS artists
    FROM scan_tracks AS tracks
    JOIN album_artist_mappings AS mappings ON mappings.album_key = tracks.album_key
    UNION ALL
    SELECT root_pos, path, 1, json_extract(track_metadata, '$.album_artists') FROM scan_tracks
    UNION ALL
    SELECT root_pos, path, 2, json_extract(track_metadata, '$.artists') FROM scan_tracks
    UNION ALL
    SELECT root_pos, path, 3, json_array(:unknown_item) FROM scan_tracks
),
-- Clean every candidate before choosing one: trim Unicode whitespace, drop
-- blanks, remove exact duplicates, and sort case-sensitively. For example,
-- [" B ", "A", "A"] becomes ["A", "B"]. Turn [] into NULL so fallback can skip it.
cleaned_artists AS (
    SELECT root_pos, path, priority, NULLIF((
        SELECT json_group_array(artist) FROM (
            SELECT DISTINCT trim(value, :unicode_whitespace) COLLATE BINARY AS artist
            FROM json_each(candidates.artists)
            WHERE trim(value, :unicode_whitespace) <> ''
            ORDER BY artist COLLATE BINARY
        )
    ), '[]') AS artists
    FROM artist_candidates AS candidates
),
-- Normalize both genre sources. Provider genres have priority 0, but only
-- win if at least one value survives normalization; otherwise use file genres.
genre_arrays AS (
    SELECT tracks.root_pos, tracks.path, 0 AS priority, overrides.genres_json AS genres
    FROM scan_tracks AS tracks
    JOIN scan_album_overrides AS overrides ON overrides.album_key = tracks.album_key
    UNION ALL
    SELECT root_pos, path, 1, json_extract(track_metadata, '$.genres') FROM scan_tracks
),
-- Turn each genre array into rows. Keep the original position for ordering
-- and give each input string an ID so split parts can be joined back to it.
genre_inputs AS (
    SELECT ROW_NUMBER() OVER (ORDER BY genre_arrays.root_pos, genre_arrays.path, genre_arrays.priority, genres.key) AS input_id,
        genre_arrays.root_pos, genre_arrays.path, genre_arrays.priority, CAST(genres.key AS INTEGER) AS genre_pos,
        genres.value AS input_value
    FROM genre_arrays JOIN json_each(genre_arrays.genres) AS genres
),
-- Replace embedded NUL bytes with semicolons, as the scanner did before.
-- Each step copies the text before the next NUL into prefix, adds ';', and
-- continues with rest. Use bytes because SQLite text operations can stop at NUL.
-- A semicolon only splits the result if it is a configured primary delimiter.
nul_cleaned(input_id, prefix, rest) AS (
    SELECT input_id, '', CAST(input_value AS BLOB) FROM genre_inputs
    UNION ALL
    SELECT input_id, prefix || CAST(substr(rest, 1, instr(rest, x'00') - 1) AS TEXT) || ';',
        substr(rest, instr(rest, x'00') + 1)
    FROM nul_cleaned WHERE instr(rest, x'00') > 0
),
-- Keep only the finished cleanup result for each input string.
split_sources AS (
    SELECT input_id, prefix || CAST(rest AS TEXT) AS input_value
    FROM nul_cleaned WHERE instr(rest, x'00') = 0
),
-- Go inserts the shared splitter here, using the primary delimiter array.
/* split_genre_values */,
-- Look up one alias per cleaned part; do not look up the replacement again.
-- Keep the resulting spelling for display and use its ICU lowercase form to
-- detect duplicates. Blank parts and parts with remaining controls are dropped.
aliased_genres AS (
    SELECT inputs.root_pos, inputs.path, inputs.priority, inputs.genre_pos, parts.part_pos,
        COALESCE(aliases.value, parts.part) AS genre,
        lower(COALESCE(aliases.value, parts.part)) AS genre_key
    FROM split_parts AS parts
    JOIN genre_inputs AS inputs USING (input_id)
    LEFT JOIN json_each(:aliases) AS aliases ON aliases.key = lower(parts.part) COLLATE BINARY
    WHERE parts.part <> ''
        -- ICU regexp rejects embedded ASCII controls after Unicode trimming.
        AND parts.part NOT REGEXP '(?s).*[\x{0000}-\x{001f}].*'
),
-- Number duplicate occurrences separately for each track and genre source.
-- Rank 1 is the first occurrence in input order: ["Rock", "ROCK"] keeps "Rock".
ranked_genres AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY root_pos, path, priority, genre_key COLLATE BINARY
        ORDER BY genre_pos, part_pos
    ) AS rank
    FROM aliased_genres WHERE genre_key <> ''
),
-- Keep the first occurrence of each genre from the preferred surviving source.
-- A blank provider list falls back to file genres. An unrecognized provider
-- value still wins: taxonomy recognition happens later, during library building.
selected_genres AS (
    SELECT * FROM ranked_genres AS genres
    WHERE rank = 1 AND priority = (
        SELECT MIN(priority) FROM ranked_genres AS candidates
        WHERE candidates.root_pos = genres.root_pos AND candidates.path = genres.path
    )
)
-- Assemble the chosen values into JSON. json(...) embeds arrays as arrays,
-- rather than as strings containing JSON text.
INSERT INTO scan_track_normalized_metadata (root_pos, path, normalized_metadata)
SELECT tracks.root_pos, tracks.path, json_object(
    -- Use the title, then the filename stem, then unknown; only '' is empty here.
    'track_display', COALESCE(NULLIF(json_extract(track_metadata, '$.title'), ''), NULLIF(stem, ''), :unknown_item),
    -- Match album identity: trim ordinary U+0020 spaces, then fall back.
    'album_display', COALESCE(NULLIF(trim(json_extract(track_metadata, '$.album')), ''), :unknown_item),
    -- Raw display arrays retain their original entries and order.
    'raw_album_artist_displays', json(CASE
        WHEN json_array_length(track_metadata, '$.album_artists') > 0 THEN json_extract(track_metadata, '$.album_artists')
        WHEN json_array_length(track_metadata, '$.artists') > 0 THEN json_extract(track_metadata, '$.artists')
        ELSE json_array(:unknown_item) END),
    -- Choose the first nonempty cleaned array: saved mapping, album artists,
    -- track artists, then unknown.
    'mapped_album_artist_displays', json((
        SELECT artists FROM cleaned_artists
        WHERE root_pos = tracks.root_pos AND path = tracks.path
            AND artists IS NOT NULL
        ORDER BY priority LIMIT 1
    )),
    'raw_track_artist_displays', json(CASE
        WHEN json_array_length(track_metadata, '$.artists') > 0 THEN json_extract(track_metadata, '$.artists')
        WHEN json_array_length(track_metadata, '$.album_artists') > 0 THEN json_extract(track_metadata, '$.album_artists')
        ELSE json_array(:unknown_item) END),
    -- Restore input order. If no genres survived, store JSON null as before.
    'genres', json(NULLIF((
        SELECT json_group_array(genre) FROM (
            SELECT genre FROM selected_genres
            WHERE root_pos = tracks.root_pos AND path = tracks.path
            ORDER BY genre_pos, part_pos
        )
    ), '[]'))
)
FROM scan_tracks AS tracks;

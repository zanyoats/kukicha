-- Shared by primary and secondary genre splitting. The caller supplies
-- split_sources(input_id, input_value) and an ordered array of literal delimiters.
-- Configuration validation rejects empty delimiters so every step makes progress.
--
-- Think of split_parts as a loop: take the text before the earliest delimiter,
-- consume that delimiter, and repeat with the remaining text. Ties use array
-- order, not longest match: ["ab", "abc"] splits "XabcY" into ["X", "cY"].
--
-- Match bytes so embedded NULs do not truncate the input. Convert each part
-- back to text before trimming the same Unicode whitespace that Go trims.
split_delimiters AS (
    SELECT CAST(key AS INTEGER) AS pos, CAST(value AS BLOB) AS delimiter
    FROM json_each(:split_delimiters)
),
split_parts(input_id, part_pos, part, rest) AS (
    -- Start with all the input remaining; this row has no part to emit.
    SELECT input_id, -1, NULL, CAST(input_value AS BLOB) FROM split_sources
    UNION ALL
    SELECT input_id, part_pos + 1,
        -- instr returns 0 for no match. Ignore those zeros and take the earliest
        -- match; if none exists, take the whole remaining input as the last part.
        trim(CAST(substr(rest, 1, COALESCE((
            SELECT MIN(NULLIF(instr(rest, delimiter), 0))
            FROM split_delimiters
        ), length(rest) + 1) - 1) AS TEXT), :unicode_whitespace),
        -- Continue after the chosen delimiter, consuming its full length.
        -- At equal positions, pos selects the first configured delimiter.
        CASE WHEN EXISTS (
            SELECT 1 FROM split_delimiters WHERE instr(rest, delimiter) > 0
        ) THEN substr(rest, (
            SELECT instr(rest, delimiter) + length(delimiter)
            FROM split_delimiters
            WHERE instr(rest, delimiter) > 0
            ORDER BY instr(rest, delimiter), pos LIMIT 1
        )) ELSE x'' END
    FROM split_parts
    -- Stop once nothing remains. Callers discard blank parts, including those
    -- produced by consecutive delimiters or whitespace-only input.
    WHERE length(rest) > 0
)

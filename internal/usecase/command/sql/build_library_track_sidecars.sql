INSERT INTO library_track_sidecars (
    track_id,
    stem,
    ext,
    path,
    file_type,
    file_size,
    file_mtime_unix_nano
)
SELECT
    tracks.id,
    sidecars.stem,
    sidecars.ext,
    sidecars.path,
    sidecars.file_type,
    sidecars.file_size,
    sidecars.file_mtime_unix_nano
FROM scan_tracks AS sidecars
JOIN scan_tracks AS audio
    ON audio.root_pos = sidecars.root_pos
    AND audio.file_type = :audio_file_type
    AND (
        (audio.path_dir = '.' AND sidecars.path_dir = '.')
        OR substr(sidecars.path, 1, length(audio.path_dir) + 1) = audio.path_dir || '/'
    )
JOIN library_roots AS roots
    ON roots.pos = audio.root_pos
JOIN library_tracks AS tracks
    ON tracks.root_id = roots.id
    AND tracks.path = audio.path
WHERE sidecars.file_type IN (:cover_file_type, :other_file_type)
ORDER BY tracks.id, sidecars.path;

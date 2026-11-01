INSERT INTO now_playing (slot, track_id, played_at, completed)
VALUES (1, ?, ?, ?)
ON CONFLICT (slot) DO UPDATE SET
    track_id = excluded.track_id,
    played_at = excluded.played_at,
    completed = excluded.completed
WHERE excluded.played_at > now_playing.played_at
   OR (excluded.played_at = now_playing.played_at
       AND excluded.track_id = now_playing.track_id
       AND excluded.completed > now_playing.completed);

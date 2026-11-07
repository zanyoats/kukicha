SELECT s.played_at, g.id, g.genre
FROM scrobbles AS s
JOIN library_track_genres AS tg ON tg.track_id = s.track_id
JOIN taxonomy_genres AS g ON g.id = tg.genre_id
WHERE s.played_at >= ? AND s.played_at < ?
ORDER BY g.genre COLLATE NOCASE, g.id

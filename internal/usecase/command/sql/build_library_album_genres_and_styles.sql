INSERT INTO library_album_genres (album_id, genre_id)
SELECT
    tracks.album_id,
    track_genres.genre_id
FROM library_track_genres AS track_genres
JOIN library_tracks AS tracks
    ON tracks.id = track_genres.track_id
GROUP BY tracks.album_id, track_genres.genre_id
ORDER BY tracks.album_id, track_genres.genre_id;

INSERT INTO library_album_styles (album_id, style_id)
SELECT
    tracks.album_id,
    track_styles.style_id
FROM library_track_styles AS track_styles
JOIN library_tracks AS tracks
    ON tracks.id = track_styles.track_id
GROUP BY tracks.album_id, track_styles.style_id
ORDER BY tracks.album_id, track_styles.style_id;

-- Album artist relationships already reflect the resolved artist split rules.
INSERT INTO artists_radios (artist_id, position, track_id)
SELECT
    artists.artist_id,
    ROW_NUMBER() OVER (
        PARTITION BY artists.artist_id ORDER BY tracks.id
    ) - 1,
    tracks.id
FROM library_album_artists AS artists
JOIN library_tracks AS tracks ON tracks.album_id = artists.album_id;

INSERT INTO genres_radios (genre_id, position, track_id)
SELECT
    genre_id,
    ROW_NUMBER() OVER (
        PARTITION BY genre_id ORDER BY track_id
    ) - 1,
    track_id
FROM library_track_genres;

INSERT INTO styles_radios (style_id, position, track_id)
SELECT
    style_id,
    ROW_NUMBER() OVER (
        PARTITION BY style_id ORDER BY track_id
    ) - 1,
    track_id
FROM library_track_styles;

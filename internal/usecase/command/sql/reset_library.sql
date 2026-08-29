DELETE FROM library_track_styles;
DELETE FROM library_track_genres;
DELETE FROM library_track_artwork;
DELETE FROM library_track_embedded_artwork;
DELETE FROM library_track_sidecars;
DELETE FROM library_tracks;
DELETE FROM library_album_artists;
DELETE FROM library_albums;
DELETE FROM library_roots;

DELETE FROM sqlite_sequence
WHERE name IN (
    'library_roots',
    'library_albums',
    'library_album_artists',
    'library_tracks',
    'library_track_sidecars',
    'library_track_embedded_artwork',
    'library_track_artwork',
    'library_track_genres',
    'library_track_styles'
);

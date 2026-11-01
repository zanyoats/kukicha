DELETE FROM artists_radios;
DELETE FROM genres_radios;
DELETE FROM styles_radios;
DELETE FROM library_album_styles;
DELETE FROM library_album_genres;
DELETE FROM library_track_styles;
DELETE FROM library_track_genres;
DELETE FROM library_track_artwork;
DELETE FROM library_track_embedded_artwork;
DELETE FROM library_album_sidecars;
DELETE FROM library_tracks;
DELETE FROM library_album_artists;
DELETE FROM library_albums;

-- Child rows are gone; finish removals requested before this build started.
DELETE FROM library_root_ids
WHERE config_id IN (SELECT config_id FROM library_roots WHERE state = 'pending_removal');
DELETE FROM library_roots WHERE state = 'pending_removal';

DELETE FROM sqlite_sequence
WHERE name IN (
    'library_album_sidecars',
    'library_track_embedded_artwork',
    'library_track_artwork'
);

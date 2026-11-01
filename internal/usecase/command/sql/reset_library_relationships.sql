-- Scan refreshes derived relationships without deleting albums or tracks.
DELETE FROM artists_radios;
DELETE FROM genres_radios;
DELETE FROM styles_radios;
DELETE FROM library_album_artists;
DELETE FROM library_album_styles;
DELETE FROM library_album_genres;
DELETE FROM library_track_styles;
DELETE FROM library_track_genres;

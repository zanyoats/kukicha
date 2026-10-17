-- Scan refreshes derived relationships without deleting albums or tracks.
DELETE FROM library_album_artists;
DELETE FROM library_album_styles;
DELETE FROM library_album_genres;
DELETE FROM library_track_styles;
DELETE FROM library_track_genres;

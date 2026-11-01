SELECT EXISTS(SELECT 1 FROM library_tracks WHERE id = ?)
OR EXISTS(SELECT 1 FROM scrobbles WHERE track_id = ? AND played_at = ?)
OR EXISTS(SELECT 1 FROM now_playing WHERE track_id = ? AND played_at = ?);

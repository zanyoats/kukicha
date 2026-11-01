INSERT INTO scrobbles (track_id, played_at) VALUES (?, ?)
ON CONFLICT (track_id, played_at) DO NOTHING;

INSERT INTO app_metadata (key, value)
VALUES (:key, :value)
ON CONFLICT(key) DO UPDATE SET value = excluded.value;

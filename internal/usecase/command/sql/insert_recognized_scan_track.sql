-- Only complete identities enter the normal library pipeline. The other rows
-- retain their original tags and are saved at successful publication.
INSERT INTO scan_tracks
SELECT * FROM scan_unrecognized_tracks
WHERE root_pos = :root_pos AND path = :path
    AND json_extract(album_key, '$[0]') <> :unknown_item
    AND json_extract(album_key, '$[1]') <> json_array(:unknown_item);

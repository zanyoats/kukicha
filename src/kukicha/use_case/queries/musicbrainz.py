from __future__ import annotations

from pathlib import Path

from ..database import connect_database
from ..musicbrainz import MusicBrainzAlbumLink, load_album_musicbrainz_links


def album_musicbrainz_link(database: Path, album_id: str) -> MusicBrainzAlbumLink | None:
    with connect_database(database, create=False) as connection:
        return load_album_musicbrainz_links(connection).get(album_id)

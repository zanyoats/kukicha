from __future__ import annotations

from pathlib import Path

from ..database import connect_database
from ..musicbrainz import MusicBrainzAlbumLink, album_musicbrainz_link_for_album_id


def album_musicbrainz_link(database: Path, album_id: str) -> MusicBrainzAlbumLink | None:
    with connect_database(database, create=False) as connection:
        return album_musicbrainz_link_for_album_id(connection, album_id)

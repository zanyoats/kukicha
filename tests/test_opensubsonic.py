from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kukicha._compat import UTC
from kukicha.models import (
    MusicLibrary,
    PlaylistItemRecord,
    PlaylistRecord,
    TrackArtwork,
    TrackRecord,
)
from kukicha.player_auth import hash_password
from kukicha.player_config import (
    OpenSubsonicOptions,
    PlayerAuthOptions,
    PlayerServerOptions,
)
from kukicha.player_web_adapter import create_player_app
from kukicha.use_case import connect_database, save_library


def subsonic_payload(response):
    return response.get_json()["subsonic-response"]


class OpenSubsonicWebAdapterTest(unittest.TestCase):
    def make_options(
        self,
        temp_path: Path,
        *,
        username: str = "guest",
        password: str = "guest",
        mount_prefix: str = "/",
    ) -> PlayerServerOptions:
        password_hash_file = temp_path / "password.hash"
        password_hash_file.write_text(f"{hash_password('browser-secret')}\n", encoding="utf-8")
        password_hash_file.chmod(0o600)
        secret_file = temp_path / "opensubsonic.secret"
        secret_file.write_text(f"{password}\n", encoding="utf-8")
        secret_file.chmod(0o600)
        return PlayerServerOptions(
            config_path=temp_path / "kukicha.toml",
            database=temp_path / "kukicha.sqlite",
            ffmpeg_path=None,
            auth=PlayerAuthOptions(
                username=username,
                password_hash_file=password_hash_file,
            ),
            opensubsonic=OpenSubsonicOptions(
                mount_prefix=mount_prefix,
                secret_file=secret_file,
            ),
        )

    def auth_params(
        self,
        *,
        username: str = "guest",
        password: str = "guest",
    ) -> dict[str, str]:
        return {
            "u": username,
            "p": password,
            "v": "1.16.1",
            "c": "kukicha-test",
            "f": "json",
        }

    def save_sample_library(self, temp_path: Path) -> tuple[TrackRecord, TrackRecord]:
        root = temp_path / "music"
        first_path = root / "Artist" / "First Album" / "01.mp3"
        second_path = root / "Artist" / "Second Album" / "01.flac"
        first_path.parent.mkdir(parents=True)
        second_path.parent.mkdir(parents=True)
        first_path.write_bytes(b"0123456789")
        second_path.write_bytes(b"second track")
        first = TrackRecord(
            path=str(first_path),
            root_position=0,
            file_type="mp3",
            artist="Artist",
            album_artist="Artist",
            album="First Album",
            title="First Track",
            track_number="1/2",
            disc_number="1/1",
            date="2026-04-21",
            genres=["Electronic"],
            duration_seconds=65.2,
            bitrate=128000,
            artwork=TrackArtwork(mime_type="image/png", data=b"small-cover"),
            album_artwork=TrackArtwork(mime_type="image/png", data=b"album-cover"),
        )
        second = TrackRecord(
            path=str(second_path),
            root_position=0,
            file_type="flac",
            artist="Artist",
            album_artist="Artist",
            album="Second Album",
            title="Second Track",
            track_number="1",
            duration_seconds=120.0,
            bitrate=900000,
        )
        save_library(
            MusicLibrary(
                roots=[str(root)],
                tracks=[first, second],
                supported_extensions=[".mp3", ".flac"],
                generated_at="2026-05-07T00:00:00+00:00",
            ),
            temp_path / "kukicha.sqlite",
        )
        return first, second

    def save_artist_library(self, temp_path: Path) -> tuple[TrackRecord, TrackRecord, TrackRecord]:
        root_a = temp_path / "music-a"
        root_b = temp_path / "music-b"
        apples_path = root_a / "The Apples" / "Red" / "01.mp3"
        ambient_path = root_a / "Brian Eno" / "Ambient 1" / "01.flac"
        green_path = root_b / "Brian Eno" / "Another Green World" / "01.flac"
        apples_path.parent.mkdir(parents=True)
        ambient_path.parent.mkdir(parents=True)
        green_path.parent.mkdir(parents=True)
        apples_path.write_bytes(b"apples")
        ambient_path.write_bytes(b"ambient")
        green_path.write_bytes(b"green")
        apples = TrackRecord(
            path=str(apples_path),
            root_position=0,
            file_type="mp3",
            artist="The Apples",
            album_artist="The Apples",
            album="Red",
            title="Red One",
            date="2024",
            genres=["Rock"],
            duration_seconds=30.0,
            album_artwork=TrackArtwork(mime_type="image/png", data=b"apples-cover"),
        )
        ambient = TrackRecord(
            path=str(ambient_path),
            root_position=0,
            file_type="flac",
            artist="Brian Eno",
            album_artist="Brian Eno",
            album="Ambient 1",
            title="1/1",
            date="1978",
            genres=["Ambient"],
            duration_seconds=70.0,
        )
        green = TrackRecord(
            path=str(green_path),
            root_position=1,
            file_type="flac",
            artist="Brian Eno",
            album_artist="Brian Eno",
            album="Another Green World",
            title="Sky Saw",
            date="1975",
            genres=["Electronic"],
            duration_seconds=80.0,
            album_artwork=TrackArtwork(mime_type="image/png", data=b"eno-cover"),
        )
        save_library(
            MusicLibrary(
                roots=[str(root_a), str(root_b)],
                tracks=[apples, ambient, green],
                supported_extensions=[".mp3", ".flac"],
                generated_at="2026-05-07T00:00:00+00:00",
            ),
            temp_path / "kukicha.sqlite",
        )
        return apples, ambient, green

    def save_split_artist_library(self, temp_path: Path) -> str:
        database = temp_path / "kukicha.sqlite"
        connection = connect_database(database)
        try:
            connection.execute(
                """
                INSERT INTO album_artist_split_mappings (
                    album_artist,
                    mapped_artists
                ) VALUES (?, ?)
                """,
                ("Brian Eno, Jon Hassell", "Brian Eno\nJon Hassell"),
            )
            connection.commit()
        finally:
            connection.close()

        root = temp_path / "music"
        path = root / "Brian Eno and Jon Hassell" / "Fourth World" / "01.flac"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"fourth-world")
        album_id = "brian-eno-jon-hassell::fourth-world-vol-1-possible-musics"
        save_library(
            MusicLibrary(
                roots=[str(root)],
                tracks=[
                    TrackRecord(
                        path=str(path),
                        root_position=0,
                        file_type="flac",
                        artist="Brian Eno",
                        album_artist="Brian Eno, Jon Hassell",
                        album="Fourth World, Vol. 1: Possible Musics",
                        title="Chemistry",
                        date="1980",
                        genres=["Ambient"],
                        duration_seconds=90.0,
                    )
                ],
                supported_extensions=[".flac"],
                generated_at="2026-05-07T00:00:00+00:00",
            ),
            database,
        )
        return album_id

    def save_album_list_library(self, temp_path: Path) -> None:
        root = temp_path / "music"
        specs = (
            ("A Artist", "Zulu", "Rock", "Classic Rock"),
            ("B Artist", "Alpha", "Electronic", "Electronica"),
            ("C Artist", "Middle", "Jazz", "Modal"),
        )
        tracks = []
        for index, (artist, album, genre, style) in enumerate(specs, start=1):
            path = root / artist / album / "01.flac"
            path.parent.mkdir(parents=True)
            path.write_bytes(f"{artist}-{album}".encode("utf-8"))
            tracks.append(
                TrackRecord(
                    path=str(path),
                    root_position=0,
                    file_type="flac",
                    artist=artist,
                    album_artist=artist,
                    album=album,
                    title=f"Track {index}",
                    genres=[genre],
                    styles=[style],
                    duration_seconds=30.0 + index,
                )
            )
        save_library(
            MusicLibrary(
                roots=[str(root)],
                tracks=tracks,
                supported_extensions=[".flac"],
                generated_at="2026-05-07T00:00:00+00:00",
            ),
            temp_path / "kukicha.sqlite",
        )

    def save_genre_library(self, temp_path: Path) -> None:
        root = temp_path / "music"
        specs = (
            ("Artist", "Alpha", "01.flac", "Electronic", "Electronica"),
            ("Artist", "Alpha", "02.flac", "Electronic", "Electronica"),
            ("Artist", "Beta", "01.flac", "Rock", ""),
            ("Artist", "Gamma", "01.flac", "Ambient", ""),
        )
        tracks = []
        for artist, album, filename, genre, style in specs:
            path = root / artist / album / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"{album}-{filename}".encode("utf-8"))
            tracks.append(
                TrackRecord(
                    path=str(path),
                    root_position=0,
                    file_type="flac",
                    artist=artist,
                    album_artist=artist,
                    album=album,
                    title=filename,
                    genres=[genre],
                    styles=[style] if style else [],
                )
            )
        save_library(
            MusicLibrary(
                roots=[str(root)],
                tracks=tracks,
                supported_extensions=[".flac"],
                generated_at="2026-05-07T00:00:00+00:00",
            ),
            temp_path / "kukicha.sqlite",
        )

    def album_list2_names(
        self,
        temp_path: Path,
        *,
        album_list_type: str,
        **params: str,
    ) -> list[str]:
        app = create_player_app(self.make_options(temp_path))
        response = app.test_client().get(
            "/rest/getAlbumList2",
            query_string={
                **self.auth_params(),
                "type": album_list_type,
                **params,
            },
        )
        return [
            album["name"]
            for album in subsonic_payload(response)["albumList2"]["album"]
        ]

    def album_starred_at(self, temp_path: Path, album_id: str) -> str | None:
        with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
            row = connection.execute(
                """
                SELECT starred_at
                FROM library_albums
                WHERE album_id = ?
                """,
                (album_id,),
            ).fetchone()
        return str(row["starred_at"]) if row is not None and row["starred_at"] else None

    def test_get_open_subsonic_extensions_is_public_and_advertises_form_post(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getOpenSubsonicExtensions",
                query_string={"f": "json"},
            )

        self.assertEqual(response.status_code, 200)
        payload = subsonic_payload(response)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(
            payload["openSubsonicExtensions"],
            [{"name": "formPost", "versions": [1]}],
        )

    def test_root_probe_routes_return_plain_success(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            root_response = client.get("/")
            rest_response = client.get("/rest")
            rest_slash_response = client.get("/rest/")

        self.assertEqual(root_response.status_code, 302)
        for response in (rest_response, rest_slash_response):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_type, "text/plain; charset=utf-8")
            self.assertEqual(response.data, b"kukicha OpenSubsonic\n")

    def test_ping_accepts_password_auth_get_and_form_post(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            get_response = client.get("/rest/ping.view", query_string=self.auth_params())
            post_response = client.post("/rest/ping", data=self.auth_params())

        self.assertEqual(subsonic_payload(get_response)["status"], "ok")
        self.assertEqual(subsonic_payload(post_response)["status"], "ok")

    def test_authenticated_requests_track_clients_with_throttle(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            database = temp_path / "kukicha.sqlite"
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            first_response = client.get(
                "/rest/ping",
                query_string={**self.auth_params(), "c": "alpha-client"},
            )
            with connect_database(database, create=False) as connection:
                first_seen = str(
                    connection.execute(
                        """
                        SELECT last_seen_at
                        FROM opensubsonic_clients
                        WHERE client_name = ?
                        """,
                        ("alpha-client",),
                    ).fetchone()["last_seen_at"]
                )

            second_response = client.get(
                "/rest/getLicense",
                query_string={**self.auth_params(), "c": "alpha-client"},
            )
            invalid_auth_response = client.get(
                "/rest/ping",
                query_string={
                    **self.auth_params(password="wrong"),
                    "c": "bad-client",
                },
            )
            handler_error_response = client.get(
                "/rest/getSong",
                query_string={**self.auth_params(), "c": "error-client"},
            )
            extension_response = client.get(
                "/rest/getOpenSubsonicExtensions",
                query_string={
                    "v": "1.16.1",
                    "c": "extension-client",
                    "f": "json",
                },
            )
            authenticated_extension_response = client.get(
                "/rest/getOpenSubsonicExtensions",
                query_string={**self.auth_params(), "c": "authenticated-extension-client"},
            )
            with connect_database(database, create=False) as connection:
                throttled_seen = str(
                    connection.execute(
                        """
                        SELECT last_seen_at
                        FROM opensubsonic_clients
                        WHERE client_name = ?
                        """,
                        ("alpha-client",),
                    ).fetchone()["last_seen_at"]
                )
                error_client_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM opensubsonic_clients
                        WHERE client_name = ?
                        """,
                        ("error-client",),
                    ).fetchone()["count"]
                )
                bad_client_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM opensubsonic_clients
                        WHERE client_name = ?
                        """,
                        ("bad-client",),
                    ).fetchone()["count"]
                )
                extension_client_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM opensubsonic_clients
                        WHERE client_name = ?
                        """,
                        ("extension-client",),
                    ).fetchone()["count"]
                )
                authenticated_extension_client_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) AS count
                        FROM opensubsonic_clients
                        WHERE client_name = ?
                        """,
                        ("authenticated-extension-client",),
                    ).fetchone()["count"]
                )
                connection.execute(
                    """
                    UPDATE opensubsonic_clients
                    SET last_seen_at = ?
                    WHERE client_name = ?
                    """,
                    ("2000-01-01T00:00:00+00:00", "alpha-client"),
                )

            third_response = client.get(
                "/rest/ping",
                query_string={**self.auth_params(), "c": "alpha-client"},
            )
            with connect_database(database, create=False) as connection:
                refreshed_seen = str(
                    connection.execute(
                        """
                        SELECT last_seen_at
                        FROM opensubsonic_clients
                        WHERE client_name = ?
                        """,
                        ("alpha-client",),
                    ).fetchone()["last_seen_at"]
                )

        self.assertEqual(subsonic_payload(first_response)["status"], "ok")
        self.assertEqual(subsonic_payload(second_response)["status"], "ok")
        self.assertEqual(subsonic_payload(invalid_auth_response)["status"], "failed")
        self.assertEqual(subsonic_payload(handler_error_response)["status"], "failed")
        self.assertEqual(subsonic_payload(extension_response)["status"], "ok")
        self.assertEqual(subsonic_payload(authenticated_extension_response)["status"], "ok")
        self.assertEqual(subsonic_payload(third_response)["status"], "ok")
        self.assertEqual(throttled_seen, first_seen)
        self.assertEqual(error_client_count, 1)
        self.assertEqual(bad_client_count, 0)
        self.assertEqual(extension_client_count, 0)
        self.assertEqual(authenticated_extension_client_count, 1)
        self.assertNotEqual(refreshed_seen, "2000-01-01T00:00:00+00:00")

    def test_mount_prefix_offsets_rest_endpoints(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(
                self.make_options(temp_path, mount_prefix="/sonic")
            )

            response = app.test_client().get(
                "/sonic/rest/ping",
                query_string=self.auth_params(),
            )

        self.assertEqual(subsonic_payload(response)["status"], "ok")

    def test_ping_defaults_to_subsonic_xml_when_format_is_omitted(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))
            params = dict(self.auth_params())
            params.pop("f")

            response = app.test_client().get("/rest/ping.view", query_string=params)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "text/xml; charset=utf-8")
        self.assertIn(b'<subsonic-response xmlns="http://subsonic.org/restapi"', response.data)
        self.assertIn(b'status="ok"', response.data)
        self.assertIn(b'version="1.16.1"', response.data)

    def test_ping_accepts_token_auth(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(
                self.make_options(temp_path, username="listener", password="secret")
            )
            salt = "pepper"
            token = hashlib.md5("secretpepper".encode("utf-8")).hexdigest()

            response = app.test_client().get(
                "/rest/ping",
                query_string={
                    "u": "listener",
                    "t": token,
                    "s": salt,
                    "v": "1.16.1",
                    "c": "kukicha-test",
                    "f": "json",
                },
            )

        self.assertEqual(subsonic_payload(response)["status"], "ok")

    def test_auth_errors_use_subsonic_failed_payloads(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            missing_response = client.get("/rest/ping", query_string={"f": "json"})
            wrong_response = client.get(
                "/rest/ping",
                query_string=self.auth_params(password="wrong"),
            )
            api_key_response = client.get(
                "/rest/ping",
                query_string={
                    "u": "guest",
                    "apiKey": "abc",
                    "v": "1.16.1",
                    "c": "kukicha-test",
                    "f": "json",
                },
            )
            conflicting_response = client.get(
                "/rest/ping",
                query_string={
                    **self.auth_params(),
                    "t": "abc",
                    "s": "salt",
                },
            )

        self.assertEqual(subsonic_payload(missing_response)["error"]["code"], 10)
        self.assertEqual(subsonic_payload(wrong_response)["error"]["code"], 40)
        self.assertEqual(subsonic_payload(api_key_response)["error"]["code"], 42)
        self.assertEqual(subsonic_payload(conflicting_response)["error"]["code"], 43)

    def test_explicit_xml_format_returns_xml_success(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/ping",
                query_string={**self.auth_params(), "f": "xml"},
            )

        self.assertEqual(response.content_type, "text/xml; charset=utf-8")
        self.assertIn(b'status="ok"', response.data)

    def test_unsupported_format_and_endpoint_return_failed_payloads(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            jsonp_response = client.get(
                "/rest/ping",
                query_string={**self.auth_params(), "f": "jsonp"},
            )
            endpoint_response = client.get(
                "/rest/search3",
                query_string=self.auth_params(),
            )

        self.assertEqual(jsonp_response.content_type, "application/json; charset=utf-8")
        self.assertEqual(subsonic_payload(jsonp_response)["status"], "failed")
        self.assertEqual(subsonic_payload(jsonp_response)["error"]["code"], 0)
        self.assertEqual(subsonic_payload(endpoint_response)["status"], "failed")
        self.assertEqual(subsonic_payload(endpoint_response)["error"]["code"], 0)

    def test_browsing_endpoints_return_roots_albums_and_songs(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, second = self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            folders_response = client.get(
                "/rest/getMusicFolders",
                query_string=self.auth_params(),
            )
            album_list_response = client.get(
                "/rest/getAlbumList2",
                query_string={
                    **self.auth_params(),
                    "type": "alphabeticalByArtist",
                    "size": "1",
                    "offset": "1",
                },
            )
            album_response = client.get(
                "/rest/getAlbum",
                query_string={**self.auth_params(), "id": "artist::first-album"},
            )
            song_response = client.get(
                "/rest/getSong",
                query_string={**self.auth_params(), "id": str(first.track_id)},
            )

        folders = subsonic_payload(folders_response)["musicFolders"]["musicFolder"]
        self.assertEqual(folders, [{"id": 0, "name": "music"}])

        album_list = subsonic_payload(album_list_response)["albumList2"]["album"]
        self.assertEqual(len(album_list), 1)
        self.assertEqual(album_list[0]["id"], "artist::second-album")
        self.assertEqual(album_list[0]["name"], "Second Album")

        album = subsonic_payload(album_response)["album"]
        self.assertEqual(album["id"], "artist::first-album")
        self.assertEqual(album["name"], "First Album")
        self.assertEqual(album["artist"], "Artist")
        self.assertEqual(album["coverArt"], "album:artist::first-album")
        self.assertEqual(album["songCount"], 1)
        self.assertEqual(album["song"][0]["id"], str(first.track_id))
        self.assertEqual(album["song"][0]["track"], 1)
        self.assertEqual(album["song"][0]["discNumber"], 1)
        self.assertEqual(album["song"][0]["bitRate"], 128)

        song = subsonic_payload(song_response)["song"]
        self.assertEqual(song["id"], str(first.track_id))
        self.assertEqual(song["albumId"], "artist::first-album")
        self.assertEqual(song["title"], "First Track")
        self.assertEqual(song["size"], 10)
        self.assertEqual(second.track_id, 2)

    def test_get_album_uses_stored_song_size_when_path_is_not_local(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            root = temp_path / "music"
            path = root / "Artist" / "Stored Size Album" / "01.flac"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(path),
                            root_position=0,
                            file_size_bytes=12345,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Stored Size Album",
                            title="Stored Size Track",
                            track_number="1",
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-07T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getAlbum.view",
                query_string={
                    "u": "guest",
                    "p": "guest",
                    "v": "1.13.0",
                    "c": "Amperfy",
                    "id": "artist::stored-size-album",
                },
            )

        self.assertEqual(response.content_type, "text/xml; charset=utf-8")
        self.assertIn(b"<song ", response.data)
        self.assertIn(b'size="12345"', response.data)

    def test_create_playlist_creates_and_replaces_manual_playlist(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, second = self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            create_query = list(self.auth_params().items()) + [
                ("name", "Road Mix"),
                ("songId", str(first.track_id)),
                ("songId", str(second.track_id)),
            ]
            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-05-08T12:00:00+00:00",
            ):
                create_response = client.get("/rest/createPlaylist", query_string=create_query)
            replace_query = list(self.auth_params().items()) + [
                ("playlistId", "1"),
                ("name", "Road Mix Updated"),
                ("songId", str(second.track_id)),
            ]
            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-05-08T13:00:00+00:00",
            ):
                replace_response = client.get("/rest/createPlaylist", query_string=replace_query)
            playlists_response = client.get(
                "/rest/getPlaylists",
                query_string=self.auth_params(),
            )
            playlist_response = client.get(
                "/rest/getPlaylist",
                query_string={**self.auth_params(), "id": "1"},
            )
            cover_response = client.get(
                "/rest/getCoverArt",
                query_string={**self.auth_params(), "id": "playlist:1"},
            )
            with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
                playlist_row = connection.execute(
                    """
                    SELECT name, source, kind, created_at, updated_at, cover_svg
                    FROM library_playlists
                    WHERE playlist_id = 1
                    """
                ).fetchone()

        created = subsonic_payload(create_response)["playlist"]
        replaced = subsonic_payload(replace_response)["playlist"]
        playlists = subsonic_payload(playlists_response)["playlists"]["playlist"]
        playlist = subsonic_payload(playlist_response)["playlist"]

        self.assertEqual(created["id"], "1")
        self.assertEqual(created["name"], "Road Mix")
        self.assertEqual(replaced["id"], "1")
        self.assertEqual(replaced["name"], "Road Mix Updated")
        self.assertEqual(replaced["songCount"], 1)
        self.assertEqual(playlists[0]["name"], "Road Mix Updated")
        self.assertEqual(playlist["entry"][0]["id"], str(second.track_id))
        self.assertEqual(str(playlist_row["source"]), "manual")
        self.assertEqual(str(playlist_row["kind"]), "local")
        self.assertEqual(str(playlist_row["created_at"]), "2026-05-08T12:00:00+00:00")
        self.assertEqual(str(playlist_row["updated_at"]), "2026-05-08T13:00:00+00:00")
        self.assertIn("Road Mix Updated", str(playlist_row["cover_svg"]))
        self.assertEqual(cover_response.content_type, "image/png")
        self.assertTrue(cover_response.data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_update_and_delete_playlist_mutate_manual_playlist(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, second = self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            create_query = list(self.auth_params().items()) + [
                ("name", "Road Mix"),
                ("songId", str(first.track_id)),
                ("songId", str(second.track_id)),
            ]
            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-05-08T12:00:00+00:00",
            ):
                client.get("/rest/createPlaylist", query_string=create_query)
            update_query = list(self.auth_params().items()) + [
                ("playlistId", "1"),
                ("name", "Road Mix Edited"),
                ("songIndexToRemove", "0"),
                ("songIdToAdd", str(first.track_id)),
            ]
            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-05-08T13:00:00+00:00",
            ):
                update_response = client.get("/rest/updatePlaylist", query_string=update_query)
            playlist_response = client.get(
                "/rest/getPlaylist",
                query_string={**self.auth_params(), "id": "1"},
            )
            delete_response = client.get(
                "/rest/deletePlaylist",
                query_string={**self.auth_params(), "id": "1"},
            )
            playlists_response = client.get(
                "/rest/getPlaylists",
                query_string=self.auth_params(),
            )
            with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
                playlist_count = int(
                    connection.execute("SELECT COUNT(*) FROM library_playlists").fetchone()[0]
                )
                item_count = int(
                    connection.execute("SELECT COUNT(*) FROM library_playlist_items").fetchone()[0]
                )

        self.assertEqual(subsonic_payload(update_response)["status"], "ok")
        playlist = subsonic_payload(playlist_response)["playlist"]
        self.assertEqual(playlist["name"], "Road Mix Edited")
        self.assertEqual(playlist["changed"], "2026-05-08T13:00:00+00:00")
        self.assertEqual(
            [entry["id"] for entry in playlist["entry"]],
            [str(second.track_id), str(first.track_id)],
        )
        self.assertEqual(subsonic_payload(delete_response)["status"], "ok")
        self.assertEqual(
            subsonic_payload(playlists_response)["playlists"]["playlist"],
            [],
        )
        self.assertEqual(playlist_count, 0)
        self.assertEqual(item_count, 0)

    def test_get_playlist_cover_art_supports_bare_playlist_id_fallback(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-08T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            client.get(
                "/rest/createPlaylist",
                query_string={**self.auth_params(), "name": "Empty Road Mix"},
            )

            playlists_response = client.get(
                "/rest/getPlaylists",
                query_string=self.auth_params(),
            )
            cover_response = client.get(
                "/rest/getCoverArt",
                query_string={**self.auth_params(), "id": "1", "size": "512"},
            )

        playlist = subsonic_payload(playlists_response)["playlists"]["playlist"][0]
        self.assertEqual(playlist["coverArt"], "playlist:1")
        self.assertEqual(cover_response.content_type, "image/png")
        self.assertTrue(cover_response.data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_get_internet_radio_stations_returns_external_http_items(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            root = temp_path / "music"
            local_track_path = root / "Artist" / "Album" / "01.flac"
            remote_track_path = "https://cdn.example.test/library-track.mp3"
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=[
                        TrackRecord(
                            path=str(local_track_path),
                            root_position=0,
                            file_type="flac",
                            artist="Artist",
                            album_artist="Artist",
                            album="Album",
                            title="Local Track",
                        ),
                        TrackRecord(
                            path=remote_track_path,
                            root_position=0,
                            file_type="mp3",
                            artist="Artist",
                            album_artist="Artist",
                            album="Remote Album",
                            title="Tracked Remote",
                        ),
                    ],
                    playlists=[
                        PlaylistRecord(
                            name="Imported Streams",
                            source="file_import",
                            items=[
                                PlaylistItemRecord(
                                    path="http://example.test/imported-one",
                                    title="Imported One",
                                ),
                                PlaylistItemRecord(
                                    path="HTTPS://example.test/imported-two",
                                    title="Imported Two",
                                ),
                                PlaylistItemRecord(
                                    path="s3+https://bucket.example.test/object",
                                    title="Remote Object",
                                ),
                            ],
                        ),
                        PlaylistRecord(
                            name="Manual Station",
                            source="manual",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/manual-live",
                                    title="Manual Live",
                                )
                            ],
                        ),
                        PlaylistRecord(
                            name="Tracked Remote",
                            source="manual",
                            items=[PlaylistItemRecord(path=remote_track_path)],
                        ),
                        PlaylistRecord(
                            name="Local Mix",
                            source="manual",
                            items=[PlaylistItemRecord(path=str(local_track_path))],
                        ),
                    ],
                    supported_extensions=[".flac", ".mp3"],
                    generated_at="2026-05-08T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getInternetRadioStations",
                query_string=self.auth_params(),
            )

        stations = subsonic_payload(response)["internetRadioStations"][
            "internetRadioStation"
        ]
        self.assertEqual(
            [station["name"] for station in stations],
            ["Imported One", "Imported Two", "Manual Live"],
        )
        self.assertEqual(
            [station["streamUrl"] for station in stations],
            [
                "http://example.test/imported-one",
                "HTTPS://example.test/imported-two",
                "https://example.test/manual-live",
            ],
        )
        self.assertEqual(
            [station["coverArt"] for station in stations],
            [
                "internetRadioStation:1",
                "internetRadioStation:2",
                "internetRadioStation:4",
            ],
        )

    def test_internet_radio_station_create_update_and_delete(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-08T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            create_query = {
                **self.auth_params(),
                "name": "A Deep Space",
                "streamUrl": "https://example.test/live",
                "homepageUrl": "https://ignored.example.test",
            }
            duplicate_query = {
                **self.auth_params(),
                "name": "B Deep Space",
                "streamUrl": "https://example.test/live",
            }
            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-05-08T12:00:00+00:00",
            ):
                create_response = client.get(
                    "/rest/createInternetRadioStation",
                    query_string=create_query,
                )
                duplicate_response = client.get(
                    "/rest/createInternetRadioStation",
                    query_string=duplicate_query,
                )
            with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
                connection.execute(
                    """
                    UPDATE library_playlist_items
                    SET duration_seconds = ?,
                        duration_is_indeterminate = ?,
                        genre = ?,
                        cover_url = ?
                    WHERE playlist_item_id = ?
                    """,
                    (
                        42,
                        0,
                        "Ambient",
                        "https://example.test/cover.jpg",
                        1,
                    ),
                )
                connection.commit()

            with patch(
                "kukicha.use_case.commands.playlists.utc_now_iso",
                return_value="2026-05-08T13:00:00+00:00",
            ):
                update_response = client.get(
                    "/rest/updateInternetRadioStation",
                    query_string={
                        **self.auth_params(),
                        "id": "1",
                        "name": "A Deep Space Edited",
                        "streamUrl": "http://example.test/edited",
                    },
                )
            stations_response = client.get(
                "/rest/getInternetRadioStations",
                query_string=self.auth_params(),
            )
            cover_response = client.get(
                "/rest/getCoverArt",
                query_string={
                    **self.auth_params(),
                    "id": "internetRadioStation:1",
                    "size": "512",
                },
            )
            bare_station_cover_response = client.get(
                "/rest/getCoverArt",
                query_string={**self.auth_params(), "id": "1", "size": "512"},
            )
            with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
                updated_playlist = connection.execute(
                    """
                    SELECT name, kind, source, created_at, updated_at, cover_svg
                    FROM library_playlists
                    WHERE playlist_id = 1
                    """
                ).fetchone()
                updated_item = connection.execute(
                    """
                    SELECT
                        path,
                        title,
                        duration_seconds,
                        duration_is_indeterminate,
                        genre,
                        cover_url
                    FROM library_playlist_items
                    WHERE playlist_item_id = 1
                    """
                ).fetchone()
            delete_first_response = client.get(
                "/rest/deleteInternetRadioStation",
                query_string={**self.auth_params(), "id": "1"},
            )
            remaining_response = client.get(
                "/rest/getInternetRadioStations",
                query_string=self.auth_params(),
            )
            delete_second_response = client.get(
                "/rest/deleteInternetRadioStation",
                query_string={**self.auth_params(), "id": "2"},
            )
            empty_response = client.get(
                "/rest/getInternetRadioStations",
                query_string=self.auth_params(),
            )
            with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
                playlist_count = int(
                    connection.execute("SELECT COUNT(*) FROM library_playlists").fetchone()[0]
                )
                item_count = int(
                    connection.execute("SELECT COUNT(*) FROM library_playlist_items").fetchone()[0]
                )

        self.assertEqual(subsonic_payload(create_response)["status"], "ok")
        self.assertEqual(subsonic_payload(duplicate_response)["status"], "ok")
        self.assertEqual(subsonic_payload(update_response)["status"], "ok")
        stations = subsonic_payload(stations_response)["internetRadioStations"][
            "internetRadioStation"
        ]
        self.assertEqual([station["id"] for station in stations], ["1", "2"])
        self.assertEqual(
            [station["name"] for station in stations],
            ["A Deep Space Edited", "B Deep Space"],
        )
        self.assertEqual(
            [station["streamUrl"] for station in stations],
            ["http://example.test/edited", "https://example.test/live"],
        )
        self.assertEqual(
            [station["coverArt"] for station in stations],
            ["internetRadioStation:1", "internetRadioStation:2"],
        )
        self.assertEqual(cover_response.content_type, "image/png")
        self.assertTrue(cover_response.data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(bare_station_cover_response.content_type, "image/png")
        self.assertTrue(bare_station_cover_response.data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(str(updated_playlist["name"]), "A Deep Space Edited")
        self.assertEqual(str(updated_playlist["kind"]), "remote")
        self.assertEqual(str(updated_playlist["source"]), "manual")
        self.assertEqual(str(updated_playlist["created_at"]), "2026-05-08T12:00:00+00:00")
        self.assertEqual(str(updated_playlist["updated_at"]), "2026-05-08T13:00:00+00:00")
        self.assertIn("A Deep Space Edited", str(updated_playlist["cover_svg"]))
        self.assertEqual(str(updated_item["path"]), "http://example.test/edited")
        self.assertEqual(str(updated_item["title"]), "A Deep Space Edited")
        self.assertEqual(float(updated_item["duration_seconds"]), 42.0)
        self.assertEqual(int(updated_item["duration_is_indeterminate"]), 0)
        self.assertEqual(str(updated_item["genre"]), "Ambient")
        self.assertEqual(str(updated_item["cover_url"]), "https://example.test/cover.jpg")
        self.assertEqual(subsonic_payload(delete_first_response)["status"], "ok")
        remaining_stations = subsonic_payload(remaining_response)["internetRadioStations"][
            "internetRadioStation"
        ]
        self.assertEqual([station["id"] for station in remaining_stations], ["2"])
        self.assertEqual(subsonic_payload(delete_second_response)["status"], "ok")
        self.assertEqual(
            subsonic_payload(empty_response)["internetRadioStations"]["internetRadioStation"],
            [],
        )
        self.assertEqual(playlist_count, 0)
        self.assertEqual(item_count, 0)

    def test_internet_radio_station_rejects_non_http_stream_urls(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-08T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            valid_response = client.get(
                "/rest/createInternetRadioStation",
                query_string={
                    **self.auth_params(),
                    "name": "Valid",
                    "streamUrl": "https://example.test/live",
                },
            )
            create_response = client.get(
                "/rest/createInternetRadioStation",
                query_string={
                    **self.auth_params(),
                    "name": "Invalid",
                    "streamUrl": "ftp://example.test/live",
                },
            )
            update_response = client.get(
                "/rest/updateInternetRadioStation",
                query_string={
                    **self.auth_params(),
                    "id": "1",
                    "name": "Still Valid",
                    "streamUrl": "file:///tmp/live.mp3",
                },
            )
            with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
                rows = list(
                    connection.execute(
                        """
                        SELECT playlists.name, items.path, items.title
                        FROM library_playlist_items AS items
                        JOIN library_playlists AS playlists
                            ON playlists.playlist_id = items.playlist_id
                        """
                    )
                )

        self.assertEqual(subsonic_payload(valid_response)["status"], "ok")
        self.assertEqual(subsonic_payload(create_response)["status"], "failed")
        self.assertEqual(subsonic_payload(update_response)["status"], "failed")
        self.assertEqual(
            subsonic_payload(create_response)["error"]["message"],
            "Internet radio station streamUrl must be an HTTP or HTTPS URL.",
        )
        self.assertEqual(
            subsonic_payload(update_response)["error"]["message"],
            "Internet radio station streamUrl must be an HTTP or HTTPS URL.",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(str(rows[0]["name"]), "Valid")
        self.assertEqual(str(rows[0]["path"]), "https://example.test/live")
        self.assertEqual(str(rows[0]["title"]), "Valid")

    def test_internet_radio_station_file_import_mutations_are_read_only(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            save_library(
                MusicLibrary(
                    roots=[],
                    tracks=[],
                    playlists=[
                        PlaylistRecord(
                            name="Imported Streams",
                            source="file_import",
                            items=[
                                PlaylistItemRecord(
                                    path="https://example.test/imported",
                                    title="Imported Stream",
                                )
                            ],
                        )
                    ],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-08T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            stations_response = client.get(
                "/rest/getInternetRadioStations",
                query_string=self.auth_params(),
            )
            station_id = subsonic_payload(stations_response)["internetRadioStations"][
                "internetRadioStation"
            ][0]["id"]

            update_response = client.get(
                "/rest/updateInternetRadioStation",
                query_string={
                    **self.auth_params(),
                    "id": station_id,
                    "name": "Changed",
                    "streamUrl": "https://example.test/changed",
                },
            )
            delete_response = client.get(
                "/rest/deleteInternetRadioStation",
                query_string={**self.auth_params(), "id": station_id},
            )
            with connect_database(temp_path / "kukicha.sqlite", create=False) as connection:
                row = connection.execute(
                    """
                    SELECT playlists.name, items.path, items.title
                    FROM library_playlist_items AS items
                    JOIN library_playlists AS playlists
                        ON playlists.playlist_id = items.playlist_id
                    """
                ).fetchone()

        self.assertEqual(subsonic_payload(update_response)["status"], "failed")
        self.assertEqual(subsonic_payload(delete_response)["status"], "failed")
        self.assertEqual(
            subsonic_payload(update_response)["error"]["message"],
            "Internet radio stations imported from playlist files are read-only.",
        )
        self.assertEqual(
            subsonic_payload(delete_response)["error"]["message"],
            "Internet radio stations imported from playlist files are read-only.",
        )
        self.assertEqual(str(row["name"]), "Imported Streams")
        self.assertEqual(str(row["path"]), "https://example.test/imported")
        self.assertEqual(str(row["title"]), "Imported Stream")

    def test_get_genres_returns_album_genre_and_style_counts(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_genre_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getGenres",
                query_string=self.auth_params(),
            )

        genres = subsonic_payload(response)["genres"]["genre"]
        self.assertEqual(
            genres,
            [
                {"value": "Ambient", "songCount": 1, "albumCount": 1},
                {"value": "Electronic", "songCount": 2, "albumCount": 1},
                {"value": "Electronica", "songCount": 2, "albumCount": 1},
                {"value": "Rock", "songCount": 1, "albumCount": 1},
            ],
        )

    def test_get_genres_returns_empty_for_empty_library(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            save_library(
                MusicLibrary(
                    roots=[str(temp_path / "music")],
                    tracks=[],
                    supported_extensions=[".flac"],
                    generated_at="2026-05-07T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getGenres",
                query_string=self.auth_params(),
            )

        self.assertEqual(subsonic_payload(response)["genres"]["genre"], [])

    def test_get_genres_supports_xml_response(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_genre_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getGenres",
                query_string={**self.auth_params(), "f": "xml"},
            )

        self.assertEqual(response.content_type, "text/xml; charset=utf-8")
        self.assertIn(b"<genres>", response.data)
        self.assertIn(
            b'<genre value="Electronic" songCount="2" albumCount="1"/>',
            response.data,
        )
        self.assertIn(
            b'<genre value="Electronica" songCount="2" albumCount="1"/>',
            response.data,
        )

    def test_scrobble_tracks_now_playing_and_submitted_play_counts(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, second = self.save_sample_library(temp_path)
            database = temp_path / "kukicha.sqlite"
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            now_response = client.get(
                "/rest/scrobble",
                query_string={
                    **self.auth_params(),
                    "id": str(first.track_id),
                    "time": "1770000000000",
                    "submission": "false",
                },
            )
            with connect_database(database, create=False) as connection:
                now_playing_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM play_now_playing"
                    ).fetchone()["count"]
                )
                submitted_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM play_track_stats"
                    ).fetchone()["count"]
                )

            submit_response = client.get(
                "/rest/scrobble",
                query_string=[
                    *self.auth_params().items(),
                    ("id", str(first.track_id)),
                    ("id", str(second.track_id)),
                    ("time", "1770000600000"),
                    ("time", "1770001200000"),
                    ("submission", "true"),
                ],
            )
            other_client_now_response = client.get(
                "/rest/scrobble",
                query_string={
                    **self.auth_params(),
                    "c": "some-client",
                    "id": str(second.track_id),
                    "time": "1770001800000",
                    "submission": "false",
                },
            )
            xml_response = client.get(
                "/rest/scrobble.view",
                query_string={
                    **self.auth_params(),
                    "f": "xml",
                    "id": str(first.track_id),
                    "submission": "false",
                },
            )
            missing_response = client.get(
                "/rest/scrobble",
                query_string=self.auth_params(),
            )
            invalid_time_response = client.get(
                "/rest/scrobble",
                query_string={
                    **self.auth_params(),
                    "id": str(first.track_id),
                    "time": "not-a-timestamp",
                },
            )

            expected_first_play = datetime.fromtimestamp(
                1770000600000 / 1000,
                tz=UTC,
            ).isoformat()
            expected_second_play = datetime.fromtimestamp(
                1770001200000 / 1000,
                tz=UTC,
            ).isoformat()
            with connect_database(database, create=False) as connection:
                track_stats = {
                    int(row["track_id"]): (int(row["play_count"]), str(row["last_played_at"]))
                    for row in connection.execute(
                        """
                        SELECT track_id, play_count, last_played_at
                        FROM play_track_stats
                        ORDER BY track_id
                        """
                    )
                }
                album_stats = {
                    str(row["album_id"]): int(row["play_count"])
                    for row in connection.execute(
                        "SELECT album_id, play_count FROM play_album_stats"
                    )
                }
                artist_row = connection.execute(
                    "SELECT artist, play_count, last_played_at FROM play_artist_stats"
                ).fetchone()
                genre_row = connection.execute(
                    "SELECT genre, play_count FROM play_genre_stats"
                ).fetchone()
                event_sources = [
                    str(row["source"])
                    for row in connection.execute(
                        "SELECT source FROM play_events ORDER BY play_event_id"
                    )
                ]
                final_now_playing_count = int(
                    connection.execute(
                        "SELECT COUNT(*) AS count FROM play_now_playing"
                    ).fetchone()["count"]
                )

        self.assertEqual(subsonic_payload(now_response)["status"], "ok")
        self.assertEqual(now_playing_count, 0)
        self.assertEqual(submitted_count, 0)
        self.assertEqual(subsonic_payload(submit_response)["status"], "ok")
        self.assertEqual(subsonic_payload(other_client_now_response)["status"], "ok")
        self.assertEqual(xml_response.content_type, "text/xml; charset=utf-8")
        self.assertIn(b'status="ok"', xml_response.data)
        self.assertEqual(subsonic_payload(missing_response)["status"], "failed")
        self.assertEqual(subsonic_payload(missing_response)["error"]["code"], 10)
        self.assertEqual(subsonic_payload(invalid_time_response)["status"], "failed")
        self.assertEqual(subsonic_payload(invalid_time_response)["error"]["code"], 10)
        self.assertEqual(
            track_stats,
            {
                first.track_id: (1, expected_first_play),
                second.track_id: (1, expected_second_play),
            },
        )
        self.assertEqual(event_sources, ["kukicha-test", "kukicha-test"])
        self.assertEqual(final_now_playing_count, 0)
        self.assertEqual(
            album_stats,
            {"artist::first-album": 1, "artist::second-album": 1},
        )
        self.assertEqual(artist_row["artist"], "Artist")
        self.assertEqual(int(artist_row["play_count"]), 2)
        self.assertEqual(artist_row["last_played_at"], expected_second_play)
        self.assertEqual(genre_row["genre"], "Electronic")
        self.assertEqual(int(genre_row["play_count"]), 1)

    def test_star_and_unstar_update_album_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            star_response = client.get(
                "/rest/star",
                query_string=[
                    *self.auth_params().items(),
                    ("albumId", "artist::first-album"),
                    ("albumId", "artist::second-album"),
                ],
            )
            first_starred_at = self.album_starred_at(temp_path, "artist::first-album")
            second_starred_at = self.album_starred_at(temp_path, "artist::second-album")

            unstar_response = client.get(
                "/rest/unstar",
                query_string={
                    **self.auth_params(),
                    "albumId": "artist::first-album",
                },
            )
            xml_unstar_response = client.get(
                "/rest/unstar.view",
                query_string={
                    **self.auth_params(),
                    "f": "xml",
                    "albumId": "artist::second-album",
                },
            )
            first_unstarred_at = self.album_starred_at(temp_path, "artist::first-album")
            second_unstarred_at = self.album_starred_at(temp_path, "artist::second-album")

        self.assertEqual(subsonic_payload(star_response)["status"], "ok")
        self.assertIsNotNone(first_starred_at)
        self.assertIsNotNone(second_starred_at)
        self.assertEqual(subsonic_payload(unstar_response)["status"], "ok")
        self.assertEqual(xml_unstar_response.content_type, "text/xml; charset=utf-8")
        self.assertIn(b'status="ok"', xml_unstar_response.data)
        self.assertIsNone(first_unstarred_at)
        self.assertIsNone(second_unstarred_at)

    def test_star_rejects_unsupported_targets_and_missing_album_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            id_response = client.get(
                "/rest/star",
                query_string={
                    **self.auth_params(),
                    "albumId": "artist::first-album",
                    "id": "1",
                },
            )
            artist_response = client.get(
                "/rest/unstar",
                query_string={
                    **self.auth_params(),
                    "albumId": "artist::second-album",
                    "artistId": "artist:Artist",
                },
            )
            missing_response = client.get(
                "/rest/star",
                query_string=self.auth_params(),
            )
            unknown_response = client.get(
                "/rest/star",
                query_string={
                    **self.auth_params(),
                    "albumId": "missing::album",
                },
            )
            first_starred_at = self.album_starred_at(temp_path, "artist::first-album")
            second_starred_at = self.album_starred_at(temp_path, "artist::second-album")

        self.assertEqual(subsonic_payload(id_response)["status"], "failed")
        self.assertEqual(subsonic_payload(id_response)["error"]["code"], 0)
        self.assertIn("id", subsonic_payload(id_response)["error"]["message"])
        self.assertEqual(subsonic_payload(artist_response)["status"], "failed")
        self.assertEqual(subsonic_payload(artist_response)["error"]["code"], 0)
        self.assertIn("artistId", subsonic_payload(artist_response)["error"]["message"])
        self.assertEqual(subsonic_payload(missing_response)["status"], "failed")
        self.assertEqual(subsonic_payload(missing_response)["error"]["code"], 10)
        self.assertEqual(subsonic_payload(unknown_response)["status"], "failed")
        self.assertEqual(subsonic_payload(unknown_response)["error"]["code"], 70)
        self.assertIsNone(first_starred_at)
        self.assertIsNone(second_starred_at)

    def test_album_list_requires_type(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getAlbumList2",
                query_string=self.auth_params(),
            )

        payload = subsonic_payload(response)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["code"], 10)

    def test_album_list2_uses_library_query_alphabetical_sorts(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_album_list_library(temp_path)

            by_name = self.album_list2_names(
                temp_path,
                album_list_type="alphabeticalByName",
            )
            by_artist = self.album_list2_names(
                temp_path,
                album_list_type="alphabeticalByArtist",
            )

        self.assertEqual(by_name, ["Alpha", "Middle", "Zulu"])
        self.assertEqual(by_artist, ["Zulu", "Alpha", "Middle"])

    def test_album_list2_includes_album_genre_for_client_side_filters(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_album_list_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getAlbumList2",
                query_string={
                    **self.auth_params(),
                    "type": "newest",
                    "size": "20",
                    "offset": "0",
                },
            )

        albums = subsonic_payload(response)["albumList2"]["album"]
        genres_by_album = {album["name"]: album.get("genre") for album in albums}
        self.assertEqual(genres_by_album["Alpha"], "Electronic")
        self.assertEqual(genres_by_album["Middle"], "Jazz")
        self.assertEqual(genres_by_album["Zulu"], "Rock")

    def test_album_list2_uses_library_query_recent_and_starred_sorts(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_album_list_library(temp_path)
            database = temp_path / "kukicha.sqlite"
            with connect_database(database, create=False) as connection:
                for album, added_at in (
                    ("Zulu", "2026-01-01T00:00:00+00:00"),
                    ("Alpha", "2026-03-01T00:00:00+00:00"),
                    ("Middle", "2026-02-01T00:00:00+00:00"),
                ):
                    connection.execute(
                        "UPDATE library_albums SET added_at = ? WHERE album = ?",
                        (added_at, album),
                    )
                for album, starred_at in (
                    ("Zulu", "2026-03-01T00:00:00+00:00"),
                    ("Middle", "2026-01-01T00:00:00+00:00"),
                ):
                    connection.execute(
                        "UPDATE library_albums SET starred_at = ? WHERE album = ?",
                        (starred_at, album),
                    )
                for album, play_count, last_played_at in (
                    ("Zulu", 9, "2026-01-01T00:00:00+00:00"),
                    ("Alpha", 1, "2026-03-01T00:00:00+00:00"),
                ):
                    connection.execute(
                        """
                        INSERT INTO play_album_stats (
                            album_id,
                            play_count,
                            last_played_at,
                            album,
                            artist
                        )
                        SELECT album_id, ?, ?, album, ''
                        FROM library_albums
                        WHERE album = ?
                        """,
                        (play_count, last_played_at, album),
                    )

            newest = self.album_list2_names(temp_path, album_list_type="newest")
            starred = self.album_list2_names(temp_path, album_list_type="starred")
            recent = self.album_list2_names(temp_path, album_list_type="recent")
            frequent = self.album_list2_names(temp_path, album_list_type="frequent")

        self.assertEqual(newest, ["Alpha", "Middle", "Zulu"])
        self.assertEqual(starred, ["Zulu", "Middle"])
        self.assertEqual(recent, ["Alpha", "Zulu"])
        self.assertEqual(frequent, ["Zulu", "Alpha"])

    def test_album_list2_uses_library_query_genre_filter(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_album_list_library(temp_path)

            by_genre = self.album_list2_names(
                temp_path,
                album_list_type="byGenre",
                genre="electronic",
            )
            missing_genre_response = create_player_app(
                self.make_options(temp_path)
            ).test_client().get(
                "/rest/getAlbumList2",
                query_string={**self.auth_params(), "type": "byGenre"},
            )

        self.assertEqual(by_genre, ["Alpha"])
        self.assertEqual(subsonic_payload(missing_genre_response)["status"], "failed")
        self.assertEqual(subsonic_payload(missing_genre_response)["error"]["code"], 10)

    def test_album_list2_by_genre_accepts_style_values(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_album_list_library(temp_path)

            by_style = self.album_list2_names(
                temp_path,
                album_list_type="byGenre",
                genre="electronica",
            )

        self.assertEqual(by_style, ["Alpha"])

    def test_album_list2_returns_empty_for_unsupported_types(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_album_list_library(temp_path)

            results = {
                album_list_type: self.album_list2_names(
                    temp_path,
                    album_list_type=album_list_type,
                )
                for album_list_type in ("random", "byYear", "highest", "not-a-type")
            }

        self.assertEqual(
            results,
            {
                "random": [],
                "byYear": [],
                "highest": [],
                "not-a-type": [],
            },
        )

    def test_album_list2_size_zero_returns_empty_without_genre(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_album_list_library(temp_path)

            names = self.album_list2_names(
                temp_path,
                album_list_type="byGenre",
                size="0",
            )

        self.assertEqual(names, [])

    def test_album_list2_collects_pages_above_library_query_page_limit(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            root = temp_path / "music"
            tracks = []
            for index in range(205):
                album = f"Album {index:03d}"
                path = root / "Artist" / album / "01.flac"
                path.parent.mkdir(parents=True)
                path.write_bytes(album.encode("utf-8"))
                tracks.append(
                    TrackRecord(
                        path=str(path),
                        root_position=0,
                        file_type="flac",
                        artist="Artist",
                        album_artist="Artist",
                        album=album,
                        title="Track",
                    )
                )
            save_library(
                MusicLibrary(
                    roots=[str(root)],
                    tracks=tracks,
                    supported_extensions=[".flac"],
                    generated_at="2026-05-07T00:00:00+00:00",
                ),
                temp_path / "kukicha.sqlite",
            )

            names = self.album_list2_names(
                temp_path,
                album_list_type="alphabeticalByName",
                size="205",
            )
            paged_names = self.album_list2_names(
                temp_path,
                album_list_type="alphabeticalByName",
                size="3",
                offset="201",
            )

        self.assertEqual(len(names), 205)
        self.assertEqual(names[0], "Album 000")
        self.assertEqual(names[-1], "Album 204")
        self.assertEqual(paged_names, ["Album 201", "Album 202", "Album 203"])

    def test_get_artists_groups_album_artists_and_reuses_album_cover_art(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_artist_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getArtists",
                query_string=self.auth_params(),
            )

        artists = subsonic_payload(response)["artists"]
        self.assertEqual(artists["ignoredArticles"], "The An A Die Das Ein Eine Les Le La")
        self.assertEqual([index["name"] for index in artists["index"]], ["A", "B"])
        self.assertEqual(
            artists["index"][0]["artist"],
            [
                {
                    "id": "artist:The Apples",
                    "name": "The Apples",
                    "coverArt": "album:the-apples::red",
                    "albumCount": 1,
                    "roles": ["albumartist"],
                }
            ],
        )
        self.assertEqual(
            artists["index"][1]["artist"],
            [
                {
                    "id": "artist:Brian Eno",
                    "name": "Brian Eno",
                    "coverArt": "album:brian-eno::another-green-world",
                    "albumCount": 2,
                    "roles": ["albumartist"],
                }
            ],
        )

    def test_get_artists_filters_by_music_folder_id(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_artist_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getArtists",
                query_string={**self.auth_params(), "musicFolderId": "0"},
            )

        indexes = subsonic_payload(response)["artists"]["index"]
        self.assertEqual([index["name"] for index in indexes], ["A", "B"])
        self.assertEqual(indexes[0]["artist"][0]["name"], "The Apples")
        self.assertEqual(indexes[0]["artist"][0]["albumCount"], 1)
        self.assertEqual(indexes[0]["artist"][0]["coverArt"], "album:the-apples::red")
        self.assertEqual(indexes[1]["artist"][0]["name"], "Brian Eno")
        self.assertEqual(indexes[1]["artist"][0]["albumCount"], 1)
        self.assertNotIn("coverArt", indexes[1]["artist"][0])

    def test_get_artist_accepts_prefixed_and_raw_artist_ids(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_artist_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()
            prefixed_response = client.get(
                "/rest/getArtist",
                query_string={**self.auth_params(), "id": "artist:Brian Eno"},
            )
            raw_response = client.get(
                "/rest/getArtist",
                query_string={**self.auth_params(), "id": "Brian Eno"},
            )

        artist = subsonic_payload(prefixed_response)["artist"]
        self.assertEqual(subsonic_payload(raw_response)["artist"], artist)
        self.assertEqual(artist["id"], "artist:Brian Eno")
        self.assertEqual(artist["name"], "Brian Eno")
        self.assertEqual(artist["albumCount"], 2)
        self.assertEqual(artist["coverArt"], "album:brian-eno::another-green-world")
        self.assertEqual(artist["roles"], ["albumartist"])
        self.assertEqual(
            [album["id"] for album in artist["album"]],
            ["brian-eno::ambient-1", "brian-eno::another-green-world"],
        )
        self.assertEqual(artist["album"][0]["parent"], "artist:Brian Eno")
        self.assertEqual(artist["album"][0]["title"], "Ambient 1")
        self.assertEqual(artist["album"][0]["album"], "Ambient 1")
        self.assertTrue(artist["album"][0]["isDir"])
        self.assertEqual(artist["album"][0]["duration"], 70)
        self.assertEqual(artist["album"][0]["genre"], "Ambient")

    def test_split_album_artists_are_exposed_in_opensubsonic_payloads(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            album_id = self.save_split_artist_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            artists_response = client.get(
                "/rest/getArtists",
                query_string=self.auth_params(),
            )
            artist_response = client.get(
                "/rest/getArtist",
                query_string={**self.auth_params(), "id": "artist:Brian Eno"},
            )
            album_response = client.get(
                "/rest/getAlbum",
                query_string={**self.auth_params(), "id": album_id},
            )

        indexes = subsonic_payload(artists_response)["artists"]["index"]
        artist_names = [
            artist["name"]
            for index in indexes
            for artist in index["artist"]
        ]
        self.assertEqual(artist_names, ["Brian Eno", "Jon Hassell"])

        artist = subsonic_payload(artist_response)["artist"]
        self.assertEqual(artist["id"], "artist:Brian Eno")
        self.assertEqual([album["id"] for album in artist["album"]], [album_id])
        self.assertEqual(artist["album"][0]["artist"], "Brian Eno, Jon Hassell")
        self.assertEqual(artist["album"][0]["artistId"], "artist:Brian Eno")
        self.assertEqual(
            artist["album"][0]["artists"],
            [
                {"id": "artist:Brian Eno", "name": "Brian Eno"},
                {"id": "artist:Jon Hassell", "name": "Jon Hassell"},
            ],
        )
        self.assertEqual(
            artist["album"][0]["displayArtist"],
            "Brian Eno, Jon Hassell",
        )

        album = subsonic_payload(album_response)["album"]
        self.assertEqual(album["artist"], "Brian Eno, Jon Hassell")
        self.assertEqual(album["artistId"], "artist:Brian Eno")
        self.assertEqual(album["song"][0]["albumArtist"], "Brian Eno, Jon Hassell")
        self.assertEqual(album["song"][0]["albumArtistId"], "artist:Brian Eno")
        self.assertEqual(album["song"][0]["albumArtists"], album["artists"])

    def test_get_artist_returns_not_found_for_missing_artist(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_artist_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getArtist",
                query_string={**self.auth_params(), "id": "artist:Missing"},
            )

        payload = subsonic_payload(response)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["code"], 70)

    def test_stream_supports_ranges_and_cover_art_returns_binary_images(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, _second = self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))
            client = app.test_client()

            stream_response = client.get(
                "/rest/stream",
                query_string={**self.auth_params(), "id": str(first.track_id)},
                headers={"Range": "bytes=1-3"},
            )
            raw_cover_response = client.get(
                "/rest/getCoverArt",
                query_string={**self.auth_params(), "id": str(first.track_id)},
            )
            album_cover_response = client.get(
                "/rest/getCoverArt.view",
                query_string={
                    **self.auth_params(),
                    "id": "album:artist::first-album",
                },
            )
            raw_album_cover_response = client.get(
                "/rest/getCoverArt.view",
                query_string={
                    **self.auth_params(),
                    "id": "artist::first-album",
                },
            )

        self.assertEqual(stream_response.status_code, 206)
        self.assertEqual(stream_response.headers["Content-Range"], "bytes 1-3/10")
        self.assertEqual(stream_response.data, b"123")
        self.assertEqual(raw_cover_response.status_code, 200)
        self.assertEqual(raw_cover_response.content_type, "image/png")
        self.assertEqual(raw_cover_response.data, b"album-cover")
        self.assertEqual(
            raw_cover_response.headers["Cache-Control"],
            "private, max-age=604800",
        )
        self.assertEqual(album_cover_response.status_code, 200)
        self.assertEqual(album_cover_response.data, b"album-cover")
        self.assertEqual(
            album_cover_response.headers["Cache-Control"],
            "private, max-age=604800",
        )
        self.assertEqual(raw_album_cover_response.status_code, 200)
        self.assertEqual(raw_album_cover_response.content_type, "image/png")
        self.assertEqual(raw_album_cover_response.data, b"album-cover")

    def test_download_returns_original_audio_as_attachment_with_range_support(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, _second = self.save_sample_library(temp_path)
            app = create_player_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/download.view",
                query_string={**self.auth_params(), "id": str(first.track_id)},
                headers={"Range": "bytes=2-5"},
            )

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content_type, "audio/mpeg")
        self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
        self.assertEqual(
            response.headers["Content-Disposition"],
            'attachment; filename="01.mp3"',
        )
        self.assertEqual(response.data, b"2345")


if __name__ == "__main__":
    unittest.main()

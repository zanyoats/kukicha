from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kukicha.models import MusicLibrary, TrackArtwork, TrackRecord
from kukicha.opensubsonic_web_adapter import create_open_subsonic_app
from kukicha.player_config import PlayerServerOptions
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
    ) -> PlayerServerOptions:
        return PlayerServerOptions(
            config_path=temp_path / "kukicha.toml",
            database=temp_path / "kukicha.sqlite",
            ffmpeg_path=None,
            open_subsonic_username=username,
            open_subsonic_password=password,
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

    def test_get_open_subsonic_extensions_is_public_and_advertises_form_post(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_open_subsonic_app(self.make_options(temp_path))

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
            app = create_open_subsonic_app(self.make_options(temp_path))
            client = app.test_client()

            root_response = client.get("/")
            rest_response = client.get("/rest")
            rest_slash_response = client.get("/rest/")

        for response in (root_response, rest_response, rest_slash_response):
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content_type, "text/plain; charset=utf-8")
            self.assertEqual(response.data, b"kukicha OpenSubsonic\n")

    def test_ping_accepts_password_auth_get_and_form_post(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_open_subsonic_app(self.make_options(temp_path))
            client = app.test_client()
            get_response = client.get("/rest/ping.view", query_string=self.auth_params())
            post_response = client.post("/rest/ping", data=self.auth_params())

        self.assertEqual(subsonic_payload(get_response)["status"], "ok")
        self.assertEqual(subsonic_payload(post_response)["status"], "ok")

    def test_ping_defaults_to_subsonic_xml_when_format_is_omitted(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_open_subsonic_app(self.make_options(temp_path))
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
            app = create_open_subsonic_app(
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
            app = create_open_subsonic_app(self.make_options(temp_path))
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
            app = create_open_subsonic_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/ping",
                query_string={**self.auth_params(), "f": "xml"},
            )

        self.assertEqual(response.content_type, "text/xml; charset=utf-8")
        self.assertIn(b'status="ok"', response.data)

    def test_unsupported_format_and_endpoint_return_failed_payloads(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            app = create_open_subsonic_app(self.make_options(temp_path))
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
            app = create_open_subsonic_app(self.make_options(temp_path))
            client = app.test_client()
            folders_response = client.get(
                "/rest/getMusicFolders",
                query_string=self.auth_params(),
            )
            album_list_response = client.get(
                "/rest/getAlbumList2",
                query_string={
                    **self.auth_params(),
                    "type": "anything",
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
        self.assertEqual(folders, [{"id": "0", "name": "music"}])

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

    def test_scrobble_tracks_now_playing_and_submitted_play_counts(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, second = self.save_sample_library(temp_path)
            database = temp_path / "kukicha.sqlite"
            app = create_open_subsonic_app(self.make_options(temp_path))
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
                now_row = connection.execute(
                    "SELECT playback_id, updated_at FROM play_now_playing"
                ).fetchone()
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

            expected_now = datetime.fromtimestamp(1770000000000 / 1000, tz=UTC).isoformat()
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

        self.assertEqual(subsonic_payload(now_response)["status"], "ok")
        self.assertEqual(int(now_row["playback_id"]), first.track_id)
        self.assertEqual(now_row["updated_at"], expected_now)
        self.assertEqual(submitted_count, 0)
        self.assertEqual(subsonic_payload(submit_response)["status"], "ok")
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
        self.assertEqual(
            album_stats,
            {"artist::first-album": 1, "artist::second-album": 1},
        )
        self.assertEqual(artist_row["artist"], "Artist")
        self.assertEqual(int(artist_row["play_count"]), 2)
        self.assertEqual(artist_row["last_played_at"], expected_second_play)
        self.assertEqual(genre_row["genre"], "Electronic")
        self.assertEqual(int(genre_row["play_count"]), 1)

    def test_album_list_requires_type(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_sample_library(temp_path)
            app = create_open_subsonic_app(self.make_options(temp_path))

            response = app.test_client().get(
                "/rest/getAlbumList2",
                query_string=self.auth_params(),
            )

        payload = subsonic_payload(response)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["code"], 10)

    def test_get_artists_groups_album_artists_and_reuses_album_cover_art(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_artist_library(temp_path)
            app = create_open_subsonic_app(self.make_options(temp_path))

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
            app = create_open_subsonic_app(self.make_options(temp_path))

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
            app = create_open_subsonic_app(self.make_options(temp_path))
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

    def test_get_artist_returns_not_found_for_missing_artist(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            self.save_artist_library(temp_path)
            app = create_open_subsonic_app(self.make_options(temp_path))

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
            app = create_open_subsonic_app(self.make_options(temp_path))
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

        self.assertEqual(stream_response.status_code, 206)
        self.assertEqual(stream_response.headers["Content-Range"], "bytes 1-3/10")
        self.assertEqual(stream_response.data, b"123")
        self.assertEqual(raw_cover_response.status_code, 200)
        self.assertEqual(raw_cover_response.content_type, "image/png")
        self.assertEqual(raw_cover_response.data, b"album-cover")
        self.assertEqual(album_cover_response.status_code, 200)
        self.assertEqual(album_cover_response.data, b"album-cover")

    def test_download_returns_original_audio_as_attachment_with_range_support(self) -> None:
        with TemporaryDirectory() as tempdir:
            temp_path = Path(tempdir)
            first, _second = self.save_sample_library(temp_path)
            app = create_open_subsonic_app(self.make_options(temp_path))

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

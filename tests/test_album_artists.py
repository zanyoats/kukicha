from __future__ import annotations

import unittest

from kukicha.album_artists import (
    DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS,
    album_artist_has_split_pattern,
    default_album_artist_mapping,
)


class AlbumArtistSplitMappingTest(unittest.TestCase):
    def test_default_mapping_splits_conservative_delimiters(self) -> None:
        examples = {
            "Brian Eno & Robert Fripp": ("Brian Eno", "Robert Fripp"),
            "Berlin Philharmonic, Bell, & Karajan": (
                "Berlin Philharmonic",
                "Bell",
                "Karajan",
            ),
            "Berlin Philharmonic, Bell & Karajan": (
                "Berlin Philharmonic",
                "Bell",
                "Karajan",
            ),
            "Brian Eno With Jon Hopkins & Leo Abrams": (
                "Brian Eno",
                "Jon Hopkins",
                "Leo Abrams",
            ),
            "Jane Doe/Rob Doe/John Doe": ("Jane Doe", "Rob Doe", "John Doe"),
        }

        for value, expected in examples.items():
            with self.subTest(value=value):
                self.assertEqual(default_album_artist_mapping(value), expected)

    def test_and_is_detected_but_not_split(self) -> None:
        value = "Brian Eno And Roger Eno"

        self.assertTrue(
            album_artist_has_split_pattern(value, DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS)
        )
        self.assertEqual(default_album_artist_mapping(value), (value,))

    def test_comma_only_is_detected_but_not_split_by_default(self) -> None:
        value = "Earth, Wind"

        self.assertTrue(
            album_artist_has_split_pattern(value, DEFAULT_ALBUM_ARTIST_SPLIT_PATTERNS)
        )
        self.assertEqual(default_album_artist_mapping(value), (value,))

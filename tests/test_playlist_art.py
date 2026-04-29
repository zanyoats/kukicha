from __future__ import annotations

import unittest
from importlib.resources import files

from kukicha.playlist_art import playlist_cover_svg, playlist_cover_title


class PlaylistArtTest(unittest.TestCase):
    def test_playlist_cover_svg_left_aligns_title_with_underline(self) -> None:
        svg = playlist_cover_svg("Road Mix")

        self.assertIn('<line x1="250" y1="190" x2="1015" y2="190"', svg)
        self.assertIn('<text x="250" y="180" text-anchor="start"', svg)
        self.assertIn(">Road Mix</text>", svg)

    def test_playlist_cover_title_truncates_with_ellipsis(self) -> None:
        self.assertEqual(playlist_cover_title("L" * 40), f"{'L' * 22}...")

    def test_static_favicon_uses_kukicha_cover_art(self) -> None:
        favicon = files("kukicha").joinpath("static", "favicon.svg").read_text()

        self.assertEqual(favicon.rstrip("\n"), playlist_cover_svg("kukicha"))

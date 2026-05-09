from __future__ import annotations

import hashlib
import unittest

from kukicha.use_case.musicbrainz import (
    build_lookup_url,
    musicbrainz_release_fingerprint,
    musicbrainz_release_variant_source,
)


class MusicBrainzReleaseFingerprintTest(unittest.TestCase):
    def test_release_lookup_requests_aliases(self) -> None:
        url = build_lookup_url("release", "cc3af5d1-caf1-45c9-9fe8-37b07e77f894")

        self.assertIn("aliases", url)

    def test_release_fingerprint_uses_barcode_before_catalog_number(self) -> None:
        release = {
            "country": "US",
            "date": "1997-05-21",
            "media": [{"format": "CD"}],
            "barcode": " 724385522921 ",
            "label-info": [{"catalog-number": "CDNODATA 02"}],
        }

        source = musicbrainz_release_variant_source(release)

        self.assertEqual(source, "us:1997:cd:724385522921")
        self.assertEqual(
            musicbrainz_release_fingerprint(release),
            hashlib.sha1(source.encode("utf-8")).hexdigest()[:3],
        )

    def test_release_fingerprint_falls_back_to_catalog_number(self) -> None:
        release = {
            "country": "GB",
            "date": "1997",
            "media": [{"format": "Vinyl"}],
            "label-info": [
                {"catalog-number": ""},
                {"catalog-number": "NODATA 02"},
            ],
        }

        self.assertEqual(
            musicbrainz_release_variant_source(release),
            "gb:1997:vinyl:nodata 02",
        )

    def test_release_fingerprint_falls_back_to_release_mbid_prefix(self) -> None:
        release = {
            "country": "",
            "date": "",
            "media": [],
        }

        self.assertEqual(
            musicbrainz_release_variant_source(
                release,
                fallback_release_mbid="11111111-1111-1111-1111-111111111111",
            ),
            "unknown-country:unknown-year:unknown-format:11111111",
        )


if __name__ == "__main__":
    unittest.main()

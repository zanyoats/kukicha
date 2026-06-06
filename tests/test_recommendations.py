from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kukicha.use_case import (
    ARTIST_ONLY_FALLBACK_RETURN_FEWER,
    CANDIDATE_FILTER_ARTIST_MATCH_REQUIRED,
    CANDIDATE_SELECTION_WEIGHTED_RANDOM,
    DEFAULT_RECOMMENDATION_LIMIT,
    DIVERSITY_STRENGTH_LOW,
    MAX_RECOMMENDATION_LIMIT,
    RECENT_PLAY_PENALTY_RANDOM_WEIGHTED,
    RECOMMENDATION_CONFIG,
    RECOMMENDATION_MODE_ARTIST_ONLY,
    RECOMMENDATION_MODE_DEFAULT,
    RECOMMENDATION_MODE_DISCOVERY,
    RECOMMENDATION_MODE_GENRE_ONLY,
    RECOMMENDATION_MODE_RANDOM,
    SUPPORTED_RECOMMENDATION_MODES,
    RecommendationQueries,
    RecommendationLimitError,
    RecommendationModeError,
    RecommendationRequest,
    TrackNotFoundError,
    connect_database,
    load_recommendation_candidate,
    load_recommendation_candidates,
    normalize_recommendation_limit,
    normalize_recommendation_mode,
    recommendation_mode_config,
)


class RecommendationModeConfigTest(unittest.TestCase):
    def test_supported_modes_normalize_to_canonical_names(self) -> None:
        self.assertEqual(
            SUPPORTED_RECOMMENDATION_MODES,
            (
                RECOMMENDATION_MODE_DEFAULT,
                RECOMMENDATION_MODE_DISCOVERY,
                RECOMMENDATION_MODE_GENRE_ONLY,
                RECOMMENDATION_MODE_ARTIST_ONLY,
                RECOMMENDATION_MODE_RANDOM,
            ),
        )
        for mode in SUPPORTED_RECOMMENDATION_MODES:
            self.assertEqual(normalize_recommendation_mode(mode), mode)
            self.assertIs(recommendation_mode_config(mode), RECOMMENDATION_CONFIG.modes[mode])

        self.assertEqual(
            normalize_recommendation_mode(" Discovery "),
            RECOMMENDATION_MODE_DISCOVERY,
        )
        self.assertEqual(normalize_recommendation_mode(None), RECOMMENDATION_MODE_DEFAULT)
        self.assertEqual(normalize_recommendation_mode(""), RECOMMENDATION_MODE_DEFAULT)

    def test_invalid_mode_raises_clear_error(self) -> None:
        with self.assertRaisesRegex(RecommendationModeError, "unsupported"):
            normalize_recommendation_mode("ambient_only")

        with self.assertRaisesRegex(RecommendationModeError, "ambient_only"):
            recommendation_mode_config("ambient_only")

    def test_limit_normalization_is_centralized(self) -> None:
        self.assertEqual(normalize_recommendation_limit(None), DEFAULT_RECOMMENDATION_LIMIT)
        self.assertEqual(normalize_recommendation_limit(""), DEFAULT_RECOMMENDATION_LIMIT)
        self.assertEqual(normalize_recommendation_limit(0), 1)
        self.assertEqual(normalize_recommendation_limit(-10), 1)
        self.assertEqual(
            normalize_recommendation_limit(MAX_RECOMMENDATION_LIMIT + 1),
            MAX_RECOMMENDATION_LIMIT,
        )

        request = RecommendationRequest(mode=" genre_only ", limit="0")
        self.assertEqual(request.mode, RECOMMENDATION_MODE_GENRE_ONLY)
        self.assertEqual(request.limit, 1)

        with self.assertRaisesRegex(RecommendationLimitError, "invalid"):
            normalize_recommendation_limit("plenty")

    def test_default_config_matches_plan_values(self) -> None:
        config = recommendation_mode_config()

        self.assertEqual(config.mode, RECOMMENDATION_MODE_DEFAULT)
        self.assertEqual(
            config.feature_weights.as_dict(),
            {
                "genres": 0.30,
                "styles": 0.40,
                "artist": 0.15,
                "decade": 0.15,
            },
        )
        self.assertEqual(config.track_play_penalty, 0.05)
        self.assertEqual(config.artist_play_penalty, 0.02)
        self.assertEqual(config.album_play_penalty, 0.02)
        self.assertEqual(config.favorite_boost, 0.05)
        self.assertEqual(config.recency_penalties.played_last_24_hours, 0.30)
        self.assertEqual(config.recency_penalties.played_last_7_days, 0.15)
        self.assertEqual(config.recency_penalties.played_last_30_days, 0.05)
        self.assertEqual(config.diversity_caps.max_tracks_per_artist, 3)
        self.assertEqual(config.diversity_caps.max_tracks_per_album, 2)
        self.assertEqual(config.diversity_caps.max_tracks_per_genre, 8)
        self.assertEqual(config.artist_only_fallback, ARTIST_ONLY_FALLBACK_RETURN_FEWER)

        with self.assertRaises(FrozenInstanceError):
            config.favorite_boost = 1.0

    def test_specialized_modes_match_plan_values(self) -> None:
        discovery = recommendation_mode_config(RECOMMENDATION_MODE_DISCOVERY)
        self.assertEqual(discovery.track_play_penalty, 0.30)
        self.assertEqual(discovery.artist_play_penalty, 0.15)
        self.assertEqual(discovery.album_play_penalty, 0.10)
        self.assertEqual(discovery.favorite_boost, 0.00)
        self.assertEqual(discovery.recency_penalties.played_last_24_hours, 0.50)

        genre_only = recommendation_mode_config(RECOMMENDATION_MODE_GENRE_ONLY)
        self.assertEqual(genre_only.feature_weights.genres, 1.00)
        self.assertEqual(genre_only.feature_weights.styles, 0.00)
        self.assertEqual(genre_only.feature_weights.artist, 0.00)
        self.assertEqual(genre_only.feature_weights.decade, 0.00)

        artist_only = recommendation_mode_config(RECOMMENDATION_MODE_ARTIST_ONLY)
        self.assertEqual(artist_only.feature_weights.artist, 1.00)
        self.assertEqual(artist_only.candidate_filter, CANDIDATE_FILTER_ARTIST_MATCH_REQUIRED)
        self.assertEqual(artist_only.diversity_strength, DIVERSITY_STRENGTH_LOW)
        self.assertFalse(artist_only.diversity_caps.apply_artist_cap)
        self.assertEqual(artist_only.artist_only_fallback, ARTIST_ONLY_FALLBACK_RETURN_FEWER)

    def test_random_mode_recency_multipliers_match_plan_values(self) -> None:
        config = recommendation_mode_config(RECOMMENDATION_MODE_RANDOM)
        multipliers = config.random_recency_multipliers

        self.assertEqual(config.candidate_selection, CANDIDATE_SELECTION_WEIGHTED_RANDOM)
        self.assertEqual(config.recent_play_penalty_strength, RECENT_PLAY_PENALTY_RANDOM_WEIGHTED)
        self.assertEqual(config.random_track_play_count_weight, 0.15)
        self.assertIsNotNone(multipliers)
        assert multipliers is not None
        self.assertEqual(multipliers.played_last_24_hours, 0.10)
        self.assertEqual(multipliers.played_last_7_days, 0.35)
        self.assertEqual(multipliers.played_last_30_days, 0.70)
        self.assertEqual(multipliers.older_or_never_played, 1.00)
        self.assertEqual(multipliers.multiplier_for_age_days(0.5), 0.10)
        self.assertEqual(multipliers.multiplier_for_age_days(3), 0.35)
        self.assertEqual(multipliers.multiplier_for_age_days(14), 0.70)
        self.assertEqual(multipliers.multiplier_for_age_days(None), 1.00)
        self.assertEqual(multipliers.multiplier_for_age_days(45), 1.00)


class RecommendationCandidateLoadingTest(unittest.TestCase):
    def build_database(self) -> Path:
        tempdir = TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        database = Path(tempdir.name) / "library.sqlite"
        with connect_database(database) as connection:
            connection.executemany(
                """
                INSERT INTO library_albums (album_id, album, year, track_count)
                VALUES (?, ?, ?, ?)
                """,
                (
                    ("album-1", "Electric Echoes", 2001, 2),
                    ("album-2", "Soft Weather", 1977, 1),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_tracks (
                    track_id,
                    album_id,
                    path,
                    file_type,
                    scan_error,
                    artist,
                    album_artist,
                    album,
                    title,
                    date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        1,
                        "album-1",
                        "/music/echoes/01.flac",
                        "flac",
                        None,
                        "Echo Lead",
                        "Echo Artist",
                        "Electric Echoes",
                        "Bright Arc",
                        "1998-03-02",
                    ),
                    (
                        2,
                        "album-2",
                        "/music/weather/01.flac",
                        "flac",
                        "",
                        "Weather Trio",
                        "",
                        "Soft Weather",
                        "Rain Study",
                        "",
                    ),
                    (
                        3,
                        "album-1",
                        "/music/echoes/broken.flac",
                        "flac",
                        "read failed",
                        "Echo Lead",
                        "Echo Artist",
                        "Electric Echoes",
                        "Broken",
                        "1999",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Jazz"),
                    (1, 1, "Fusion"),
                    (2, 0, "Ambient"),
                    (3, 0, "Jazz"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Post-Bop"),
                    (1, 1, "Electric Jazz"),
                    (2, 0, "Minimal"),
                    (3, 0, "Post-Bop"),
                ),
            )
            connection.execute(
                """
                INSERT INTO track_user_state (track_path, starred_at)
                VALUES (?, ?)
                """,
                ("/music/echoes/01.flac", "2026-05-01T12:00:00+00:00"),
            )
            connection.execute(
                """
                INSERT INTO play_track_stats (
                    track_path,
                    play_count,
                    last_played_at,
                    track_id,
                    album_id,
                    path,
                    title,
                    artist,
                    album
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "/music/echoes/01.flac",
                    7,
                    "2026-05-30T10:00:00+00:00",
                    1,
                    "album-1",
                    "/music/echoes/01.flac",
                    "Bright Arc",
                    "Echo Lead",
                    "Electric Echoes",
                ),
            )
            connection.execute(
                """
                INSERT INTO play_album_stats (
                    album_id,
                    play_count,
                    last_played_at,
                    album,
                    artist
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "album-1",
                    11,
                    "2026-05-29T10:00:00+00:00",
                    "Electric Echoes",
                    "Echo Artist",
                ),
            )
            connection.execute(
                """
                INSERT INTO play_artist_stats (
                    artist_key,
                    artist,
                    play_count,
                    last_played_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    "echo artist",
                    "Echo Artist",
                    13,
                    "2026-05-28T10:00:00+00:00",
                ),
            )
        return database

    def test_candidates_load_metadata_and_listening_stats(self) -> None:
        database = self.build_database()

        with connect_database(database, create=False) as connection:
            candidates = load_recommendation_candidates(connection)

        self.assertEqual([item.metadata.track_id for item in candidates], [1, 2])

        first = candidates[0]
        self.assertEqual(first.metadata.path, "/music/echoes/01.flac")
        self.assertEqual(first.metadata.title, "Bright Arc")
        self.assertEqual(first.metadata.artist, "Echo Lead")
        self.assertEqual(first.metadata.album_artist, "Echo Artist")
        self.assertEqual(first.metadata.album_id, "album-1")
        self.assertEqual(first.metadata.album, "Electric Echoes")
        self.assertEqual(first.metadata.date, "1998-03-02")
        self.assertEqual(first.metadata.decade, "1990s")
        self.assertCountEqual(first.metadata.genres, ("Jazz", "Fusion"))
        self.assertCountEqual(first.metadata.styles, ("Post-Bop", "Electric Jazz"))
        self.assertTrue(first.metadata.is_favorite)
        self.assertEqual(first.metadata.starred_at, "2026-05-01T12:00:00+00:00")
        self.assertEqual(first.listening.track_play_count, 7)
        self.assertEqual(first.listening.album_play_count, 11)
        self.assertEqual(first.listening.artist_play_count, 13)
        self.assertEqual(
            first.listening.track_last_played_at,
            "2026-05-30T10:00:00+00:00",
        )
        self.assertEqual(
            first.listening.album_last_played_at,
            "2026-05-29T10:00:00+00:00",
        )
        self.assertEqual(
            first.listening.artist_last_played_at,
            "2026-05-28T10:00:00+00:00",
        )

        second = candidates[1]
        self.assertEqual(second.metadata.decade, "1970s")
        self.assertCountEqual(second.metadata.genres, ("Ambient",))
        self.assertCountEqual(second.metadata.styles, ("Minimal",))
        self.assertFalse(second.metadata.is_favorite)
        self.assertEqual(second.listening.track_play_count, 0)
        self.assertEqual(second.listening.album_play_count, 0)
        self.assertEqual(second.listening.artist_play_count, 0)
        self.assertIsNone(second.listening.track_last_played_at)
        self.assertIsNone(second.listening.album_last_played_at)
        self.assertIsNone(second.listening.artist_last_played_at)

    def test_seed_candidate_lookup_uses_candidate_filtering(self) -> None:
        database = self.build_database()

        with connect_database(database, create=False) as connection:
            candidate = load_recommendation_candidate(connection, 2)
            with self.assertRaises(TrackNotFoundError):
                load_recommendation_candidate(connection, 3)
            with self.assertRaises(TrackNotFoundError):
                load_recommendation_candidate(connection, 404)

        self.assertEqual(candidate.metadata.track_id, 2)
        self.assertEqual(candidate.metadata.title, "Rain Study")

    def test_recommendation_queries_wrap_database_path(self) -> None:
        database = self.build_database()
        queries = RecommendationQueries(database)

        self.assertEqual(
            [item.metadata.track_id for item in queries.list_candidates()],
            [1, 2],
        )
        self.assertEqual(queries.get_candidate(1).metadata.title, "Bright Arc")


if __name__ == "__main__":
    unittest.main()

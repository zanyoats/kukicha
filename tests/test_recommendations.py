from __future__ import annotations

from dataclasses import FrozenInstanceError
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
    RecommendationLimitError,
    RecommendationModeError,
    RecommendationRequest,
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


if __name__ == "__main__":
    unittest.main()

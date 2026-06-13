from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kukicha.use_case import (
    ARTIST_ONLY_FALLBACK_RETURN_FEWER,
    CANDIDATE_FILTER_ARTIST_MATCH_REQUIRED,
    CANDIDATE_SELECTION_WEIGHTED_RANDOM,
    AlbumNotFoundError,
    ArtistNotFoundError,
    CandidateMetadata,
    DEFAULT_RECOMMENDATION_LIMIT,
    DiversityCaps,
    DIVERSITY_STRENGTH_LOW,
    MAX_RECOMMENDATION_LIMIT,
    RECENT_PLAY_PENALTY_RANDOM_WEIGHTED,
    RANDOM_RECOMMENDATION_CONFIG,
    RECOMMENDATION_CONFIG,
    RECOMMENDATION_MODE_ARTIST_ONLY,
    RECOMMENDATION_MODE_DEFAULT,
    RECOMMENDATION_MODE_DISCOVERY,
    SUPPORTED_RECOMMENDATION_MODES,
    ListeningStats,
    RecommendationCandidate,
    RecommendationQueries,
    RecommendationLimitError,
    RecommendationModeError,
    RecommendationProfileSeed,
    RecommendationResult,
    RecommendationScore,
    RecommendationService,
    TrackNotFoundError,
    add_sparse_vectors,
    build_recommendation_album_profile,
    build_recommendation_artist_profile,
    build_recommendation_explanation,
    build_recommendation_profile,
    build_recommendation_track_profile,
    build_recommendation_track_vector,
    build_recommendation_track_vectors,
    build_recommendation_user_profile,
    build_recommendation_vocabulary,
    connect_database,
    load_recommendation_candidate,
    load_recommendation_candidates,
    normalize_recommendation_limit,
    normalize_recommendation_mode,
    recommendation_mode_config,
    rerank_recommendation_results,
    scale_sparse_vector,
    score_recommendation_candidates,
    sparse_cosine_similarity,
    sparse_dot_product,
    sparse_vector_norm,
    weighted_average_sparse_vectors,
)


class FixedRandomSource:
    def __init__(self, *draws: float) -> None:
        self.draws = list(draws)

    def random(self) -> float:
        if not self.draws:
            return 0.0
        return self.draws.pop(0)


class RecommendationModeConfigTest(unittest.TestCase):
    def test_supported_modes_normalize_to_canonical_names(self) -> None:
        self.assertEqual(
            SUPPORTED_RECOMMENDATION_MODES,
            (
                RECOMMENDATION_MODE_DEFAULT,
                RECOMMENDATION_MODE_DISCOVERY,
                RECOMMENDATION_MODE_ARTIST_ONLY,
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
        for mode in ("ambient_only", "genre_only", "random"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(RecommendationModeError, "unsupported"):
                    normalize_recommendation_mode(mode)

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
        self.assertEqual(config.recent_play_suppression_days, 1.0)
        self.assertEqual(config.diversity_caps.max_tracks_per_artist, 3)
        self.assertEqual(config.diversity_caps.max_tracks_per_album, 1)
        self.assertEqual(config.diversity_caps.max_tracks_per_genre, 8)
        self.assertEqual(config.artist_only_fallback, ARTIST_ONLY_FALLBACK_RETURN_FEWER)

        with self.assertRaises(FrozenInstanceError):
            config.favorite_boost = 1.0

    def test_specialized_modes_match_plan_values(self) -> None:
        discovery = recommendation_mode_config(RECOMMENDATION_MODE_DISCOVERY)
        self.assertEqual(discovery.feature_weights.genres, 0.25)
        self.assertEqual(discovery.feature_weights.styles, 0.55)
        self.assertEqual(discovery.feature_weights.artist, 0.05)
        self.assertEqual(discovery.feature_weights.decade, 0.15)
        self.assertEqual(discovery.track_play_penalty, 0.60)
        self.assertEqual(discovery.artist_play_penalty, 0.12)
        self.assertEqual(discovery.album_play_penalty, 0.08)
        self.assertEqual(discovery.favorite_boost, 0.00)
        self.assertEqual(discovery.recency_penalties.played_last_24_hours, 0.70)
        self.assertEqual(discovery.recency_penalties.played_last_7_days, 0.45)
        self.assertEqual(discovery.recency_penalties.played_last_30_days, 0.20)
        self.assertEqual(discovery.recency_penalties.played_last_180_days, 0.05)
        self.assertEqual(discovery.recent_play_suppression_days, 7.0)

        artist_only = recommendation_mode_config(RECOMMENDATION_MODE_ARTIST_ONLY)
        self.assertEqual(artist_only.feature_weights.artist, 1.00)
        self.assertEqual(artist_only.candidate_filter, CANDIDATE_FILTER_ARTIST_MATCH_REQUIRED)
        self.assertEqual(artist_only.diversity_strength, DIVERSITY_STRENGTH_LOW)
        self.assertFalse(artist_only.diversity_caps.apply_artist_cap)
        self.assertEqual(artist_only.recent_play_suppression_days, 1.0)
        self.assertEqual(artist_only.artist_only_fallback, ARTIST_ONLY_FALLBACK_RETURN_FEWER)
        self.assertFalse(artist_only.exclude_seed_track)
        self.assertFalse(artist_only.exclude_seed_album_tracks)

    def test_random_mode_recency_multipliers_match_plan_values(self) -> None:
        config = RANDOM_RECOMMENDATION_CONFIG
        multipliers = config.random_recency_multipliers

        self.assertEqual(config.candidate_selection, CANDIDATE_SELECTION_WEIGHTED_RANDOM)
        self.assertEqual(config.recent_play_penalty_strength, RECENT_PLAY_PENALTY_RANDOM_WEIGHTED)
        self.assertEqual(config.random_track_play_count_weight, 0.15)
        self.assertIsNone(config.recent_play_suppression_days)
        self.assertFalse(config.exclude_seed_track)
        self.assertFalse(config.exclude_seed_album_tracks)
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


class RecommendationVectorTest(unittest.TestCase):
    def candidate(
        self,
        track_id: int,
        *,
        artist: str = "",
        album_artist: str = "",
        decade: str | None = None,
        genres: tuple[str, ...] = (),
        styles: tuple[str, ...] = (),
    ) -> RecommendationCandidate:
        return RecommendationCandidate(
            metadata=CandidateMetadata(
                track_id=track_id,
                path=f"/music/{track_id}.flac",
                title=f"Track {track_id}",
                artist=artist,
                album_artist=album_artist,
                decade=decade,
                genres=genres,
                styles=styles,
            )
        )

    def similarity(
        self,
        left: dict[str, float],
        right: dict[str, float],
    ) -> float:
        return sum(value * right.get(key, 0.0) for key, value in left.items())

    def vector_norm(self, vector: dict[str, float]) -> float:
        return math.sqrt(sum(value * value for value in vector.values()))

    def test_vocabulary_builds_tfidf_terms_from_candidate_pool(self) -> None:
        candidates = (
            self.candidate(
                1,
                artist="Seed Artist",
                decade="1990s",
                genres=("Rock",),
                styles=("Dream Pop",),
            ),
            self.candidate(
                2,
                artist="Other Artist",
                decade="1970s",
                genres=("Pop",),
                styles=("Dream Pop",),
            ),
            self.candidate(
                3,
                artist="Third Artist",
                decade="1970s",
                genres=("Rock",),
                styles=("Garage Rock",),
            ),
            self.candidate(
                4,
                artist="Fourth Artist",
                decade="2010s",
                genres=("Ambient",),
                styles=("Minimal",),
            ),
        )

        vocabulary = build_recommendation_vocabulary(candidates)

        self.assertEqual(vocabulary.document_count, 4)
        self.assertEqual(vocabulary.genre_terms, ("ambient", "pop", "rock"))
        self.assertEqual(
            vocabulary.style_terms,
            ("dream pop", "garage rock", "minimal"),
        )
        self.assertEqual(vocabulary.decade_terms, ("1970s", "1990s", "2010s"))
        self.assertLess(vocabulary.genre_idf["rock"], vocabulary.genre_idf["ambient"])
        self.assertIn("genre:rock", vocabulary.genre_features)
        self.assertIn("style:dream pop", vocabulary.style_features)
        self.assertIn("artist:seed artist", vocabulary.artist_features)
        self.assertIn("decade:1990s", vocabulary.decade_features)

    def test_style_match_scores_higher_than_broad_genre_match(self) -> None:
        seed = self.candidate(
            1,
            artist="Seed Artist",
            decade="1990s",
            genres=("Rock",),
            styles=("Dream Pop",),
        )
        style_match = self.candidate(
            2,
            artist="Other Artist",
            decade="1970s",
            genres=("Pop",),
            styles=("Dream Pop",),
        )
        genre_match = self.candidate(
            3,
            artist="Third Artist",
            decade="1970s",
            genres=("Rock",),
            styles=("Garage Rock",),
        )
        vocabulary = build_recommendation_vocabulary((seed, style_match, genre_match))
        vectors = build_recommendation_track_vectors(
            (seed, style_match, genre_match),
            vocabulary=vocabulary,
        )

        self.assertAlmostEqual(self.vector_norm(vectors[1]), 1.0)
        self.assertGreater(
            self.similarity(vectors[1], vectors[2]),
            self.similarity(vectors[1], vectors[3]),
        )

    def test_mode_weights_zero_unused_feature_groups(self) -> None:
        candidate = self.candidate(
            1,
            artist="Seed Artist",
            decade="1990s",
            genres=("Rock",),
            styles=("Dream Pop",),
        )
        vocabulary = build_recommendation_vocabulary((candidate,))

        artist_only = build_recommendation_track_vector(
            candidate,
            vocabulary,
            mode=RECOMMENDATION_MODE_ARTIST_ONLY,
        )

        self.assertEqual(tuple(artist_only), ("artist:seed artist",))

    def test_empty_metadata_produces_stable_empty_vectors(self) -> None:
        candidate = self.candidate(1)
        vocabulary = build_recommendation_vocabulary((candidate,))

        self.assertEqual(vocabulary.document_count, 1)
        self.assertEqual(vocabulary.genre_terms, ())
        self.assertEqual(build_recommendation_track_vector(candidate, vocabulary), {})
        self.assertEqual(
            build_recommendation_track_vectors((candidate,), vocabulary=vocabulary),
            {1: {}},
        )
        self.assertEqual(build_recommendation_track_vectors(()), {})


class RecommendationProfileTest(unittest.TestCase):
    def candidate(
        self,
        track_id: int,
        *,
        artist: str = "",
        album_artist: str = "",
        album_id: str | None = None,
    ) -> RecommendationCandidate:
        return RecommendationCandidate(
            metadata=CandidateMetadata(
                track_id=track_id,
                path=f"/music/{track_id}.flac",
                title=f"Track {track_id}",
                artist=artist,
                album_artist=album_artist,
                album_id=album_id,
            )
        )

    def test_sparse_vector_math_supports_cosine_similarity(self) -> None:
        normalized = {"feature:a": 0.6, "feature:b": 0.8}

        self.assertEqual(
            sparse_vector_norm({"feature:a": 3.0, "feature:b": 4.0}),
            5.0,
        )
        self.assertAlmostEqual(sparse_dot_product(normalized, normalized), 1.0)
        self.assertAlmostEqual(
            sparse_cosine_similarity(normalized, normalized),
            1.0,
        )
        self.assertEqual(
            sparse_cosine_similarity({"feature:a": 1.0}, {"feature:b": 1.0}),
            0.0,
        )
        self.assertEqual(sparse_cosine_similarity({}, normalized), 0.0)

        self.assertEqual(
            add_sparse_vectors(
                {"feature:b": 2.0, "feature:a": 1.0},
                {"feature:b": -2.0, "feature:c": 4.0},
            ),
            {"feature:a": 1.0, "feature:c": 4.0},
        )
        self.assertEqual(
            scale_sparse_vector({"feature:a": 2.0, "feature:b": 0.0}, 0.5),
            {"feature:a": 1.0},
        )

    def test_weighted_average_sparse_vectors_favors_higher_weight_seed(self) -> None:
        average = weighted_average_sparse_vectors(
            (
                ({"style:dream pop": 1.0}, 3.0),
                ({"style:garage rock": 1.0}, 1.0),
            )
        )

        self.assertEqual(
            average,
            {"style:dream pop": 0.75, "style:garage rock": 0.25},
        )
        self.assertGreater(
            sparse_cosine_similarity(average, {"style:dream pop": 1.0}),
            sparse_cosine_similarity(average, {"style:garage rock": 1.0}),
        )

        with self.assertRaisesRegex(ValueError, "non-negative"):
            weighted_average_sparse_vectors((({"style:dream pop": 1.0}, -1.0),))

    def test_profile_builders_share_weighted_vector_math(self) -> None:
        candidates = (
            self.candidate(
                1,
                artist="Seed Artist",
                album_artist="Seed Collective",
                album_id="album-1",
            ),
            self.candidate(
                2,
                artist="Guest Artist",
                album_artist="Seed Collective",
                album_id="album-1",
            ),
            self.candidate(
                3,
                artist="Other Artist",
                album_artist="Other Artist",
                album_id="album-2",
            ),
        )
        track_vectors = {
            1: {"style:dream pop": 1.0},
            2: {"style:shoegaze": 1.0},
            3: {"style:garage rock": 1.0},
        }

        track_profile = build_recommendation_track_profile(1, track_vectors)
        self.assertEqual(track_profile.vector, {"style:dream pop": 1.0})
        self.assertEqual(track_profile.seed_track_ids, (1,))
        self.assertEqual(track_profile.total_seed_weight, 1.0)

        album_profile = build_recommendation_album_profile(
            "album-1",
            candidates,
            track_vectors,
        )
        self.assertEqual(
            album_profile.vector,
            {"style:dream pop": 0.5, "style:shoegaze": 0.5},
        )
        self.assertEqual(album_profile.seed_track_ids, (1, 2))

        artist_profile = build_recommendation_artist_profile(
            "seed collective",
            candidates,
            track_vectors,
        )
        self.assertEqual(
            artist_profile.vector,
            {"style:dream pop": 0.5, "style:shoegaze": 0.5},
        )
        self.assertEqual(artist_profile.seed_track_ids, (1, 2))

        user_profile = build_recommendation_user_profile(
            (
                RecommendationProfileSeed(track_id=1, weight=3.0),
                RecommendationProfileSeed(track_id=3, weight=1.0),
            ),
            track_vectors,
        )
        self.assertEqual(
            user_profile.vector,
            {"style:dream pop": 0.75, "style:garage rock": 0.25},
        )
        self.assertEqual(user_profile.seed_track_ids, (1, 3))
        self.assertEqual(user_profile.total_seed_weight, 4.0)

    def test_empty_profile_input_returns_cold_start_profile(self) -> None:
        self.assertTrue(build_recommendation_profile((), {}).is_cold_start)

        missing_track = build_recommendation_track_profile(
            404,
            {1: {"style:dream pop": 1.0}},
        )

        self.assertFalse(missing_track.has_seed_tracks)
        self.assertTrue(missing_track.is_cold_start)
        self.assertEqual(missing_track.vector, {})
        self.assertEqual(missing_track.total_seed_weight, 0.0)

class RecommendationExplanationTest(unittest.TestCase):
    def candidate(
        self,
        track_id: int,
        *,
        artist: str = "",
        album_artist: str = "",
        decade: str | None = None,
        genres: tuple[str, ...] = (),
        styles: tuple[str, ...] = (),
    ) -> RecommendationCandidate:
        return RecommendationCandidate(
            metadata=CandidateMetadata(
                track_id=track_id,
                path=f"/music/{track_id}.flac",
                artist=artist,
                album_artist=album_artist,
                decade=decade,
                genres=genres,
                styles=styles,
            )
        )

    def test_explanation_identifies_shared_seed_metadata(self) -> None:
        seed = self.candidate(
            1,
            artist="Seed Artist",
            album_artist="Seed Collective",
            decade="1990s",
            genres=("Rock", "Pop"),
            styles=("Dream Pop", "Shoegaze"),
        )
        candidate = self.candidate(
            2,
            artist="Other Artist",
            album_artist="Other Artist",
            decade="1992",
            genres=("Rock", "Ambient"),
            styles=("Dream Pop", "Drone"),
        )
        same_artist = self.candidate(
            3,
            artist="Seed Artist",
            decade="2010s",
            genres=("Jazz",),
            styles=("Post-Bop",),
        )
        score = RecommendationScore(base_similarity=0.75)

        explanation = build_recommendation_explanation(
            candidate,
            (seed,),
            score=score,
        )
        same_artist_explanation = build_recommendation_explanation(
            same_artist,
            (seed,),
            score=RecommendationScore(base_similarity=0.15),
        )

        self.assertEqual(explanation.matched_genres, ("Rock",))
        self.assertEqual(explanation.matched_styles, ("Dream Pop",))
        self.assertEqual(explanation.matched_decade, "1990s")
        self.assertFalse(explanation.same_artist)
        self.assertIs(explanation.score, score)
        self.assertEqual(explanation.score.base_similarity, 0.75)
        self.assertEqual(explanation.score.favorite_boost, 0.0)
        self.assertEqual(explanation.score.track_play_penalty, 0.0)
        self.assertEqual(explanation.score.artist_play_penalty, 0.0)
        self.assertEqual(explanation.score.album_play_penalty, 0.0)
        self.assertEqual(explanation.score.recency_penalty, 0.0)

        self.assertTrue(same_artist_explanation.same_artist)
        self.assertEqual(same_artist_explanation.matched_genres, ())
        self.assertEqual(same_artist_explanation.matched_styles, ())
        self.assertIsNone(same_artist_explanation.matched_decade)

    def test_explanation_handles_sparse_candidate_metadata(self) -> None:
        seed = self.candidate(
            1,
            artist="Seed Artist",
            decade="1990s",
            genres=("Rock",),
            styles=("Dream Pop",),
        )
        sparse_candidate = self.candidate(2)

        explanation = build_recommendation_explanation(
            sparse_candidate,
            (seed,),
            score=RecommendationScore(base_similarity=0.0),
        )

        self.assertEqual(explanation.matched_genres, ())
        self.assertEqual(explanation.matched_styles, ())
        self.assertIsNone(explanation.matched_decade)
        self.assertFalse(explanation.same_artist)
        self.assertEqual(explanation.score.base_similarity, 0.0)
        self.assertEqual(explanation.score.favorite_boost, 0.0)
        self.assertEqual(explanation.score.track_play_penalty, 0.0)
        self.assertEqual(explanation.score.artist_play_penalty, 0.0)
        self.assertEqual(explanation.score.album_play_penalty, 0.0)
        self.assertEqual(explanation.score.recency_penalty, 0.0)


class RecommendationListeningAdjustmentTest(unittest.TestCase):
    fixed_now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

    def candidate(
        self,
        track_id: int,
        *,
        starred_at: str | None = None,
        track_play_count: int = 0,
        album_play_count: int = 0,
        artist_play_count: int = 0,
        track_last_played_at: str | None = None,
    ) -> RecommendationCandidate:
        return RecommendationCandidate(
            metadata=CandidateMetadata(
                track_id=track_id,
                path=f"/music/{track_id}.flac",
                title=f"Track {track_id}",
                artist=f"Artist {track_id}",
                album_id=f"album-{track_id}",
                starred_at=starred_at,
            ),
            listening=ListeningStats(
                track_play_count=track_play_count,
                album_play_count=album_play_count,
                artist_play_count=artist_play_count,
                track_last_played_at=track_last_played_at,
            ),
        )

    def score_by_track_id(
        self,
        candidates: tuple[RecommendationCandidate, ...],
        *,
        mode: object | None = RECOMMENDATION_MODE_DEFAULT,
        config: object | None = None,
    ) -> dict[int, RecommendationScore]:
        profile = build_recommendation_profile(
            (RecommendationProfileSeed(track_id=1),),
            {1: {"style:dream pop": 1.0}},
        )
        vectors = {
            candidate.metadata.track_id: {"style:dream pop": 1.0}
            for candidate in candidates
        }
        return {
            result.candidate.metadata.track_id: result.score
            for result in score_recommendation_candidates(
                profile,
                candidates,
                vectors,
                mode=mode,
                config=config,
                current_time=self.fixed_now,
            )
        }

    def test_high_play_count_tracks_are_lightly_downranked_in_default_mode(
        self,
    ) -> None:
        scores = self.score_by_track_id(
            (
                self.candidate(
                    2,
                    track_play_count=100,
                    album_play_count=50,
                    artist_play_count=25,
                ),
                self.candidate(3),
            )
        )

        self.assertEqual(scores[2].base_similarity, 1.0)
        self.assertEqual(scores[3].base_similarity, 1.0)
        self.assertAlmostEqual(scores[2].track_play_penalty, 0.05)
        self.assertAlmostEqual(scores[2].album_play_penalty, 0.02)
        self.assertAlmostEqual(scores[2].artist_play_penalty, 0.02)
        self.assertEqual(scores[3].track_play_penalty, 0.0)
        self.assertEqual(scores[3].album_play_penalty, 0.0)
        self.assertEqual(scores[3].artist_play_penalty, 0.0)
        self.assertLess(scores[2].final_score, scores[3].final_score)
        self.assertAlmostEqual(scores[2].final_score, 0.91)

    def test_discovery_prefers_underplayed_style_matches_over_overplayed_exact_matches(
        self,
    ) -> None:
        overplayed_exact_match = self.candidate(2, track_play_count=100)
        underplayed_style_match = self.candidate(3)
        profile = build_recommendation_profile(
            (RecommendationProfileSeed(track_id=1),),
            {1: {"style:dream pop": 1.0}},
        )
        partial_match_vector = {
            "style:dream pop": 0.65,
            "style:neighbor": math.sqrt(1.0 - (0.65 ** 2)),
        }

        default_results = score_recommendation_candidates(
            profile,
            (overplayed_exact_match, underplayed_style_match),
            {
                2: {"style:dream pop": 1.0},
                3: partial_match_vector,
            },
            current_time=self.fixed_now,
        )
        discovery_results = score_recommendation_candidates(
            profile,
            (overplayed_exact_match, underplayed_style_match),
            {
                2: {"style:dream pop": 1.0},
                3: partial_match_vector,
            },
            mode=RECOMMENDATION_MODE_DISCOVERY,
            current_time=self.fixed_now,
        )
        default_by_id = {
            result.candidate.metadata.track_id: result
            for result in default_results
        }
        discovery_by_id = {
            result.candidate.metadata.track_id: result
            for result in discovery_results
        }

        self.assertGreater(
            default_by_id[2].final_score,
            default_by_id[3].final_score,
        )
        self.assertGreater(
            discovery_by_id[3].final_score,
            discovery_by_id[2].final_score,
        )
        self.assertAlmostEqual(discovery_by_id[2].score.track_play_penalty, 0.60)
        self.assertAlmostEqual(discovery_by_id[3].score.base_similarity, 0.65)

    def test_recently_played_tracks_receive_expected_penalty_bucket(self) -> None:
        def played_at(age: timedelta) -> str:
            return (self.fixed_now - age).isoformat()

        scores = self.score_by_track_id(
            (
                self.candidate(
                    2,
                    track_last_played_at=played_at(timedelta(hours=12)),
                ),
                self.candidate(
                    3,
                    track_last_played_at=played_at(timedelta(days=3)),
                ),
                self.candidate(
                    4,
                    track_last_played_at=played_at(timedelta(days=14)),
                ),
                self.candidate(
                    5,
                    track_last_played_at=played_at(timedelta(days=45)),
                ),
            )
        )

        self.assertEqual(scores[2].recency_penalty, 0.30)
        self.assertEqual(scores[3].recency_penalty, 0.15)
        self.assertEqual(scores[4].recency_penalty, 0.05)
        self.assertEqual(scores[5].recency_penalty, 0.0)

    def test_discovery_recency_penalty_expires_at_180_days(self) -> None:
        def played_at(age: timedelta) -> str:
            return (self.fixed_now - age).isoformat()

        scores = self.score_by_track_id(
            (
                self.candidate(
                    2,
                    track_last_played_at=played_at(timedelta(days=179)),
                ),
                self.candidate(
                    3,
                    track_last_played_at=played_at(timedelta(days=180)),
                ),
                self.candidate(4),
            ),
            mode=RECOMMENDATION_MODE_DISCOVERY,
        )

        self.assertEqual(scores[2].recency_penalty, 0.05)
        self.assertEqual(scores[3].recency_penalty, 0.0)
        self.assertEqual(scores[4].recency_penalty, 0.0)

    def test_random_mode_scores_listening_selection_weights(self) -> None:
        def played_at(age: timedelta) -> str:
            return (self.fixed_now - age).isoformat()

        scores = self.score_by_track_id(
            (
                self.candidate(
                    2,
                    track_play_count=100,
                    track_last_played_at=played_at(timedelta(hours=12)),
                ),
                self.candidate(
                    3,
                    track_last_played_at=played_at(timedelta(days=3)),
                ),
                self.candidate(
                    4,
                    track_last_played_at=played_at(timedelta(days=14)),
                ),
                self.candidate(5),
            ),
            config=RANDOM_RECOMMENDATION_CONFIG,
        )

        self.assertEqual(scores[2].base_similarity, 0.0)
        self.assertEqual(scores[2].random_recency_multiplier, 0.10)
        self.assertEqual(scores[3].random_recency_multiplier, 0.35)
        self.assertEqual(scores[4].random_recency_multiplier, 0.70)
        self.assertEqual(scores[5].random_recency_multiplier, 1.00)
        self.assertAlmostEqual(scores[2].random_play_count_multiplier, 0.85)
        self.assertEqual(scores[5].random_play_count_multiplier, 1.00)
        self.assertAlmostEqual(scores[2].random_selection_weight, 0.085)
        self.assertEqual(
            scores[2].final_score,
            scores[2].random_selection_weight,
        )
        self.assertLess(
            scores[2].random_selection_weight or 0.0,
            scores[3].random_selection_weight or 0.0,
        )
        self.assertLess(
            scores[3].random_selection_weight or 0.0,
            scores[4].random_selection_weight or 0.0,
        )
        self.assertLess(
            scores[4].random_selection_weight or 0.0,
            scores[5].random_selection_weight or 0.0,
        )

    def test_favorite_boosts_follow_the_selected_mode_config(self) -> None:
        candidate = self.candidate(
            2,
            starred_at="2026-06-01T10:00:00+00:00",
        )

        default_score = self.score_by_track_id((candidate,))[2]
        discovery_score = self.score_by_track_id(
            (candidate,),
            mode=RECOMMENDATION_MODE_DISCOVERY,
        )[2]

        self.assertEqual(default_score.favorite_boost, 0.05)
        self.assertEqual(discovery_score.favorite_boost, 0.0)
        self.assertAlmostEqual(default_score.final_score, 1.05)
        self.assertAlmostEqual(discovery_score.final_score, 1.0)

    def test_missing_play_stats_do_not_penalize_a_track(self) -> None:
        score = self.score_by_track_id((self.candidate(2),))[2]

        self.assertEqual(score.base_similarity, 1.0)
        self.assertEqual(score.favorite_boost, 0.0)
        self.assertEqual(score.track_play_penalty, 0.0)
        self.assertEqual(score.album_play_penalty, 0.0)
        self.assertEqual(score.artist_play_penalty, 0.0)
        self.assertEqual(score.recency_penalty, 0.0)
        self.assertEqual(score.final_score, score.base_similarity)

    def test_score_explanations_carry_listening_adjustments(self) -> None:
        candidate = self.candidate(
            2,
            starred_at="2026-06-01T10:00:00+00:00",
            track_play_count=10,
            track_last_played_at=(self.fixed_now - timedelta(days=3)).isoformat(),
        )
        profile = build_recommendation_profile(
            (RecommendationProfileSeed(track_id=1),),
            {1: {"style:dream pop": 1.0}},
        )

        result = score_recommendation_candidates(
            profile,
            (candidate,),
            {2: {"style:dream pop": 1.0}},
            current_time=self.fixed_now,
        )[0]

        self.assertIs(result.explanation.score, result.score)
        self.assertEqual(result.explanation.score.favorite_boost, 0.05)
        self.assertEqual(result.explanation.score.track_play_penalty, 0.05)
        self.assertEqual(result.explanation.score.recency_penalty, 0.15)


class RecommendationDiversityRerankingTest(unittest.TestCase):
    fixed_now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)

    def result(
        self,
        track_id: int,
        *,
        artist: str = "",
        album_id: str | None = None,
        genres: tuple[str, ...] = ("Rock",),
        base_similarity: float = 1.0,
        track_last_played_at: str | None = None,
    ) -> RecommendationResult:
        candidate = RecommendationCandidate(
            metadata=CandidateMetadata(
                track_id=track_id,
                path=f"/music/{track_id}.flac",
                title=f"Track {track_id}",
                artist=artist,
                album_id=album_id,
                genres=genres,
            ),
            listening=ListeningStats(track_last_played_at=track_last_played_at),
        )
        score = RecommendationScore(base_similarity=base_similarity)
        return RecommendationResult(candidate=candidate, score=score)

    def test_default_reranking_caps_artist_and_album_clusters(self) -> None:
        results = (
            self.result(1, artist="Dominant Artist", album_id="album-a", base_similarity=0.99),
            self.result(2, artist="Dominant Artist", album_id="album-a", base_similarity=0.98),
            self.result(3, artist="Dominant Artist", album_id="album-a", base_similarity=0.97),
            self.result(4, artist="Dominant Artist", album_id="album-b", base_similarity=0.96),
            self.result(5, artist="Dominant Artist", album_id="album-c", base_similarity=0.95),
            self.result(6, artist="Second Artist", album_id="album-d", base_similarity=0.94),
            self.result(7, artist="Third Artist", album_id="album-e", base_similarity=0.93),
        )

        reranked = rerank_recommendation_results(results, 5)

        self.assertEqual(
            [result.candidate.metadata.track_id for result in reranked],
            [1, 6, 5, 7, 4],
        )
        for previous, current in zip(reranked, reranked[1:]):
            self.assertNotEqual(
                previous.candidate.metadata.album_id,
                current.candidate.metadata.album_id,
            )
            self.assertNotEqual(
                previous.candidate.metadata.artist,
                current.candidate.metadata.artist,
            )
        self.assertLessEqual(
            sum(
                result.candidate.metadata.artist == "Dominant Artist"
                for result in reranked
            ),
            3,
        )
        self.assertLessEqual(
            sum(result.candidate.metadata.album_id == "album-a" for result in reranked),
            2,
        )

    def test_reranking_breaks_alternating_album_pairs(self) -> None:
        results = (
            self.result(1, artist="Artist A1", album_id="album-a", base_similarity=0.99),
            self.result(2, artist="Artist B1", album_id="album-b", base_similarity=0.98),
            self.result(3, artist="Artist A2", album_id="album-a", base_similarity=0.97),
            self.result(4, artist="Artist B2", album_id="album-b", base_similarity=0.96),
            self.result(5, artist="Artist C", album_id="album-c", base_similarity=0.95),
            self.result(6, artist="Artist D", album_id="album-d", base_similarity=0.94),
        )

        reranked = rerank_recommendation_results(results, 6)

        self.assertEqual(
            [result.candidate.metadata.track_id for result in reranked],
            [1, 4, 5, 6],
        )
        self.assertNotIn(2, [result.candidate.metadata.track_id for result in reranked])
        self.assertNotIn(3, [result.candidate.metadata.track_id for result in reranked])
        self.assertNotEqual(
            [
                result.candidate.metadata.album_id
                for result in reranked[:4]
            ],
            ["album-a", "album-b", "album-a", "album-b"],
        )
        self.assertNotIn(
            "album-a",
            [
                result.candidate.metadata.album_id
                for result in reranked[1:4]
            ],
        )

    def test_reranking_caps_album_reuse_at_one_track(self) -> None:
        config = replace(
            recommendation_mode_config(),
            diversity_strength=DIVERSITY_STRENGTH_LOW,
        )
        results = (
            self.result(1, artist="Artist A1", album_id="album-a", base_similarity=0.99),
            self.result(2, artist="Artist B", album_id="album-b", base_similarity=0.98),
            self.result(3, artist="Artist A2", album_id="album-a", base_similarity=0.97),
            self.result(4, artist="Artist C", album_id="album-c", base_similarity=0.96),
            self.result(5, artist="Artist D", album_id="album-d", base_similarity=0.95),
        )

        reranked = rerank_recommendation_results(results, 5, config=config)

        self.assertEqual(
            [result.candidate.metadata.track_id for result in reranked],
            [1, 2, 4, 5],
        )
        self.assertEqual(
            [
                index
                for index, result in enumerate(reranked)
                if result.candidate.metadata.album_id == "album-a"
            ],
            [0],
        )

    def test_reranking_penalizes_repeated_artist_cadence(self) -> None:
        config = replace(
            recommendation_mode_config(),
            diversity_strength=DIVERSITY_STRENGTH_LOW,
            diversity_caps=DiversityCaps(
                max_tracks_per_artist=3,
                max_tracks_per_album=3,
                max_tracks_per_genre=20,
            ),
        )
        results = (
            self.result(1, artist="Artist A", album_id="album-a1", base_similarity=0.99),
            self.result(2, artist="Artist B", album_id="album-b1", base_similarity=0.98),
            self.result(3, artist="Artist C", album_id="album-c1", base_similarity=0.97),
            self.result(4, artist="Artist D", album_id="album-d1", base_similarity=0.96),
            self.result(5, artist="Artist E", album_id="album-e1", base_similarity=0.95),
            self.result(6, artist="Artist A", album_id="album-a2", base_similarity=0.94),
            self.result(7, artist="Artist B", album_id="album-b2", base_similarity=0.93),
            self.result(8, artist="Artist C", album_id="album-c2", base_similarity=0.92),
            self.result(9, artist="Artist D", album_id="album-d2", base_similarity=0.91),
            self.result(10, artist="Artist E", album_id="album-e2", base_similarity=0.90),
            self.result(11, artist="Artist F", album_id="album-f", base_similarity=0.89),
        )

        reranked = rerank_recommendation_results(results, 11, config=config)

        self.assertEqual(
            [
                result.candidate.metadata.artist
                for result in reranked[:8]
            ],
            [
                "Artist A",
                "Artist B",
                "Artist C",
                "Artist D",
                "Artist E",
                "Artist A",
                "Artist C",
                "Artist B",
            ],
        )

    def test_reranking_penalizes_repeated_album_cadence(self) -> None:
        config = replace(
            recommendation_mode_config(),
            diversity_strength=DIVERSITY_STRENGTH_LOW,
            diversity_caps=DiversityCaps(
                max_tracks_per_artist=3,
                max_tracks_per_album=3,
                max_tracks_per_genre=20,
            ),
        )
        results = (
            self.result(1, artist="Artist 1", album_id="album-a", base_similarity=0.99),
            self.result(2, artist="Artist 2", album_id="album-b", base_similarity=0.98),
            self.result(3, artist="Artist 3", album_id="album-c", base_similarity=0.97),
            self.result(4, artist="Artist 4", album_id="album-d", base_similarity=0.96),
            self.result(5, artist="Artist 5", album_id="album-e", base_similarity=0.95),
            self.result(6, artist="Artist 6", album_id="album-a", base_similarity=0.94),
            self.result(7, artist="Artist 7", album_id="album-b", base_similarity=0.93),
            self.result(8, artist="Artist 8", album_id="album-c", base_similarity=0.92),
            self.result(9, artist="Artist 9", album_id="album-d", base_similarity=0.91),
            self.result(10, artist="Artist 10", album_id="album-e", base_similarity=0.90),
            self.result(11, artist="Artist 11", album_id="album-f", base_similarity=0.89),
        )

        reranked = rerank_recommendation_results(results, 11, config=config)

        self.assertEqual(
            [
                result.candidate.metadata.album_id
                for result in reranked[:8]
            ],
            [
                "album-a",
                "album-b",
                "album-c",
                "album-d",
                "album-e",
                "album-a",
                "album-c",
                "album-b",
            ],
        )

    def test_artist_only_reranking_is_exempt_from_same_artist_cap(self) -> None:
        results = tuple(
            self.result(
                track_id,
                artist="Seed Artist",
                album_id=f"album-{track_id}",
                base_similarity=1.0 - (track_id / 100.0),
            )
            for track_id in range(1, 5)
        )

        reranked = rerank_recommendation_results(
            results,
            4,
            mode=RECOMMENDATION_MODE_ARTIST_ONLY,
        )

        self.assertEqual(
            [result.candidate.metadata.track_id for result in reranked],
            [1, 2, 3, 4],
        )

    def test_genre_cap_limits_repeated_primary_genres_when_configured(self) -> None:
        config = replace(
            recommendation_mode_config(),
            diversity_caps=DiversityCaps(
                max_tracks_per_artist=10,
                max_tracks_per_album=10,
                max_tracks_per_genre=2,
            ),
        )
        results = (
            self.result(1, artist="A", album_id="a", genres=("Rock",), base_similarity=0.90),
            self.result(2, artist="B", album_id="b", genres=("Rock",), base_similarity=0.80),
            self.result(3, artist="C", album_id="c", genres=("Rock",), base_similarity=0.70),
            self.result(4, artist="D", album_id="d", genres=("Jazz",), base_similarity=0.60),
        )

        reranked = rerank_recommendation_results(results, 3, config=config)

        self.assertEqual(
            [result.candidate.metadata.track_id for result in reranked],
            [1, 2, 4],
        )

    def test_reranking_preserves_score_order_when_caps_are_not_at_risk(self) -> None:
        results = (
            self.result(3, artist="C", album_id="c", base_similarity=0.70),
            self.result(1, artist="A", album_id="a", base_similarity=0.90),
            self.result(2, artist="B", album_id="b", base_similarity=0.80),
        )

        reranked = rerank_recommendation_results(results, 3)

        self.assertEqual(
            [result.candidate.metadata.track_id for result in reranked],
            [1, 2, 3],
        )

    def test_reranking_suppresses_recent_tracks_when_mode_configured(self) -> None:
        recently_played_at = (
            self.fixed_now - timedelta(hours=12)
        ).isoformat()
        results = (
            self.result(
                1,
                artist="Recent Artist",
                album_id="recent",
                base_similarity=0.99,
                track_last_played_at=recently_played_at,
            ),
            self.result(2, artist="Older Artist", album_id="older", base_similarity=0.98),
            self.result(3, artist="Other Artist", album_id="other", base_similarity=0.97),
        )

        reranked = rerank_recommendation_results(
            results,
            2,
            current_time=self.fixed_now,
        )

        self.assertEqual(
            [result.candidate.metadata.track_id for result in reranked],
            [2, 3],
        )


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


class RecommendationServiceTest(unittest.TestCase):
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
                    ("album-1", "Seed Album", 1992, 1),
                    ("album-2", "Closest Album", 1992, 1),
                    ("album-3", "Artist Drift", 1992, 1),
                    ("album-4", "Garage Album", 1985, 1),
                    ("album-5", "Quiet Album", 1975, 1),
                    ("album-6", "Quiet Album II", 1975, 1),
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
                        "/music/seed/01.flac",
                        "flac",
                        None,
                        "Seed Artist",
                        "Seed Artist",
                        "Seed Album",
                        "Seed Song",
                        "1992-04-01",
                    ),
                    (
                        2,
                        "album-2",
                        "/music/closest/01.flac",
                        "flac",
                        None,
                        "Other Artist",
                        "Other Artist",
                        "Closest Album",
                        "Closest Song",
                        "1992",
                    ),
                    (
                        3,
                        "album-3",
                        "/music/drift/01.flac",
                        "flac",
                        None,
                        "Seed Artist",
                        "Seed Artist",
                        "Artist Drift",
                        "Same Artist Drift",
                        "1992",
                    ),
                    (
                        4,
                        "album-4",
                        "/music/garage/01.flac",
                        "flac",
                        None,
                        "Garage Band",
                        "Garage Band",
                        "Garage Album",
                        "Genre Cousin",
                        "1985",
                    ),
                    (
                        5,
                        "album-5",
                        "/music/quiet/01.flac",
                        "flac",
                        None,
                        "Quiet Artist",
                        "Quiet Artist",
                        "Quiet Album",
                        "Quiet One",
                        "1975",
                    ),
                    (
                        6,
                        "album-6",
                        "/music/quiet/02.flac",
                        "flac",
                        None,
                        "Another Quiet Artist",
                        "Another Quiet Artist",
                        "Quiet Album II",
                        "Quiet Two",
                        "1975",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Rock"),
                    (2, 0, "Rock"),
                    (3, 0, "Modern Classical"),
                    (4, 0, "Rock"),
                    (5, 0, "Ambient"),
                    (6, 0, "Ambient"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Dream Pop"),
                    (2, 0, "Dream Pop"),
                    (3, 0, "Minimalism"),
                    (4, 0, "Garage Rock"),
                    (5, 0, "Drone"),
                    (6, 0, "Drone"),
                ),
            )
        return database

    def build_multi_seed_database(self) -> Path:
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
                    ("album-seed", "Two Moods", 2001, 2),
                    ("album-ambient", "Night Weather", 2001, 1),
                    ("album-rock", "Guitar Mirror", 1992, 1),
                    ("album-unrelated", "Brass Roads", 1970, 1),
                    ("album-artist-third", "Another Seed Study", 2010, 1),
                    ("album-minimal", "Minimal Cousin", 2010, 1),
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
                        "album-seed",
                        "/music/two-moods/01.flac",
                        "flac",
                        None,
                        "Seed Artist",
                        "Seed Artist",
                        "Two Moods",
                        "Guitar Light",
                        "1992",
                    ),
                    (
                        2,
                        "album-seed",
                        "/music/two-moods/02.flac",
                        "flac",
                        None,
                        "Guest Singer",
                        "Seed Artist",
                        "Two Moods",
                        "Cloud Room",
                        "2001",
                    ),
                    (
                        3,
                        "album-ambient",
                        "/music/night-weather/01.flac",
                        "flac",
                        None,
                        "Other Ambient",
                        "Other Ambient",
                        "Night Weather",
                        "Cloud Echo",
                        "2001",
                    ),
                    (
                        4,
                        "album-rock",
                        "/music/guitar-mirror/01.flac",
                        "flac",
                        None,
                        "Other Rock",
                        "Other Rock",
                        "Guitar Mirror",
                        "Guitar Echo",
                        "1992",
                    ),
                    (
                        5,
                        "album-unrelated",
                        "/music/brass-roads/01.flac",
                        "flac",
                        None,
                        "Brass Group",
                        "Brass Group",
                        "Brass Roads",
                        "Old Streets",
                        "1970",
                    ),
                    (
                        6,
                        "album-artist-third",
                        "/music/seed-study/01.flac",
                        "flac",
                        None,
                        "Seed Artist",
                        "Seed Artist",
                        "Another Seed Study",
                        "Small Pattern",
                        "2010",
                    ),
                    (
                        7,
                        "album-minimal",
                        "/music/minimal-cousin/01.flac",
                        "flac",
                        None,
                        "Pattern Ensemble",
                        "Pattern Ensemble",
                        "Minimal Cousin",
                        "Pattern Echo",
                        "2010",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Rock"),
                    (2, 0, "Ambient"),
                    (3, 0, "Ambient"),
                    (4, 0, "Rock"),
                    (5, 0, "Jazz"),
                    (6, 0, "Electronic"),
                    (7, 0, "Electronic"),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Dream Pop"),
                    (2, 0, "Drone"),
                    (3, 0, "Drone"),
                    (4, 0, "Dream Pop"),
                    (5, 0, "Post-Bop"),
                    (6, 0, "Minimalism"),
                    (7, 0, "Minimalism"),
                ),
            )
        return database

    def build_artist_only_database(self) -> Path:
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
                    ("album-seed", "The Pearl", 1984, 2),
                    ("album-eno", "Another Green World", 1975, 1),
                    ("album-budd", "The Pavilion of Dreams", 1978, 1),
                    ("album-laraaji", "Ambient Three", 1980, 1),
                    ("album-collab", "Fourth World", 1980, 1),
                    ("album-compilation", "Curated Ambient", 1981, 1),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_album_artists (album_id, position, artist)
                VALUES (?, ?, ?)
                """,
                (
                    ("album-seed", 0, "Brian Eno"),
                    ("album-seed", 1, "Harold Budd"),
                    ("album-eno", 0, "Brian Eno"),
                    ("album-budd", 0, "Harold Budd"),
                    ("album-laraaji", 0, "Laraaji"),
                    ("album-collab", 0, "Brian Eno"),
                    ("album-collab", 1, "Jon Hassell"),
                    ("album-compilation", 0, "Compilation Curator"),
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
                        "album-seed",
                        "/music/pearl/01.flac",
                        "flac",
                        None,
                        "Brian Eno",
                        "Brian Eno, Harold Budd",
                        "The Pearl",
                        "Late October",
                        "1984",
                    ),
                    (
                        2,
                        "album-seed",
                        "/music/pearl/02.flac",
                        "flac",
                        None,
                        "Harold Budd",
                        "Brian Eno, Harold Budd",
                        "The Pearl",
                        "A Stream With Bright Fish",
                        "1984",
                    ),
                    (
                        3,
                        "album-eno",
                        "/music/eno/01.flac",
                        "flac",
                        None,
                        "Brian Eno",
                        "Brian Eno",
                        "Another Green World",
                        "Becalmed",
                        "1975",
                    ),
                    (
                        4,
                        "album-budd",
                        "/music/budd/01.flac",
                        "flac",
                        None,
                        "Harold Budd",
                        "Harold Budd",
                        "The Pavilion of Dreams",
                        "Bismillahi Rrahmani Rrahim",
                        "1978",
                    ),
                    (
                        5,
                        "album-laraaji",
                        "/music/laraaji/01.flac",
                        "flac",
                        None,
                        "Laraaji",
                        "Laraaji",
                        "Ambient Three",
                        "The Dance",
                        "1980",
                    ),
                    (
                        6,
                        "album-collab",
                        "/music/fourth-world/01.flac",
                        "flac",
                        None,
                        "Jon Hassell",
                        "Brian Eno, Jon Hassell",
                        "Fourth World",
                        "Charm",
                        "1980",
                    ),
                    (
                        7,
                        "album-compilation",
                        "/music/curated/01.flac",
                        "flac",
                        None,
                        "Brian Eno",
                        "Compilation Curator",
                        "Curated Ambient",
                        "Licensed Eno Track",
                        "1981",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                tuple((track_id, 0, "Ambient") for track_id in range(1, 8)),
            )
            connection.executemany(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?)
                """,
                tuple((track_id, 0, "Minimal") for track_id in range(1, 8)),
            )
        return database

    def build_diversity_database(self) -> Path:
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
                    ("album-seed", "Seed Album", 1992, 1),
                    ("album-dominant-a", "Dominant A", 1992, 3),
                    ("album-dominant-b", "Dominant B", 1992, 1),
                    ("album-dominant-c", "Dominant C", 1992, 1),
                    ("album-second", "Second Album", 1992, 1),
                    ("album-third", "Third Album", 1992, 1),
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
                        "album-seed",
                        "/music/seed/01.flac",
                        "flac",
                        None,
                        "Seed Artist",
                        "Seed Artist",
                        "Seed Album",
                        "Seed Song",
                        "1992",
                    ),
                    (
                        2,
                        "album-dominant-a",
                        "/music/dominant-a/01.flac",
                        "flac",
                        None,
                        "Dominant Artist",
                        "Dominant Artist",
                        "Dominant A",
                        "Dominant One",
                        "1992",
                    ),
                    (
                        3,
                        "album-dominant-a",
                        "/music/dominant-a/02.flac",
                        "flac",
                        None,
                        "Dominant Artist",
                        "Dominant Artist",
                        "Dominant A",
                        "Dominant Two",
                        "1992",
                    ),
                    (
                        4,
                        "album-dominant-a",
                        "/music/dominant-a/03.flac",
                        "flac",
                        None,
                        "Dominant Artist",
                        "Dominant Artist",
                        "Dominant A",
                        "Dominant Three",
                        "1992",
                    ),
                    (
                        5,
                        "album-dominant-b",
                        "/music/dominant-b/01.flac",
                        "flac",
                        None,
                        "Dominant Artist",
                        "Dominant Artist",
                        "Dominant B",
                        "Dominant Four",
                        "1992",
                    ),
                    (
                        6,
                        "album-dominant-c",
                        "/music/dominant-c/01.flac",
                        "flac",
                        None,
                        "Dominant Artist",
                        "Dominant Artist",
                        "Dominant C",
                        "Dominant Five",
                        "1992",
                    ),
                    (
                        7,
                        "album-second",
                        "/music/second/01.flac",
                        "flac",
                        None,
                        "Second Artist",
                        "Second Artist",
                        "Second Album",
                        "Second Song",
                        "1992",
                    ),
                    (
                        8,
                        "album-third",
                        "/music/third/01.flac",
                        "flac",
                        None,
                        "Third Artist",
                        "Third Artist",
                        "Third Album",
                        "Third Song",
                        "1992",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                tuple((track_id, 0, "Rock") for track_id in range(1, 9)),
            )
            connection.executemany(
                """
                INSERT INTO library_track_styles (track_id, position, style)
                VALUES (?, ?, ?)
                """,
                tuple((track_id, 0, "Dream Pop") for track_id in range(1, 9)),
            )
        return database

    def build_album_radio_regression_database(self) -> Path:
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
                    ("album-seed", "Seed Electronic Album", 1997, 1),
                    *(
                        (
                            f"album-electronic-{index}",
                            f"Electronic Match {index}",
                            1997,
                            1,
                        )
                        for index in range(1, 11)
                    ),
                    ("album-classical", "Same Decade Classical", 1997, 1),
                    ("album-jazz", "Same Decade Jazz", 1997, 1),
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
                        "album-seed",
                        "/music/seed-electronic/01.flac",
                        "flac",
                        None,
                        "Seed Electronic Artist",
                        "Seed Electronic Artist",
                        "Seed Electronic Album",
                        "Seed Electronic Track",
                        "1997",
                    ),
                    *(
                        (
                            index + 1,
                            f"album-electronic-{index}",
                            f"/music/electronic-{index}/01.flac",
                            "flac",
                            None,
                            f"Electronic Artist {index}",
                            f"Electronic Artist {index}",
                            f"Electronic Match {index}",
                            f"Electronic Match Track {index}",
                            "1997",
                        )
                        for index in range(1, 11)
                    ),
                    (
                        12,
                        "album-classical",
                        "/music/classical/01.flac",
                        "flac",
                        None,
                        "Classical Artist",
                        "Classical Artist",
                        "Same Decade Classical",
                        "Same Decade Classical Track",
                        "1997",
                    ),
                    (
                        13,
                        "album-jazz",
                        "/music/jazz/01.flac",
                        "flac",
                        None,
                        "Jazz Artist",
                        "Jazz Artist",
                        "Same Decade Jazz",
                        "Same Decade Jazz Track",
                        "1997",
                    ),
                ),
            )
            connection.executemany(
                """
                INSERT INTO library_track_genres (track_id, position, genre)
                VALUES (?, ?, ?)
                """,
                (
                    (1, 0, "Electronic"),
                    *((track_id, 0, "Electronic") for track_id in range(2, 12)),
                    (12, 0, "Classical"),
                    (13, 0, "Jazz"),
                ),
            )
        return database

    def test_acceptance_fixture_track_radio_modes_match_source_plan(self) -> None:
        database = self.build_database()
        with connect_database(database, create=False) as connection:
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
                    "/music/closest/01.flac",
                    100,
                    "2026-01-01T00:00:00+00:00",
                    2,
                    "album-2",
                    "/music/closest/01.flac",
                    "Closest Song",
                    "Other Artist",
                    "Closest Album",
                ),
            )
        service = RecommendationService(database)

        default_results = service.get_track_radio(1, limit=10)
        default_by_id = {
            result.candidate.metadata.track_id: result
            for result in default_results
        }
        default_track_ids = [
            result.candidate.metadata.track_id
            for result in default_results
        ]

        self.assertIn(1, default_track_ids)
        self.assertEqual(default_track_ids[0], 1)
        self.assertEqual(default_track_ids[1], 2)
        self.assertEqual(default_by_id[2].explanation.matched_genres, ("Rock",))
        self.assertEqual(
            default_by_id[2].explanation.matched_styles,
            ("Dream Pop",),
        )
        self.assertEqual(default_by_id[2].explanation.matched_decade, "1990s")
        self.assertFalse(default_by_id[2].explanation.same_artist)

        genre_radio_results = service.get_genre_radio("Rock", limit=10)
        genre_radio_by_id = {
            result.candidate.metadata.track_id: result
            for result in genre_radio_results
        }
        self.assertIn(1, genre_radio_by_id)
        self.assertIn(2, genre_radio_by_id)
        self.assertIn(4, genre_radio_by_id)
        self.assertNotIn(3, genre_radio_by_id)
        self.assertEqual(genre_radio_by_id[2].explanation.matched_genres, ("Rock",))
        self.assertEqual(
            genre_radio_by_id[2].explanation.matched_styles,
            ("Dream Pop",),
        )

        artist_only_results = service.get_track_radio(
            1,
            mode=RECOMMENDATION_MODE_ARTIST_ONLY,
            limit=10,
        )
        self.assertEqual(
            [
                result.candidate.metadata.track_id
                for result in artist_only_results
            ],
            [1, 3],
        )

        discovery_by_id = {
            result.candidate.metadata.track_id: result
            for result in service.get_track_radio(
                1,
                mode=RECOMMENDATION_MODE_DISCOVERY,
                limit=10,
            )
        }
        self.assertGreater(
            discovery_by_id[2].score.track_play_penalty,
            default_by_id[2].score.track_play_penalty,
        )

        random_result = RecommendationService(
            database,
            random_source=FixedRandomSource(0.0),
        ).get_random_playlist(
            limit=1,
        )[0]
        self.assertEqual(random_result.candidate.metadata.track_id, 1)
        self.assertEqual(random_result.score.base_similarity, 0.0)
        self.assertEqual(random_result.explanation.matched_genres, ())
        self.assertIsNotNone(random_result.score.random_draw)

    def test_acceptance_fixture_album_and_artist_radio_match_source_plan(
        self,
    ) -> None:
        service = RecommendationService(self.build_multi_seed_database())

        album_results = service.get_album_radio("album-seed", limit=10)
        album_track_ids = [
            result.candidate.metadata.track_id
            for result in album_results
        ]
        album_by_id = {
            result.candidate.metadata.track_id: result
            for result in album_results
        }

        self.assertEqual(len({1, 2}.intersection(album_track_ids)), 1)
        self.assertEqual(
            len(
                {
                    result.candidate.metadata.album_id
                    for result in album_results
                }
            ),
            len(album_results),
        )
        self.assertGreater(album_by_id[3].score.base_similarity, 0.0)
        self.assertEqual(album_by_id[3].explanation.matched_genres, ("Ambient",))
        self.assertEqual(album_by_id[3].explanation.matched_styles, ("Drone",))
        self.assertGreater(album_by_id[4].score.base_similarity, 0.0)
        self.assertEqual(album_by_id[4].explanation.matched_genres, ("Rock",))
        self.assertEqual(album_by_id[4].explanation.matched_styles, ("Dream Pop",))

        artist_results = service.get_artist_radio("Seed Artist", limit=10)
        artist_track_ids = [
            result.candidate.metadata.track_id
            for result in artist_results
        ]
        self.assertIn(7, artist_track_ids)
        self.assertLessEqual(
            sum(
                result.candidate.metadata.artist == "Seed Artist"
                for result in artist_results
            ),
            3,
        )

        artist_only_results = service.get_artist_radio(
            "Seed Artist",
            mode=RECOMMENDATION_MODE_ARTIST_ONLY,
            limit=10,
        )
        for result in artist_only_results:
            metadata = result.candidate.metadata
            self.assertIn(
                "Seed Artist",
                (metadata.artist, metadata.album_artist, *metadata.album_artists),
            )

        random_playlist_results = RecommendationService(
            self.build_multi_seed_database(),
            random_source=FixedRandomSource(0.0, 0.0),
        ).get_random_playlist(
            limit=2,
        )
        self.assertEqual(
            [
                result.candidate.metadata.track_id
                for result in random_playlist_results
            ],
            [1, 3],
        )

    def test_album_radio_relaxes_genre_cap_and_omits_decade_only_fill(
        self,
    ) -> None:
        service = RecommendationService(self.build_album_radio_regression_database())

        results = service.get_album_radio("album-seed", limit=12)

        track_ids = [result.candidate.metadata.track_id for result in results]
        self.assertCountEqual(track_ids, list(range(1, 12)))
        self.assertNotEqual(track_ids, list(range(2, 12)))
        self.assertNotIn(12, track_ids)
        self.assertNotIn(13, track_ids)
        for result in results:
            self.assertEqual(result.candidate.metadata.genres, ("Electronic",))
            self.assertEqual(result.explanation.matched_genres, ("Electronic",))

    def test_track_radio_includes_seed_and_ranks_default_similarity(self) -> None:
        service = RecommendationService(self.build_database())

        results = service.get_track_radio(1, limit=10)

        track_ids = [result.candidate.metadata.track_id for result in results]
        self.assertEqual(track_ids, [1, 2, 4, 3])
        self.assertLess(track_ids.index(4), track_ids.index(3))
        self.assertNotIn(5, track_ids)
        self.assertNotIn(6, track_ids)

        results_by_id = {
            result.candidate.metadata.track_id: result
            for result in results
        }
        self.assertGreater(
            results_by_id[2].score.base_similarity,
            results_by_id[3].score.base_similarity,
        )
        self.assertEqual(
            results_by_id[2].score.final_score,
            results_by_id[2].score.base_similarity,
        )
        for result in results:
            self.assertIs(result.explanation.score, result.score)

        self.assertEqual(results_by_id[2].explanation.matched_genres, ("Rock",))
        self.assertEqual(
            results_by_id[2].explanation.matched_styles,
            ("Dream Pop",),
        )
        self.assertEqual(results_by_id[2].explanation.matched_decade, "1990s")
        self.assertFalse(results_by_id[2].explanation.same_artist)

        self.assertTrue(results_by_id[3].explanation.same_artist)
        self.assertEqual(results_by_id[3].explanation.matched_genres, ())
        self.assertEqual(results_by_id[3].explanation.matched_styles, ())
        self.assertEqual(results_by_id[3].explanation.matched_decade, "1990s")
        self.assertEqual(results_by_id[3].explanation.score.favorite_boost, 0.0)
        self.assertEqual(
            results_by_id[3].explanation.score.track_play_penalty,
            0.0,
        )
        self.assertEqual(
            results_by_id[3].explanation.score.artist_play_penalty,
            0.0,
        )
        self.assertEqual(
            results_by_id[3].explanation.score.album_play_penalty,
            0.0,
        )
        self.assertEqual(results_by_id[3].explanation.score.recency_penalty, 0.0)

    def test_track_radio_applies_mode_specific_listening_adjustments(self) -> None:
        database = self.build_database()
        recently_played_at = datetime.now(timezone.utc).isoformat()
        with connect_database(database, create=False) as connection:
            connection.execute(
                """
                INSERT INTO track_user_state (track_path, starred_at)
                VALUES (?, ?)
                """,
                ("/music/closest/01.flac", "2026-06-01T10:00:00+00:00"),
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
                    "/music/closest/01.flac",
                    100,
                    recently_played_at,
                    2,
                    "album-2",
                    "/music/closest/01.flac",
                    "Closest Song",
                    "Other Artist",
                    "Closest Album",
                ),
            )
        service = RecommendationService(database)

        default_result_list = service.get_track_radio(1, limit=10)
        discovery_result_list = service.get_track_radio(
            1,
            mode=RECOMMENDATION_MODE_DISCOVERY,
            limit=10,
        )
        default_track_ids = [
            result.candidate.metadata.track_id
            for result in default_result_list
        ]
        discovery_track_ids = [
            result.candidate.metadata.track_id
            for result in discovery_result_list
        ]
        self.assertNotIn(2, default_track_ids)
        self.assertNotIn(2, discovery_track_ids)
        self.assertEqual(default_track_ids[:2], [1, 4])
        self.assertEqual(discovery_track_ids[:2], [1, 4])

    def test_genre_radio_uses_default_features_with_hard_parent_genre_filter(
        self,
    ) -> None:
        service = RecommendationService(self.build_database())

        default_results = {
            result.candidate.metadata.track_id: result
            for result in service.get_track_radio(1, limit=10)
        }
        genre_radio_results = {
            result.candidate.metadata.track_id: result
            for result in service.get_genre_radio("Rock", limit=10)
        }

        self.assertGreater(
            default_results[2].score.base_similarity,
            default_results[4].score.base_similarity,
        )
        self.assertGreater(
            genre_radio_results[2].score.base_similarity,
            genre_radio_results[4].score.base_similarity,
        )
        self.assertIn(1, genre_radio_results)
        self.assertNotIn(3, genre_radio_results)
        self.assertEqual(
            genre_radio_results[2].explanation.matched_genres,
            ("Rock",),
        )
        self.assertEqual(
            genre_radio_results[2].explanation.matched_styles,
            ("Dream Pop",),
        )
        self.assertEqual(genre_radio_results[2].explanation.matched_decade, "1990s")

    def test_genre_radio_includes_style_derived_parent_genre_matches(self) -> None:
        service = RecommendationService(self.build_database())

        results = service.get_genre_radio("Classical", limit=10)

        self.assertEqual(
            [result.candidate.metadata.track_id for result in results],
            [3],
        )

    def test_artist_only_track_radio_filters_to_seed_track_artist(self) -> None:
        service = RecommendationService(self.build_database())

        results = service.get_track_radio(
            1,
            mode=RECOMMENDATION_MODE_ARTIST_ONLY,
            limit=10,
        )

        self.assertEqual(
            [result.candidate.metadata.track_id for result in results],
            [1, 3],
        )
        for result in results:
            self.assertTrue(result.explanation.same_artist)
            self.assertEqual(result.explanation.matched_genres, ())
            self.assertEqual(result.explanation.matched_styles, ())
            self.assertIsNone(result.explanation.matched_decade)

    def test_track_radio_applies_limit_after_ranking(self) -> None:
        service = RecommendationService(self.build_database())

        results = service.get_track_radio(1, limit=2)

        self.assertEqual(
            [result.candidate.metadata.track_id for result in results],
            [1, 2],
        )

    def test_default_track_radio_applies_diversity_caps(self) -> None:
        service = RecommendationService(self.build_diversity_database())

        results = service.get_track_radio(1, limit=5)

        track_ids = [result.candidate.metadata.track_id for result in results]
        self.assertEqual(track_ids, [1, 5, 7, 2, 8])
        self.assertNotIn(3, track_ids)
        self.assertNotIn(4, track_ids)
        for previous, current in zip(results, results[1:]):
            self.assertNotEqual(
                previous.candidate.metadata.album_id,
                current.candidate.metadata.album_id,
            )
            self.assertNotEqual(
                previous.candidate.metadata.artist,
                current.candidate.metadata.artist,
            )
        self.assertLessEqual(
            sum(
                result.candidate.metadata.artist == "Dominant Artist"
                for result in results
            ),
            3,
        )
        self.assertLessEqual(
            sum(
                result.candidate.metadata.album_id == "album-dominant-a"
                for result in results
            ),
            1,
        )

    def test_track_radio_missing_seed_raises_track_not_found(self) -> None:
        service = RecommendationService(self.build_database())

        with self.assertRaises(TrackNotFoundError):
            service.get_track_radio(404)

    def test_album_radio_allows_one_seed_album_track_and_uses_all_album_tracks(self) -> None:
        service = RecommendationService(self.build_multi_seed_database())

        results = service.get_album_radio("album-seed", limit=10)

        track_ids = [result.candidate.metadata.track_id for result in results]
        self.assertEqual(len({1, 2}.intersection(track_ids)), 1)
        self.assertEqual(
            len(
                {
                    result.candidate.metadata.album_id
                    for result in results
                }
            ),
            len(results),
        )

        results_by_id = {
            result.candidate.metadata.track_id: result
            for result in results
        }
        self.assertNotIn(5, results_by_id)
        self.assertGreater(results_by_id[3].score.base_similarity, 0.0)
        self.assertEqual(results_by_id[3].explanation.matched_genres, ("Ambient",))
        self.assertEqual(results_by_id[3].explanation.matched_styles, ("Drone",))

    def test_artist_radio_includes_seed_artist_and_uses_full_catalog_profile(
        self,
    ) -> None:
        service = RecommendationService(self.build_multi_seed_database())

        results = service.get_artist_radio("Seed Artist", limit=10)

        track_ids = [result.candidate.metadata.track_id for result in results]
        self.assertEqual(len({1, 2}.intersection(track_ids)), 1)
        self.assertIn(6, track_ids)

        results_by_id = {
            result.candidate.metadata.track_id: result
            for result in results
        }
        self.assertNotIn(5, results_by_id)
        self.assertGreater(results_by_id[7].score.base_similarity, 0.0)
        self.assertEqual(
            results_by_id[7].explanation.matched_genres,
            ("Electronic",),
        )
        self.assertEqual(
            results_by_id[7].explanation.matched_styles,
            ("Minimalism",),
        )

    def test_genre_radio_on_multi_seed_database_stays_inside_parent_genre(self) -> None:
        service = RecommendationService(self.build_multi_seed_database())

        ambient_results = {
            result.candidate.metadata.track_id: result
            for result in service.get_genre_radio("Ambient", limit=10)
        }

        self.assertEqual(set(ambient_results), {2, 3})
        self.assertEqual(
            ambient_results[3].explanation.matched_genres,
            ("Ambient",),
        )
        self.assertEqual(
            ambient_results[3].explanation.matched_styles,
            ("Drone",),
        )

    def test_artist_only_album_radio_uses_all_split_album_artists(self) -> None:
        service = RecommendationService(self.build_artist_only_database())

        results = service.get_album_radio(
            "album-seed",
            mode=RECOMMENDATION_MODE_ARTIST_ONLY,
            limit=10,
        )

        track_ids = [result.candidate.metadata.track_id for result in results]
        self.assertEqual(set(track_ids), {1, 3, 4, 6})
        self.assertNotIn(2, track_ids)
        self.assertNotIn(5, track_ids)
        self.assertNotIn(7, track_ids)

        results_by_id = {
            result.candidate.metadata.track_id: result
            for result in results
        }
        self.assertEqual(
            results_by_id[6].candidate.metadata.album_artists,
            ("Brian Eno", "Jon Hassell"),
        )
        self.assertTrue(results_by_id[3].explanation.same_artist)
        self.assertTrue(results_by_id[4].explanation.same_artist)

    def test_artist_only_artist_radio_returns_fewer_without_unrelated_artists(
        self,
    ) -> None:
        service = RecommendationService(self.build_database())

        results = service.get_artist_radio(
            "Quiet Artist",
            mode=RECOMMENDATION_MODE_ARTIST_ONLY,
            limit=10,
        )

        self.assertEqual(
            [result.candidate.metadata.track_id for result in results],
            [5],
        )

    def test_random_playlist_uses_deterministic_weighted_sampling(self) -> None:
        service = RecommendationService(
            self.build_database(),
            random_source=FixedRandomSource(0.99, 0.0, 0.51),
        )

        results = service.get_random_playlist(
            limit=3,
        )

        self.assertEqual(
            [result.candidate.metadata.track_id for result in results],
            [6, 1, 4],
        )
        self.assertEqual(
            [result.score.random_draw for result in results],
            [0.99, 0.0, 0.51],
        )
        for result in results:
            self.assertEqual(result.score.base_similarity, 0.0)
            self.assertEqual(result.score.random_recency_multiplier, 1.0)
            self.assertEqual(result.score.random_play_count_multiplier, 1.0)
            self.assertEqual(result.score.random_selection_weight, 1.0)
            self.assertEqual(result.score.final_score, 1.0)
            self.assertIs(result.explanation.score, result.score)

    def test_random_playlist_recent_tracks_are_less_likely_but_eligible(
        self,
    ) -> None:
        database = self.build_database()
        recently_played_at = (
            datetime.now(timezone.utc) - timedelta(hours=12)
        ).isoformat()
        with connect_database(database, create=False) as connection:
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
                    "/music/closest/01.flac",
                    0,
                    recently_played_at,
                    2,
                    "album-2",
                    "/music/closest/01.flac",
                    "Closest Song",
                    "Other Artist",
                    "Closest Album",
                ),
            )
        service = RecommendationService(
            database,
            random_source=FixedRandomSource(0.21),
        )

        result = service.get_random_playlist(
            limit=1,
        )[0]

        self.assertEqual(result.candidate.metadata.track_id, 2)
        self.assertEqual(result.score.random_draw, 0.21)
        self.assertEqual(result.score.random_recency_multiplier, 0.10)
        self.assertEqual(result.score.random_play_count_multiplier, 1.0)
        self.assertEqual(result.score.random_selection_weight, 0.10)

    def test_random_playlist_includes_former_seed_tracks(self) -> None:
        service = RecommendationService(
            self.build_multi_seed_database(),
            random_source=FixedRandomSource(0.0, 0.0),
        )

        results = service.get_random_playlist(
            limit=2,
        )

        self.assertEqual(
            [result.candidate.metadata.track_id for result in results],
            [1, 3],
        )

    def test_random_playlist_applies_artist_and_album_caps(self) -> None:
        service = RecommendationService(
            self.build_diversity_database(),
            random_source=FixedRandomSource(0.0, 0.0, 0.0, 0.0, 0.0),
        )

        results = service.get_random_playlist(
            limit=5,
        )

        track_ids = [result.candidate.metadata.track_id for result in results]
        self.assertEqual(track_ids, [1, 2, 5, 6, 7])
        self.assertNotIn(4, track_ids)
        self.assertNotIn(3, track_ids)
        self.assertLessEqual(
            sum(
                result.candidate.metadata.artist == "Dominant Artist"
                for result in results
            ),
            3,
        )
        self.assertLessEqual(
            sum(
                result.candidate.metadata.album_id == "album-dominant-a"
                for result in results
            ),
            1,
        )

    def test_random_playlist_samples_available_library(self) -> None:
        service = RecommendationService(
            self.build_multi_seed_database(),
            random_source=FixedRandomSource(0.99),
        )

        results = service.get_random_playlist(
            limit=1,
        )

        self.assertEqual(
            [result.candidate.metadata.track_id for result in results],
            [7],
        )

    def test_discovery_album_and_artist_radio_can_run(self) -> None:
        service = RecommendationService(self.build_multi_seed_database())

        album_results = service.get_album_radio(
            "album-seed",
            mode=RECOMMENDATION_MODE_DISCOVERY,
            limit=10,
        )
        artist_results = service.get_artist_radio(
            "Seed Artist",
            mode=RECOMMENDATION_MODE_DISCOVERY,
            limit=10,
        )

        self.assertGreater(len(album_results), 0)
        self.assertGreater(len(artist_results), 0)

    def test_invalid_modes_are_rejected_by_radio_surfaces(self) -> None:
        service = RecommendationService(self.build_multi_seed_database())

        with self.assertRaises(RecommendationModeError):
            service.get_track_radio(1, mode="ambient_only")
        with self.assertRaises(RecommendationModeError):
            service.get_album_radio("album-seed", mode="ambient_only")
        with self.assertRaises(RecommendationModeError):
            service.get_artist_radio("Seed Artist", mode="ambient_only")

    def test_album_and_artist_radio_missing_seeds_raise_query_errors(self) -> None:
        service = RecommendationService(self.build_multi_seed_database())

        with self.assertRaises(AlbumNotFoundError):
            service.get_album_radio("missing-album")
        with self.assertRaises(ArtistNotFoundError):
            service.get_artist_radio("Missing Artist")


if __name__ == "__main__":
    unittest.main()

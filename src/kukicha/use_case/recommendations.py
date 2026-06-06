from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from sqlite3 import Connection, Row
from types import MappingProxyType
from typing import Literal, cast

from ..album_artists import normalized_album_artist_values
from ..models import normalize_genre_values
from ..text import normalize_text
from .database import connect_existing_database
from .library import split_genres_and_styles
from .queries.library import taxonomy_sets, track_values_by_track
from .queries.models import TrackNotFoundError
from .queries.sql import placeholders_for


RecommendationMode = Literal[
    "default",
    "discovery",
    "genre_only",
    "artist_only",
    "random",
]

RECOMMENDATION_MODE_DEFAULT: RecommendationMode = "default"
RECOMMENDATION_MODE_DISCOVERY: RecommendationMode = "discovery"
RECOMMENDATION_MODE_GENRE_ONLY: RecommendationMode = "genre_only"
RECOMMENDATION_MODE_ARTIST_ONLY: RecommendationMode = "artist_only"
RECOMMENDATION_MODE_RANDOM: RecommendationMode = "random"

SUPPORTED_RECOMMENDATION_MODES: tuple[RecommendationMode, ...] = (
    RECOMMENDATION_MODE_DEFAULT,
    RECOMMENDATION_MODE_DISCOVERY,
    RECOMMENDATION_MODE_GENRE_ONLY,
    RECOMMENDATION_MODE_ARTIST_ONLY,
    RECOMMENDATION_MODE_RANDOM,
)
RECOMMENDATION_MODE_VALUES = frozenset(SUPPORTED_RECOMMENDATION_MODES)

ARTIST_ONLY_FALLBACK_RETURN_FEWER = "return_fewer"
ARTIST_ONLY_FALLBACK_DEFAULT_MODE = "default_mode"
ArtistOnlyFallback = Literal["return_fewer", "default_mode"]

CANDIDATE_FILTER_ARTIST_MATCH_REQUIRED = "artist_match_required"
CandidateFilter = Literal["artist_match_required"]

CANDIDATE_SELECTION_WEIGHTED_RANDOM = "weighted_random"
CandidateSelection = Literal["weighted_random"]

RECENT_PLAY_PENALTY_MEDIUM = "medium"
RECENT_PLAY_PENALTY_HIGH = "high"
RECENT_PLAY_PENALTY_RANDOM_WEIGHTED = "random_weighted"
RecentPlayPenaltyStrength = Literal["medium", "high", "random_weighted"]

DIVERSITY_STRENGTH_LOW = "low"
DIVERSITY_STRENGTH_MEDIUM = "medium"
DIVERSITY_STRENGTH_HIGH = "high"
DiversityStrength = Literal["low", "medium", "high"]

DEFAULT_RECOMMENDATION_LIMIT = 25
MAX_RECOMMENDATION_LIMIT = 500
YEAR_PATTERN = re.compile(r"(?<!\d)(\d{4})(?!\d)")
SparseVector = dict[str, float]

GENRE_FEATURE_PREFIX = "genre"
STYLE_FEATURE_PREFIX = "style"
ARTIST_FEATURE_PREFIX = "artist"
DECADE_FEATURE_PREFIX = "decade"


class RecommendationModeError(ValueError):
    """Raised when a recommendation mode name is not supported."""


class RecommendationLimitError(ValueError):
    """Raised when a recommendation limit cannot be parsed."""


def normalize_recommendation_mode(value: object | None) -> RecommendationMode:
    if value is None:
        return RECOMMENDATION_MODE_DEFAULT
    normalized = str(value).strip().casefold()
    if not normalized:
        return RECOMMENDATION_MODE_DEFAULT
    if normalized in RECOMMENDATION_MODE_VALUES:
        return cast(RecommendationMode, normalized)
    supported = ", ".join(SUPPORTED_RECOMMENDATION_MODES)
    raise RecommendationModeError(
        f"unsupported recommendation mode: {value!r}; expected one of: {supported}"
    )


def normalize_recommendation_limit(
    value: object | None,
    *,
    default: int = DEFAULT_RECOMMENDATION_LIMIT,
    max_limit: int = MAX_RECOMMENDATION_LIMIT,
) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        value = default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise RecommendationLimitError(f"invalid recommendation limit: {value!r}") from error
    return min(max_limit, max(1, parsed))


@dataclass(frozen=True, slots=True)
class FeatureWeights:
    genres: float = 0.0
    styles: float = 0.0
    artist: float = 0.0
    decade: float = 0.0

    def __post_init__(self) -> None:
        for name in ("genres", "styles", "artist", "decade"):
            value = float(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} feature weight must not be negative")
            object.__setattr__(self, name, value)

    @property
    def uses_content_similarity(self) -> bool:
        return any((self.genres, self.styles, self.artist, self.decade))

    def as_dict(self) -> dict[str, float]:
        return {
            "genres": self.genres,
            "styles": self.styles,
            "artist": self.artist,
            "decade": self.decade,
        }


@dataclass(frozen=True, slots=True)
class RecencyPenalties:
    played_last_24_hours: float = 0.0
    played_last_7_days: float = 0.0
    played_last_30_days: float = 0.0
    older_or_never_played: float = 0.0

    def penalty_for_age_days(self, days_since_played: float | None) -> float:
        if days_since_played is None or days_since_played > 30:
            return self.older_or_never_played
        if days_since_played <= 1:
            return self.played_last_24_hours
        if days_since_played <= 7:
            return self.played_last_7_days
        return self.played_last_30_days


@dataclass(frozen=True, slots=True)
class RandomRecencyMultipliers:
    played_last_24_hours: float = 0.10
    played_last_7_days: float = 0.35
    played_last_30_days: float = 0.70
    older_or_never_played: float = 1.00

    def multiplier_for_age_days(self, days_since_played: float | None) -> float:
        if days_since_played is None or days_since_played > 30:
            return self.older_or_never_played
        if days_since_played <= 1:
            return self.played_last_24_hours
        if days_since_played <= 7:
            return self.played_last_7_days
        return self.played_last_30_days


@dataclass(frozen=True, slots=True)
class DiversityCaps:
    max_tracks_per_artist: int = 3
    max_tracks_per_album: int = 2
    max_tracks_per_genre: int = 8
    top_track_count: int = 25
    apply_artist_cap: bool = True
    apply_album_cap: bool = True
    apply_genre_cap: bool = True


@dataclass(frozen=True, slots=True)
class RecommendationModeConfig:
    mode: RecommendationMode
    feature_weights: FeatureWeights
    track_play_penalty: float = 0.0
    artist_play_penalty: float = 0.0
    album_play_penalty: float = 0.0
    favorite_boost: float = 0.0
    recency_penalties: RecencyPenalties = field(default_factory=RecencyPenalties)
    diversity_caps: DiversityCaps = field(default_factory=DiversityCaps)
    recent_play_penalty_strength: RecentPlayPenaltyStrength = RECENT_PLAY_PENALTY_MEDIUM
    diversity_strength: DiversityStrength = DIVERSITY_STRENGTH_MEDIUM
    candidate_filter: CandidateFilter | None = None
    candidate_selection: CandidateSelection | None = None
    artist_only_fallback: ArtistOnlyFallback = ARTIST_ONLY_FALLBACK_RETURN_FEWER
    exclude_seed_album_tracks: bool = True
    random_recency_multipliers: RandomRecencyMultipliers | None = None
    random_track_play_count_weight: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_recommendation_mode(self.mode))

    @property
    def uses_weighted_random_selection(self) -> bool:
        return self.candidate_selection == CANDIDATE_SELECTION_WEIGHTED_RANDOM


@dataclass(frozen=True, slots=True)
class RecommendationConfig:
    modes: Mapping[RecommendationMode, RecommendationModeConfig]
    default_limit: int = DEFAULT_RECOMMENDATION_LIMIT
    max_limit: int = MAX_RECOMMENDATION_LIMIT

    def __post_init__(self) -> None:
        normalized_max_limit = normalize_recommendation_limit(
            self.max_limit,
            default=MAX_RECOMMENDATION_LIMIT,
            max_limit=MAX_RECOMMENDATION_LIMIT,
        )
        normalized_modes = {
            normalize_recommendation_mode(mode): config
            for mode, config in self.modes.items()
        }
        object.__setattr__(self, "modes", MappingProxyType(normalized_modes))
        object.__setattr__(
            self,
            "default_limit",
            normalize_recommendation_limit(
                self.default_limit,
                default=DEFAULT_RECOMMENDATION_LIMIT,
                max_limit=normalized_max_limit,
            ),
        )
        object.__setattr__(self, "max_limit", normalized_max_limit)

    def mode_config(self, mode: object | None = None) -> RecommendationModeConfig:
        normalized_mode = normalize_recommendation_mode(mode)
        return self.modes[normalized_mode]

    def normalize_limit(self, value: object | None) -> int:
        return normalize_recommendation_limit(
            value,
            default=self.default_limit,
            max_limit=self.max_limit,
        )


@dataclass(frozen=True, slots=True)
class RecommendationRequest:
    mode: RecommendationMode = RECOMMENDATION_MODE_DEFAULT
    limit: int = DEFAULT_RECOMMENDATION_LIMIT

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", normalize_recommendation_mode(self.mode))
        object.__setattr__(self, "limit", normalize_recommendation_limit(self.limit))


@dataclass(frozen=True, slots=True)
class CandidateMetadata:
    track_id: int
    path: str
    title: str = ""
    artist: str = ""
    album_artist: str = ""
    album_id: str | None = None
    album: str = ""
    date: str | None = None
    decade: str | None = None
    genres: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    starred_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", int(self.track_id))
        object.__setattr__(self, "path", str(self.path))
        object.__setattr__(self, "title", str(self.title or ""))
        object.__setattr__(self, "artist", str(self.artist or ""))
        object.__setattr__(self, "album_artist", str(self.album_artist or ""))
        object.__setattr__(self, "album_id", normalized_optional_text(self.album_id))
        object.__setattr__(self, "album", str(self.album or ""))
        object.__setattr__(self, "date", normalized_optional_text(self.date))
        object.__setattr__(self, "decade", normalized_optional_text(self.decade))
        object.__setattr__(self, "genres", normalized_unique_text_tuple(self.genres))
        object.__setattr__(self, "styles", normalized_unique_text_tuple(self.styles))
        object.__setattr__(self, "starred_at", normalized_optional_text(self.starred_at))

    @property
    def is_favorite(self) -> bool:
        return self.starred_at is not None


@dataclass(frozen=True, slots=True)
class ListeningStats:
    track_play_count: int = 0
    album_play_count: int = 0
    artist_play_count: int = 0
    track_last_played_at: str | None = None
    album_last_played_at: str | None = None
    artist_last_played_at: str | None = None

    def __post_init__(self) -> None:
        for name in ("track_play_count", "album_play_count", "artist_play_count"):
            object.__setattr__(self, name, max(0, int(getattr(self, name))))


@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    metadata: CandidateMetadata
    listening: ListeningStats = field(default_factory=ListeningStats)


@dataclass(frozen=True, slots=True)
class RecommendationProfileSeed:
    track_id: int
    weight: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", int(self.track_id))
        weight = float(self.weight)
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("recommendation profile seed weight must be non-negative")
        object.__setattr__(self, "weight", weight)


@dataclass(frozen=True, slots=True)
class RecommendationProfile:
    vector: Mapping[str, float] = field(default_factory=dict)
    seed_track_ids: tuple[int, ...] = ()
    total_seed_weight: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "vector",
            MappingProxyType(sparse_vector_copy(self.vector)),
        )
        object.__setattr__(
            self,
            "seed_track_ids",
            tuple(dict.fromkeys(int(track_id) for track_id in self.seed_track_ids)),
        )
        object.__setattr__(self, "total_seed_weight", float(self.total_seed_weight))

    @property
    def has_seed_tracks(self) -> bool:
        return bool(self.seed_track_ids)

    @property
    def has_vector(self) -> bool:
        return bool(self.vector)

    @property
    def is_cold_start(self) -> bool:
        return not self.has_vector


@dataclass(frozen=True, slots=True)
class RecommendationVocabulary:
    document_count: int = 0
    genre_terms: tuple[str, ...] = ()
    style_terms: tuple[str, ...] = ()
    artist_terms: tuple[str, ...] = ()
    decade_terms: tuple[str, ...] = ()
    genre_idf: Mapping[str, float] = field(default_factory=dict)
    style_idf: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_count", max(0, int(self.document_count)))
        object.__setattr__(
            self,
            "genre_terms",
            normalized_feature_terms(self.genre_terms),
        )
        object.__setattr__(
            self,
            "style_terms",
            normalized_feature_terms(self.style_terms),
        )
        object.__setattr__(
            self,
            "artist_terms",
            normalized_feature_terms(self.artist_terms),
        )
        object.__setattr__(
            self,
            "decade_terms",
            normalized_decade_terms(self.decade_terms),
        )
        object.__setattr__(
            self,
            "genre_idf",
            MappingProxyType(normalized_float_mapping(self.genre_idf)),
        )
        object.__setattr__(
            self,
            "style_idf",
            MappingProxyType(normalized_float_mapping(self.style_idf)),
        )

    @property
    def genre_features(self) -> tuple[str, ...]:
        return tuple(
            recommendation_feature_key(GENRE_FEATURE_PREFIX, term)
            for term in self.genre_terms
        )

    @property
    def style_features(self) -> tuple[str, ...]:
        return tuple(
            recommendation_feature_key(STYLE_FEATURE_PREFIX, term)
            for term in self.style_terms
        )

    @property
    def artist_features(self) -> tuple[str, ...]:
        return tuple(
            recommendation_feature_key(ARTIST_FEATURE_PREFIX, term)
            for term in self.artist_terms
        )

    @property
    def decade_features(self) -> tuple[str, ...]:
        return tuple(
            recommendation_feature_key(DECADE_FEATURE_PREFIX, term)
            for term in self.decade_terms
        )


@dataclass(frozen=True, slots=True)
class RecommendationScore:
    base_similarity: float = 0.0
    favorite_boost: float = 0.0
    track_play_penalty: float = 0.0
    artist_play_penalty: float = 0.0
    album_play_penalty: float = 0.0
    recency_penalty: float = 0.0
    random_draw: float | None = None
    random_recency_multiplier: float | None = None
    random_play_count_multiplier: float | None = None
    random_selection_weight: float | None = None

    @property
    def final_score(self) -> float:
        if self.random_selection_weight is not None:
            return self.random_selection_weight
        return (
            self.base_similarity
            + self.favorite_boost
            - self.track_play_penalty
            - self.artist_play_penalty
            - self.album_play_penalty
            - self.recency_penalty
        )


@dataclass(frozen=True, slots=True)
class RecommendationExplanation:
    matched_genres: tuple[str, ...] = ()
    matched_styles: tuple[str, ...] = ()
    matched_decade: str | None = None
    same_artist: bool = False
    score: RecommendationScore = field(default_factory=RecommendationScore)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "matched_genres",
            normalized_unique_text_tuple(self.matched_genres),
        )
        object.__setattr__(
            self,
            "matched_styles",
            normalized_unique_text_tuple(self.matched_styles),
        )


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    candidate: RecommendationCandidate
    score: RecommendationScore
    explanation: RecommendationExplanation = field(default_factory=RecommendationExplanation)

    @property
    def final_score(self) -> float:
        return self.score.final_score


class RecommendationQueries:
    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)

    def list_candidates(self) -> tuple[RecommendationCandidate, ...]:
        with connect_existing_database(self.database) as connection:
            return load_recommendation_candidates(connection)

    def get_candidate(self, track_id: int) -> RecommendationCandidate:
        with connect_existing_database(self.database) as connection:
            return load_recommendation_candidate(connection, track_id)


class RecommendationService:
    def __init__(self, database: str | Path) -> None:
        self.queries = RecommendationQueries(database)

    def get_track_radio(
        self,
        track_id: int,
        mode: object | None = RECOMMENDATION_MODE_DEFAULT,
        limit: object | None = DEFAULT_RECOMMENDATION_LIMIT,
    ) -> tuple[RecommendationResult, ...]:
        normalized_track_id = int(track_id)
        config = recommendation_mode_config(mode)
        normalized_limit = RECOMMENDATION_CONFIG.normalize_limit(limit)
        candidates = self.queries.list_candidates()
        if (
            recommendation_candidate_by_track_id(candidates, normalized_track_id)
            is None
        ):
            raise TrackNotFoundError(normalized_track_id)

        vocabulary = build_recommendation_vocabulary(candidates)
        track_vectors = build_recommendation_track_vectors(
            candidates,
            mode=config.mode,
            vocabulary=vocabulary,
        )
        profile = build_recommendation_track_profile(
            normalized_track_id,
            track_vectors,
        )
        scored = score_recommendation_candidates(
            profile,
            candidates,
            track_vectors,
            exclude_track_ids=(normalized_track_id,),
        )
        return rank_recommendation_results(scored)[:normalized_limit]


def recommendation_mode_config(mode: object | None = None) -> RecommendationModeConfig:
    return RECOMMENDATION_CONFIG.mode_config(mode)


def load_recommendation_candidates(
    connection: Connection,
) -> tuple[RecommendationCandidate, ...]:
    rows = recommendation_candidate_rows(connection)
    return recommendation_candidates_from_rows(connection, rows)


def load_recommendation_candidate(
    connection: Connection,
    track_id: int,
) -> RecommendationCandidate:
    normalized_track_id = int(track_id)
    rows = recommendation_candidate_rows(connection, track_ids=(normalized_track_id,))
    candidates = recommendation_candidates_from_rows(connection, rows)
    if not candidates:
        raise TrackNotFoundError(normalized_track_id)
    return candidates[0]


def build_recommendation_vocabulary(
    candidates: Iterable[RecommendationCandidate],
) -> RecommendationVocabulary:
    candidate_pool = tuple(candidates)
    genre_document_counts = recommendation_term_document_counts(
        candidate_pool,
        lambda metadata: metadata.genres,
    )
    style_document_counts = recommendation_term_document_counts(
        candidate_pool,
        lambda metadata: metadata.styles,
    )
    artist_terms = sorted(
        {
            term
            for candidate in candidate_pool
            if (term := recommendation_artist_term(candidate.metadata))
        }
    )
    decade_terms = sorted(
        {
            term
            for candidate in candidate_pool
            if (term := normalize_decade_term(candidate.metadata.decade))
        },
        key=decade_sort_key,
    )
    document_count = len(candidate_pool)
    return RecommendationVocabulary(
        document_count=document_count,
        genre_terms=tuple(sorted(genre_document_counts)),
        style_terms=tuple(sorted(style_document_counts)),
        artist_terms=tuple(artist_terms),
        decade_terms=tuple(decade_terms),
        genre_idf={
            term: recommendation_inverse_document_frequency(
                document_count,
                document_frequency,
            )
            for term, document_frequency in genre_document_counts.items()
        },
        style_idf={
            term: recommendation_inverse_document_frequency(
                document_count,
                document_frequency,
            )
            for term, document_frequency in style_document_counts.items()
        },
    )


def build_recommendation_track_vectors(
    candidates: Iterable[RecommendationCandidate],
    *,
    mode: object | None = None,
    vocabulary: RecommendationVocabulary | None = None,
) -> dict[int, SparseVector]:
    candidate_pool = tuple(candidates)
    if vocabulary is None:
        vocabulary = build_recommendation_vocabulary(candidate_pool)
    return {
        candidate.metadata.track_id: build_recommendation_track_vector(
            candidate,
            vocabulary,
            mode=mode,
        )
        for candidate in candidate_pool
    }


def build_recommendation_track_vector(
    candidate: RecommendationCandidate,
    vocabulary: RecommendationVocabulary,
    *,
    mode: object | None = None,
) -> SparseVector:
    config = recommendation_mode_config(mode)
    weights = config.feature_weights
    vector: SparseVector = {}
    vector.update(
        weighted_feature_group_vector(
            recommendation_genre_feature_vector(candidate, vocabulary),
            weights.genres,
        )
    )
    vector.update(
        weighted_feature_group_vector(
            recommendation_style_feature_vector(candidate, vocabulary),
            weights.styles,
        )
    )
    vector.update(
        weighted_feature_group_vector(
            recommendation_artist_feature_vector(candidate, vocabulary),
            weights.artist,
        )
    )
    vector.update(
        weighted_feature_group_vector(
            recommendation_decade_feature_vector(candidate, vocabulary),
            weights.decade,
        )
    )
    return normalize_sparse_vector(vector)


def build_recommendation_track_profile(
    track_id: int,
    track_vectors: Mapping[int, Mapping[str, float]],
) -> RecommendationProfile:
    return build_recommendation_profile(
        (RecommendationProfileSeed(track_id=track_id),),
        track_vectors,
    )


def build_recommendation_album_profile(
    album_id: object | None,
    candidates: Iterable[RecommendationCandidate],
    track_vectors: Mapping[int, Mapping[str, float]],
) -> RecommendationProfile:
    normalized_album_id = normalized_optional_text(album_id)
    if normalized_album_id is None:
        return RecommendationProfile()
    return build_recommendation_profile(
        (
            RecommendationProfileSeed(track_id=candidate.metadata.track_id)
            for candidate in candidates
            if candidate.metadata.album_id == normalized_album_id
        ),
        track_vectors,
    )


def build_recommendation_artist_profile(
    artist: object | None,
    candidates: Iterable[RecommendationCandidate],
    track_vectors: Mapping[int, Mapping[str, float]],
) -> RecommendationProfile:
    artist_terms = set(recommendation_artist_terms((artist,)))
    if not artist_terms:
        return RecommendationProfile()
    return build_recommendation_profile(
        (
            RecommendationProfileSeed(track_id=candidate.metadata.track_id)
            for candidate in candidates
            if artist_terms.intersection(
                recommendation_metadata_artist_terms(candidate.metadata)
            )
        ),
        track_vectors,
    )


def build_recommendation_user_profile(
    seeds: Iterable[RecommendationProfileSeed],
    track_vectors: Mapping[int, Mapping[str, float]],
) -> RecommendationProfile:
    return build_recommendation_profile(seeds, track_vectors)


def build_recommendation_profile(
    seeds: Iterable[RecommendationProfileSeed],
    track_vectors: Mapping[int, Mapping[str, float]],
) -> RecommendationProfile:
    seed_track_ids: list[int] = []
    weighted_vectors: list[tuple[Mapping[str, float], float]] = []
    total_seed_weight = 0.0
    for seed in seeds:
        normalized_seed = RecommendationProfileSeed(seed.track_id, seed.weight)
        if (
            normalized_seed.weight <= 0
            or normalized_seed.track_id not in track_vectors
        ):
            continue
        seed_track_ids.append(normalized_seed.track_id)
        total_seed_weight += normalized_seed.weight
        vector = track_vectors[normalized_seed.track_id]
        if vector:
            weighted_vectors.append((vector, normalized_seed.weight))
    return RecommendationProfile(
        vector=weighted_average_sparse_vectors(weighted_vectors),
        seed_track_ids=tuple(seed_track_ids),
        total_seed_weight=total_seed_weight,
    )


def score_recommendation_candidate(
    profile: RecommendationProfile,
    candidate: RecommendationCandidate,
    track_vector: Mapping[str, float],
) -> RecommendationResult:
    score = RecommendationScore(
        base_similarity=sparse_cosine_similarity(profile.vector, track_vector),
    )
    return RecommendationResult(
        candidate=candidate,
        score=score,
        explanation=RecommendationExplanation(score=score),
    )


def score_recommendation_candidates(
    profile: RecommendationProfile,
    candidates: Iterable[RecommendationCandidate],
    track_vectors: Mapping[int, Mapping[str, float]],
    *,
    exclude_track_ids: Iterable[int] = (),
) -> tuple[RecommendationResult, ...]:
    excluded_track_ids = set(int(track_id) for track_id in exclude_track_ids)
    return tuple(
        score_recommendation_candidate(
            profile,
            candidate,
            track_vectors.get(candidate.metadata.track_id, {}),
        )
        for candidate in candidates
        if candidate.metadata.track_id not in excluded_track_ids
    )


def rank_recommendation_results(
    results: Iterable[RecommendationResult],
) -> tuple[RecommendationResult, ...]:
    return tuple(
        sorted(
            results,
            key=lambda result: (
                -result.final_score,
                result.candidate.metadata.track_id,
            ),
        )
    )


def recommendation_candidate_by_track_id(
    candidates: Iterable[RecommendationCandidate],
    track_id: int,
) -> RecommendationCandidate | None:
    normalized_track_id = int(track_id)
    for candidate in candidates:
        if candidate.metadata.track_id == normalized_track_id:
            return candidate
    return None


def recommendation_candidate_rows(
    connection: Connection,
    *,
    track_ids: Iterable[int] = (),
) -> list[Row]:
    normalized_track_ids = tuple(dict.fromkeys(int(track_id) for track_id in track_ids))
    track_filter_sql = ""
    params: list[object] = []
    if normalized_track_ids:
        track_filter_sql = (
            f" AND tracks.track_id IN ({placeholders_for(normalized_track_ids)})"
        )
        params.extend(normalized_track_ids)
    return list(
        connection.execute(
            f"""
            SELECT
                tracks.track_id,
                tracks.path,
                tracks.title,
                tracks.artist,
                tracks.album_artist,
                tracks.album_id,
                tracks.album,
                tracks.date,
                albums.year AS album_year,
                track_state.starred_at,
                COALESCE(track_stats.play_count, 0) AS track_play_count,
                track_stats.last_played_at AS track_last_played_at,
                COALESCE(album_stats.play_count, 0) AS album_play_count,
                album_stats.last_played_at AS album_last_played_at
            FROM library_tracks AS tracks
            LEFT JOIN library_albums AS albums
                ON albums.album_id = tracks.album_id
            LEFT JOIN track_user_state AS track_state
                ON track_state.track_path = tracks.path
                    AND track_state.starred_at IS NOT NULL
            LEFT JOIN play_track_stats AS track_stats
                ON track_stats.track_path = tracks.path
            LEFT JOIN play_album_stats AS album_stats
                ON album_stats.album_id = tracks.album_id
            WHERE COALESCE(tracks.scan_error, '') = ''
                {track_filter_sql}
            ORDER BY tracks.track_id
            """,
            params,
        )
    )


def recommendation_candidates_from_rows(
    connection: Connection,
    rows: Iterable[Row],
) -> tuple[RecommendationCandidate, ...]:
    track_rows = list(rows)
    track_ids = [int(row["track_id"]) for row in track_rows]
    genres_by_track = track_values_by_track(
        connection,
        track_ids,
        table="library_track_genres",
        column="genre",
    )
    styles_by_track = track_values_by_track(
        connection,
        track_ids,
        table="library_track_styles",
        column="style",
    )
    taxonomy_genres, taxonomy_styles = taxonomy_sets(connection)
    artist_stats_by_key = recommendation_artist_stats_by_key(connection, track_rows)
    candidates: list[RecommendationCandidate] = []
    for row in track_rows:
        track_id = int(row["track_id"])
        genres, styles = split_genres_and_styles(
            normalize_genre_values(genres_by_track.get(track_id, [])),
            normalize_genre_values(styles_by_track.get(track_id, [])),
            taxonomy_genres=taxonomy_genres,
            taxonomy_styles=taxonomy_styles,
        )
        artist_stats = artist_stats_by_key.get(candidate_artist_stats_key(row))
        candidates.append(
            RecommendationCandidate(
                metadata=CandidateMetadata(
                    track_id=track_id,
                    path=str(row["path"]),
                    title=text_or_empty(row["title"]),
                    artist=text_or_empty(row["artist"]),
                    album_artist=text_or_empty(row["album_artist"]),
                    album_id=optional_text(row["album_id"]),
                    album=text_or_empty(row["album"]),
                    date=optional_text(row["date"]),
                    decade=recommendation_decade(row["date"], row["album_year"]),
                    genres=tuple(genres),
                    styles=tuple(styles),
                    starred_at=optional_text(row["starred_at"]),
                ),
                listening=ListeningStats(
                    track_play_count=int(row["track_play_count"] or 0),
                    album_play_count=int(row["album_play_count"] or 0),
                    artist_play_count=artist_stats[0] if artist_stats else 0,
                    track_last_played_at=optional_text(row["track_last_played_at"]),
                    album_last_played_at=optional_text(row["album_last_played_at"]),
                    artist_last_played_at=artist_stats[1] if artist_stats else None,
                ),
            )
        )
    return tuple(candidates)


def recommendation_artist_stats_by_key(
    connection: Connection,
    rows: Iterable[Row],
) -> dict[str, tuple[int, str | None]]:
    keys = tuple(
        sorted({key for row in rows if (key := candidate_artist_stats_key(row))})
    )
    if not keys:
        return {}
    placeholders = placeholders_for(keys)
    return {
        str(row["artist_key"]): (
            int(row["play_count"] or 0),
            optional_text(row["last_played_at"]),
        )
        for row in connection.execute(
            f"""
            SELECT artist_key, play_count, last_played_at
            FROM play_artist_stats
            WHERE artist_key IN ({placeholders})
            """,
            keys,
        )
    }


def candidate_artist_stats_key(row: Row) -> str:
    artists = normalized_album_artist_values((optional_text(row["album_artist"]),))
    if not artists:
        artists = normalized_album_artist_values((optional_text(row["artist"]),))
    if not artists:
        return ""
    return normalize_text(artists[0])


def recommendation_decade(
    track_date: object | None,
    album_year: object | None = None,
) -> str | None:
    year = year_from_text(track_date)
    if year is None:
        year = year_from_value(album_year)
    if year is None:
        return None
    decade = (year // 10) * 10
    return f"{decade}s"


def recommendation_term_document_counts(
    candidates: Iterable[RecommendationCandidate],
    values_for_metadata: Callable[[CandidateMetadata], Iterable[str | None]],
) -> dict[str, int]:
    document_counts: dict[str, int] = {}
    for candidate in candidates:
        terms = normalized_feature_terms(values_for_metadata(candidate.metadata))
        for term in terms:
            document_counts[term] = document_counts.get(term, 0) + 1
    return document_counts


def recommendation_inverse_document_frequency(
    document_count: int,
    document_frequency: int,
) -> float:
    normalized_document_count = max(0, int(document_count))
    normalized_document_frequency = min(
        normalized_document_count,
        max(0, int(document_frequency)),
    )
    if normalized_document_count == 0 or normalized_document_frequency == 0:
        return 0.0
    return (
        math.log(
            (1.0 + normalized_document_count)
            / (1.0 + normalized_document_frequency)
        )
        + 1.0
    )


def recommendation_genre_feature_vector(
    candidate: RecommendationCandidate,
    vocabulary: RecommendationVocabulary,
) -> SparseVector:
    return recommendation_tfidf_feature_vector(
        candidate.metadata.genres,
        idf_by_term=vocabulary.genre_idf,
        feature_prefix=GENRE_FEATURE_PREFIX,
    )


def recommendation_style_feature_vector(
    candidate: RecommendationCandidate,
    vocabulary: RecommendationVocabulary,
) -> SparseVector:
    return recommendation_tfidf_feature_vector(
        candidate.metadata.styles,
        idf_by_term=vocabulary.style_idf,
        feature_prefix=STYLE_FEATURE_PREFIX,
    )


def recommendation_tfidf_feature_vector(
    values: Iterable[str | None],
    *,
    idf_by_term: Mapping[str, float],
    feature_prefix: str,
) -> SparseVector:
    vector: SparseVector = {}
    for term in normalized_feature_terms(values):
        idf = float(idf_by_term.get(term, 0.0))
        if idf > 0:
            vector[recommendation_feature_key(feature_prefix, term)] = idf
    return vector


def recommendation_artist_feature_vector(
    candidate: RecommendationCandidate,
    vocabulary: RecommendationVocabulary,
) -> SparseVector:
    term = recommendation_artist_term(candidate.metadata)
    if not term or term not in vocabulary.artist_terms:
        return {}
    return {recommendation_feature_key(ARTIST_FEATURE_PREFIX, term): 1.0}


def recommendation_decade_feature_vector(
    candidate: RecommendationCandidate,
    vocabulary: RecommendationVocabulary,
) -> SparseVector:
    source_decade = decade_start_year(candidate.metadata.decade)
    if source_decade is None:
        return {}
    vector: SparseVector = {}
    for decade_term in vocabulary.decade_terms:
        target_decade = decade_start_year(decade_term)
        if target_decade is None:
            continue
        distance = abs(source_decade - target_decade) // 10
        weight = soft_decade_weight(distance)
        if weight > 0:
            vector[recommendation_feature_key(DECADE_FEATURE_PREFIX, decade_term)] = (
                weight
            )
    return vector


def sparse_dot_product(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(
        float(value) * float(right.get(key, 0.0))
        for key, value in left.items()
    )


def sparse_vector_norm(vector: Mapping[str, float]) -> float:
    squared_norm = sum(float(value) * float(value) for value in vector.values())
    if squared_norm <= 0:
        return 0.0
    return math.sqrt(squared_norm)


def sparse_cosine_similarity(
    left: Mapping[str, float],
    right: Mapping[str, float],
) -> float:
    left_norm = sparse_vector_norm(left)
    right_norm = sparse_vector_norm(right)
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    similarity = sparse_dot_product(left, right) / (left_norm * right_norm)
    return max(-1.0, min(1.0, similarity))


def add_sparse_vectors(*vectors: Mapping[str, float]) -> SparseVector:
    result: SparseVector = {}
    for vector in vectors:
        for key, value in vector.items():
            normalized_value = float(value)
            if normalized_value:
                result[key] = result.get(key, 0.0) + normalized_value
                if result[key] == 0:
                    del result[key]
    return sparse_vector_copy(result)


def scale_sparse_vector(
    vector: Mapping[str, float],
    scalar: float,
) -> SparseVector:
    normalized_scalar = float(scalar)
    if normalized_scalar == 0:
        return {}
    return sparse_vector_copy(
        {
            key: float(value) * normalized_scalar
            for key, value in vector.items()
            if value
        }
    )


def weighted_average_sparse_vectors(
    weighted_vectors: Iterable[tuple[Mapping[str, float], float]],
) -> SparseVector:
    weighted_sum: SparseVector = {}
    total_weight = 0.0
    for vector, weight in weighted_vectors:
        normalized_weight = float(weight)
        if not math.isfinite(normalized_weight) or normalized_weight < 0:
            raise ValueError("sparse vector weight must be non-negative")
        if normalized_weight == 0 or not vector:
            continue
        total_weight += normalized_weight
        weighted_sum = add_sparse_vectors(
            weighted_sum,
            scale_sparse_vector(vector, normalized_weight),
        )
    if total_weight <= 0:
        return {}
    return scale_sparse_vector(weighted_sum, 1.0 / total_weight)


def weighted_feature_group_vector(
    vector: Mapping[str, float],
    weight: float,
) -> SparseVector:
    normalized_weight = float(weight)
    if normalized_weight <= 0:
        return {}
    normalized = normalize_sparse_vector(vector)
    if not normalized:
        return {}
    return {key: value * normalized_weight for key, value in normalized.items()}


def normalize_sparse_vector(vector: Mapping[str, float]) -> SparseVector:
    squared_norm = sum(float(value) * float(value) for value in vector.values())
    if squared_norm <= 0:
        return {}
    norm = math.sqrt(squared_norm)
    return {
        key: float(value) / norm
        for key, value in sorted(vector.items())
        if value
    }


def sparse_vector_copy(vector: Mapping[str, float]) -> SparseVector:
    return {
        str(key): float(value)
        for key, value in sorted(vector.items())
        if value
    }


def recommendation_feature_key(feature_prefix: str, term: str) -> str:
    return f"{feature_prefix}:{term}"


def recommendation_artist_term(metadata: CandidateMetadata) -> str:
    artists = normalized_album_artist_values((metadata.artist,))
    if not artists:
        artists = normalized_album_artist_values((metadata.album_artist,))
    if not artists:
        return ""
    return recommendation_feature_term(artists[0])


def recommendation_metadata_artist_terms(
    metadata: CandidateMetadata,
) -> tuple[str, ...]:
    return recommendation_artist_terms((metadata.artist, metadata.album_artist))


def recommendation_artist_terms(values: Iterable[object | None]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            term
            for artist in normalized_album_artist_values(
                tuple(optional_text(value) for value in values)
            )
            if (term := recommendation_feature_term(artist))
        )
    )


def recommendation_feature_term(value: object | None) -> str:
    if value is None:
        return ""
    return normalize_text(str(value))


def normalized_feature_terms(values: Iterable[object | None]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                term
                for value in values
                if (term := recommendation_feature_term(value))
            }
        )
    )


def normalized_decade_terms(values: Iterable[object | None]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                term
                for value in values
                if (term := normalize_decade_term(value))
            },
            key=decade_sort_key,
        )
    )


def normalized_float_mapping(values: Mapping[str, float]) -> dict[str, float]:
    return {
        term: float(value)
        for key, value in values.items()
        if (term := recommendation_feature_term(key))
    }


def normalize_decade_term(value: object | None) -> str | None:
    decade = decade_start_year(value)
    if decade is None:
        return None
    return f"{decade}s"


def decade_start_year(value: object | None) -> int | None:
    year = year_from_text(value)
    if year is None:
        return None
    return (year // 10) * 10


def decade_sort_key(value: object | None) -> tuple[bool, int, str]:
    decade = decade_start_year(value)
    return (decade is None, decade or 0, str(value or ""))


def soft_decade_weight(distance: int) -> float:
    if distance == 0:
        return 1.0
    if distance == 1:
        return 0.4
    if distance == 2:
        return 0.1
    return 0.0


def year_from_text(value: object | None) -> int | None:
    if value is None:
        return None
    match = YEAR_PATTERN.search(str(value))
    if match is None:
        return None
    return year_from_value(match.group(1))


def year_from_value(value: object | None) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if 1000 <= year <= 9999:
        return year
    return None


def normalized_unique_text_tuple(values: Iterable[str | None]) -> tuple[str, ...]:
    normalized: dict[str, str] = {}
    for value in values:
        if not value:
            continue
        text = value.strip()
        if not text:
            continue
        normalized.setdefault(" ".join(text.casefold().split()), text)
    return tuple(normalized.values())


def normalized_optional_text(value: object | None) -> str | None:
    text = optional_text(value)
    if text is None:
        return None
    return " ".join(text.split())


def optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def text_or_empty(value: object | None) -> str:
    return optional_text(value) or ""


DEFAULT_RECENCY_PENALTIES = RecencyPenalties(
    played_last_24_hours=0.30,
    played_last_7_days=0.15,
    played_last_30_days=0.05,
)
DISCOVERY_RECENCY_PENALTIES = RecencyPenalties(
    played_last_24_hours=0.50,
    played_last_7_days=0.30,
    played_last_30_days=0.10,
)
DEFAULT_DIVERSITY_CAPS = DiversityCaps(
    max_tracks_per_artist=3,
    max_tracks_per_album=2,
    max_tracks_per_genre=8,
    top_track_count=25,
)

RECOMMENDATION_MODE_CONFIGS: Mapping[RecommendationMode, RecommendationModeConfig] = (
    MappingProxyType(
        {
            RECOMMENDATION_MODE_DEFAULT: RecommendationModeConfig(
                mode=RECOMMENDATION_MODE_DEFAULT,
                feature_weights=FeatureWeights(
                    genres=0.30,
                    styles=0.40,
                    artist=0.15,
                    decade=0.15,
                ),
                track_play_penalty=0.05,
                artist_play_penalty=0.02,
                album_play_penalty=0.02,
                favorite_boost=0.05,
                recency_penalties=DEFAULT_RECENCY_PENALTIES,
                diversity_caps=DEFAULT_DIVERSITY_CAPS,
                recent_play_penalty_strength=RECENT_PLAY_PENALTY_MEDIUM,
                diversity_strength=DIVERSITY_STRENGTH_MEDIUM,
            ),
            RECOMMENDATION_MODE_DISCOVERY: RecommendationModeConfig(
                mode=RECOMMENDATION_MODE_DISCOVERY,
                feature_weights=FeatureWeights(
                    genres=0.30,
                    styles=0.40,
                    artist=0.15,
                    decade=0.15,
                ),
                track_play_penalty=0.30,
                artist_play_penalty=0.15,
                album_play_penalty=0.10,
                favorite_boost=0.00,
                recency_penalties=DISCOVERY_RECENCY_PENALTIES,
                diversity_caps=DEFAULT_DIVERSITY_CAPS,
                recent_play_penalty_strength=RECENT_PLAY_PENALTY_HIGH,
                diversity_strength=DIVERSITY_STRENGTH_HIGH,
            ),
            RECOMMENDATION_MODE_GENRE_ONLY: RecommendationModeConfig(
                mode=RECOMMENDATION_MODE_GENRE_ONLY,
                feature_weights=FeatureWeights(
                    genres=1.00,
                    styles=0.00,
                    artist=0.00,
                    decade=0.00,
                ),
                track_play_penalty=0.05,
                artist_play_penalty=0.00,
                album_play_penalty=0.00,
                favorite_boost=0.03,
                recency_penalties=DEFAULT_RECENCY_PENALTIES,
                diversity_caps=DEFAULT_DIVERSITY_CAPS,
                recent_play_penalty_strength=RECENT_PLAY_PENALTY_MEDIUM,
                diversity_strength=DIVERSITY_STRENGTH_MEDIUM,
            ),
            RECOMMENDATION_MODE_ARTIST_ONLY: RecommendationModeConfig(
                mode=RECOMMENDATION_MODE_ARTIST_ONLY,
                feature_weights=FeatureWeights(
                    genres=0.00,
                    styles=0.00,
                    artist=1.00,
                    decade=0.00,
                ),
                track_play_penalty=0.05,
                artist_play_penalty=0.00,
                album_play_penalty=0.02,
                favorite_boost=0.03,
                recency_penalties=DEFAULT_RECENCY_PENALTIES,
                diversity_caps=DiversityCaps(
                    max_tracks_per_artist=3,
                    max_tracks_per_album=2,
                    max_tracks_per_genre=8,
                    top_track_count=25,
                    apply_artist_cap=False,
                ),
                recent_play_penalty_strength=RECENT_PLAY_PENALTY_MEDIUM,
                diversity_strength=DIVERSITY_STRENGTH_LOW,
                candidate_filter=CANDIDATE_FILTER_ARTIST_MATCH_REQUIRED,
                artist_only_fallback=ARTIST_ONLY_FALLBACK_RETURN_FEWER,
            ),
            RECOMMENDATION_MODE_RANDOM: RecommendationModeConfig(
                mode=RECOMMENDATION_MODE_RANDOM,
                feature_weights=FeatureWeights(
                    genres=0.00,
                    styles=0.00,
                    artist=0.00,
                    decade=0.00,
                ),
                track_play_penalty=0.00,
                artist_play_penalty=0.00,
                album_play_penalty=0.00,
                favorite_boost=0.00,
                recency_penalties=RecencyPenalties(),
                diversity_caps=DEFAULT_DIVERSITY_CAPS,
                recent_play_penalty_strength=RECENT_PLAY_PENALTY_RANDOM_WEIGHTED,
                diversity_strength=DIVERSITY_STRENGTH_MEDIUM,
                candidate_selection=CANDIDATE_SELECTION_WEIGHTED_RANDOM,
                random_recency_multipliers=RandomRecencyMultipliers(),
                random_track_play_count_weight=0.15,
            ),
        }
    )
)

RECOMMENDATION_CONFIG = RecommendationConfig(modes=RECOMMENDATION_MODE_CONFIGS)


__all__ = [
    "ARTIST_ONLY_FALLBACK_DEFAULT_MODE",
    "ARTIST_ONLY_FALLBACK_RETURN_FEWER",
    "CANDIDATE_FILTER_ARTIST_MATCH_REQUIRED",
    "CANDIDATE_SELECTION_WEIGHTED_RANDOM",
    "DEFAULT_DIVERSITY_CAPS",
    "DEFAULT_RECENCY_PENALTIES",
    "DEFAULT_RECOMMENDATION_LIMIT",
    "DISCOVERY_RECENCY_PENALTIES",
    "DIVERSITY_STRENGTH_HIGH",
    "DIVERSITY_STRENGTH_LOW",
    "DIVERSITY_STRENGTH_MEDIUM",
    "MAX_RECOMMENDATION_LIMIT",
    "RECENT_PLAY_PENALTY_HIGH",
    "RECENT_PLAY_PENALTY_MEDIUM",
    "RECENT_PLAY_PENALTY_RANDOM_WEIGHTED",
    "RECOMMENDATION_CONFIG",
    "ARTIST_FEATURE_PREFIX",
    "DECADE_FEATURE_PREFIX",
    "GENRE_FEATURE_PREFIX",
    "RECOMMENDATION_MODE_ARTIST_ONLY",
    "RECOMMENDATION_MODE_CONFIGS",
    "RECOMMENDATION_MODE_DEFAULT",
    "RECOMMENDATION_MODE_DISCOVERY",
    "RECOMMENDATION_MODE_GENRE_ONLY",
    "RECOMMENDATION_MODE_RANDOM",
    "RECOMMENDATION_MODE_VALUES",
    "STYLE_FEATURE_PREFIX",
    "SUPPORTED_RECOMMENDATION_MODES",
    "ArtistOnlyFallback",
    "CandidateFilter",
    "CandidateMetadata",
    "CandidateSelection",
    "DiversityCaps",
    "DiversityStrength",
    "FeatureWeights",
    "ListeningStats",
    "RandomRecencyMultipliers",
    "RecentPlayPenaltyStrength",
    "RecommendationCandidate",
    "RecommendationConfig",
    "RecommendationExplanation",
    "RecommendationLimitError",
    "RecommendationMode",
    "RecommendationModeConfig",
    "RecommendationModeError",
    "RecommendationProfile",
    "RecommendationProfileSeed",
    "RecommendationQueries",
    "RecommendationRequest",
    "RecommendationResult",
    "RecommendationScore",
    "RecommendationService",
    "RecommendationVocabulary",
    "RecencyPenalties",
    "SparseVector",
    "add_sparse_vectors",
    "build_recommendation_album_profile",
    "build_recommendation_artist_profile",
    "build_recommendation_profile",
    "build_recommendation_track_profile",
    "build_recommendation_track_vector",
    "build_recommendation_track_vectors",
    "build_recommendation_user_profile",
    "build_recommendation_vocabulary",
    "load_recommendation_candidate",
    "load_recommendation_candidates",
    "normalize_recommendation_limit",
    "normalize_recommendation_mode",
    "normalize_sparse_vector",
    "normalized_unique_text_tuple",
    "recommendation_feature_key",
    "recommendation_inverse_document_frequency",
    "recommendation_candidate_rows",
    "recommendation_candidates_from_rows",
    "recommendation_decade",
    "recommendation_mode_config",
    "rank_recommendation_results",
    "scale_sparse_vector",
    "score_recommendation_candidate",
    "score_recommendation_candidates",
    "sparse_cosine_similarity",
    "sparse_dot_product",
    "sparse_vector_norm",
    "weighted_average_sparse_vectors",
]

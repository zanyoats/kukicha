from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast


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
        object.__setattr__(self, "album", str(self.album or ""))
        object.__setattr__(self, "genres", normalized_unique_text_tuple(self.genres))
        object.__setattr__(self, "styles", normalized_unique_text_tuple(self.styles))

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


def recommendation_mode_config(mode: object | None = None) -> RecommendationModeConfig:
    return RECOMMENDATION_CONFIG.mode_config(mode)


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
    "RECOMMENDATION_MODE_ARTIST_ONLY",
    "RECOMMENDATION_MODE_CONFIGS",
    "RECOMMENDATION_MODE_DEFAULT",
    "RECOMMENDATION_MODE_DISCOVERY",
    "RECOMMENDATION_MODE_GENRE_ONLY",
    "RECOMMENDATION_MODE_RANDOM",
    "RECOMMENDATION_MODE_VALUES",
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
    "RecommendationRequest",
    "RecommendationResult",
    "RecommendationScore",
    "RecencyPenalties",
    "normalize_recommendation_limit",
    "normalize_recommendation_mode",
    "normalized_unique_text_tuple",
    "recommendation_mode_config",
]

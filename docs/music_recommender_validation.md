# Music Recommender Validation Notes

Date: 2026-06-07

These notes capture the first hardening pass from Task 17. The automated tests
use small SQLite fixtures that are deterministic and safe to run in CI. A real
private music-library database was not available in this workspace, so the
manual real-library section below records the checks to run against a local
player database after scanning a personal library.

## Automated Fixture Validation

The acceptance fixture tests in `tests/test_recommendations.py` cover the
source-plan behavior across track radio, album radio, artist radio, daily
playlists, and all supported modes.

Concrete examples from the fixtures:

- Track radio seeded from `Seed Song` excludes track `1` and ranks
  `Closest Song` first in default mode because it shares `Rock`, `Dream Pop`,
  and the `1990s` decade.
- Genre-only track radio for `Seed Song` treats `Closest Song` and
  `Genre Cousin` as equal genre matches because both share `Rock`; it does not
  give `Same Artist Drift` credit for the artist match.
- Artist-only track radio for `Seed Song` returns only `Same Artist Drift`; the
  seed track is excluded and unrelated artists are not used as fill.
- Random track radio excludes the seed track and sets content similarity to
  `0.0`; the explanation is driven by random draw and listening weights.
- Album radio for `Two Moods` excludes seed-album tracks `Guitar Light` and
  `Cloud Room`, then validates both sides of the album profile by matching
  `Cloud Echo` on `Ambient`/`Drone` and `Guitar Echo` on `Rock`/`Dream Pop`.
- Artist radio for `Seed Artist` validates that default mode can include a
  similar non-seed artist (`Pattern Echo`) while artist-only mode keeps results
  tied to the requested artist through track, album-artist, or split
  album-artist metadata.
- Daily recommendations are stable for the same date and limit. The daily
  fixture proves that `Favorite Seed Song` and `Played Seed Song` influence
  profile matches, default mode lightly penalizes the overplayed seed, discovery
  suppresses the recently played seed, cold start applies diversity caps, and
  daily random results reload from persistence.

## Manual Real-Library Checklist

Run these checks after pointing Kukicha at a scanned local player database:

```bash
uv run kukicha --config /path/to/kukicha.toml
```

Open the player and choose three known references:

- A track with rich genre/style metadata and several plausible neighbors.
- An album with at least two distinct moods, such as one guitar-heavy track and
  one ambient/electronic track.
- An artist with more than one album plus adjacent artists in the library.

For each reference, open or request:

```text
/recommendations/radio/track/<track_id>?mode=default&limit=25
/recommendations/radio/track/<track_id>?mode=genre_only&limit=25
/recommendations/radio/track/<track_id>?mode=artist_only&limit=25
/recommendations/radio/track/<track_id>?mode=random&limit=25
/recommendations/radio/album/<album_id>?mode=default&limit=25
/recommendations/radio/artist/<artist>?mode=default&limit=25
/recommendations/daily?mode=default&limit=30
/recommendations/daily?mode=random&limit=30
```

Expected observations:

- Default mode should feel close but not same-artist-only. For example, a
  dream-pop track should prefer other dream-pop or nearby rock tracks before
  unrelated tracks by the same artist.
- Genre-only should widen the result set. For example, a `Rock` seed can bring
  in garage-rock or psychedelic-rock tracks even when style and decade differ.
- Artist-only should return fewer results instead of filling with unrelated
  artists when the artist catalog is small.
- Random should behave like shuffle with memory: recently played tracks can
  appear, but explanations should show lower recency multipliers.
- Album radio should not include tracks from the seed album by default and
  should reflect more than the first track's metadata.
- Daily playlists should remain identical when refreshed for the same date,
  mode, and limit.

## Deferred Follow-Ups

- MMR reranking remains deferred. The current implementation uses simple
  artist, album, and genre caps, which are easier to inspect and tune.
- Playlist UI persistence is deferred. Daily playlists are persisted in the
  recommender tables, but there is not yet a richer generated-playlist UI.
- Skip counts and completion counts are not used yet; only play count,
  favorite state, and last-played timestamps affect listening adjustments.
- External artist similarity is not used. Artist-only mode is intentionally a
  hard local-library eligibility filter.
- Vector precomputation and cache invalidation are deferred until profiling
  shows the current on-demand scoring path is too slow for larger libraries.

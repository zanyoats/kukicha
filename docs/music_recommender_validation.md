# Music Recommender Validation Notes

Date: 2026-06-07

These notes capture the first hardening pass from Task 17. The automated tests
use small SQLite fixtures that are deterministic and safe to run in CI. A real
private music-library database was not available in this workspace, so the
manual real-library section below records the checks to run against a local
player database after scanning a personal library.

## Automated Fixture Validation

The acceptance fixture tests in `tests/test_recommendations.py` cover the
source-plan behavior across track radio, album radio, artist radio, genre
radio, random playlists, and all supported seeded-radio modes.

Concrete examples from the fixtures:

- Track radio seeded from `Seed Song` excludes track `1` and ranks
  `Closest Song` first in default mode because it shares `Rock`, `Dream Pop`,
  and the `1990s` decade.
- Genre radio for `Rock` builds a default-mode profile from all Rock tracks,
  keeps candidates inside that parent genre, and still rewards closer style and
  decade matches within the genre.
- Artist-only track radio for `Seed Song` returns only `Same Artist Drift`; the
  seed track is excluded and unrelated artists are not used as fill.
- Random playlist generation is seedless across the full library and sets
  content similarity to `0.0`; the explanation is driven by random draw and
  listening weights.
- Album radio for `Two Moods` excludes seed-album tracks `Guitar Light` and
  `Cloud Room`, then validates both sides of the album profile by matching
  `Cloud Echo` on `Ambient`/`Drone` and `Guitar Echo` on `Rock`/`Dream Pop`.
- Artist radio for `Seed Artist` validates that default mode can include a
  similar non-seed artist (`Pattern Echo`) while artist-only mode keeps results
  tied to the requested artist through track, album-artist, or split
  album-artist metadata.

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

For each reference, generate radio from the player UI, or request the POST
routes directly:

```text
POST /recommendations/radio/track/<track_id>?mode=default
POST /recommendations/radio/track/<track_id>?mode=artist_only
POST /recommendations/radio/album/<album_id>?mode=default
POST /recommendations/radio/artist/<artist>?mode=discovery
POST /recommendations/radio/genre/<genre>
POST /recommendations/radio/random
```

Expected observations:

- Default mode should feel close but not same-artist-only. For example, a
  dream-pop track should prefer other dream-pop or nearby rock tracks before
  unrelated tracks by the same artist.
- Genre radio should keep every candidate inside the selected parent genre,
  including tracks that match through a style's taxonomy parent, while still
  ranking with default-mode genre/style/artist/decade features.
- Artist-only should return fewer results instead of filling with unrelated
  artists when the artist catalog is small.
- Random should behave like shuffle with memory across the full library:
  recently played tracks can appear, but explanations should show lower recency
  multipliers.
- Album radio should not include tracks from the seed album by default and
  should reflect more than the first track's metadata.

## Deferred Follow-Ups

- MMR reranking remains deferred. The current implementation uses simple
  artist, album, and genre caps, which are easier to inspect and tune.
- Generated playlists are handled as player jobs and can be loaded into the
  queue when generation completes.
- Skip counts and completion counts are not used yet; only play count,
  favorite state, and last-played timestamps affect listening adjustments.
- External artist similarity is not used. Artist-only mode is intentionally a
  hard local-library eligibility filter.
- Vector precomputation and cache invalidation are deferred until profiling
  shows the current on-demand scoring path is too slow for larger libraries.

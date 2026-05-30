# Development

This project uses a `src/` package layout. Import the package as `kukicha`; do
not run `src/kukicha/cli.py` directly.

## Local Setup

Create the project virtual environment and install the package in editable mode:

```bash
uv sync
```

Run the CLI through the installed script or module form:

```bash
uv run kukicha --help
uv run python -m kukicha --help
```

## Tests

The test suite uses `unittest`, not pytest. Run all tests from the repo root:

```bash
uv run python -m unittest discover -s tests
```

Run tests with warnings visible before finishing warning-cleanup work:

```bash
uv run python -W default -m unittest discover -s tests
```

For resource-warning debugging, use tracemalloc:

```bash
uv run python -X tracemalloc=10 -W default -m unittest discover -s tests
```

Run a specific test module:

```bash
uv run python -m unittest tests.test_search
```

Run the lightweight browser-player JavaScript tests:

```bash
npm test
```

## Git Hooks

Use the repo-local pre-commit hook:

```bash
git config core.hooksPath .githooks
```

The hook runs Ruff, the Python `unittest` suite, and the JavaScript tests before
each commit.

## Release A Version

Kukicha releases are published to PyPI and mirrored with a GitHub release for the
same git tag.

1. Update `version` in `pyproject.toml`.

   Use a PEP 440 pre-release version when the release is not final:

   ```toml
   version = "0.1.0a1"
   ```

2. Update the lockfile and run the test suite:

   ```bash
   uv lock
   uv run python -m unittest discover -s tests
   ```

3. Build and validate the PyPI artifacts:

   ```bash
   uv build --clear
   uv run --group release twine check dist/*
   ```

4. Commit the version and lockfile changes before publishing:

   ```bash
   git add pyproject.toml uv.lock
   git commit -m "chore: release 0.1.0a1"
   ```

5. Upload the release to PyPI:

   ```bash
   UV_PUBLISH_TOKEN="$PYPI_TOKEN" uv publish dist/kukicha-0.1.0a1*
   ```

   For the first upload of a new PyPI project, use an account-scoped token;
   after the project exists, prefer a project-scoped token.

6. Tag the release commit and push the tag:

   ```bash
   git tag v0.1.0a1
   git push origin v0.1.0a1
   ```

7. Create a GitHub release from the tag.

   Use the tag name as the release title, for example `v0.1.0a1`. Mark alpha,
   beta, or release-candidate versions as GitHub pre-releases.

PyPI files and git tags are effectively immutable release records. If a release
needs a fix, publish a new version instead of reusing an existing version or
moving an existing tag.

## Build The Taxonomy TSV

The repo-local taxonomy tool builds the TSV consumed by the runtime `kukicha`
package. It is intentionally outside `src/kukicha` so the installed CLI does not
carry the Discogs construction flow.

Build and review taxonomy sources, then export the TSV that ships with the
package:


```bash
# from repo root
mkdir -p ./build

# Download discogs_20260301_masters.xml.gz
curl -L \
  -o ./build/discogs_20260301_masters.xml.gz \
  'https://data.discogs.com/?download=data%2F2026%2Fdiscogs_20260301_masters.xml.gz'

# seed the taxonomy db with discog data
uv run python -m tools.taxonomy build-discogs \
  --discog-masters build/discogs_20260301_masters.xml.gz \
  --source discogs_20260301_masters \
  --database build/taxonomy.sqlite

# create a musicbrainz review taxonomy
uv run python -m tools.taxonomy musicbrainz-review \
  --database build/taxonomy.sqlite \
  --terms tools/taxonomy/data/mb_genres_v0.txt \
  --source mb_genres_v0 \
  > tools/taxonomy/data/mb_genres_review_v0.tsv

# merge review taxonomy updates in db
uv run python -m tools.taxonomy merge-review \
  --database build/taxonomy.sqlite \
  --review-file tools/taxonomy/data/mb_genres_review_v0.tsv

# install updated taxonomy for app
uv run python -m tools.taxonomy export \
  --database build/taxonomy.sqlite \
  --output src/kukicha/data/taxonomy.tsv
```

The exported TSV includes versioned source names and count columns. Runtime
SQLite tables only store canonical genres, canonical styles with parent genres,
and matching aliases.

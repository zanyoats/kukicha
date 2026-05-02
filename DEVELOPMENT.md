# Development

This project uses a `src/` package layout. Import the package as `kukicha`; do
not run `src/kukicha/cli.py` directly.

## Local Setup

Create a virtual environment and install the package in editable mode:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Run the CLI through the installed script or module form:

```bash
kukicha --help
python -m kukicha --help
```

## Tests

The test suite uses `unittest`, not pytest. Run all tests from the repo root:

```bash
python -m unittest discover -s tests
```

Run tests with warnings visible before finishing warning-cleanup work:

```bash
python -W default -m unittest discover -s tests
```

For resource-warning debugging, use tracemalloc:

```bash
python -X tracemalloc=10 -W default -m unittest discover -s tests
```

Run a specific test module:

```bash
python -m unittest tests.test_search
```

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
python -m tools.taxonomy build-discogs \
  --discog-masters build/discogs_20260301_masters.xml.gz \
  --source discogs_20260301_masters \
  --database build/taxonomy.sqlite

# create a musicbrainz review taxonomy
python -m tools.taxonomy musicbrainz-review \
  --database build/taxonomy.sqlite \
  --terms tools/taxonomy/data/mb_genres_v0.txt \
  --source mb_genres_v0 \
  > tools/taxonomy/data/mb_genres_review_v0.tsv

# merge review taxonomy updates in db
python -m tools.taxonomy merge-review \
  --database build/taxonomy.sqlite \
  --review-file tools/taxonomy/data/mb_genres_review_v0.tsv

# install updated taxonomy for app
python -m tools.taxonomy export \
  --database build/taxonomy.sqlite \
  --output src/kukicha/data/taxonomy.tsv
```

The exported TSV includes versioned source names and count columns. Runtime
SQLite tables only store canonical genres, canonical styles with parent genres,
and matching aliases.

# AGENTS.md

## Python Environment

There is a `.venv` folder where `pyproject.toml` dependencies are installed.
Use `.venv/bin/python` for local commands so imports and installed package
dependencies match the project environment.

## Project Layout

This project uses a `src/` package layout. Import the package as `kukicha`;
do not run `src/kukicha/cli.py` directly. To exercise the CLI, use the installed
script or module form:

```bash
.venv/bin/kukicha --help
.venv/bin/python -m kukicha --help
```

## Tests

The test suite uses `unittest`, not pytest. Pytest is not a project dependency,
and the existing tests are `unittest.TestCase` based.

Run all tests from the repo root with:

```bash
.venv/bin/python -m unittest discover -s tests
```

Run tests with warnings visible before finishing warning-cleanup work:

```bash
.venv/bin/python -W default -m unittest discover -s tests
```

For resource-warning debugging, use tracemalloc:

```bash
.venv/bin/python -X tracemalloc=10 -W default -m unittest discover -s tests
```

Run a specific test module with:

```bash
.venv/bin/python -m unittest tests.test_search
```

## SQLite Connections

Use `kukicha.database.connect_database()` for application database access. Its
context manager closes the connection on exit; direct callers that assign the
connection should still close it explicitly in a `finally` block.

Avoid `with sqlite3.connect(...) as connection:` in tests and app code when the
connection needs to close at block exit: Python's built-in sqlite context manager
commits or rolls back, but does not close the database handle.

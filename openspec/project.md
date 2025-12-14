# Project Context

## Purpose
Sitesync is a Python CLI that captures and synchronizes website assets (starting with HTML pages) into a structured, resumable local dataset backed by SQLite.

## Tech Stack
- Python (project metadata targets `>=3.12.9,<3.13`)
- CLI: Typer (Click under the hood)
- Validation: Pydantic
- Fetching: Playwright (optional at runtime until crawl execution), plus a `NullFetcher` for offline/testing
- Parsing: BeautifulSoup
- Retry/backoff: Tenacity
- Config: YAML + optional dotenv (`.env`)
- Storage: SQLite (via stdlib `sqlite3`)
- UI: Rich live dashboard
- Tooling: uv, pytest, mypy, ruff, black, pylint (dev extras)

## Project Conventions

### Code Style
- Black + Ruff with line length 100.
- Type hints are expected; mypy is configured as strict.
- No emojis in documentation, console output, or logs.

### Architecture Patterns
- Layered modules: CLI orchestration, async crawl executor, fetchers, plugins, persistence, reporting, and UI.
- Fetchers return a standardized `FetchResult`; plugins normalize raw payloads into `AssetRecord`s; persistence stores run/task/asset state.

### Testing Strategy
- pytest with pytest-asyncio (strict mode).
- Prefer unit tests that do not require network access.

### Git Workflow
- Not specified (repository may be used without git metadata).

## Domain Context
- A “run” is a crawl session for a specific source profile.
- Crawl work is stored as tasks in SQLite; the system is intended to be resumable after failures.

## Important Constraints
- Avoid writing secrets back into config files.
- Keep logs deterministic and text-only (no emoji).
- Preserve resumability semantics across crashes and manual stops.

## External Dependencies
- Playwright may need to download browser binaries on first use (`playwright install`).
- Optional proxy / session environment variables (see `.env.example`).

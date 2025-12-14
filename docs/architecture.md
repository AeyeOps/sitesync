# Sitesync Architecture

## Objectives
- Maintain an asset-centric mirror of target websites with high fidelity and resumable synchronization.
- Provide modular acquisition so new asset types and sources can be added without touching the core engine.
- Guarantee strong hygiene: deterministic logging, linting, and testing across the entire codebase.

## System Overview
Sitesync is a Python-based CLI application composed of three tiers:
1. **Orchestration Core** – coordinates configuration, task queues, worker pools, and persistence.
2. **Acquisition Layer** – headless browser and HTTP fetchers that collect raw content while mimicking human browsing.
3. **Asset Plugins** – normalization modules that translate raw payloads into the shared ontology and emit diffs.

The application stores crawl state and asset histories in a local database so any run can resume after failure and historical context is always available.

## Component Breakdown

### CLI and Command Tree
- Executable name: `sitesync`.
- Implemented with Typer (or Click) to expose commands for crawl control, status inspection, asset browsing, queue introspection, configuration checks, logging utilities, and database maintenance.
- All commands load configuration by merging defaults, selected source profile, and CLI overrides. Version information is read from `pyproject.toml` via `importlib.metadata`.

### Configuration
- Primary configuration lives in `config/default.yaml` with optional overrides in `config/local.yaml`.
- Structure includes: global defaults, source profiles (start URLs, allowed domains, depth limits, plugin toggles), crawler settings (parallelism, jitter, throttles), storage paths, and logging directives.
- `.env` files populate environment variables; secrets such as cookies or proxy credentials are never written back to YAML.
- Configuration is validated on startup using Pydantic models for predictable failure when values are missing or malformed.

### Logging and Observability
- Logger is named `sitesync` and defaults to writing under the current working directory unless overridden in config.
- Uses `logging.handlers.RotatingFileHandler` with a 2 MB file size limit and up to five archives.
- Log format: `%(asctime)s %(process)08x %(thread)08x %(levelname).1s %(module)s %(message)s`.
- Console output is rendered with Rich for progress dashboards. File logs remain text-only. Emojis are not used anywhere in the system.
- Optional OpenTelemetry exporters can be layered in later without changing the public interface.

### Task Queue and Resumability
- Crawl work is stored in a persistent queue table (`crawl_tasks`) within the database. Tasks represent URLs plus metadata (depth, source profile, plugin hints).
- Workers claim tasks with optimistic locking, mark them `in_progress`, and commit progress after each fetch.
- On crash or shutdown, unacknowledged tasks return to `pending`, allowing `sitesync crawl --resume` to continue seamlessly.
- Frontier seeds are pulled from the active source profile but can be overridden via CLI (`--start-url`, `--start-from-file`).

### Acquisition Layer
- Default fetcher uses Playwright with Chromium in headless stealth mode. Features include user-agent rotation, viewport variance, persistent browser context, adaptive rate limiting, and scripted navigation when required.
- Random jitter between navigations and configurable throttling help evade anti-automation measures.
- Supplemental HTTP fetcher (requests-based) is used for feeds or APIs when direct requests are allowed.
- HTML snapshots from Playwright are passed to parsing routines so the system can reuse the same normalization logic regardless of fetch method.

### Parsing and Normalization
- BeautifulSoup provides robust HTML traversal. For large documents or performance-sensitive paths, `lxml` or `selectolax` can be swapped in through the plugin API.
- Asset plugins define discovery hooks, normalization steps, validation, and diff policies. Plugins register via entry points so new families can be added without editing core modules.
- Normalization outputs `AssetRecord` objects containing ontology fields, relationships, checksums, raw payload references, and provenance metadata.

### Persistence and Diffing
- SQLite (or DuckDB) stores:
  - `assets`: canonical record per asset and source.
  - `asset_versions`: immutable snapshots with normalized and raw payload hashes.
  - `runs`: high-level run metadata (source, start, end, plugin coverage).
  - `crawl_tasks`: pending/in-progress/completed queue items.
  - `exceptions`: unresolved events requiring manual follow-up.
- Checksums are content-addressable (e.g., SHA-256 of normalized representation). Diffing classifies assets as `new`, `updated`, `unchanged`, or `missing`.
- Missing assets are added to the exceptions table with full context; the system never deletes records automatically.

### Reporting
- After each run, Sitesync updates a single Markdown report (e.g., `tracking/status.md`).
- Top section summarizes the latest run (timestamp, coverage ratios, outstanding exceptions). Prior entries are retained below up to a configurable limit.
- CLI commands can regenerate the report or export structured JSON for external tooling.

### Exception Management
- Exceptions are captured whenever fetching, parsing, or validation fails, or when assets disappear.
- Each entry includes source profile, URL, asset type, failure reason, timestamp, retry count, and notes.
- CLI tooling supports listing, filtering, exporting, and resolving exceptions. Resolution keeps history but marks the item as handled.

### Testing and Quality Gates
- Tooling stack: Pylint, Ruff, Black, Mypy, Pytest.
- `pyproject.toml` separates runtime dependencies from development extras. `uv` manages lockfiles and reproducible installs.
- CI pipeline (e.g., GitHub Actions) runs lint, type-check, and test targets on every push.
- Sample HTML snapshots and recorded Playwright sessions support repeatable integration tests without network calls.

### Deployment
- Distribution via `uv build` (sdist/wheel). PyInstaller bundle optional for standalone executable; Makefile target `bundle` encapsulates the recipe.
- Docker image definition will sit under `build/` if needed, bundling Playwright browsers and environment setup.
- Documentation resides under `docs/`. Root `README.md` provides overview and links to architecture, operations, and contribution guides.

## Future Enhancements
- GraphQL or REST endpoints to expose the asset ontology programmatically.
- OpenTelemetry tracing for deeper observability.
- Plugin marketplace or configuration-driven registration for third-party asset handlers.
- Scheduling integration (systemd timers, cron, or task queues such as Arq) for automated runs.

## Open Questions
- Which database engine (SQLite vs DuckDB) should be the default, and do we need an abstraction to swap between them seamlessly?
- Do we need built-in redaction for sensitive content before storing snapshots?
- What retention policy, if any, should apply to browser session artifacts (cookies, HAR files)?

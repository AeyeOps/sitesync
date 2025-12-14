# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-01-20

### Added
- GitHub Actions CI workflow (`ci.yaml`) - runs `make validate` on PRs, e2e tests on main
- GitHub Actions release workflow (`release.yaml`) - builds wheel + Linux binaries (x64/ARM64) on tag push or manual dispatch
- Pre-commit hooks configuration (`.pre-commit-config.yaml`) with ruff check and format
- `pre-commit` added to dev dependencies
- `make release-build` target for local wheel build testing

### Changed
- CI validates code quality on every PR before merge

## [0.5.2] - 2026-01-05

### Added
- Pytype added to dev linting/tooling dependencies

### Changed
- Updated runtime/development dependency baselines to current releases
- Python requirement set to 3.12.9

## [0.5.1] - 2026-01-05

### Added
- Default Makefile help target with concise build/install commands
- `make install` wheel install flow targeting `/opt/bin`
- `make standalone` for bundled executable build + install via `sitesync.spec`
- Expanded PyInstaller hidden imports to cover UI, fetchers, and reports modules

## [0.5.0] - 2026-01-05

### Added
- Per-domain allow/deny path globs for crawl boundaries (deny wins)
- Hard per-task fetch timeout via `crawler.fetch_timeout_seconds`
- Auth-redirect guardrails with runtime deny rules and end-of-run config suggestions
- Queue backpressure and lease-expiry retry/backoff handling
- End-of-run summary line in `crawl` output

### Changed
- **Breaking**: `allowed_domains` is now a mapping of domain -> path filter rules
- Resume now starts a new run when no resumable run exists

### Fixed
- Terminal cleanup after ESC shutdown to restore input state
- Suppressed Playwright shutdown errors during interrupt/ESC exit

## [0.4.0] - 2025-12-15

### Added
- Source-first `sitesync data` CLI redesign for improved discoverability
  - `data` (no subcommand) - Shows sources overview table
  - `data sources` - List all sources
  - `data sources grep <pattern>` - Search content across ALL sources
  - `data source <name>` - Show source summary
  - `data source <name> assets` - List assets (was `data assets`)
  - `data source <name> content <id>` - View content (was `data content`)
  - `data source <name> export <dir>` - Export assets (was `data export`)
  - `data source <name> runs` - List runs (was `data runs`)
  - `data source <name> tasks` - List tasks (was `data tasks`)
  - `data source <name> stats` - Detailed source statistics
  - `data source <name> grep <pattern>` - Search content within source
  - `data source <name> delete` - Delete all data for a source
- Grep functionality with case-insensitive default, regex support, context lines
- Source statistics: run/asset/task counts, storage usage, timeline
- Database layer methods: `list_sources`, `get_source_summary`, `get_source_stats`, `delete_source`

### Changed
- **Breaking**: Removed old top-level `data runs`, `data assets`, etc. commands
- Source selection now via `data source <name>` instead of global `--source` option
- Database fallback to `./sitesync.sqlite` when no config provided

### Removed
- Global `--source` option for data commands (replaced by `data source <name>`)

## [0.3.0] - 2025-12-15

### Added
- `sitesync data` command group for querying crawled assets
  - `data runs` - List and inspect crawl runs
  - `data assets` - List, filter, search assets by type/URL pattern
  - `data content` - View raw or normalized asset content
  - `data tasks` - Inspect crawl task queue
  - `data export` - Bulk export assets to directory
- Enhanced `init` command with full configuration prompting
  - Multiple allowed domains (loop input like start URLs)
  - Parallel agents setting (default: 4)
  - Pages per agent setting (default: 5)
  - Fetcher options including wait_after_load (default: 3.0)
- Database query methods for asset access: `get_latest_run`, `list_assets`, `get_asset`, `get_asset_by_url`, `get_asset_version`, `list_tasks_for_run`
- Database index on `assets(run_id)` for improved query performance

### Changed
- Default depth changed from 1 to 5 in init command
- Default parallel_agents changed from 2 to 4 in init command
- Default pages_per_agent changed from 2 to 5 in init command
- Default wait_after_load changed from 1.5 to 3.0 in init command

## [0.0.2] - 2025-12-15

### Added
- PlaywrightFetcher `wait_for_selector` and `wait_for_selector_timeout` options for smarter page readiness detection.
- Stale-lease reclamation during task acquisition, allowing runs to resume after crashes.
- `make install-dev` for deterministic installation of lint/test/typecheck tooling.
- `make bundle` and `make install-bundle` for building/installing a standalone executable.

### Changed
- PlaywrightFetcher `wait_after_load` default changed from `0.0` to `1.5` seconds for better JS site support.
- Runs stopped via double-ESC are recorded as `stopped` (distinct from `completed`).

### Fixed
- Bounded transient retry behavior: retry exhaustion now marks tasks as `error` and the executor terminates.
- Plugin discovery via `importlib.metadata.entry_points()` works on modern Python.
- `sitesync crawl` now marks runs complete and emits metadata when there is no work to process.
- Documentation now reflects Typer global option ordering (e.g., `sitesync --source NAME crawl`).

## [0.0.1] - 2025-12-14

### Added
- Initial public release with CLI scaffolding, SQLite-backed task queue, and a basic normalization plugin.

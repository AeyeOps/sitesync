# Change: Update Crawl Reliability

## Why
The current Sitesync implementation is close to runnable end-to-end, but a full codebase review found a few correctness and ergonomics issues that can cause hangs, disable plugin discovery on modern Python, and make local tooling non-deterministic.

This change proposal documents everything found and defines a small, high-leverage set of fixes that restore intended behavior and improve resumability.

## Findings (Everything Found)

### Critical
- **Retry exhaustion can hang indefinitely**: `CrawlExecutor` configures Tenacity with `reraise=True` but handles `RetryError`, so a persistent `TransientFetchError` can loop forever and never mark the task as `error`. This is observable as a test hang/timeout.
- **Plugin entrypoint loading is incompatible with modern `importlib.metadata`**: `entry_points()` returns an `EntryPoints` object (no `.get()`), so `PluginRegistry.load_entrypoints()` will not load third-party plugins.

### High
- **`make typecheck` is non-functional**: `uv run mypy` is invoked without any target module/path, so it errors immediately.
- **Dev tooling is not reproducible by default**: `make lint`/`make format`/`make test` rely on dev tools, but `make install` runs `uv sync` without dev extras. On some machines this will fail; on others it may fall back to globally-installed tools, masking missing dependencies.
- **Python version mismatch risk**: project metadata targets `>=3.14,<3.15`, but local invocations may execute under other interpreters depending on environment/tooling configuration.

### Medium
- **Lease expiry is not reclaimed**: tasks are leased (`lease_expires_at`) but there is no mechanism to reclaim expired leases after crashes or long stalls.
- **Stop semantics likely misreport run state**: a user-triggered stop releases tasks back to `pending` but still marks the run as `completed`.
- **Configuration knobs not yet honored**: `jitter_seconds`, `max_pages`, and the source `plugins` list are present in config but not used to control runtime behavior.
- **Exceptions table is currently write-only-by-design but unused**: schema supports `exceptions`, and dashboards/reporting count open exceptions, but no code inserts exception records yet.

### Low
- **Lint debt**: `ruff check` reports a large number of style and modernization findings (import sorting, typing modernizations, line length, test style). This is valuable but not required to unblock reliability.
- **Docs drift**: `docs/architecture.md` describes a broader future system; current code implements a smaller subset. This is expected for scaffolding, but readers may assume more is implemented than currently is.
- **Timestamp format inconsistency in SQLite defaults**: some table defaults use SQLite `DATETIME('now')` formatting while runtime code uses an ISO-like format; current paths override defaults, but alignment reduces future foot-guns.

### Security/Secrets
- No obvious secrets found in the repository outside vendored environments.
- Operational risk: raw HTML snapshots and normalized outputs may contain sensitive data depending on target sites; retention/redaction policies are not defined yet.

## What Changes
- Fix retry exhaustion so persistent `TransientFetchError` terminates after `max_retries` and marks the task as `error`.
- Fix plugin entrypoint discovery to work with modern `importlib.metadata.entry_points()` APIs.
- Add stale-lease reclamation so a run can resume cleanly after crashes without manual intervention.
- Clarify and implement run stop semantics (status and timestamps) so “stopped” runs are distinguishable from “completed”.
- Make developer tooling deterministic by ensuring `make typecheck` targets code and by adding an explicit dev install path for lint/test/typecheck tools.

## Impact
- **Affected code areas**: `src/sitesync/core/executor.py`, `src/sitesync/plugins/registry.py`, `src/sitesync/storage/db.py`, `src/sitesync/cli/app.py`, `Makefile`, tests under `tests/`.
- **New/updated capabilities** (spec deltas in this change):
  - `crawl-executor`
  - `task-leasing`
  - `plugin-registry`
  - `developer-tooling`

## Non-Goals (This Change)
- Full implementation of all configuration knobs (`plugins`, `max_pages`, `jitter_seconds`) beyond what is required to fix hangs and resumability.
- Eliminating all Ruff/Pylint lint findings in one sweep.
- Implementing full exception ingestion and redaction/retention policies (can be proposed separately once behavior is agreed).

## Open Questions
1. Should a user-requested stop set run status to `stopped` (recommended), or still be treated as `completed` with a note?
2. Do we want to enforce Python `>=3.14` at runtime/tooling (align with `pyproject.toml`) or relax `requires-python` to match current execution environments?

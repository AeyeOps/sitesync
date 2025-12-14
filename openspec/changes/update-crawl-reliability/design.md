# Design: update-crawl-reliability

## Overview
This change focuses on reliability and resumability of the crawl loop and adjacent integration points:

- **Orchestrator** seeds tasks and marks a run `running`.
- **CrawlExecutor** leases tasks from SQLite, runs concurrent workers, and updates task state.
- **Fetchers** (Playwright or Null) produce a `FetchResult`.
- **Plugins** normalize payloads into `AssetRecord`s and persistence stores versions.

The review identified a small number of issues that undermine the intended behavior (termination, plugin discovery, crash recovery, and deterministic tooling).

## 1) Retry exhaustion semantics

### Current behavior (problem)
`CrawlExecutor` uses Tenacity with `reraise=True`. With `reraise=True`, Tenacity re-raises the last exception after retries are exhausted (e.g., `TransientFetchError`) rather than raising `RetryError`.

The code currently catches `RetryError` and treats it as “retry exhausted”. When the fetcher always raises `TransientFetchError`, the exception bypasses the `RetryError` handler and is handled by the broad `except Exception` block, which requeues the task as pending with backoff. This can cause an infinite loop and hangs.

### Proposed behavior
After `max_retries` attempts for a task that fails with `TransientFetchError`, the system SHOULD:
- Mark the task `error` (terminal)
- Record a useful error message in `last_error`
- Stop retrying that task so the overall run can drain and terminate

### Design choice
Prefer the minimal and most explicit correction:
- **Option A (minimal):** set Tenacity `reraise=False` so Tenacity raises `RetryError` on exhaustion, matching the existing `except RetryError` handler.
- **Option B:** keep `reraise=True` and add an explicit `except TransientFetchError` exhaustion path.

Option A is preferred because it aligns with existing control flow and minimizes behavioral surface area.

## 2) Lease expiry reclamation

### Current behavior (problem)
Tasks are leased by setting `status='in_progress'` plus `lease_owner` and `lease_expires_at`. However, there is no reclaim path for tasks whose leases expire (e.g., process crash). As a result, runs can become permanently stuck with tasks in progress.

### Proposed behavior
When acquiring tasks, the system SHOULD reclaim tasks whose lease has expired:
- Any task with `status='in_progress'` and `lease_expires_at <= now` becomes eligible for reacquisition.
- Reclaim should be concurrency-safe.

### Design choice
Implement reclamation inside the same SQLite transaction used by `acquire_tasks()`:
1. `BEGIN IMMEDIATE`
2. `UPDATE crawl_tasks SET status='pending', lease_owner=NULL, lease_expires_at=NULL, last_error='lease expired', updated_at=? WHERE run_id=? AND status='in_progress' AND lease_expires_at <= ?`
3. Select `pending` tasks eligible by `next_run_at <= now`
4. Mark selected tasks `in_progress` with a new lease
5. `COMMIT`

This keeps all state transitions atomic and avoids introducing a separate periodic “janitor” task.

## 3) Plugin entrypoint discovery

### Current behavior (problem)
`importlib.metadata.entry_points()` returns an `EntryPoints` object on modern Python, which does not support `.get(...)`. This prevents third-party plugin discovery via the `sitesync.plugins` entry point group.

### Proposed behavior
`PluginRegistry.load_entrypoints()` SHOULD:
- Support modern `EntryPoints.select(group="sitesync.plugins")`
- Fall back to older dict-style entry points when `.select` is unavailable

On plugin load errors, default behavior should be “skip + log” (do not crash a crawl) unless the user explicitly requests fail-fast in the future.

## 4) Run status on stop

### Current behavior (problem)
A user-triggered stop releases tasks back to `pending` but the run is still marked `completed`. This makes status reporting misleading and complicates resumption workflows.

### Proposed behavior
Introduce and use an explicit `stopped` status:
- Natural termination (queue drained) → `completed`
- User-triggered stop → `stopped`

Whether `stopped` sets `completed_at` should be decided; storing an end timestamp for stopped runs is still useful for observability.

## 5) Deterministic developer tooling

### Current behavior (problem)
`make typecheck` invokes mypy without targets and fails. Additionally, `make install` does not install dev tools, so lint/typecheck/test may rely on ambient/global tooling.

### Proposed behavior
- `make typecheck` MUST run mypy against explicit project targets.
- Provide an explicit dev install target (e.g., `make install-dev`) that installs the `dev` extra via uv.

This keeps runtime installs lightweight while allowing contributors to get a reproducible tooling environment.

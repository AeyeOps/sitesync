# Tasks: update-crawl-reliability

## 1. Reproduce and lock in failures
- [x] Add/adjust a regression test that fails on the current retry exhaustion behavior (hang/timeout) and passes once fixed.
- [x] Add a unit test covering plugin entrypoint discovery using `importlib.metadata.entry_points()` semantics.
- [x] Add a unit test covering stale lease reclamation (expired `lease_expires_at` returns tasks to `pending`).

## 2. Fix retry exhaustion semantics
- [x] Update `CrawlExecutor` retry configuration/handling so `TransientFetchError` tasks terminate after `max_retries`.
- [x] Ensure task status and counters reflect outcomes:
  - `finished` on success
  - `error` after retry exhaustion
  - `pending` with backoff for other failures
- [x] Ensure the executor run terminates once the queue is drained (no infinite loops).

## 3. Fix plugin entrypoint discovery
- [x] Update `PluginRegistry.load_entrypoints()` to support modern `EntryPoints.select(group=...)` and older dict-style APIs.
- [x] Decide and document expected behavior on plugin load failure (skip + log vs fail fast).

## 4. Implement stale-lease reclamation
- [x] Define lease expiry behavior (e.g., reclaim at `acquire_tasks()` time under a transaction).
- [x] Ensure reclaim is safe under concurrency (no double-claiming tasks).
- [x] Confirm resume behavior: a restarted executor should see previously leased tasks become eligible again.

## 5. Clarify stop/run status semantics
- [x] Decide final semantics for “stopped” vs “completed” runs (ties to Open Question #1).
- [x] Update run status recording and run metadata to reflect stop/completion accurately.

## 6. Make developer tooling deterministic
- [x] Fix `make typecheck` to run mypy on the intended targets (e.g., `src/sitesync`).
- [x] Add `make install-dev` (or equivalent) so dev tools are installed deterministically with uv extras.
- [x] Update `README.md` quick start to match the intended developer workflow.

## 7. Validation
- [x] Run `make test` and confirm it completes without timeouts/hangs.
- [x] Run `make typecheck` and confirm it runs against code (even if strictness failures remain to be addressed).
- [x] Run `make lint` and record any remaining findings that are out of scope for this change.

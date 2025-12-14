## Context
Crawl runs are stalling due to unbounded domain expansion and a leasing model that does not count expired leases as retries. GitHub-heavy seeds generate thousands of in-progress tasks that churn without reaching terminal states. We need deterministic bounds on discovery and robust handling of stuck fetches.

## Goals / Non-Goals
- Goals:
  - Constrain crawl discovery using per-domain path allow/deny rules.
  - Prevent indefinite worker hangs with a hard fetch timeout.
  - Convert lease expiry into retry accounting with backoff and terminal erroring.
  - Reduce in-progress bloat with basic backpressure.
- Non-Goals:
  - Preserve backward compatibility for legacy `allowed_domains` lists.
  - Add new fetcher implementations.

## Decisions
- Decision: Replace `allowed_domains` list with domain->filter mapping.
  - Why: Needed to attach path rules directly to domains without an additional parallel structure.
- Decision: Path filters are applied to discovered URLs before enqueueing.
  - Why: Keep queue size bounded and prevent needless tasks.
- Decision: Lease expiry increments attempt_count and applies backoff; after max retries, mark error.
  - Why: Avoid infinite lease churn and surface failures clearly.
- Decision: Add a hard per-task fetch timeout using `asyncio.wait_for`.
  - Why: Prevent hung pages from blocking workers indefinitely.
- Decision: Introduce queue backpressure based on a computed max in-flight threshold.
  - Why: Prevent mass pre-claiming that inflates `in_progress` and worsens lease churn.
- Decision: Detect auth redirects and add runtime deny rules for the remainder of the run.
  - Why: Avoid getting trapped in login flows (e.g., `/auth/login?continue=...`).

## Risks / Trade-offs
- Changing config schema is breaking; all in-repo YAML files must be updated.
- Overly strict path filters can exclude desired pages; documentation must be explicit.
- Path filters are exact by default; use glob wildcards (e.g., `/**`) for subtrees.
- Deny rules override allow rules.

## Migration Plan
1. Update YAML configs in-repo to new `allowed_domains` mapping.
2. Update config loader and tests to enforce the new schema.
3. Implement filtering + timeout + lease retry logic.
4. Run crawl on example config to verify completion.

## Open Questions
- Should backpressure be configurable, or fixed to a conservative multiple of worker capacity?

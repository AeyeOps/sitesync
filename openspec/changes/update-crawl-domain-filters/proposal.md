# Change: Domain path filters and stall prevention

## Why
Current crawls can stall indefinitely when large domains (e.g., GitHub) expand without bounds and when in-progress leases expire without retry accounting. This makes runs non-terminating and obscures progress, even though the crawl is actively processing pages.

## What Changes
- **BREAKING**: `allowed_domains` changes from a list of strings to a mapping of domain -> path filter rules.
- Add per-domain path filters (`allow_paths`/`deny_paths`) to constrain discovery and crawling.
- Treat expired task leases as retry attempts with backoff and eventual erroring after the configured retry limit.
- Add a hard per-task fetch timeout to prevent hung pages from blocking worker completion.
- Add queue backpressure so task acquisition does not balloon `in_progress` counts.

## Impact
- Affected specs: `crawl-config`, `crawl-execution`
- Affected code: config loader/models, executor link discovery, task leasing/backoff, CLI config init, tests
- Affected configs: `config/default.yaml`, `src/sitesync/config/default.yaml`, `local.yaml`, and any other YAML configs in-repo

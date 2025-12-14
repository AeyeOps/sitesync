## 1. Implementation
- [x] 1.1 Update config schema: replace `allowed_domains: list[str]` with domain->filters mapping
- [x] 1.2 Update YAML configs in-repo to new schema (no backward compatibility)
- [x] 1.3 Implement per-domain path filters in link discovery
- [x] 1.4 Add hard per-task fetch timeout
- [x] 1.5 Treat lease expiry as retries with backoff and terminal error after max attempts
- [x] 1.6 Add queue backpressure to limit in-flight task claims
- [x] 1.7 Update CLI init prompts and config show output to new schema
- [x] 1.8 Add/adjust unit tests for config parsing, link filtering, and lease expiry behavior
- [x] 1.9 Update README/docs to describe new config shape and filters

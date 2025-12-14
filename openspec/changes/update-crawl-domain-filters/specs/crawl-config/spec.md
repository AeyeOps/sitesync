## ADDED Requirements

### Requirement: Domain Path Filters
The system SHALL accept `allowed_domains` as a mapping from domain to path filter rules.

#### Scenario: Allow and deny paths per domain (glob matching)
- **WHEN** a source defines:
  - `allowed_domains.github.com.allow_paths: ["/example/docs/**"]`
  - `allowed_domains.github.com.deny_paths: ["/login", "/signup"]`
- **THEN** only URLs under `https://github.com/example/docs` are eligible and the denied paths are excluded.

#### Notes
- Path rules are exact by default; use glob wildcards (e.g., `/**`) to match subpaths.
- Deny rules take precedence over allow rules.

### Requirement: Fetch Timeout Configuration
The system SHALL allow configuring a hard per-task fetch timeout via `crawler.fetch_timeout_seconds`.

#### Scenario: Timeout configured
- **WHEN** `crawler.fetch_timeout_seconds` is set to `20`
- **THEN** any single fetch attempt SHALL be cancelled after 20 seconds.

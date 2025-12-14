# Sitesync Agents

## Purpose
Document the roles and expectations for autonomous agents and workers that participate in Sitesync crawls. This guide ensures consistent behavior when scaling concurrency or introducing new specialized agents.

## Agent Types
- **Crawler Workers**: Playwright-driven processes responsible for fetching pages. They honor throttling and jitter settings, persist session data, and hand off HTML to the normalization pipeline.
- **Parser Workers**: Processes that consume fetched payloads, apply the appropriate asset plugin, extract structured data, compute checksums, and submit diff results.
- **Persistence Workers**: Manage database commits for asset versions, queue updates, and exception records. They ensure resumability by committing after each unit of work.
- **Maintenance Agents**: Optional periodic jobs that vacuum the database, rotate logs, or regenerate reports without performing new crawls.

## Concurrency Expectations
- Each agent must poll the shared task queue, claim work atomically, and release it promptly upon completion or failure.
- Agents should record heartbeats so stalled tasks can be re-queued if a worker disappears.
- Configuration controls the pool size per agent type; default values are conservative but customizable per source profile or CLI override.

## Error Handling
- Exceptions are never swallowed. Failures are logged, stored in the exception table with rich context, and the task is either retried or escalated based on policy.
- Agents must respect backoff rules when encountering repeated transient failures to avoid triggering anti-bot mechanisms.

## Operational Guidance
- Agents do not emit emojis in logs, console output, or reports.
- Shared utilities handle logging setup so every worker uses the same format and rotation policy.
- When adding a new agent type, document its responsibilities here and ensure it integrates with the central configuration and metrics interfaces.

## Future Extensions
- Specialized agents for media downloads, PDF parsing, or API harvesting can be registered through the plugin system.
- Coordinators could allocate tasks based on priority, asset type, or rate-limit partitions (per domain or path).

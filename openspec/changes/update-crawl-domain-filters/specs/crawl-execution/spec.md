## ADDED Requirements

### Requirement: Apply Path Filters During Discovery
The system SHALL apply per-domain path allow/deny rules (glob matching, deny wins) before enqueueing discovered URLs.

#### Scenario: Discovered link excluded by deny list
- **WHEN** a page on `github.com` links to `/login`
- **AND** the domain filter includes `deny_paths: ["/login"]`
- **THEN** the `/login` URL is not enqueued.

### Requirement: Lease Expiry Counts as Retry
The system SHALL treat an expired task lease as a retry attempt and apply backoff before requeueing.

#### Scenario: Lease expiry increments attempts
- **WHEN** a task lease expires while the task is `in_progress`
- **THEN** its `attempt_count` is incremented and `next_run_at` is advanced by a backoff interval.

### Requirement: Terminal Failure After Retry Limit
The system SHALL mark a task as `error` once its retry attempts exceed the configured limit.

#### Scenario: Max retries reached
- **WHEN** a task exceeds `crawler.max_retries`
- **THEN** the task status becomes `error` and it is no longer re-enqueued.

### Requirement: Queue Backpressure
The system SHALL limit task acquisition so the number of in-progress tasks does not grow without bound.

#### Scenario: Acquisition throttles at capacity
- **WHEN** in-flight tasks reach the configured capacity for the run
- **THEN** the producer waits before acquiring additional tasks.

### Requirement: Auth Redirect Suppression
The system SHALL detect auth redirects and suppress further discovery from those pages for the current run.

#### Scenario: Auth login redirect with continue path
- **WHEN** a fetch ends at `/auth/login?continue=/settings/roles`
- **THEN** the system skips link discovery for that page
- **AND** the system adds runtime deny rules for `/auth/**` and `/settings/roles/**` for the remainder of the run.

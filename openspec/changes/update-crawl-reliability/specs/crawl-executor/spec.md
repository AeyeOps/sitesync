# Capability: crawl-executor

## ADDED Requirements

### Requirement: Transient retries are bounded
The system SHALL treat `TransientFetchError` failures as retryable up to the configured maximum retry count (`crawler.max_retries`) and SHALL stop retrying once that limit is reached.

#### Scenario: Retry exhaustion marks task as error
- **GIVEN** a run with a single pending task
- **AND** a fetcher that raises `TransientFetchError` on every attempt
- **WHEN** the crawl executor processes the task
- **THEN** the task status is recorded as `error`
- **AND** the executor run terminates without hanging

### Requirement: Successful retry records completion
The system SHALL allow a transient failure to be retried and, when a later attempt succeeds, SHALL mark the task as `finished`.

#### Scenario: A task succeeds after one transient failure
- **GIVEN** a run with a single pending task
- **AND** a fetcher that raises `TransientFetchError` on the first call and succeeds on the second call
- **WHEN** the crawl executor processes the task
- **THEN** the task status is recorded as `finished`
- **AND** the executor run terminates

### Requirement: Run termination is deterministic
The system SHALL terminate a crawl execution loop when there is no remaining work for the run (no `pending` or `in_progress` tasks).

#### Scenario: Drained queue exits
- **GIVEN** a run with multiple pending tasks
- **WHEN** the executor processes all tasks successfully
- **THEN** the executor returns control to the caller without requiring a manual stop


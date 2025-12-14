# Capability: task-leasing

## ADDED Requirements

### Requirement: Expired leases are reclaimable
The system SHALL reclaim tasks whose leases have expired so that runs can resume after crashes or stalls.

#### Scenario: Expired lease returns task to pending
- **GIVEN** a task with status `in_progress`
- **AND** the task has a `lease_expires_at` timestamp in the past
- **WHEN** the system attempts to acquire tasks for the run
- **THEN** the expired task becomes eligible for acquisition again

### Requirement: Leasing operations are atomic
The system SHALL update task leasing state atomically to prevent multiple workers from claiming the same task concurrently.

#### Scenario: Concurrent acquisition does not double-claim
- **GIVEN** multiple workers attempting to acquire tasks for the same run
- **WHEN** tasks are acquired concurrently
- **THEN** each task is leased to at most one worker at a time


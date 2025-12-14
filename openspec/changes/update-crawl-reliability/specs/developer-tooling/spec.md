# Capability: developer-tooling

## ADDED Requirements

### Requirement: Typechecking is runnable from Makefile
The system SHALL provide a `make typecheck` target that runs mypy against the project code without requiring manual arguments.

#### Scenario: Typecheck target runs with explicit targets
- **GIVEN** a developer has installed dev dependencies
- **WHEN** the developer runs `make typecheck`
- **THEN** mypy is invoked with at least one target path/module (e.g., `src/sitesync`)

### Requirement: Dev dependency installation is explicit
The system SHALL provide a deterministic path to install developer tooling dependencies (linting, formatting, typechecking, testing).

#### Scenario: Developer installs dev tools via a documented target
- **GIVEN** a fresh checkout
- **WHEN** the developer runs the documented dev install target (e.g., `make install-dev`)
- **THEN** the developer can run `make test`, `make lint`, and `make typecheck` without relying on globally installed tools


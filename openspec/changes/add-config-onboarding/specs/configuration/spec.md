# Capability: configuration

## ADDED Requirements

### Requirement: Config selection is replace-by-default
The system SHALL treat `--config PATH` as selecting a complete configuration document and SHALL NOT merge in local default/override files unless explicitly requested.

#### Scenario: Selected config is used exclusively
- **GIVEN** the user provides `--config PATH`
- **AND** there is a `config/local.yaml` in the current working directory
- **WHEN** Sitesync loads configuration
- **THEN** Sitesync uses only `PATH` (plus schema defaults) to build the effective configuration

### Requirement: On-demand configuration initialization
The system SHALL provide an on-demand onboarding command that can generate a valid starter configuration for a new user.

#### Scenario: Initialize a local configuration file
- **GIVEN** the user has no existing `config/local.yaml`
- **WHEN** the user runs the onboarding command
- **THEN** the system writes a valid configuration file at the chosen path
- **AND** the configuration contains at least one seed URL

### Requirement: Configuration is inspectable
The system SHALL provide a command to display the effective configuration after merging all applicable sources.

#### Scenario: Show effective configuration
- **GIVEN** the user has a default configuration and optional local overrides
- **WHEN** the user runs the configuration inspection command
- **THEN** the system prints the resolved configuration in a human-readable format

### Requirement: Default configuration is available for wheel installs
The system SHALL make a default configuration available when Sitesync is installed as a wheel so that it does not require `config/default.yaml` to exist in the current working directory.

#### Scenario: Installed usage loads packaged defaults
- **GIVEN** Sitesync is installed into a Python environment
- **AND** there is no `config/default.yaml` in the current working directory
- **WHEN** the user runs a Sitesync command that requires configuration
- **THEN** the system loads a packaged default configuration

### Requirement: Onboarding does not persist secrets
The system SHALL NOT persist secrets (tokens, passwords, session cookies) into generated configuration files.

#### Scenario: Sensitive values are not written to disk
- **GIVEN** the user completes onboarding prompts
- **WHEN** the system writes the configuration file
- **THEN** the file does not contain any secret values
- **AND** the user is directed to use environment variables for sensitive configuration

## ADDED Requirements

### Requirement: Makefile Help Menu
The system SHALL provide a default Makefile target that prints a concise list of available build commands.

#### Scenario: Default make invocation
- **WHEN** `make` is run with no target
- **THEN** a help menu listing available commands is printed

### Requirement: Install Target
The system SHALL provide `make install` to build a wheel and install the executable into `/opt/bin`.

#### Scenario: Install build
- **WHEN** `make install` is invoked
- **THEN** a wheel is built and `sitesync` is installed into `/opt/bin`

### Requirement: Standalone Target
The system SHALL provide `make standalone` to build a bundled executable and copy it to `/opt/bin`.

#### Scenario: Standalone build
- **WHEN** `make standalone` is invoked
- **THEN** a bundled executable is produced and copied to `/opt/bin`

### Requirement: Bundler Spec
The system SHALL keep a committed bundler spec file with required hidden imports.

#### Scenario: Bundler spec usage
- **WHEN** the standalone target is executed
- **THEN** the committed spec file is used to include hidden imports

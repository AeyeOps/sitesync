# Capability: plugin-registry

## ADDED Requirements

### Requirement: Built-in plugins are loadable
The system SHALL load built-in plugins so that core asset types (e.g., `page`) can be normalized without third-party installation steps.

#### Scenario: Built-in plugin is available
- **GIVEN** the application starts a crawl run
- **WHEN** built-in plugins are loaded
- **THEN** the registry contains at least one plugin that supports the `page` asset type

### Requirement: Entry point plugins are discoverable
The system SHALL discover plugins registered via the `sitesync.plugins` entry point group using modern `importlib.metadata` APIs.

#### Scenario: Entry points are registered and loaded
- **GIVEN** one or more installed packages exposing entry points in the `sitesync.plugins` group
- **WHEN** the registry loads entry points
- **THEN** those plugins are registered and can be selected by asset type

### Requirement: Plugin load failures are isolated
The system SHALL treat a single plugin load failure as non-fatal and SHALL continue loading other plugins.

#### Scenario: One bad plugin does not block others
- **GIVEN** multiple entry point plugins
- **AND** one plugin raises an exception during loading
- **WHEN** the registry loads entry points
- **THEN** other valid plugins are still registered


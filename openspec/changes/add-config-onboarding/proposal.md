# Change: Add on-demand configuration onboarding

## Why
Sitesync currently assumes it is being run from a source checkout that contains `config/default.yaml`. New users who install Sitesync as a package or run the standalone executable without a local config directory can hit non-obvious “missing config” or “no seed URLs” outcomes.

This change proposes a user-friendly, on-demand configuration flow that helps users create a valid configuration without needing to read internal file layout details.

## What Changes
- Add an onboarding command that can generate a starter configuration (e.g., `sitesync init` or `sitesync config init`).
- Provide a way to view the resolved/effective configuration (e.g., `sitesync config show`) for troubleshooting.
- Treat configuration files as “documents”: selecting `--config PATH` uses that config exclusively (replace-by-default).
- Make a default configuration available even when installed as a wheel (via packaged resources), while still allowing local overrides.
- Ensure onboarding avoids persisting secrets and instead points users to environment variables for sensitive values.

## Impact
- **Affected areas**: CLI commands, configuration loading, packaging of default config, documentation.
- **Primary user benefit**: a first-run experience that is guided and actionable rather than error-driven.

## Non-Goals
- Implementing a full TUI configuration editor.
- Managing authentication secrets interactively or persisting credentials to disk.
- Changing crawl semantics beyond configuration UX.

## Open Questions
1. Command shape: `sitesync init` vs `sitesync config init` (preferred: `sitesync init` for discoverability).
2. Output target: generate `config/local.yaml` in the current directory vs a user-selected path.
3. Should the onboarding create a new project directory layout (e.g., `./config/`, `./data/`) or only a config file?

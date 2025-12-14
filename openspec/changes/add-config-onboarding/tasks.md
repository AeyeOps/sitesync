# Tasks: add-config-onboarding

## 1. Spec and UX alignment
- [x] Confirm the preferred command shape (`sitesync init` vs `sitesync config init`).
- [x] Confirm the default output location for generated config.
- [x] Decide whether to package a default config for wheel installs under `sitesync/`.

## 2. Implement onboarding command
- [x] Add the new CLI command and prompts.
- [x] Generate a valid config file matching the existing schema.
- [x] Add `--force` and `--path` (or equivalent) for non-interactive usage.

## 3. Implement `config show`
- [x] Add a command that prints the resolved configuration.
- [x] Include a mode that explains config precedence (default/local/override).

## 4. Package default config for wheel installs
- [x] Ship a default config YAML as package data.
- [x] Update config loader to use `importlib.resources` as a fallback when no local default exists.
- [x] Add unit tests for “installed” behavior using `importlib.resources`.

## 5. Documentation
- [x] Update `README.md` with “first run” guidance using `sitesync init`.
- [x] Document config precedence and where files live.

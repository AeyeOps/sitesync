# Design: add-config-onboarding

## Goals
- Make first-run configuration discoverable and low-friction.
- Keep configuration files readable and reviewable (YAML).
- Avoid writing secrets to disk.

## Proposed UX

### 0) Loading a different config (configuration as a document)
Sitesync SHOULD treat configuration files as “documents” that the user opens and runs with.

Selection:
- `sitesync --config PATH …` selects a configuration document and uses it **exclusively** (replace-by-default).
- `sitesync --source NAME …` selects a source profile within the loaded configuration.

Persistence:
- Configuration selection is not persisted across invocations today; this change keeps that posture.
- Within a single invocation, the loaded config is reused across commands.

### 1) `sitesync init` (on-demand onboarding)
Interactive prompts (minimal set):
- Source name (default: `default`)
- One or more start URLs
- Allowed domains (suggest derived from the start URL host)
- Depth (default from global config)
- Fetcher selection (`playwright` or `null`)

Output:
- Writes a YAML file (default: `config/local.yaml`) using the existing schema.
- If the user enters a directory path, Sitesync writes `<dir>/config/local.yaml`.
- If the output file exists, prompts for confirmation before overwriting (or requires `--force` for non-interactive usage).

Interactive flow (visual):

```text
$ sitesync init

Config path [config/local.yaml]: <PATH>
  ├─ (if <PATH> is a directory) -> write <PATH>/config/local.yaml
  ├─ (if destination exists) -> Overwrite? [y/N]
  ├─ Source name [default]
  ├─ Start URL(s) (repeat until blank)
  ├─ Allowed domains (default derived from start URLs)
  ├─ Depth [1]
  └─ Fetcher [playwright/null] [playwright]

Wrote <destination>
```

Example interactive flow:

```text
$ sitesync init

Config path [config/local.yaml]:
Source name [default]:
Start URL (enter blank to finish): https://example.com/
Start URL (enter blank to finish):
Allowed domains [example.com]:
Depth [1]:
Fetcher [playwright/null] (playwright):

Wrote config/local.yaml
```

Overwrite confirmation:

```text
$ sitesync init

Config path [config/local.yaml]:
config/local.yaml already exists. Overwrite? [y/N]: n
Aborted.
```

### 2) `sitesync config show`
Prints the effective configuration for the current invocation.

- If `--config` is provided, this displays that configuration document (plus schema defaults).
- Otherwise, this displays the merged configuration from `config/default.yaml` and optional `config/local.yaml`.

Optional enhancements:
- `--format yaml|json`
- `--paths` to show which files were loaded and in what order

## In-app switching (non-goal for now)
Sitesync does not provide an interactive “in-app” mode today; each command invocation loads configuration once.
Switching configs while a crawl is running is out of scope—stop the run (double-ESC) and restart with `--config`.

## Configuration source behavior (wheel + source checkout)
When running from a source checkout, Sitesync continues to load:
- `config/default.yaml`
- `config/local.yaml` (optional)

When installed from a wheel (or otherwise not running in a checkout), Sitesync additionally supports:
- A packaged default configuration shipped as part of the Python package (resolved via `importlib.resources`).

This avoids relying on `config/default.yaml` existing in the current working directory.

## Guardrails
- Never prompt for or persist secrets (tokens, passwords, cookies).
- For sensitive configuration, use `.env` and documented environment variables.

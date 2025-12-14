# Sitesync

[![CI](https://github.com/aeyeops/sitesync/actions/workflows/ci.yaml/badge.svg)](https://github.com/aeyeops/sitesync/actions/workflows/ci.yaml)

Sitesync is an experimental CLI for synchronizing web source assets into a local, queryable asset store
(SQLite + normalized outputs).

This repository is early-stage. Interfaces and storage layouts may change.

## Features
- Resumable runs backed by SQLite (task leasing + restart support)
- Pluggable normalization via asset plugins (built-in `page` normalizer, plus entry points)
- Run metadata + a lightweight Markdown status report
- Data access CLI for querying and exporting captured assets
- Domain-scoped allow/deny globs to keep crawl scope bounded
- Configurable fetch timeouts and queue backpressure for long-running runs
- Auth-redirect guardrails with suggested config updates

## Requirements
- Python 3.13
- `uv` (used by the Make targets): https://github.com/astral-sh/uv
- Optional: Playwright browser binaries if using the Playwright fetcher (`uv run playwright install chromium`)

## Install

Install Sitesync into `/usr/local/bin` using a wheel build (no sudo):

```bash
make install
# or: make install PREFIX=$HOME/.local
```

Developer tooling (validate/test, creates/updates `.venv`):

```bash
make install-dev
```

Run Sitesync without activating a virtualenv:

```bash
uv run sitesync --help
```

## Standalone executable
Build and install a bundled executable (PyInstaller spec is committed as `sitesync.spec`):

```bash
make standalone
# or: make standalone BINDIR=$HOME/.local/bin
```

## Quick Start
Start a run from one or more seed URLs:

```bash
uv run sitesync crawl --start-url https://example.com --depth 1
```

To stop a running run, press `Esc` twice.

Check status/history:

```bash
uv run sitesync status
```

## Configuration
Sitesync loads YAML configuration in one of two modes:
- **Config document**: pass `--config PATH` to load a single configuration file (replace-by-default).
- **Default precedence**: when `--config` is not provided, load:
  1) `config/default.yaml` (or packaged default if missing)
  2) `config/local.yaml` (optional; ignored by git)

If a configuration contains multiple source profiles, select one with `--source NAME`.

Environment variables are optional. See `.env.example`.

`allowed_domains` is a mapping from domain to optional path filters. Path rules are
exact by default; use glob wildcards for broader matches. Deny rules take precedence.

```yaml
allowed_domains:
  example.com:
    allow_paths:
      - /docs          # exact match only
      - /docs/**       # allow subtree
    deny_paths:
      - /login         # exact match
      - /docs/private/**  # deny subtree
  api.example.com: {}
```

Optional hard per-task timeout:

```yaml
crawler:
  fetch_timeout_seconds: 20
```

Auth redirects

If a fetch ends on `/auth/login` with a `continue=` parameter, Sitesync will
skip link discovery on that page and add a runtime deny rule for `/auth/**`
and the `continue` path subtree for the rest of the run.
When this happens, Sitesync prints a suggested YAML update at the end of the run
so you can make the deny rules permanent in your config.

## First Run
To create a starter config interactively:

```bash
uv run sitesync init
```

Then run a crawl:

```bash
uv run sitesync crawl
```

Inspect the effective configuration:

```bash
uv run sitesync config show --paths
```

## Outputs
- SQLite database: `storage.path` or `./sitesync.sqlite`
- Run metadata JSON: `outputs.base_path/outputs.metadata_subdir` (per run)
- Status report: `tracking/status.md`

## Querying Data
After a run completes, use the `data` command group to inspect results.

List all sources:

```bash
uv run sitesync data
```

View source summary:

```bash
uv run sitesync data source MySite
```

List runs for a source:

```bash
uv run sitesync data source MySite runs
```

List assets from the most recent completed run:

```bash
uv run sitesync data source MySite assets
uv run sitesync data source MySite assets --type page
uv run sitesync data source MySite assets --url "**/products/*"
```

View asset content:

```bash
uv run sitesync data source MySite content 1234
uv run sitesync data source MySite content --url "https://example.com/page"
```

Search content within a source:

```bash
uv run sitesync data source MySite grep "pricing"
uv run sitesync data source MySite grep "pricing" --regex -C 2
```

Search content across all sources:

```bash
uv run sitesync data sources grep "spend management"
```

View detailed statistics:

```bash
uv run sitesync data source MySite stats
```

Export assets to a directory:

```bash
uv run sitesync data source MySite export ./output --with-metadata
uv run sitesync data source MySite export ./output --dry-run
```

Delete all data for a source:

```bash
uv run sitesync data source OldSource delete
uv run sitesync data source OldSource delete --force
```

## Responsible Use
Sitesync fetches and normalizes content from configured sources. Ensure you have permission to access targets and comply
with applicable laws, access policies, and site terms. Captured content may contain sensitive data; store
and handle it appropriately.

## Commands
- `sitesync crawl`: start or resume a crawl run
- `sitesync init`: interactively generate a starter config file
- `sitesync config show`: show the effective configuration for this invocation
- `sitesync status`: show recent runs and queue summary
- `sitesync data`: list sources (discovery)
- `sitesync data sources`: list sources
- `sitesync data sources grep`: search across all sources
- `sitesync data source <name>`: show source summary
- `sitesync data source <name> runs`: list runs
- `sitesync data source <name> assets`: list assets
- `sitesync data source <name> content`: view asset content
- `sitesync data source <name> tasks`: inspect task queue
- `sitesync data source <name> export`: export assets to directory
- `sitesync data source <name> stats`: detailed statistics
- `sitesync data source <name> grep`: search within source
- `sitesync data source <name> delete`: delete source data
- `sitesync version`: print version

## Development

### Pre-commit Hooks
Install pre-commit hooks to run ruff on every commit:

```bash
uv run pre-commit install
```

Run hooks manually on all files:

```bash
uv run pre-commit run --all-files
```

### Quality Checks

```bash
make validate      # ruff check, ruff format, uv ty, pytest
make test          # unit tests only
make e2e           # end-to-end tests (requires playwright)
```

### Releases
Releases are automated via GitHub Actions:
- Push a tag `v*` to trigger a release build
- Or use manual workflow dispatch from the Actions tab

Release artifacts:
- Python wheel (`sitesync-{version}-py3-none-any.whl`)
- Linux x64 binary (`sitesync-{version}-linux-x64`)
- Linux ARM64 binary (`sitesync-{version}-linux-arm64`)

Build a wheel locally for testing:

```bash
make release-build
```

## Documentation
- Architecture: `docs/architecture.md`
- Agent roles: `docs/agents.md`
- Contributing: `CONTRIBUTING.md`
- Code of Conduct: `CODE_OF_CONDUCT.md`
- Security: `SECURITY.md`
- Support: `SUPPORT.md`

## Changelog
See `CHANGELOG.md`.

## License
- MIT License: `LICENSE`
- Third-party notices: `THIRD_PARTY_NOTICES.md`

No emojis are used in documentation, console output, or logs.

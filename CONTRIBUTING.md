# Contributing to Sitesync

Thanks for considering a contribution.

## Development setup

### Requirements
- Python 3.13 (see `.python-version`)
- `uv` (https://github.com/astral-sh/uv)

### Install
```bash
make install-dev
```

### Common commands
```bash
make validate      # all quality checks (ruff, uv ty, pytest)
make test          # unit tests only
make e2e           # end-to-end tests
```

### Standalone executable
```bash
make bundle
make install-bundle          # installs to /usr/local/bin
# or: make install-bundle BINDIR=$HOME/.local/bin
```

## Pull requests
- Keep changes focused and scoped to a single concern.
- Add or update tests when behavior changes.
- Update docs (`README.md`, `CHANGELOG.md`) when user-facing behavior changes.
- Follow existing code style (Ruff; line length 100).
- Do not add emojis to docs, console output, or logs.

## Reporting issues
If you think you found a security issue, do not open a public issue. See `SECURITY.md`.

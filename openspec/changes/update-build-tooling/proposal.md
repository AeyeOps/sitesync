# Change: Build tooling updates for uv bundling and Make targets

## Why
We want repeatable build workflows that fit uv while exposing a small, discoverable set of Make targets. This enables consistent installs, linting, and standalone packaging without requiring users to remember bespoke commands.

## What Changes
- Add/refresh Makefile targets with a default help menu.
- Update install flow to build a wheel and install it into the path.
- Add a standalone build target that bundles an executable and copies it to `/opt/bin` without sudo.
- Keep a committed `sitesync.spec` with explicit hidden imports needed for bundling.
- Document the new build/installation flow in README and changelog.

## Impact
- Affected specs: build-tooling (new).
- Affected code: Makefile, `sitesync.spec`, README, CHANGELOG, packaging metadata.

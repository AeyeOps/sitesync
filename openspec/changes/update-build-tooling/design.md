## Context
Sitesync currently ships with uv-based dependency management and a PyInstaller-based standalone build. The workflow is functional but not discoverable, and the Makefile lacks a default help menu. We need a standard entry point for install/lint/standalone that is compatible with uv and keeps the bundling behavior stable.

## Goals / Non-Goals
- Goals:
  - Provide a default Makefile help menu with concise command descriptions.
  - Ensure `make install` installs an executable into `/opt/bin` without sudo.
  - Provide a single `make standalone` target that builds and copies a bundled executable.
  - Keep `sitesync.spec` committed and updated for hidden imports.
  - Prefer uv-native build/bundle support when available.
- Non-Goals:
  - Redesign runtime behavior or CLI semantics.
  - Change dependency versions beyond what bundling requires.

## Decisions
- Decision: Keep Makefile as the primary entry point for build tasks.
  - Why: It already exists and is consistent with current workflows.
- Decision: Prefer uv bundling if it can replace PyInstaller without losing functionality.
  - Why: Simplifies tooling and keeps workflows aligned with uv.
- Decision: Default Makefile target prints a help menu.
  - Why: Improves discoverability.

## Risks / Trade-offs
- uv bundling capability may not cover all hidden imports; we may need to keep PyInstaller as a fallback.
- Installing into `/opt/bin` assumes write access; document that expectation.

## Migration Plan
1. Implement Makefile changes and bundling decision.
2. Update `sitesync.spec` and README.
3. Validate with a local build and standalone run.

## Open Questions
- Does uv bundling fully replace PyInstaller in this repo, or should `make standalone` retain PyInstaller for now?

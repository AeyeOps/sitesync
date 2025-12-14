"""Sitesync package initialization."""

from __future__ import annotations

import tomllib
from functools import lru_cache
from importlib import metadata
from pathlib import Path


def _find_pyproject(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        path = candidate / "pyproject.toml"
        if path.is_file():
            return path
    return None


def _read_version_from_pyproject(pyproject: Path) -> str | None:
    try:
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError:  # pragma: no cover - filesystem errors
        return None

    project = data.get("project")
    if not isinstance(project, dict):
        return None

    name = project.get("name")
    if name != "sitesync":
        return None

    version = project.get("version")
    if not isinstance(version, str) or not version.strip():
        return None

    return version.strip()


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return the Sitesync version.

    Version source of truth is `pyproject.toml`. When running from a source checkout, Sitesync reads
    `[project].version` directly. When installed as a package, it falls back to installed package
    metadata (which is generated from `pyproject.toml` at build time).
    """

    pyproject = _find_pyproject(Path(__file__).resolve().parent)
    if pyproject is not None:
        version = _read_version_from_pyproject(pyproject)
        if version is not None:
            return version

    try:
        return metadata.version("sitesync")
    except metadata.PackageNotFoundError as exc:  # pragma: no cover - occurs in dev
        raise RuntimeError("Unable to determine Sitesync version.") from exc


__all__ = ["get_version"]

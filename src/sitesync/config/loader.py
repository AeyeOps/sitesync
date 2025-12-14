"""Configuration loading for Sitesync."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path("config/default.yaml")
LOCAL_CONFIG_PATH = Path("config/local.yaml")


class LoggingSettings(BaseModel):
    """Logging configuration."""

    model_config = ConfigDict(extra="forbid")

    path: Path | None = None
    level: str = Field(default="info")

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_level(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("Logging level must be a string.")
        normalized = value.strip().lower()
        if normalized not in {"debug", "info", "warn", "warning", "error", "critical"}:
            raise ValueError(f"Unsupported logging level: {value!r}")
        return normalized


class CrawlerSettings(BaseModel):
    """Global crawler defaults."""

    model_config = ConfigDict(extra="forbid")

    parallel_agents: int = Field(default=2, ge=1)
    pages_per_agent: int = Field(default=2, ge=1)
    jitter_seconds: float = Field(default=1.0, ge=0.0)
    heartbeat_seconds: float = Field(default=30.0, ge=1.0)
    max_retries: int = Field(default=3, ge=0)
    backoff_min_seconds: float = Field(default=1.0, ge=0.0)
    backoff_max_seconds: float = Field(default=60.0, ge=0.0)
    backoff_multiplier: float = Field(default=2.0, ge=1.0)
    fetch_timeout_seconds: float | None = Field(default=None, ge=0.1)


class DomainFilter(BaseModel):
    """Allowed domain filter rules (exact by default, supports glob-style wildcards)."""

    model_config = ConfigDict(extra="forbid")

    allow_paths: list[str] = Field(default_factory=list)
    deny_paths: list[str] = Field(default_factory=list)

    @field_validator("allow_paths", "deny_paths", mode="before")
    @classmethod
    def _normalize_paths(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise TypeError("Path filters must be a list of strings.")
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("Path filters must be strings.")
            path = item.strip()
            if not path:
                continue
            if "://" in path:
                parsed = urlparse(path)
                path = parsed.path or "/"
            if not path.startswith("/"):
                path = f"/{path}"
            if path != "/" and not any(ch in path for ch in ("*", "?", "[")) and path.endswith("/"):
                path = path.rstrip("/") or "/"
            cleaned.append(path)
        return cleaned


class SourceSettings(BaseModel):
    """Per-source configuration profile."""

    model_config = ConfigDict(extra="forbid")

    name: str
    start_urls: list[str] = Field(default_factory=list)
    allowed_domains: dict[str, DomainFilter] = Field(default_factory=dict)
    depth: int = Field(default=1, ge=0)
    plugins: list[str] = Field(default_factory=list)
    parallel_agents: int | None = Field(default=None, ge=1)
    pages_per_agent: int | None = Field(default=None, ge=1)
    jitter_seconds: float | None = Field(default=None, ge=0.0)
    max_pages: int | None = Field(default=None, ge=1)
    fetcher: str = Field(default="playwright")
    fetcher_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fetcher", mode="before")
    @classmethod
    def _normalize_fetcher(cls, value: Any) -> str:
        if value is None:
            return "playwright"
        if not isinstance(value, str):
            raise TypeError("Fetcher must be a string or null to inherit the default.")
        return value

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _normalize_allowed_domains(cls, value: Any) -> dict[str, DomainFilter]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("allowed_domains must be a mapping of domain -> filter rules.")
        normalized: dict[str, DomainFilter] = {}
        for key, raw in value.items():
            if not isinstance(key, str):
                raise TypeError("allowed_domains keys must be strings.")
            domain = key.strip().lower()
            if not domain:
                continue
            normalized[domain] = raw
        return normalized


class StorageSettings(BaseModel):
    """Storage configuration."""

    model_config = ConfigDict(extra="forbid")

    path: Path | None = None


class OutputSettings(BaseModel):
    """Output directory configuration."""

    model_config = ConfigDict(extra="forbid")

    base_path: Path = Path("data")
    raw_subdir: str = "raw"
    normalized_subdir: str = "normalized"
    metadata_subdir: str = "runs"


class ConfigModel(BaseModel):
    """Root configuration model."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    default_source: str = "default"
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    crawler: CrawlerSettings = Field(default_factory=CrawlerSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    outputs: OutputSettings = Field(default_factory=OutputSettings)
    sources: list[SourceSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_sources(self) -> ConfigModel:
        names = {source.name for source in self.sources}
        if self.sources and len(names) != len(self.sources):
            raise ValueError("Source names must be unique.")
        if self.sources and self.default_source not in names:
            raise ValueError(
                f"Default source '{self.default_source}' is not defined in sources section."
            )
        return self


@dataclass(slots=True)
class Config:
    """Validated configuration with convenience helpers."""

    model: ConfigModel
    raw: Mapping[str, Any] = field(repr=False)
    loaded_from: tuple[str, ...] = field(default_factory=tuple, repr=False)
    _sources_by_name: dict[str, SourceSettings] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._sources_by_name = {source.name: source for source in self.model.sources}

    @property
    def logging(self) -> LoggingSettings:
        """Return logging settings."""

        return self.model.logging

    @property
    def crawler(self) -> CrawlerSettings:
        """Return crawler defaults."""

        return self.model.crawler

    @property
    def storage(self) -> StorageSettings:
        """Return storage configuration."""

        return self.model.storage

    @property
    def outputs(self) -> OutputSettings:
        """Return output directory configuration."""

        return self.model.outputs

    @property
    def default_source(self) -> str:
        """Return the default source name."""

        return self.model.default_source

    def get_source(self, name: str | None = None) -> SourceSettings:
        """Fetch a source configuration by name."""

        target = name or self.default_source
        try:
            return self._sources_by_name[target]
        except KeyError as exc:  # pragma: no cover - trivial branch
            raise KeyError(f"Source profile '{target}' is not defined.") from exc

    def model_dump(self) -> Mapping[str, Any]:
        """Expose the parsed configuration as a mapping."""

        return self.model.model_dump()


def load_config(path: Path | None = None) -> Config:
    """Load configuration from defaults/local overrides, or from an explicit config document."""

    merged: dict[str, Any] = {}
    loaded_from: list[str] = []

    if path is not None:
        override_path = _resolve_path(path)
        if override_path is None or not override_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        merged = _merge_dicts(merged, _read_yaml(override_path))
        loaded_from.append(str(override_path))
    else:
        default_candidate = _resolve_path(DEFAULT_CONFIG_PATH)
        packaged_default = _resolve_packaged_path(DEFAULT_CONFIG_PATH)
        if default_candidate and default_candidate.exists():
            merged = _merge_dicts(merged, _read_yaml(default_candidate))
            loaded_from.append(str(default_candidate))
        elif packaged_default and packaged_default.exists():
            merged = _merge_dicts(merged, _read_yaml(packaged_default))
            loaded_from.append(str(packaged_default))
        else:
            packaged_payload = _read_packaged_yaml("sitesync.config", "default.yaml")
            if packaged_payload is not None:
                merged = _merge_dicts(merged, packaged_payload)
                loaded_from.append("sitesync.config:default.yaml")

        local_candidate = _resolve_path(LOCAL_CONFIG_PATH)
        packaged_local = _resolve_packaged_path(LOCAL_CONFIG_PATH)
        if local_candidate and local_candidate.exists():
            merged = _merge_dicts(merged, _read_yaml(local_candidate))
            loaded_from.append(str(local_candidate))
        elif (
            packaged_local and packaged_local.exists()
        ):  # pragma: no cover - reserved for future use
            merged = _merge_dicts(merged, _read_yaml(packaged_local))
            loaded_from.append(str(packaged_local))

    if not merged:
        raise FileNotFoundError("No configuration data could be loaded.")

    try:
        model = ConfigModel.model_validate(merged)
    except ValidationError as exc:  # pragma: no cover - validation tested via unit tests
        raise ValueError(f"Invalid configuration: {exc}") from exc

    return Config(model=model, raw=merged, loaded_from=tuple(loaded_from))


def _resolve_path(path: Path) -> Path | None:
    """Resolve configuration paths relative to the current working directory."""

    if path is None:
        return None
    return path if path.is_absolute() else Path.cwd() / path


def _resolve_packaged_path(path: Path) -> Path | None:
    """Resolve paths embedded in packaged binaries (e.g., PyInstaller)."""

    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    return Path(base) / path


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file into a dictionary."""

    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration file {path} must define a mapping at the top level.")
    return data


def _read_packaged_yaml(package: str, name: str) -> dict[str, Any] | None:
    """Read YAML embedded in a Python package via importlib.resources."""

    try:
        content = resources.files(package).joinpath(name).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = yaml.safe_load(content) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"Packaged configuration {package}:{name} must define a mapping at the top level."
        )
    return data


def _merge_dicts(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-merge two dictionaries, with override values taking precedence."""

    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if key == "sources" and isinstance(result.get(key), list) and isinstance(value, list):
            result[key] = _merge_sources(result[key], value)
        elif key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def _merge_sources(base: list[Any], override: list[Any]) -> list[Any]:
    """Merge source lists by name while preserving unspecified fields."""

    result: list[Any] = []
    name_to_index: dict[str, int] = {}

    for entry in base:
        if isinstance(entry, dict) and "name" in entry:
            clone = deepcopy(entry)
            position = len(result)
            name_to_index[str(clone["name"])] = position
            result.append(clone)
        else:
            result.append(deepcopy(entry))

    for entry in override:
        if isinstance(entry, dict) and "name" in entry:
            name = str(entry["name"])
            replacement = deepcopy(entry)
            if name in name_to_index:
                index = name_to_index[name]
                existing = result[index]
                if isinstance(existing, dict):
                    result[index] = _merge_dicts(existing, replacement)
                else:
                    result[index] = replacement
            else:
                name_to_index[name] = len(result)
                result.append(replacement)
        else:
            result.append(deepcopy(entry))

    return result

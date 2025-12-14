"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import yaml

from sitesync.config import load_config


def _write_yaml(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_load_config_merges_default_and_local(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    default_payload = {
        "version": 1,
        "default_source": "primary",
        "logging": {"level": "info"},
        "crawler": {
            "parallel_agents": 2,
            "pages_per_agent": 2,
            "jitter_seconds": 1.0,
            "heartbeat_seconds": 10.0,
            "max_retries": 2,
        },
        "sources": [
            {
                "name": "primary",
                "start_urls": ["https://example.com"],
                "allowed_domains": {"example.com": {}},
                "depth": 2,
                "plugins": ["pages"],
            }
        ],
    }
    local_payload = {
        "logging": {"level": "warn"},
        "sources": [
            {
                "name": "primary",
                "depth": 3,
                "plugins": ["pages", "media"],
            },
            {
                "name": "secondary",
                "start_urls": ["https://example.org"],
                "allowed_domains": {"example.org": {}},
                "depth": 1,
                "plugins": [],
            },
        ],
    }

    _write_yaml(config_dir / "default.yaml", default_payload)
    _write_yaml(config_dir / "local.yaml", local_payload)

    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.default_source == "primary"
    assert config.logging.level == "warn"

    primary = config.get_source("primary")
    assert primary.depth == 3
    assert primary.plugins == ["pages", "media"]
    assert primary.start_urls == ["https://example.com"]
    assert primary.fetcher == "playwright"
    assert config.outputs.base_path == Path("data")

    secondary = config.get_source("secondary")
    assert secondary.start_urls == ["https://example.org"]


def test_load_config_with_explicit_override(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    base_payload = {
        "version": 1,
        "default_source": "default",
        "logging": {"level": "warn"},
        "sources": [
            {
                "name": "default",
                "start_urls": [],
                "allowed_domains": {},
                "depth": 1,
                "plugins": [],
            }
        ],
    }
    local_payload = {"logging": {"level": "error"}}
    override_payload = {
        "default_source": "custom",
        "sources": [
            {
                "name": "custom",
                "start_urls": ["https://custom.example"],
                "allowed_domains": {"custom.example": {}},
                "depth": 4,
                "plugins": ["pages"],
            }
        ],
    }

    _write_yaml(config_dir / "default.yaml", base_payload)
    _write_yaml(config_dir / "local.yaml", local_payload)
    override_path = tmp_path / "extra.yaml"
    _write_yaml(override_path, override_payload)

    monkeypatch.chdir(tmp_path)

    config = load_config(override_path)

    assert config.default_source == "custom"
    assert config.logging.level == "info"
    source = config.get_source()
    assert source.depth == 4


def test_load_config_falls_back_to_packaged_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.default_source == "default"
    assert config.get_source("default").depth == 1

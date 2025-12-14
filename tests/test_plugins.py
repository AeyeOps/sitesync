"""Plugin normalization tests."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from sitesync.plugins.registry import PluginRegistry
from sitesync.plugins.simple_page import SimplePagePlugin


@pytest.mark.asyncio
async def test_simple_page_plugin_normalizes(tmp_path):
    raw_path = tmp_path / "raw.html"
    raw_path.write_text(
        "<html><head><title>Example</title></head><body>Hello World</body></html>", encoding="utf-8"
    )

    plugin = SimplePagePlugin()
    normalized_dir = tmp_path / "normalized"

    records = await plugin.normalize(
        source_url="https://example.com",
        raw_path=str(raw_path),
        metadata_json=None,
        normalized_dir=normalized_dir,
    )

    assert len(records) == 1
    record = records[0]
    assert record.asset_type == "page"
    assert record.metadata["title"] == "Example"
    assert Path(record.normalized_path).exists()


class DummyPlugin:
    def __init__(self, name: str) -> None:
        self.name = name

    def supports(self, asset_type: str) -> bool:
        return asset_type == "page"


class DummyEntryPoint:
    def __init__(
        self, name: str, plugin: DummyPlugin | None = None, *, raises: bool = False
    ) -> None:
        self.name = name
        self._plugin = plugin
        self._raises = raises

    def load(self) -> DummyPlugin:
        if self._raises:
            raise RuntimeError("boom")
        assert self._plugin is not None
        return self._plugin


class DummyEntryPoints(list[DummyEntryPoint]):
    def select(self, *, group: str | None = None):  # noqa: ANN001 - mimics stdlib API
        if group == "sitesync.plugins":
            return self
        return []


def test_plugin_registry_loads_entrypoints_via_select(monkeypatch):
    registry = PluginRegistry()
    good_plugin = DummyPlugin("good")
    entry_points = DummyEntryPoints(
        [
            DummyEntryPoint("bad", raises=True),
            DummyEntryPoint("good", plugin=good_plugin),
        ]
    )
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: entry_points)
    registry.load_entrypoints()

    assert registry.find("page") == [good_plugin]

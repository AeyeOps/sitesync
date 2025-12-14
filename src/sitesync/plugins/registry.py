"""Plugin registry for Sitesync."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from importlib import import_module
from typing import Any

from sitesync.plugins.base import AssetPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Registry that discovers and selects asset plugins."""

    def __init__(self) -> None:
        self._plugins: list[AssetPlugin] = []

    def register(self, plugin: AssetPlugin) -> None:
        if any(existing.name == plugin.name for existing in self._plugins):
            return
        self._plugins.append(plugin)

    def clear(self) -> None:
        self._plugins.clear()

    def load_entrypoints(self) -> None:
        try:
            from importlib.metadata import entry_points
        except ImportError:  # pragma: no cover - python <3.10 not used
            return

        try:
            resolved_entry_points = entry_points()
        except Exception as exc:  # pragma: no cover - defensive around stdlib shims
            logger.warning("Failed to enumerate plugin entry points: %s", exc)
            return

        candidates: Iterable[Any]
        if hasattr(resolved_entry_points, "select"):
            candidates = resolved_entry_points.select(group="sitesync.plugins")
        elif isinstance(
            resolved_entry_points, dict
        ):  # pragma: no cover - legacy importlib-metadata
            candidates = resolved_entry_points.get("sitesync.plugins", [])
        else:  # pragma: no cover - unexpected shim
            candidates = []

        for entry_point in candidates:
            try:
                plugin = entry_point.load()
            except Exception as exc:  # pylint: disable=broad-except
                name = getattr(entry_point, "name", repr(entry_point))
                logger.warning("Failed to load plugin entry point %s: %s", name, exc)
                continue

            try:
                self.register(plugin)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning(
                    "Failed to register plugin %s: %s", getattr(plugin, "name", plugin), exc
                )

    def find(self, asset_type: str) -> Sequence[AssetPlugin]:
        return [plugin for plugin in self._plugins if plugin.supports(asset_type)]


registry = PluginRegistry()


def load_default_plugins() -> None:
    """Import built-in plugins so they self-register."""

    import_module("sitesync.plugins.simple_page")

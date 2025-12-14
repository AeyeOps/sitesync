"""Base definitions for Sitesync plugins."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class AssetRecord:
    """Placeholder asset record representation."""

    identifier: str
    asset_type: str
    source_url: str
    checksum: str
    tags: list[str] = field(default_factory=list)
    normalized_path: str = ""
    metadata: dict[str, Any] | None = None


class AssetPlugin(Protocol):
    """Interface for asset plugins."""

    name: str

    def supports(self, asset_type: str) -> bool:
        """Return True if plugin handles the given asset type."""

    async def normalize(
        self,
        *,
        source_url: str,
        raw_path: str,
        metadata_json: str | None,
        normalized_dir: Path,
    ) -> Iterable[AssetRecord]:
        """Produce normalized asset records for the given payload."""

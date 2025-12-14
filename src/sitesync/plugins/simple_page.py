"""Simple HTML normalization plugin."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from bs4 import BeautifulSoup

from sitesync.plugins.base import AssetPlugin, AssetRecord
from sitesync.plugins.registry import registry


@dataclass(slots=True)
class SimplePagePlugin(AssetPlugin):
    name: str = "simple-page"

    def supports(self, asset_type: str) -> bool:
        return asset_type == "page"

    async def normalize(
        self,
        *,
        source_url: str,
        raw_path: str,
        metadata_json: str | None,
        normalized_dir: Path,
    ) -> Iterable[AssetRecord]:
        loop = asyncio.get_running_loop()
        html = await loop.run_in_executor(None, Path(raw_path).read_text, "utf-8")

        def _parse() -> AssetRecord:
            soup = BeautifulSoup(html, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            text_content = soup.get_text(" ", strip=True)

            normalized_dir.mkdir(parents=True, exist_ok=True)
            normalized_path = normalized_dir / (Path(raw_path).stem + ".txt")
            normalized_path.write_text(text_content, encoding="utf-8")

            tags = ["page"]
            if title:
                tags.append(f"title:{title}")

            checksum = sha256(text_content.encode("utf-8")).hexdigest()

            return AssetRecord(
                identifier=source_url,
                asset_type="page",
                source_url=source_url,
                checksum=checksum,
                tags=tags,
                normalized_path=str(normalized_path),
                metadata={"title": title},
            )

        record = await loop.run_in_executor(None, _parse)
        return [record]


registry.register(SimplePagePlugin())

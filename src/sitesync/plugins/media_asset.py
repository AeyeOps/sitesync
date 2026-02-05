"""Media asset normalization plugin."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Set

from sitesync.plugins.base import AssetPlugin, AssetRecord
from sitesync.plugins.registry import registry

_CATEGORY_MAP: Dict[str, str] = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/svg+xml": "image",
    "image/x-icon": "image",
    "image/vnd.microsoft.icon": "image",
    "image/bmp": "image",
    "image/tiff": "image",
    "image/avif": "image",
    "video/mp4": "video",
    "video/webm": "video",
    "video/ogg": "video",
    "audio/mpeg": "audio",
    "audio/ogg": "audio",
    "audio/wav": "audio",
    "audio/webm": "audio",
    "application/pdf": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
    "application/vnd.ms-excel": "document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "document",
    "application/vnd.ms-powerpoint": "document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "document",
    "application/zip": "archive",
    "application/gzip": "archive",
    "application/x-tar": "archive",
    "application/x-7z-compressed": "archive",
    "application/x-rar-compressed": "archive",
    "text/css": "stylesheet",
    "application/javascript": "script",
    "text/javascript": "script",
    "font/woff": "font",
    "font/woff2": "font",
    "font/ttf": "font",
    "font/otf": "font",
    "application/font-woff": "font",
    "application/font-woff2": "font",
}


def _classify_content_type(content_type: Optional[str]) -> str:
    """Classify a MIME type into a high-level category."""
    if not content_type:
        return "binary"
    mime = content_type.split(";")[0].strip().lower()
    category = _CATEGORY_MAP.get(mime)
    if category:
        return category
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("font/"):
        return "font"
    return "binary"


@dataclass(slots=True)
class MediaAssetPlugin(AssetPlugin):
    name: str = "media-asset"

    def supports(self, asset_type: str) -> bool:
        return asset_type == "media"

    async def normalize(
        self,
        *,
        source_url: str,
        raw_path: str,
        metadata_json: str | None,
        normalized_dir: Path,
    ) -> Iterable[AssetRecord]:
        meta: Dict = {}
        content_type: Optional[str] = None
        checksum: Optional[str] = None
        extension: str = ""

        if metadata_json:
            try:
                meta = json.loads(metadata_json)
            except json.JSONDecodeError:
                meta = {}
            content_type = meta.get("content_type")
            checksum = meta.get("checksum", "")
            extension = meta.get("extension", "")

        category = _classify_content_type(content_type)
        ext_tag = extension.lstrip(".") if extension else ""

        tags = ["media", category]
        if ext_tag:
            tags.append(ext_tag)

        return [
            AssetRecord(
                identifier=source_url,
                asset_type="media",
                source_url=source_url,
                checksum=checksum or "",
                tags=tags,
                normalized_path=raw_path,  # media files are already in final form
                metadata={"category": category, "content_type": content_type},
            )
        ]


registry.register(MediaAssetPlugin())

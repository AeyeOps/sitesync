"""HTTP fetcher for binary/media asset downloads."""

from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from sitesync.core.executor import FetchError, FetchResult, Fetcher, TransientFetchError
from sitesync.storage import TaskRecord

# Common MIME type to extension mapping for types not well-covered by mimetypes module
_MIME_EXTENSIONS: Dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/avif": ".avif",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/ogg": ".ogv",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".weba",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/gzip": ".gz",
    "application/x-tar": ".tar",
    "application/x-7z-compressed": ".7z",
    "application/x-rar-compressed": ".rar",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
    "text/css": ".css",
    "application/javascript": ".js",
    "text/javascript": ".js",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "application/font-woff": ".woff",
    "application/font-woff2": ".woff2",
}


def _extension_from_content_type(content_type: Optional[str], url: str) -> str:
    """Determine file extension from Content-Type header, falling back to URL path."""
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        ext = _MIME_EXTENSIONS.get(mime)
        if ext:
            return ext
        guessed = mimetypes.guess_extension(mime)
        if guessed:
            return guessed

    # Fallback to URL path extension
    url_path = url.split("?")[0].split("#")[0]
    if "." in url_path.split("/")[-1]:
        ext = "." + url_path.split("/")[-1].rsplit(".", 1)[-1].lower()
        if len(ext) <= 6:  # reasonable extension length
            return ext

    return ".bin"


@dataclass(slots=True)
class HttpFetcher(Fetcher):
    """Fetcher for binary/media assets via HTTP streaming."""

    logger: logging.Logger
    media_dir: Path
    timeout: float = 30.0
    max_size_bytes: int = 100_000_000  # 100 MB

    async def fetch(self, task: TaskRecord) -> FetchResult:
        """Stream-download a URL to a content-addressed file in media_dir."""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=self.timeout
            ) as client:
                async with client.stream("GET", task.url) as response:
                    if 400 <= response.status_code < 500:
                        raise FetchError(
                            f"HTTP {response.status_code} for {task.url}"
                        )
                    if response.status_code >= 500:
                        raise TransientFetchError(
                            f"HTTP {response.status_code} for {task.url}"
                        )

                    content_type = response.headers.get("content-type")
                    ext = _extension_from_content_type(content_type, task.url)

                    hasher = hashlib.sha256()
                    chunks: list[bytes] = []
                    total_bytes = 0

                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        total_bytes += len(chunk)
                        if total_bytes > self.max_size_bytes:
                            raise FetchError(
                                f"Response exceeds {self.max_size_bytes} bytes for {task.url}"
                            )
                        hasher.update(chunk)
                        chunks.append(chunk)

                    checksum = hasher.hexdigest()
                    filename = f"{checksum}{ext}"
                    dest = self.media_dir / filename

                    self.media_dir.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"".join(chunks))

                    metadata = {
                        "url": str(response.url),
                        "status": response.status_code,
                        "content_type": content_type,
                        "content_length": total_bytes,
                        "checksum": checksum,
                        "extension": ext,
                    }
                    import json

                    return FetchResult(
                        assets_created=1,
                        raw_payload_path=str(dest),
                        checksum=checksum,
                        asset_type="media",
                        metadata_json=json.dumps(metadata),
                    )

        except (FetchError, TransientFetchError):
            raise
        except httpx.TimeoutException as exc:
            raise TransientFetchError(f"Timeout fetching {task.url}: {exc}") from exc
        except httpx.ConnectError as exc:
            raise TransientFetchError(f"Connection error for {task.url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise TransientFetchError(f"HTTP error for {task.url}: {exc}") from exc
        except OSError as exc:
            raise FetchError(f"IO error saving {task.url}: {exc}") from exc

    @classmethod
    def from_options(
        cls, logger: logging.Logger, *, options: Optional[Dict[str, Any]] = None
    ) -> "HttpFetcher":
        """Create an HttpFetcher from option dict."""
        opts = options or {}
        media_dir = opts.get("media_dir")
        if media_dir is None:
            raise ValueError("HttpFetcher requires 'media_dir' option")
        if not isinstance(media_dir, Path):
            media_dir = Path(media_dir)
        return cls(
            logger=logger,
            media_dir=media_dir,
            timeout=float(opts.get("timeout", 30.0)),
            max_size_bytes=int(opts.get("max_size_bytes", 100_000_000)),
        )

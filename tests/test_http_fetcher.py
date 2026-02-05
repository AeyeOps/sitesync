"""Tests for the HTTP fetcher."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sitesync.core.executor import FetchError, TransientFetchError
from sitesync.fetchers.http import HttpFetcher, _extension_from_content_type
from sitesync.storage.db import TaskRecord


def _make_task(url: str = "https://cdn.example.com/image.png") -> TaskRecord:
    return TaskRecord(
        id=1,
        url=url,
        depth=0,
        status="in_progress",
        attempt_count=0,
        lease_owner="test",
        lease_expires_at=None,
        next_run_at="2025-01-01T00:00:00.000000Z",
        task_type="media",
    )


class TestExtensionFromContentType:
    def test_known_mime(self):
        assert _extension_from_content_type("image/png", "https://example.com/x") == ".png"

    def test_mime_with_charset(self):
        assert _extension_from_content_type("image/jpeg; charset=utf-8", "https://example.com/x") == ".jpg"

    def test_fallback_to_url(self):
        assert _extension_from_content_type(None, "https://example.com/file.pdf") == ".pdf"

    def test_fallback_to_url_with_query(self):
        assert _extension_from_content_type(None, "https://example.com/file.gif?v=1") == ".gif"

    def test_unknown_mime_unknown_url(self):
        assert _extension_from_content_type("application/octet-stream", "https://example.com/download") == ".bin"

    def test_no_content_type_no_ext(self):
        assert _extension_from_content_type(None, "https://example.com/download") == ".bin"

    def test_css_mime(self):
        assert _extension_from_content_type("text/css", "https://example.com/style") == ".css"

    def test_woff2_mime(self):
        assert _extension_from_content_type("font/woff2", "https://example.com/font") == ".woff2"


class TestHttpFetcherFromOptions:
    def test_requires_media_dir(self):
        import logging
        logger = logging.getLogger("test")
        with pytest.raises(ValueError, match="media_dir"):
            HttpFetcher.from_options(logger, options={})

    def test_creates_with_defaults(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        fetcher = HttpFetcher.from_options(logger, options={"media_dir": tmp_path / "media"})
        assert fetcher.media_dir == tmp_path / "media"
        assert fetcher.timeout == 30.0
        assert fetcher.max_size_bytes == 100_000_000

    def test_custom_options(self, tmp_path):
        import logging
        logger = logging.getLogger("test")
        fetcher = HttpFetcher.from_options(
            logger,
            options={"media_dir": str(tmp_path), "timeout": 10.0, "max_size_bytes": 1024},
        )
        assert fetcher.timeout == 10.0
        assert fetcher.max_size_bytes == 1024


@pytest.mark.asyncio
async def test_fetch_success(tmp_path):
    """Test successful binary download."""
    import logging
    import httpx

    logger = logging.getLogger("test")
    media_dir = tmp_path / "media"
    fetcher = HttpFetcher(logger=logger, media_dir=media_dir)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

    async def mock_stream(*args, **kwargs):
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "image/png"}
        response.url = "https://cdn.example.com/image.png"

        async def aiter_bytes(chunk_size=65536):
            yield png_bytes

        response.aiter_bytes = aiter_bytes
        return response

    mock_client = AsyncMock()
    mock_client.stream = MagicMock()

    # Use a context manager mock for the stream
    import contextlib

    class MockStreamCM:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "image/png"}
            resp.url = "https://cdn.example.com/image.png"

            async def aiter_bytes(chunk_size=65536):
                yield png_bytes

            resp.aiter_bytes = aiter_bytes
            return resp

        async def __aexit__(self, *args):
            pass

    class MockClientCM:
        async def __aenter__(self):
            client = MagicMock()
            client.stream = MagicMock(return_value=MockStreamCM())
            return client

        async def __aexit__(self, *args):
            pass

    with patch("httpx.AsyncClient", return_value=MockClientCM()):
        task = _make_task()
        result = await fetcher.fetch(task)

    assert result.asset_type == "media"
    assert result.checksum is not None
    assert result.raw_payload_path is not None
    assert Path(result.raw_payload_path).exists()
    assert Path(result.raw_payload_path).read_bytes() == png_bytes

    meta = json.loads(result.metadata_json)
    assert meta["content_type"] == "image/png"
    assert meta["extension"] == ".png"


@pytest.mark.asyncio
async def test_fetch_404_raises_fetch_error(tmp_path):
    """Test that 4xx responses raise permanent FetchError."""
    import logging

    logger = logging.getLogger("test")
    fetcher = HttpFetcher(logger=logger, media_dir=tmp_path / "media")

    class MockStreamCM:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status_code = 404
            resp.headers = {}
            return resp

        async def __aexit__(self, *args):
            pass

    class MockClientCM:
        async def __aenter__(self):
            client = MagicMock()
            client.stream = MagicMock(return_value=MockStreamCM())
            return client

        async def __aexit__(self, *args):
            pass

    with patch("httpx.AsyncClient", return_value=MockClientCM()):
        with pytest.raises(FetchError, match="404"):
            await fetcher.fetch(_make_task())


@pytest.mark.asyncio
async def test_fetch_500_raises_transient_error(tmp_path):
    """Test that 5xx responses raise retryable TransientFetchError."""
    import logging

    logger = logging.getLogger("test")
    fetcher = HttpFetcher(logger=logger, media_dir=tmp_path / "media")

    class MockStreamCM:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status_code = 502
            resp.headers = {}
            return resp

        async def __aexit__(self, *args):
            pass

    class MockClientCM:
        async def __aenter__(self):
            client = MagicMock()
            client.stream = MagicMock(return_value=MockStreamCM())
            return client

        async def __aexit__(self, *args):
            pass

    with patch("httpx.AsyncClient", return_value=MockClientCM()):
        with pytest.raises(TransientFetchError, match="502"):
            await fetcher.fetch(_make_task())


@pytest.mark.asyncio
async def test_fetch_exceeds_size_limit(tmp_path):
    """Test that downloads exceeding max_size_bytes raise FetchError."""
    import logging

    logger = logging.getLogger("test")
    fetcher = HttpFetcher(logger=logger, media_dir=tmp_path / "media", max_size_bytes=10)

    class MockStreamCM:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "image/png"}
            resp.url = "https://example.com/big.png"

            async def aiter_bytes(chunk_size=65536):
                yield b"\x00" * 100

            resp.aiter_bytes = aiter_bytes
            return resp

        async def __aexit__(self, *args):
            pass

    class MockClientCM:
        async def __aenter__(self):
            client = MagicMock()
            client.stream = MagicMock(return_value=MockStreamCM())
            return client

        async def __aexit__(self, *args):
            pass

    with patch("httpx.AsyncClient", return_value=MockClientCM()):
        with pytest.raises(FetchError, match="exceeds"):
            await fetcher.fetch(_make_task())


@pytest.mark.asyncio
async def test_content_addressed_filename(tmp_path):
    """Test that files are named by SHA256 checksum."""
    import hashlib
    import logging

    logger = logging.getLogger("test")
    media_dir = tmp_path / "media"
    fetcher = HttpFetcher(logger=logger, media_dir=media_dir)

    content = b"test content for hashing"
    expected_hash = hashlib.sha256(content).hexdigest()

    class MockStreamCM:
        async def __aenter__(self):
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "application/pdf"}
            resp.url = "https://example.com/doc.pdf"

            async def aiter_bytes(chunk_size=65536):
                yield content

            resp.aiter_bytes = aiter_bytes
            return resp

        async def __aexit__(self, *args):
            pass

    class MockClientCM:
        async def __aenter__(self):
            client = MagicMock()
            client.stream = MagicMock(return_value=MockStreamCM())
            return client

        async def __aexit__(self, *args):
            pass

    with patch("httpx.AsyncClient", return_value=MockClientCM()):
        result = await fetcher.fetch(_make_task("https://example.com/doc.pdf"))

    assert result.checksum == expected_hash
    assert Path(result.raw_payload_path).name == f"{expected_hash}.pdf"

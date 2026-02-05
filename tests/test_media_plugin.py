"""Tests for the media asset plugin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sitesync.plugins.media_asset import MediaAssetPlugin, _classify_content_type


class TestClassifyContentType:
    def test_image_png(self):
        assert _classify_content_type("image/png") == "image"

    def test_image_jpeg_with_charset(self):
        assert _classify_content_type("image/jpeg; charset=utf-8") == "image"

    def test_video_mp4(self):
        assert _classify_content_type("video/mp4") == "video"

    def test_audio_mpeg(self):
        assert _classify_content_type("audio/mpeg") == "audio"

    def test_pdf(self):
        assert _classify_content_type("application/pdf") == "document"

    def test_zip(self):
        assert _classify_content_type("application/zip") == "archive"

    def test_css(self):
        assert _classify_content_type("text/css") == "stylesheet"

    def test_javascript(self):
        assert _classify_content_type("application/javascript") == "script"

    def test_font_woff2(self):
        assert _classify_content_type("font/woff2") == "font"

    def test_unknown_image(self):
        assert _classify_content_type("image/x-custom") == "image"

    def test_unknown_video(self):
        assert _classify_content_type("video/x-custom") == "video"

    def test_none(self):
        assert _classify_content_type(None) == "binary"

    def test_octet_stream(self):
        assert _classify_content_type("application/octet-stream") == "binary"


class TestMediaAssetPlugin:
    def test_supports_media(self):
        plugin = MediaAssetPlugin()
        assert plugin.supports("media") is True

    def test_does_not_support_page(self):
        plugin = MediaAssetPlugin()
        assert plugin.supports("page") is False

    @pytest.mark.asyncio
    async def test_normalize_produces_record(self, tmp_path):
        plugin = MediaAssetPlugin()
        metadata = json.dumps({
            "content_type": "image/png",
            "checksum": "abc123",
            "extension": ".png",
        })
        records = await plugin.normalize(
            source_url="https://example.com/image.png",
            raw_path=str(tmp_path / "abc123.png"),
            metadata_json=metadata,
            normalized_dir=tmp_path / "normalized",
        )
        records = list(records)
        assert len(records) == 1
        record = records[0]
        assert record.asset_type == "media"
        assert record.checksum == "abc123"
        assert "media" in record.tags
        assert "image" in record.tags
        assert "png" in record.tags

    @pytest.mark.asyncio
    async def test_normalize_handles_missing_metadata(self, tmp_path):
        plugin = MediaAssetPlugin()
        records = await plugin.normalize(
            source_url="https://example.com/file",
            raw_path=str(tmp_path / "file.bin"),
            metadata_json=None,
            normalized_dir=tmp_path / "normalized",
        )
        records = list(records)
        assert len(records) == 1
        assert "binary" in records[0].tags

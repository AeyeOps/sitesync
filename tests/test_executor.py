"""Tests for the async crawl executor."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from sitesync.config.loader import (
    Config as ConfigWrapper,
    ConfigModel,
    CrawlerSettings,
    DomainFilter,
    LoggingSettings,
    OutputSettings,
    SourceSettings,
    StorageSettings,
)
from sitesync.core.executor import CrawlExecutor, FetchResult, TransientFetchError
from sitesync.storage import Database


def _build_config(tmp_path):
    model = ConfigModel(
        version=1,
        default_source="default",
        logging=LoggingSettings(level="info"),
        crawler=CrawlerSettings(
            parallel_agents=2,
            pages_per_agent=2,
            jitter_seconds=0.1,
            heartbeat_seconds=5.0,
            max_retries=3,
            backoff_min_seconds=0.05,
            backoff_max_seconds=0.2,
            backoff_multiplier=2.0,
        ),
        storage=StorageSettings(path=tmp_path / "sitesync.sqlite"),
        outputs=OutputSettings(
            base_path=tmp_path / "outputs",
            raw_subdir="raw",
            normalized_subdir="normalized",
            metadata_subdir="meta",
        ),
        sources=[
            SourceSettings(
                name="default",
                start_urls=["https://example.com/a", "https://example.com/b"],
                allowed_domains={"example.com": {}},
                depth=1,
                plugins=[],
            )
        ],
    )
    return model


class DummyFetcher:
    def __init__(self, fail_first: bool = False) -> None:
        self.calls: int = 0
        self.fail_first = fail_first

    async def fetch(self, task):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise TransientFetchError("temporary")
        await asyncio.sleep(0)
        return FetchResult(assets_created=1, checksum="checksum", raw_payload_path="/tmp/raw.html")


class AlwaysFailFetcher:
    def __init__(self) -> None:
        self.calls: int = 0

    async def fetch(self, task):  # noqa: D401
        self.calls += 1
        raise TransientFetchError("permanent failure")


def test_path_filters_enforced(tmp_path):
    model = _build_config(tmp_path)
    model.sources[0].allowed_domains = {
        "example.com": DomainFilter(allow_paths=["/docs/**"], deny_paths=["/docs/private/**"])
    }
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    fetcher = DummyFetcher()
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=fetcher,
        logger=logger,
    )

    assert executor._path_allowed("example.com", "/docs/intro") is True
    # /docs/private itself matches allow /docs/** but NOT deny /docs/private/**
    # (the /** glob requires a trailing path segment after the prefix)
    assert executor._path_allowed("example.com", "/docs/private") is True
    assert executor._path_allowed("example.com", "/docs/private/secret") is False
    assert executor._path_allowed("example.com", "/other") is False
    # Exact match: /docs matches only /docs, not children
    executor.source.allowed_domains["example.com"] = DomainFilter(allow_paths=["/docs"], deny_paths=[])
    assert executor._path_allowed("example.com", "/docs") is True
    assert executor._path_allowed("example.com", "/docs/intro") is False


def test_auth_redirect_adds_runtime_denies(tmp_path):
    model = _build_config(tmp_path)
    model.sources[0].allowed_domains = {"hire.lever.co": DomainFilter()}
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    fetcher = DummyFetcher()
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=fetcher,
        logger=logger,
    )

    result = FetchResult(
        assets_created=1,
        checksum="checksum",
        raw_payload_path="/tmp/raw.html",
        metadata_json=json.dumps(
            {
                "url": "https://hire.lever.co/auth/login?continue=%2Fsettings%2Froles",
                "status": 200,
                "title": "Sign in",
            }
        ),
    )

    assert executor._handle_auth_redirect("https://hire.lever.co/settings/roles", result) is True
    # Runtime deny adds /settings/roles/** so children are denied, not the path itself
    assert executor._path_allowed("hire.lever.co", "/settings/roles/admin") is False
    assert executor._path_allowed("hire.lever.co", "/auth/login") is False


@pytest.mark.asyncio
async def test_executor_processes_tasks(tmp_path):
    model = _build_config(tmp_path)
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    run = database.start_run("default")
    database.enqueue_seed_tasks(
        run.id, [("https://example.com/a", 1), ("https://example.com/b", 1)]
    )

    fetcher = DummyFetcher()
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=fetcher,
        logger=logger,
    )

    await executor.run(run_id=run.id, parallel_agents=2, log_path="test.log")

    assert database.count_pending_tasks(run.id) == 0
    assert fetcher.calls >= 2


@pytest.mark.asyncio
async def test_executor_retries_transient_failures(tmp_path):
    model = _build_config(tmp_path)
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    run = database.start_run("default")
    database.enqueue_seed_tasks(run.id, [("https://example.com/a", 1)])

    fetcher = DummyFetcher(fail_first=True)
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=fetcher,
        logger=logger,
    )

    await executor.run(run_id=run.id, parallel_agents=1, log_path="test.log")

    assert fetcher.calls >= 2  # retried
    assert database.count_pending_tasks(run.id) == 0


@pytest.mark.asyncio
async def test_executor_marks_error_after_retry_exhaustion(tmp_path):
    model = _build_config(tmp_path)
    model.crawler.max_retries = 2
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    run = database.start_run("default")
    database.enqueue_seed_tasks(run.id, [("https://example.com/a", 1)])

    fetcher = AlwaysFailFetcher()
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=fetcher,
        logger=logger,
    )

    await asyncio.wait_for(
        executor.run(run_id=run.id, parallel_agents=1, log_path="test.log"),
        timeout=2.0,
    )

    counts = database.get_task_status_counts(run.id)
    assert counts.get("error", 0) == 1
    assert counts.get("pending", 0) == 0
    assert fetcher.calls == model.crawler.max_retries


def test_classify_url_type():
    from sitesync.core.executor import CrawlExecutor

    assert CrawlExecutor._classify_url_type("/image.png") == "media"
    assert CrawlExecutor._classify_url_type("/image.jpg") == "media"
    assert CrawlExecutor._classify_url_type("/doc.pdf") == "media"
    assert CrawlExecutor._classify_url_type("/style.css") == "media"
    assert CrawlExecutor._classify_url_type("/font.woff2") == "media"
    assert CrawlExecutor._classify_url_type("/page.html") == "page"
    assert CrawlExecutor._classify_url_type("/about") == "page"
    assert CrawlExecutor._classify_url_type("/") == "page"


def test_strip_tracking_params():
    from sitesync.core.executor import CrawlExecutor

    assert CrawlExecutor._strip_tracking_params(
        "https://example.com/image.png?utm_source=google&utm_medium=cpc&v=1"
    ) == "https://example.com/image.png?v=1"

    assert CrawlExecutor._strip_tracking_params(
        "https://example.com/image.png"
    ) == "https://example.com/image.png"

    assert CrawlExecutor._strip_tracking_params(
        "https://example.com/page?hsutk=abc&__hstc=def"
    ) == "https://example.com/page"


@pytest.mark.asyncio
async def test_discover_links_extracts_media(tmp_path):
    """Test that _discover_links finds img/video/audio/link tags and queues them as media."""
    model = _build_config(tmp_path)
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    run = database.start_run("default")

    html = """
    <html>
    <head>
        <link rel="stylesheet" href="/style.css">
        <link rel="icon" href="/favicon.ico">
    </head>
    <body>
        <a href="/page2">Link</a>
        <a href="/doc.pdf">PDF</a>
        <img src="/hero.png">
        <img srcset="/small.jpg 300w, /large.jpg 900w">
        <video src="/clip.mp4" poster="/poster.jpg"></video>
        <audio src="/song.mp3"></audio>
        <source src="/alt-clip.webm">
        <meta property="og:image" content="https://example.com/og.png">
        <meta name="twitter:image" content="https://example.com/tw.jpg">
        <object data="/widget.swf"></object>
        <embed src="/embed.pdf">
    </body>
    </html>
    """
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    raw_file = raw_dir / "page.html"
    raw_file.write_text(html, encoding="utf-8")

    fetcher = DummyFetcher()
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=fetcher,
        logger=logger,
    )

    task = database.acquire_tasks(
        run.id, limit=1, lease_owner="test", lease_seconds=60,
        max_retries=0, backoff_seconds=0,
    )
    # Create a fake task record to test discovery
    from sitesync.storage.db import TaskRecord as TR
    fake_task = TR(
        id=999, url="https://example.com/", depth=3, status="in_progress",
        attempt_count=0, lease_owner="test", lease_expires_at=None,
        next_run_at="2025-01-01T00:00:00.000000Z", task_type="page",
    )
    result = FetchResult(
        assets_created=1, raw_payload_path=str(raw_file), checksum="abc",
    )

    await executor._discover_links(run.id, fake_task, result)

    # Check what was queued
    tasks = database.list_tasks_for_run(run.id, limit=100)
    page_tasks = [t for t in tasks if t.task_type == "page"]
    media_tasks = [t for t in tasks if t.task_type == "media"]

    page_urls = {t.url for t in page_tasks}
    media_urls = {t.url for t in media_tasks}

    # /page2 should be a page task
    assert "https://example.com/page2" in page_urls

    # Binary URLs should be media tasks
    assert "https://example.com/hero.png" in media_urls
    assert "https://example.com/clip.mp4" in media_urls
    assert "https://example.com/song.mp3" in media_urls
    assert "https://example.com/style.css" in media_urls
    assert "https://example.com/favicon.ico" in media_urls
    assert "https://example.com/poster.jpg" in media_urls
    assert "https://example.com/og.png" in media_urls
    assert "https://example.com/tw.jpg" in media_urls
    assert "https://example.com/small.jpg" in media_urls
    assert "https://example.com/large.jpg" in media_urls
    assert "https://example.com/alt-clip.webm" in media_urls
    assert "https://example.com/embed.pdf" in media_urls

    # Media tasks should have depth=0
    for t in media_tasks:
        assert t.depth == 0, f"Media task {t.url} should have depth=0, got {t.depth}"

    # /doc.pdf from <a> tag should be classified as media
    assert "https://example.com/doc.pdf" in media_urls


@pytest.mark.asyncio
async def test_media_task_uses_media_fetcher(tmp_path):
    """Test that media tasks dispatch to media_fetcher when set."""
    model = _build_config(tmp_path)
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    run = database.start_run("default")
    database.enqueue_seed_tasks(
        run.id, [("https://example.com/image.png", 0)], task_type="media"
    )

    page_fetcher = DummyFetcher()
    media_fetcher = DummyFetcher()
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=page_fetcher,
        logger=logger,
        media_fetcher=media_fetcher,
    )

    await executor.run(run_id=run.id, parallel_agents=1, log_path="test.log")

    assert media_fetcher.calls >= 1
    assert page_fetcher.calls == 0


@pytest.mark.asyncio
async def test_media_task_bypasses_domain_filter(tmp_path):
    """Test that media tasks skip domain/path filtering in the producer."""
    model = _build_config(tmp_path)
    model.sources[0].allowed_domains = {
        "example.com": DomainFilter(allow_paths=["/docs/**"])
    }
    config = ConfigWrapper(model=model, raw=model.model_dump())
    database = Database(config.storage.path)
    database.initialize()

    run = database.start_run("default")
    # Enqueue a media task on a CDN domain not in allowed_domains
    database.enqueue_seed_tasks(
        run.id, [("https://cdn.example.net/image.png", 0)], task_type="media"
    )

    fetcher = DummyFetcher()
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    executor = CrawlExecutor(
        config=config,
        source=config.get_source("default"),
        database=database,
        fetcher=fetcher,
        logger=logger,
    )

    await executor.run(run_id=run.id, parallel_agents=1, log_path="test.log")

    # Media task should have been processed, not filtered
    counts = database.get_task_status_counts(run.id)
    assert counts.get("finished", 0) == 1
    assert counts.get("error", 0) == 0

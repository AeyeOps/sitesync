"""Tests for the async crawl executor."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from sitesync.config.loader import (
    Config as ConfigWrapper,
)
from sitesync.config.loader import (
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

    # Pattern /docs/** matches paths starting with /docs/, not /docs itself
    assert executor._path_allowed("example.com", "/docs/intro") is True
    # Deny pattern /docs/private/** matches paths starting with /docs/private/
    assert executor._path_allowed("example.com", "/docs/private/secret") is False
    # Path not under any allow pattern
    assert executor._path_allowed("example.com", "/other") is False
    # Exact path match requires exact pattern (not glob)
    executor.source.allowed_domains["example.com"].allow_paths = ["/docs"]
    executor.source.allowed_domains["example.com"].deny_paths = []
    assert executor._path_allowed("example.com", "/docs") is True
    assert executor._path_allowed("example.com", "/docs/intro") is False


def test_auth_redirect_adds_runtime_denies(tmp_path):
    model = _build_config(tmp_path)
    model.sources[0].allowed_domains = {"app.example.com": DomainFilter()}
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
                "url": "https://app.example.com/auth/login?continue=%2Fsettings%2Froles",
                "status": 200,
                "title": "Sign in",
            }
        ),
    )

    assert executor._handle_auth_redirect("https://app.example.com/settings/roles", result) is True
    # Runtime denies use /** patterns, so they match child paths
    assert executor._path_allowed("app.example.com", "/settings/roles/edit") is False
    assert executor._path_allowed("app.example.com", "/auth/callback") is False


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

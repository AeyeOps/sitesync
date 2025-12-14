"""Tests for the orchestrator."""

from __future__ import annotations

import logging

from sitesync.config.loader import (
    ConfigModel,
    CrawlerSettings,
    LoggingSettings,
    SourceSettings,
    StorageSettings,
)
from sitesync.core import Orchestrator
from sitesync.storage import Database


def _build_config(tmp_path) -> tuple[ConfigModel, Database]:
    model = ConfigModel(
        version=1,
        default_source="default",
        logging=LoggingSettings(level="info"),
        crawler=CrawlerSettings(
            parallel_agents=2,
            pages_per_agent=2,
            jitter_seconds=0.5,
            heartbeat_seconds=10.0,
            max_retries=2,
        ),
        storage=StorageSettings(path=tmp_path / "sitesync.sqlite"),
        sources=[
            SourceSettings(
                name="default",
                start_urls=["https://example.com"],
                allowed_domains={"example.com": {}},
                depth=2,
                plugins=[],
            )
        ],
    )

    database = Database(model.storage.path)
    database.initialize()

    return model, database


def test_orchestrator_run_initializes_queue(tmp_path):
    model, database = _build_config(tmp_path)

    config = model
    # Wrap using Config dataclass from loader to mirror runtime behavior
    from sitesync.config.loader import Config as ConfigWrapper

    config_wrapper = ConfigWrapper(model=config, raw=config.model_dump())

    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    orchestrator = Orchestrator(
        config=config_wrapper,
        source=config_wrapper.get_source("default"),
        database=database,
        logger=logger,
    )

    summary = orchestrator.run()

    assert summary.run.status == "running"
    assert summary.queued_seeds == 1
    assert summary.depth == 2
    assert summary.parallel_agents == 2
    assert database.count_pending_tasks(summary.run.id) == 1


def test_orchestrator_resume_falls_back_to_new_run(tmp_path):
    model, database = _build_config(tmp_path)

    from sitesync.config.loader import Config as ConfigWrapper

    config_wrapper = ConfigWrapper(model=model, raw=model.model_dump())
    logger = logging.getLogger("sitesync-test")
    logger.addHandler(logging.NullHandler())

    orchestrator = Orchestrator(
        config=config_wrapper,
        source=config_wrapper.get_source("default"),
        database=database,
        logger=logger,
    )

    summary = orchestrator.run(resume=True)

    assert summary.run.status == "running"
    assert summary.queued_seeds == 1

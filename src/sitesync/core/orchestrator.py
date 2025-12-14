"""Orchestrator for Sitesync runs."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace

from sitesync.config import Config, SourceSettings
from sitesync.storage import Database, RunRecord


@dataclass(slots=True)
class RunSummary:
    """Result of initializing a crawl run."""

    run: RunRecord
    queued_seeds: int
    resumed: bool
    depth: int
    parallel_agents: int
    seed_urls: list[str]


@dataclass(slots=True)
class Orchestrator:
    """Coordinate crawl runs and persistence."""

    config: Config
    source: SourceSettings
    database: Database
    logger: logging.Logger

    def run(
        self,
        *,
        resume: bool = False,
        start_urls: Iterable[str] | None = None,
        depth_override: int | None = None,
        parallel_override: int | None = None,
        label: str | None = None,
    ) -> RunSummary:
        """Prepare a crawl run by seeding the task queue."""

        run_record = self._resume_or_start(resume=resume, label=label)

        effective_depth = depth_override if depth_override is not None else self.source.depth
        effective_parallel = (
            parallel_override
            if parallel_override is not None
            else (self.source.parallel_agents or self.config.crawler.parallel_agents)
        )

        seeds = list(start_urls or self.source.start_urls)
        seed_depth_pairs = [(url, effective_depth) for url in seeds]
        queued = self.database.enqueue_seed_tasks(run_record.id, seed_depth_pairs)

        self.database.mark_run_status(run_record.id, "running")
        run_record = replace(run_record, status="running")

        if queued == 0 and not seeds:
            self.logger.warning(
                "Run %s for source '%s' has no seed URLs to queue.",
                run_record.id,
                self.source.name,
            )
        else:
            self.logger.info(
                "Run %s for source '%s' queued %s seed task(s).",
                run_record.id,
                self.source.name,
                queued,
            )

        self.logger.info(
            "Run %s ready with depth=%s parallel_agents=%s.",
            run_record.id,
            effective_depth,
            effective_parallel,
        )

        return RunSummary(
            run=run_record,
            queued_seeds=queued,
            resumed=resume,
            depth=effective_depth,
            parallel_agents=effective_parallel,
            seed_urls=seeds,
        )

    def _resume_or_start(self, *, resume: bool, label: str | None) -> RunRecord:
        if resume:
            run = self.database.resume_run(self.source.name)
            if run is None:
                self.logger.warning(
                    "Resume requested but no resumable run found for source '%s'. "
                    "Starting a new run instead.",
                    self.source.name,
                )
                run = self.database.start_run(self.source.name, label=label)
                self.logger.info("Started new run %s for source '%s'.", run.id, self.source.name)
                return run
            self.logger.info("Resuming run %s for source '%s'.", run.id, self.source.name)
            return run

        run = self.database.start_run(self.source.name, label=label)
        self.logger.info("Started new run %s for source '%s'.", run.id, self.source.name)
        return run

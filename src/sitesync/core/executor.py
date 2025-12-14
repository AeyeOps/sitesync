"""Async crawl executor using task leasing and tenacity-based retries."""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import time
from asyncio import QueueEmpty, QueueFull
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from sitesync.config import Config, SourceSettings
from sitesync.storage import Database, TaskRecord
from sitesync.ui import AgentSnapshot, Dashboard, QueueSnapshot, RunSnapshot


class FetchError(Exception):
    """Raised when a fetch ultimately fails."""


class TransientFetchError(Exception):
    """Raised for retryable fetch failures."""


@dataclass(slots=True)
class FetchResult:
    """Result returned by a fetcher implementation."""

    assets_created: int
    raw_payload_path: str | None = None
    normalized_payload_path: str | None = None
    checksum: str | None = None
    asset_type: str = "page"
    metadata_json: str | None = None


class Fetcher(Protocol):
    """Protocol for fetch implementations."""

    async def fetch(self, task: TaskRecord) -> FetchResult:
        """Perform the fetch and return a result."""


FetchHook = Callable[[TaskRecord, FetchResult], Awaitable[None]]


@dataclass(slots=True)
class CrawlExecutor:
    """Coordinates concurrent crawl workers."""

    config: Config
    source: SourceSettings
    database: Database
    fetcher: Fetcher
    logger: logging.Logger
    on_success: FetchHook | None = None
    on_failure: Callable[[TaskRecord, Exception], Awaitable[None]] | None = None
    dashboard: Dashboard | None = None
    _agent_metrics: dict[str, AgentMetrics] = field(default_factory=dict, init=False)
    _start_time: float = field(default=0.0, init=False)
    _run_id: int = field(default=-1, init=False)
    _parallel_agents: int = field(default=0, init=False)
    _log_path: str = field(default="sitesync.log", init=False)
    _runtime_denies: dict[str, set[str]] = field(default_factory=dict, init=False)

    async def run(
        self,
        run_id: int,
        *,
        parallel_agents: int,
        log_path: str,
        stop_signal: asyncio.Event | None = None,
    ) -> None:
        """Run crawl workers until queue is drained."""

        queue: asyncio.Queue[TaskRecord | None] = asyncio.Queue()
        stop_event = stop_signal or asyncio.Event()

        self._start_time = time.monotonic()
        self._run_id = run_id
        self._parallel_agents = parallel_agents
        self._log_path = log_path
        counts = self.database.get_task_status_counts(run_id)
        self.logger.info(
            "Bootstrapping run %s: pending=%s in_progress=%s finished=%s errors=%s",
            run_id,
            counts.get("pending", 0),
            counts.get("in_progress", 0),
            counts.get("finished", 0),
            counts.get("error", 0),
        )
        if self.source.allowed_domains:
            for domain, rules in self.source.allowed_domains.items():
                allow = rules.allow_paths or []
                deny = rules.deny_paths or []
                self.logger.debug("Domain filter %s allow=%s deny=%s", domain, allow, deny)
        else:
            self.logger.debug("No domain filters configured.")
        if self.dashboard:
            self._update_run_snapshot()

        producer = asyncio.create_task(
            self._producer_loop(
                run_id=run_id,
                queue=queue,
                stop_event=stop_event,
                worker_count=parallel_agents,
            )
        )
        workers = [
            asyncio.create_task(
                self._worker_loop(
                    name=f"agent-{index + 1:02d}",
                    run_id=run_id,
                    queue=queue,
                    stop_event=stop_event,
                )
            )
            for index in range(parallel_agents)
        ]

        work = asyncio.gather(producer, *workers, return_exceptions=True)
        stop_waiter = asyncio.create_task(stop_event.wait())

        try:
            done, _ = await asyncio.wait({work, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)
            if stop_waiter in done and not work.done():
                self._handle_stop_signal(
                    run_id=run_id,
                    queue=queue,
                    workers=workers,
                    producer=producer,
                    worker_count=parallel_agents,
                )
            await work
        finally:
            stop_waiter.cancel()
            await asyncio.gather(stop_waiter, return_exceptions=True)

    def get_runtime_denies(self) -> dict[str, list[str]]:
        """Return runtime deny rules accumulated during the run."""
        return {domain: sorted(patterns) for domain, patterns in self._runtime_denies.items()}

    async def _producer_loop(
        self,
        *,
        run_id: int,
        queue: asyncio.Queue[TaskRecord | None],
        stop_event: asyncio.Event,
        worker_count: int,
    ) -> None:
        lease_seconds = self.config.crawler.heartbeat_seconds
        lease_owner_prefix = "sitesync"
        sentinels_emitted = False
        per_agent = self.source.pages_per_agent or self.config.crawler.pages_per_agent
        max_queue = max(1, per_agent * worker_count * 2)
        allowed_suffixes = self._build_allowed_suffixes("")

        while not stop_event.is_set():
            if queue.qsize() >= max_queue:
                await asyncio.sleep(0.25)
                continue
            tasks = self.database.acquire_tasks(
                run_id,
                limit=self.source.pages_per_agent or self.config.crawler.pages_per_agent,
                lease_owner=f"{lease_owner_prefix}-{asyncio.get_running_loop().time():.0f}",
                lease_seconds=lease_seconds,
                max_retries=self.config.crawler.max_retries,
                backoff_seconds=self.config.crawler.backoff_min_seconds,
            )

            if not tasks:
                active = self.database.count_active_tasks(run_id)
                if active == 0:
                    self.logger.info("No pending tasks and no active leases; stopping producer.")
                    for _ in range(worker_count):
                        await queue.put(None)
                    sentinels_emitted = True
                    self._update_queue_snapshot(run_id)
                    break
                self.logger.debug(
                    "No tasks acquired; active leases=%s queue=%s",
                    active,
                    queue.qsize(),
                )
                await asyncio.sleep(1.0)
                self._update_queue_snapshot(run_id)
                continue

            queued_count = 0
            filtered_invalid = 0
            filtered_domain = 0
            filtered_path = 0
            for task in tasks:
                parsed = urlparse(task.url)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    self.database.mark_task_error(task.id, error="filtered invalid url")
                    filtered_invalid += 1
                    continue
                host = parsed.netloc.lower()
                if not self._host_allowed(host, allowed_suffixes):
                    self.database.mark_task_error(task.id, error="filtered by domain rules")
                    filtered_domain += 1
                    continue
                if not self._path_allowed(host, parsed.path):
                    self.database.mark_task_error(task.id, error="filtered by path rules")
                    filtered_path += 1
                    continue
                await queue.put(task)
                queued_count += 1

            if filtered_invalid or filtered_domain or filtered_path:
                self.logger.debug(
                    "Filtered tasks invalid=%s domain=%s path=%s (queued=%s)",
                    filtered_invalid,
                    filtered_domain,
                    filtered_path,
                    queued_count,
                )
            self._update_queue_snapshot(run_id)

        if not sentinels_emitted:
            for _ in range(worker_count):
                await queue.put(None)

        self._update_queue_snapshot(run_id)

    def _handle_stop_signal(
        self,
        *,
        run_id: int,
        queue: asyncio.Queue[TaskRecord | None],
        workers: list[asyncio.Task],
        producer: asyncio.Task,
        worker_count: int,
    ) -> None:
        self.logger.info("Stop signal received; cancelling crawl workers.")
        producer.cancel()
        for worker in workers:
            worker.cancel()

        drained = 0
        while True:
            try:
                queued = queue.get_nowait()
            except QueueEmpty:
                break

            if queued is None:
                queue.task_done()
                continue

            self.database.release_task(queued.id, reason="stopped")
            queue.task_done()
            drained += 1

        released = self.database.release_in_progress_tasks(run_id, reason="stopped")

        for _ in range(worker_count):
            try:
                queue.put_nowait(None)
            except QueueFull:  # pragma: no cover - unbounded queue by default
                break

        self.logger.info(
            "Stop signal handled; returned %s queued task(s) and %s in-progress task(s) "
            "to pending.",
            drained,
            released,
        )
        self._update_queue_snapshot(run_id)
        self._update_run_snapshot()

    async def _worker_loop(
        self,
        *,
        name: str,
        run_id: int,
        queue: asyncio.Queue[TaskRecord | None],
        stop_event: asyncio.Event,
    ) -> None:
        retry_policy = AsyncRetrying(
            stop=stop_after_attempt(self.config.crawler.max_retries or 1),
            wait=wait_exponential_jitter(
                initial=self.config.crawler.backoff_min_seconds,
                max=self.config.crawler.backoff_max_seconds,
                exp_base=self.config.crawler.backoff_multiplier,
            ),
            retry=retry_if_exception_type(TransientFetchError),
            reraise=False,
        )

        task: TaskRecord | None = None
        try:
            while True:
                task = await queue.get()
                try:
                    if task is None:
                        break

                    if stop_event.is_set():
                        self.logger.debug("%s stopping; returning task %s", name, task.id)
                        self.database.release_task(task.id, reason="stopped")
                        self._update_agent_snapshot(
                            name,
                            state="stopped",
                            current_url="",
                            last_status="stopping",
                        )
                        break

                    self.logger.debug("%s picked task %s", name, task.id)

                    try:
                        result: FetchResult | None = None
                        success_attempt = 1
                        async for attempt in retry_policy:
                            attempt_number = attempt.retry_state.attempt_number
                            self._update_agent_snapshot(
                                name,
                                state="fetching",
                                current_url=task.url,
                                last_status=f"attempt {attempt_number}",
                            )
                            with attempt:
                                try:
                                    if self.config.crawler.fetch_timeout_seconds:
                                        result = await asyncio.wait_for(
                                            self.fetcher.fetch(task),
                                            timeout=self.config.crawler.fetch_timeout_seconds,
                                        )
                                    else:
                                        result = await self.fetcher.fetch(task)
                                except TimeoutError as exc:
                                    raise TransientFetchError(
                                        f"Timeout while fetching {task.url}"
                                    ) from exc
                                success_attempt = attempt.retry_state.attempt_number

                        if result is None:
                            raise FetchError("Fetcher returned no result")

                        self.database.complete_task(task.id)
                        retries_used = max(0, success_attempt - 1)
                        self._update_agent_snapshot(
                            name,
                            state="idle",
                            current_url="",
                            last_status="completed",
                            fetch_increment=1,
                            retry_increment=retries_used,
                            asset_increment=result.assets_created,
                        )
                        if self.on_success is not None:
                            await self.on_success(task, result)
                        auth_redirected = self._handle_auth_redirect(task.url, result)
                        if not auth_redirected:
                            await self._discover_links(run_id, task, result)
                        self.logger.debug("%s completed task %s", name, task.id)
                        self._update_queue_snapshot(run_id)
                    except RetryError as exc:
                        last = exc.last_attempt
                        raw_error = last.exception() if last else exc
                        error = raw_error if isinstance(raw_error, Exception) else exc
                        attempts = last.attempt_number if last else 0
                        self.logger.warning(
                            "%s exhausted retries for task %s: %s",
                            name,
                            task.id,
                            error,
                        )
                        self.database.mark_task_error(task.id, error=str(error))
                        self._update_agent_snapshot(
                            name,
                            state="error",
                            current_url=task.url,
                            last_status="retry exhausted",
                            retry_increment=attempts,
                        )
                        if self.on_failure is not None:
                            await self.on_failure(task, error)
                        self._update_queue_snapshot(run_id)
                    except Exception as exc:  # pylint: disable=broad-except
                        self.logger.error(
                            "%s encountered fatal error on task %s: %s", name, task.id, exc
                        )
                        self.database.fail_task(
                            task.id,
                            error=str(exc),
                            backoff_seconds=self.config.crawler.backoff_min_seconds,
                        )
                        self._update_agent_snapshot(
                            name,
                            state="error",
                            current_url=task.url,
                            last_status=str(exc),
                        )
                        if self.on_failure is not None:
                            await self.on_failure(task, exc)
                        self._update_queue_snapshot(run_id)
                finally:
                    queue.task_done()
                    task = None
        except asyncio.CancelledError:
            if task is not None:
                self.logger.debug("%s cancelled; returning task %s", name, task.id)
                self.database.release_task(task.id, reason="stopped")
                queue.task_done()
                task = None
            self._update_agent_snapshot(
                name,
                state="stopped",
                current_url="",
                last_status="cancelled",
            )
            raise

        self._update_agent_snapshot(
            name,
            state="idle" if not stop_event.is_set() else "stopped",
            current_url="",
            last_status="stopped" if stop_event.is_set() else "idle",
        )
        self._update_run_snapshot()

    def _update_agent_snapshot(
        self,
        name: str,
        *,
        state: str | None = None,
        current_url: str | None = None,
        last_status: str | None = None,
        fetch_increment: int = 0,
        retry_increment: int = 0,
        asset_increment: int = 0,
    ) -> None:
        metrics = self._agent_metrics.setdefault(name, AgentMetrics())
        metrics.fetches += fetch_increment
        metrics.retries += retry_increment
        metrics.assets += asset_increment
        if state is not None:
            metrics.state = state
        if current_url is not None:
            metrics.current_url = current_url
        if last_status is not None:
            metrics.last_status = last_status

        if self.dashboard:
            snapshot = AgentSnapshot(
                name=name,
                state=metrics.state,
                current_url=metrics.current_url,
                last_status=metrics.last_status,
                fetches=metrics.fetches,
                retries=metrics.retries,
                assets=metrics.assets,
            )
            self.dashboard.update_agent(snapshot)

    def _update_queue_snapshot(self, run_id: int) -> None:
        if not self.dashboard or self._start_time == 0:
            return

        counts = self.database.get_task_status_counts(run_id)
        pending = counts.get("pending", 0)
        in_progress = counts.get("in_progress", 0)
        finished = counts.get("finished", 0)
        errors = counts.get("error", 0)
        exceptions_open = self.database.count_open_exceptions(run_id)

        elapsed_seconds = max(time.monotonic() - self._start_time, 0.0)
        elapsed_minutes = elapsed_seconds / 60 if elapsed_seconds else 0.0
        throughput = (finished / elapsed_minutes) if elapsed_minutes else 0.0

        snapshot = QueueSnapshot(
            pending=pending,
            in_progress=in_progress,
            finished=finished,
            errors=errors,
            exceptions_open=exceptions_open,
            throughput_per_minute=throughput,
        )
        self.dashboard.update_queue(snapshot)
        self._update_run_snapshot()

    def _update_run_snapshot(self) -> None:
        if not self.dashboard or self._start_time == 0:
            return

        elapsed = timedelta(seconds=time.monotonic() - self._start_time)
        snapshot = RunSnapshot(
            run_id=self._run_id,
            source=self.source.name,
            depth=self.source.depth,
            parallel_agents=self._parallel_agents,
            elapsed=elapsed,
            log_path=self._log_path,
        )
        self.dashboard.set_run_snapshot(snapshot)

    async def _discover_links(self, run_id: int, task: TaskRecord, result: FetchResult) -> None:
        if task.depth <= 1:
            return

        raw_path = result.raw_payload_path
        if not raw_path:
            return

        path = Path(raw_path)
        if not path.exists():
            return

        try:
            html = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="ignore")
        except OSError as exc:
            self.logger.debug("Unable to read raw payload %s: %s", raw_path, exc)
            return

        soup = BeautifulSoup(html, "html.parser")
        if soup is None:
            return

        base_url = task.url
        if result.metadata_json:
            try:
                metadata = json.loads(result.metadata_json)
            except json.JSONDecodeError:
                metadata = {}
            base_url = metadata.get("url", base_url)

        allowed_suffixes = self._build_allowed_suffixes(base_url)
        next_depth = task.depth - 1

        discovered: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            if not href or not isinstance(href, str):
                continue
            href = href.strip()
            if not href:
                continue

            absolute = urljoin(base_url, href)
            absolute = urldefrag(absolute)[0]
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                continue

            host = parsed.netloc.lower()
            if not self._host_allowed(host, allowed_suffixes):
                continue
            if not self._path_allowed(host, parsed.path):
                continue

            if absolute == task.url:
                continue

            if self._is_binary_path(parsed.path):
                continue

            discovered.add(absolute)

        if not discovered:
            return

        seeds = [(url, next_depth) for url in discovered]
        queued = self.database.enqueue_seed_tasks(run_id, seeds)
        if queued:
            self.logger.debug("Queued %s new URL(s) from %s", queued, task.url)

    def _build_allowed_suffixes(self, base_url: str) -> set[str]:
        suffixes: set[str] = set()

        for raw_domain in self.source.allowed_domains:
            domain = raw_domain.lower().lstrip(".")
            if not domain:
                continue
            suffixes.add(domain)
            if domain.startswith("www."):
                suffixes.add(domain[4:])

        parsed = urlparse(base_url)
        if parsed.netloc:
            host = parsed.netloc.lower()
            suffixes.add(host)
            if parsed.hostname:
                suffixes.add(parsed.hostname.lower())

        return suffixes

    def _path_allowed(self, host: str, path: str) -> bool:
        rules = self._match_domain_rules(host)
        if rules is None:
            return True
        candidate = path or "/"
        deny = [rule for rule in rules.deny_paths if rule]
        runtime_deny = list(self._match_runtime_denies(host))
        if runtime_deny:
            deny.extend(runtime_deny)
        for pattern in deny:
            if self._path_matches(candidate, pattern):
                return False
        allow = [rule for rule in rules.allow_paths if rule]
        if allow:
            return any(self._path_matches(candidate, pattern) for pattern in allow)
        return True

    @staticmethod
    def _path_matches(path: str, pattern: str) -> bool:
        if not pattern:
            return False
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if not prefix.endswith("/"):
                prefix += "/"
            return path.startswith(prefix)
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            if not prefix.endswith("/"):
                prefix += "/"
            return path.startswith(prefix)
        if any(ch in pattern for ch in ("*", "?", "[")):
            return fnmatch.fnmatchcase(path, pattern)
        return path == pattern

    def _match_domain_rules(self, host: str):
        host = host.lower()
        best_domain = ""
        best_rules = None
        for raw_domain, rules in self.source.allowed_domains.items():
            domain = raw_domain.lower().lstrip(".")
            if not domain:
                continue
            is_match = host == domain or host.endswith(f".{domain}")
            if is_match and len(domain) > len(best_domain):
                best_domain = domain
                best_rules = rules
        return best_rules

    def _match_runtime_denies(self, host: str) -> set[str]:
        host = host.lower()
        best_domain = ""
        best_rules: set[str] = set()
        for raw_domain, rules in self._runtime_denies.items():
            domain = raw_domain.lower().lstrip(".")
            if not domain:
                continue
            is_match = host == domain or host.endswith(f".{domain}")
            if is_match and len(domain) > len(best_domain):
                best_domain = domain
                best_rules = rules
        return best_rules

    def _handle_auth_redirect(self, task_url: str, result: FetchResult) -> bool:
        if not result.metadata_json:
            return False
        try:
            metadata = json.loads(result.metadata_json)
        except json.JSONDecodeError:
            return False
        final_url = metadata.get("url")
        if not final_url:
            return False

        parsed = urlparse(final_url)
        host = parsed.netloc.lower()
        if not host:
            return False

        auth_path_prefixes = ("/auth/", "/oauth/", "/login", "/signin")
        if not parsed.path.startswith(auth_path_prefixes):
            return False

        added: list[str] = []
        self._add_runtime_deny(host, "/auth/**", added)

        if parsed.path.startswith("/auth/login"):
            query = parse_qs(parsed.query)
            continue_target = query.get("continue", [""])[0]
            if continue_target:
                cont_path = urlparse(continue_target).path
                if cont_path and cont_path != "/":
                    cont_path = cont_path.rstrip("/") or "/"
                    self._add_runtime_deny(host, f"{cont_path}/**", added)

        if added:
            self.logger.info(
                "Auth redirect detected for %s -> %s; added deny rules %s",
                task_url,
                final_url,
                added,
            )
            if self.dashboard:
                self.dashboard.add_notice(
                    f"Auth redirect: added deny rules for {host}: {', '.join(added)}"
                )
        return True

    def _add_runtime_deny(self, host: str, pattern: str, added: list[str] | None = None) -> None:
        if not pattern:
            return
        host = host.lower()
        rules = self._runtime_denies.setdefault(host, set())
        if pattern not in rules:
            rules.add(pattern)
            if added is not None:
                added.append(pattern)

    @staticmethod
    def _host_allowed(host: str, suffixes: set[str]) -> bool:
        if not suffixes:
            return True
        host = host.lower()
        for suffix in suffixes:
            if host == suffix:
                return True
            if host.endswith(f".{suffix}"):
                return True
        return False

    @staticmethod
    def _is_binary_path(path: str) -> bool:
        path = path.lower()
        binary_exts = {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".svg",
            ".ico",
            ".mp4",
            ".mp3",
            ".wav",
            ".avi",
            ".mov",
            ".wmv",
            ".mkv",
            ".pdf",
            ".zip",
            ".tar",
            ".gz",
            ".rar",
            ".7z",
            ".dmg",
            ".exe",
            ".iso",
            ".ppt",
            ".pptx",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
        }
        return any(path.endswith(ext) for ext in binary_exts)


@dataclass(slots=True)
class AgentMetrics:
    """Mutable metrics per agent."""

    fetches: int = 0
    retries: int = 0
    assets: int = 0
    state: str = "idle"
    current_url: str = ""
    last_status: str = ""

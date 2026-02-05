"""Command line interface for Sitesync."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import platform
import signal
import sys
from dataclasses import dataclass
from itertools import islice
from datetime import datetime, timezone
from hashlib import sha256
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse
from uuid import uuid4

import typer
import yaml

from dotenv import load_dotenv

from sitesync import get_version
from sitesync.config import Config, SourceSettings, load_config
from sitesync.core import CrawlExecutor, Orchestrator
from sitesync.fetchers import HttpFetcher, NullFetcher, PlaywrightFetcher
from sitesync.logging import configure_logging
from sitesync.storage import Database, RunRecord
from sitesync.ui import Dashboard
from sitesync.ui.hotkeys import monitor_double_escape

try:  # Playwright is optional until crawl runs
    from playwright._impl._errors import Error as PlaywrightError  # type: ignore[attr-defined]
    from playwright._impl._errors import TargetClosedError  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - playwright not installed
    PlaywrightError = None  # type: ignore
    TargetClosedError = None  # type: ignore
from sitesync.plugins.registry import load_default_plugins, registry as plugin_registry
from sitesync.reports import write_status_report
from sitesync.cli.data import data_app


@dataclass(slots=True)
class OutputDirs:
    base: pathlib.Path
    raw: pathlib.Path
    normalized: pathlib.Path
    metadata: pathlib.Path
    media: pathlib.Path


def _load_environment(env_file: Optional[pathlib.Path]) -> None:
    """Load environment variables from .env files."""

    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=True)
    else:
        load_dotenv(override=False)


def _prepare_logging(
    config: Config,
    override_path: Optional[pathlib.Path],
    override_level: Optional[str],
) -> tuple[logging.Logger, pathlib.Path]:
    """Configure logging based on configuration and overrides."""

    configured_path = override_path or config.logging.path
    configured_level = (override_level or config.logging.level).upper()
    logger = configure_logging(
        log_path=configured_path,
        level=configured_level,
        mirror_to_console=False,
    )
    handlers = getattr(logger, "handlers", [])
    if handlers:
        file_handler = next((h for h in handlers if hasattr(h, "baseFilename")), None)
        if file_handler is not None:
            return logger, pathlib.Path(file_handler.baseFilename)
    fallback_path = (
        pathlib.Path(configured_path) if configured_path else pathlib.Path.cwd() / "sitesync.log"
    )
    return logger, fallback_path


def _prepare_output_dirs(config: Config) -> OutputDirs:
    base = config.outputs.base_path
    if not base.is_absolute():
        base = pathlib.Path.cwd() / base
    raw = base / config.outputs.raw_subdir
    normalized = base / config.outputs.normalized_subdir
    metadata = base / config.outputs.metadata_subdir
    media = base / config.outputs.media_subdir

    for path in {base, raw, normalized, metadata, media}:
        path.mkdir(parents=True, exist_ok=True)

    return OutputDirs(base=base, raw=raw, normalized=normalized, metadata=metadata, media=media)


def _build_fetcher(source: SourceSettings, logger: logging.Logger, outputs: OutputDirs):
    """Instantiate the fetcher configured for this source."""

    fetcher_type = (source.fetcher or "playwright").lower()
    options = dict(source.fetcher_options or {})
    if fetcher_type == "playwright":
        options.setdefault("raw_dir", outputs.raw)
        options.setdefault("normalized_dir", outputs.normalized)

    if fetcher_type == "null":
        return NullFetcher()
    if fetcher_type == "playwright":
        return PlaywrightFetcher.from_options(logger=logger, options=options)

    raise typer.BadParameter(f"Unsupported fetcher '{source.fetcher}'", param_hint="--source")


def _write_run_metadata(
    *,
    run_record,
    summary,
    config: Config,
    source: SourceSettings,
    output_dirs: OutputDirs,
    database: Database,
) -> None:
    counts = database.get_task_status_counts(run_record.id)
    exceptions_open = database.count_open_exceptions(run_record.id)
    allowed_domains = {
        domain: rules.model_dump()
        for domain, rules in source.allowed_domains.items()
    }

    metadata = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run": {
            "id": run_record.id,
            "source": run_record.source,
            "status": run_record.status,
            "started_at": run_record.started_at,
            "completed_at": run_record.completed_at,
            "label": run_record.label,
            "resumed": summary.resumed,
            "queued_seeds": summary.queued_seeds,
            "seed_urls": summary.seed_urls,
            "depth": summary.depth,
            "parallel_agents": summary.parallel_agents,
        },
        "source": {
            "name": source.name,
            "fetcher": source.fetcher,
            "fetcher_options": source.fetcher_options,
            "allowed_domains": allowed_domains,
        },
        "config": {
            "crawler": config.crawler.model_dump(),
            "outputs": {
                "base_path": str(output_dirs.base),
                "raw_dir": str(output_dirs.raw),
                "normalized_dir": str(output_dirs.normalized),
                "metadata_dir": str(output_dirs.metadata),
            },
        },
        "stats": {
            "tasks": counts,
            "exceptions_open": exceptions_open,
        },
        "environment": {
            "sitesync_version": get_version(),
            "python_version": platform.python_version(),
        },
    }

    output_dirs.metadata.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dirs.metadata / f"run-{run_record.id}.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def _relative_path(path: pathlib.Path) -> str:
    try:
        return str(path.resolve().relative_to(pathlib.Path.cwd()))
    except ValueError:
        return str(path)


def _load_run_metadata(metadata_dir: pathlib.Path, run_id: int) -> Optional[Dict[str, object]]:
    path = metadata_dir / f"run-{run_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


app = typer.Typer(
    name="sitesync",
    help="Synchronize website assets into a structured ontology.",
    no_args_is_help=True,
    add_completion=False,
)

config_app = typer.Typer(help="Configuration utilities.", no_args_is_help=True)
app.add_typer(config_app, name="config")
app.add_typer(data_app, name="data")


def _version_callback(value: bool) -> None:
    """Print the package version and exit when requested."""

    if value:
        typer.echo(get_version())
        raise typer.Exit()


@app.callback()
def main(  # pragma: no cover - exercised via CLI invocation
    ctx: typer.Context,
    config: Optional[pathlib.Path] = typer.Option(
        None,
        "--config",
        metavar="PATH",
        help="Path to YAML configuration file (used exclusively).",
    ),
    env_file: Optional[pathlib.Path] = typer.Option(
        None,
        "--env-file",
        metavar="PATH",
        help="Load environment variables from .env-style file before execution.",
    ),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        metavar="LEVEL",
        help="Override the configured log level (debug, info, warn, error).",
    ),
    log_path: Optional[pathlib.Path] = typer.Option(
        None,
        "--log-path",
        metavar="PATH",
        help="Override the base directory or file for log output.",
    ),
    source: Optional[str] = typer.Option(
        None,
        "--source",
        metavar="NAME",
        help="Select a configured source profile to crawl.",
    ),
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show Sitesync version and exit.",
    ),
) -> None:
    """CLI root; loads configuration, logging, and shared context."""

    ctx.ensure_object(dict)

    _load_environment(env_file)

    if ctx.invoked_subcommand == "init":
        return

    config_obj = load_config(config)
    load_default_plugins()
    plugin_registry.load_entrypoints()

    selected_source = source or config_obj.default_source
    try:
        source_config = config_obj.get_source(selected_source)
    except KeyError as exc:
        raise typer.BadParameter(str(exc), param_hint="--source") from exc

    output_dirs = _prepare_output_dirs(config_obj)
    logger, log_file = _prepare_logging(config_obj, log_path, log_level)

    ctx.obj.update(
        {
            "config": config_obj,
            "config_path": config,
            "selected_source": source_config,
            "selected_source_name": selected_source,
            "log_file": log_file,
            "logger": logger,
            "output_dirs": output_dirs,
        }
    )


@app.command()
def init(
    path: Optional[pathlib.Path] = typer.Option(
        None,
        "--path",
        metavar="PATH",
        help="Path to write the generated configuration (default: config/local.yaml).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite the destination file if it already exists.",
    ),
) -> None:
    """Interactively create a starter configuration file."""

    if path is None:
        destination_text = typer.prompt("Config path", default="config/local.yaml")
        destination = pathlib.Path(destination_text)
    else:
        destination = path

    if not destination.is_absolute():
        destination = pathlib.Path.cwd() / destination
    destination = destination.expanduser()

    if destination.exists() and destination.is_dir():
        destination = destination / "config" / "local.yaml"
        typer.echo(f"Config path is a directory; writing {destination}", err=True)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not force:
        if not typer.confirm(f"{destination} already exists. Overwrite?", default=False):
            typer.echo("Aborted.")
            raise typer.Exit(code=1)

    source_name = typer.prompt("Source name", default="default").strip() or "default"

    def _prompt_start_urls() -> list[str]:
        urls: list[str] = []
        while True:
            value = typer.prompt(
                "Start URL (enter blank to finish)", default="", show_default=False
            )
            url = value.strip()
            if not url:
                break
            urls.append(url)
        return urls

    start_urls = _prompt_start_urls()
    while not start_urls:
        typer.echo("At least one start URL is required.")
        start_urls = _prompt_start_urls()

    # Derive default domains from start URLs
    derived_domains: list[str] = []
    seen_domains: set[str] = set()
    for url in start_urls:
        hostname = urlparse(url).hostname
        if not hostname:
            continue
        normalized = hostname.strip().lower()
        if normalized and normalized not in seen_domains:
            seen_domains.add(normalized)
            derived_domains.append(normalized)

    def _prompt_allowed_domains(defaults: list[str]) -> list[str]:
        """Prompt for allowed domains one at a time."""
        domains: list[str] = []
        # First, prompt with defaults pre-filled
        for i, default in enumerate(defaults):
            value = typer.prompt(
                "Allowed domain (blank to finish)",
                default=default,
                show_default=True,
            )
            if not value.strip():
                break
            domains.append(value.strip().lower())
        # Continue prompting for additional domains
        while True:
            value = typer.prompt(
                "Allowed domain (blank to finish)",
                default="",
                show_default=False,
            )
            if not value.strip():
                break
            domains.append(value.strip().lower())
        return domains

    def _prompt_path_list(label: str, domain: str) -> list[str]:
        value = typer.prompt(
            f"{label} paths for {domain} (comma-separated; exact by default, use /path/** for subtree)",
            default="",
            show_default=False,
        )
        raw = [item.strip() for item in value.split(",")] if value else []
        return [item for item in raw if item]

    allowed_domains_list = _prompt_allowed_domains(derived_domains)
    while not allowed_domains_list:
        typer.echo("At least one allowed domain is required.")
        allowed_domains_list = _prompt_allowed_domains(derived_domains)

    allowed_domains: dict[str, dict[str, list[str]]] = {}
    for domain in allowed_domains_list:
        allow_paths = _prompt_path_list("Allow", domain)
        deny_paths = _prompt_path_list("Deny", domain)
        entry: dict[str, list[str]] = {}
        if allow_paths:
            entry["allow_paths"] = allow_paths
        if deny_paths:
            entry["deny_paths"] = deny_paths
        allowed_domains[domain] = entry

    depth = typer.prompt("Depth", default=5, type=int)
    while depth < 0:
        typer.echo("Depth must be >= 0.")
        depth = typer.prompt("Depth", default=5, type=int)

    parallel_agents = typer.prompt("Parallel agents", default=4, type=int)
    while parallel_agents < 1:
        typer.echo("Parallel agents must be >= 1.")
        parallel_agents = typer.prompt("Parallel agents", default=4, type=int)

    pages_per_agent = typer.prompt("Pages per agent", default=5, type=int)
    while pages_per_agent < 1:
        typer.echo("Pages per agent must be >= 1.")
        pages_per_agent = typer.prompt("Pages per agent", default=5, type=int)

    fetch_timeout_seconds = typer.prompt(
        "Fetch timeout seconds (0 to disable)", default=20.0, type=float
    )
    while fetch_timeout_seconds < 0:
        typer.echo("Fetch timeout seconds must be >= 0.")
        fetch_timeout_seconds = typer.prompt(
            "Fetch timeout seconds (0 to disable)", default=20.0, type=float
        )

    fetcher = typer.prompt("Fetcher [playwright/null]", default="playwright").strip().lower()
    while fetcher not in {"playwright", "null"}:
        typer.echo("Fetcher must be 'playwright' or 'null'.")
        fetcher = typer.prompt("Fetcher [playwright/null]", default="playwright").strip().lower()

    fetcher_options: dict[str, object] = {}
    if fetcher == "playwright":
        wait_after_load = typer.prompt("Wait after load (seconds)", default=3.0, type=float)
        while wait_after_load < 0:
            typer.echo("Wait after load must be >= 0.")
            wait_after_load = typer.prompt("Wait after load (seconds)", default=3.0, type=float)
        fetcher_options["wait_after_load"] = wait_after_load

    config_doc = {
        "version": 1,
        "default_source": source_name,
        "crawler": {
            "fetch_timeout_seconds": None if fetch_timeout_seconds == 0 else fetch_timeout_seconds
        },
        "sources": [
            {
                "name": source_name,
                "start_urls": start_urls,
                "allowed_domains": allowed_domains,
                "depth": depth,
                "parallel_agents": parallel_agents,
                "pages_per_agent": pages_per_agent,
                "fetcher": fetcher,
                "fetcher_options": fetcher_options,
            }
        ],
    }

    try:
        destination.write_text(yaml.safe_dump(config_doc, sort_keys=False), encoding="utf-8")
    except OSError as exc:
        typer.echo(f"Unable to write configuration to {destination}: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Wrote {destination}")


@config_app.command("show")
def config_show(
    ctx: typer.Context,
    format: str = typer.Option(
        "yaml",
        "--format",
        help="Output format (yaml or json).",
    ),
    paths: bool = typer.Option(
        False,
        "--paths",
        help="Explain configuration precedence and selected inputs.",
    ),
) -> None:
    """Show the effective configuration for this invocation."""

    config: Config = ctx.obj["config"]
    config_path: Optional[pathlib.Path] = ctx.obj.get("config_path")

    normalized_format = format.strip().lower()
    if normalized_format not in {"yaml", "json"}:
        raise typer.BadParameter("Format must be 'yaml' or 'json'.", param_hint="--format")

    if paths:
        if config.loaded_from:
            typer.echo("Loaded configuration from:", err=True)
            for entry in config.loaded_from:
                typer.echo(f"- {entry}", err=True)
        if config_path is not None:
            typer.echo("Mode: replace-by-default", err=True)
        else:
            typer.echo("Config precedence (when --config is not provided):", err=True)
            typer.echo("1) ./config/default.yaml (or packaged default if missing)", err=True)
            typer.echo("2) ./config/local.yaml (optional)", err=True)

    data = config.model.model_dump(mode="json")
    if normalized_format == "json":
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(yaml.safe_dump(data, sort_keys=False))


@app.command()
def crawl(  # pragma: no cover - placeholder until implementation
    ctx: typer.Context,
    resume: bool = typer.Option(False, "--resume", help="Resume an interrupted crawl."),
    start_url: Optional[list[str]] = typer.Option(
        None,
        "--start-url",
        metavar="URL",
        help="Seed URL to enqueue for the run. May be provided multiple times.",
    ),
    depth: Optional[int] = typer.Option(
        None,
        "--depth",
        min=0,
        help="Override maximum crawl depth while this command runs.",
    ),
    parallel: Optional[int] = typer.Option(
        None,
        "--parallel",
        min=1,
        max=64,
        help="Override number of concurrent browser agents.",
    ),
) -> None:
    """Start or resume a crawl run (placeholder)."""
    config: Config = ctx.obj["config"]
    source = ctx.obj["selected_source"]
    logger: logging.Logger = ctx.obj["logger"]
    source: SourceSettings = ctx.obj["selected_source"]
    output_dirs: OutputDirs = ctx.obj["output_dirs"]

    database = Database(config.storage.path)
    database.initialize()

    orchestrator = Orchestrator(
        config=config,
        source=source,
        database=database,
        logger=logger,
    )

    summary = orchestrator.run(
        resume=resume,
        start_urls=start_url,
        depth_override=depth,
        parallel_override=parallel,
    )

    typer.echo(
        f"Run {summary.run.id} ({'resumed' if summary.resumed else 'new'}) queued {summary.queued_seeds} seed task(s)."
    )
    typer.echo(
        f"Depth={summary.depth} parallel_agents={summary.parallel_agents} log={ctx.obj['log_file']}"
    )

    counts = database.get_task_status_counts(summary.run.id)
    pending = counts.get("pending", 0)
    in_progress = counts.get("in_progress", 0)
    finished = counts.get("finished", 0)

    seed_preview = list(islice(summary.seed_urls, 3))
    seed_more = max(summary.queued_seeds - len(seed_preview), 0)

    if summary.resumed:
        typer.echo(
            f"Resuming run {summary.run.id}: pending={pending} in_progress={in_progress} finished={finished}"
        )
    else:
        if summary.seed_urls:
            preview_text = ", ".join(seed_preview)
            if seed_more > 0:
                preview_text += f", … (+{seed_more} more)"
            typer.echo(f"Seeded {summary.queued_seeds} URL(s): {preview_text}")
        else:
            typer.echo("No seed URLs supplied; nothing to crawl.")

    if pending + in_progress == 0:
        logger.info("Run %s has no tasks to process; marking completed.", summary.run.id)
        database.mark_run_status(summary.run.id, "completed", completed=True)
        run_record = database.get_run(summary.run.id)
        _write_run_metadata(
            run_record=run_record,
            summary=summary,
            config=config,
            source=source,
            output_dirs=output_dirs,
            database=database,
        )

        report_path = pathlib.Path.cwd() / "tracking" / "status.md"
        write_status_report(output_dirs.metadata, report_path, limit=10)
        return

    fetcher = _build_fetcher(source, logger, output_dirs)
    http_fetcher = HttpFetcher.from_options(logger, options={"media_dir": output_dirs.media})
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    dashboard = Dashboard(enabled=interactive)

    overview_counts = database.count_tasks_by_status_for_source(source.name)
    if overview_counts:
        dashboard.update_overview(overview_counts)

    dashboard.update_run_summary(
        {
            "run_id": summary.run.id,
            "resumed": summary.resumed,
            "start": _format_time(summary.run.started_at),
            "depth": summary.depth,
            "parallel": summary.parallel_agents,
            "counts": counts,
            "seed_preview": seed_preview,
            "seed_more": seed_more,
            "log_path": str(ctx.obj["log_file"]),
        }
    )

    recent_runs = database.list_recent_runs(limit=5, source=source.name)
    history_data = _compute_history(database, recent_runs, summary.run.id, summary.resumed)
    dashboard.update_history(history_data)

    async def _refresh_overview(loop: asyncio.AbstractEventLoop) -> None:
        counts = await loop.run_in_executor(
            None,
            database.count_tasks_by_status_for_source,
            source.name,
        )
        if counts:
            dashboard.update_overview(counts)

    async def handle_success(task, result):
        if not result.raw_payload_path:
            return

        loop = asyncio.get_running_loop()

        fetch_metadata = {}
        if result.metadata_json:
            try:
                fetch_metadata = json.loads(result.metadata_json)
            except json.JSONDecodeError:
                fetch_metadata = {"raw": result.metadata_json}

        def _fallback_checksum() -> str:
            return sha256(f"{task.url}-{uuid4()}".encode("utf-8")).hexdigest()

        async def _store_record(
            *,
            asset_key: str,
            asset_type: str,
            checksum: str,
            normalized_path: str | None,
            tags: list[str],
            extra_metadata: dict | None,
        ) -> None:
            meta: dict[str, object] = {}
            if tags:
                meta["tags"] = tags
            if fetch_metadata:
                meta["fetch"] = fetch_metadata
            if extra_metadata:
                meta["normalized"] = extra_metadata

            metadata_json = json.dumps(meta) if meta else None

            def _record() -> None:
                database.record_asset(
                    summary.run.id,
                    source_url=task.url,
                    asset_key=asset_key,
                    asset_type=asset_type,
                    checksum=checksum,
                    raw_path=result.raw_payload_path,
                    normalized_path=normalized_path,
                    metadata_json=metadata_json,
                )

            await loop.run_in_executor(None, _record)

        plugins = plugin_registry.find(result.asset_type)

        if plugins:
            for plugin in plugins:
                records = await plugin.normalize(
                    source_url=task.url,
                    raw_path=result.raw_payload_path,
                    metadata_json=result.metadata_json,
                    normalized_dir=output_dirs.normalized,
                )
                for record in records:
                    checksum = record.checksum or result.checksum or _fallback_checksum()
                    normalized_path = (
                        record.normalized_path
                        or result.normalized_payload_path
                        or result.raw_payload_path
                    )
                    await _store_record(
                        asset_key=record.identifier,
                        asset_type=record.asset_type,
                        checksum=checksum,
                        normalized_path=normalized_path,
                        tags=record.tags,
                        extra_metadata=record.metadata,
                    )
        else:
            checksum = result.checksum or _fallback_checksum()
            normalized_path = result.normalized_payload_path or result.raw_payload_path
            await _store_record(
                asset_key=task.url,
                asset_type=result.asset_type,
                checksum=checksum,
                normalized_path=normalized_path,
                tags=[],
                extra_metadata=None,
            )

        await _refresh_overview(loop)

    async def handle_failure(task, error):  # noqa: D401
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        await _refresh_overview(loop)

    executor = CrawlExecutor(
        config=config,
        source=source,
        database=database,
        fetcher=fetcher,
        logger=logger,
        dashboard=dashboard,
        on_success=handle_success,
        on_failure=handle_failure,
        media_fetcher=http_fetcher,
    )

    stop_event = asyncio.Event()
    signal_triggered = False

    def _install_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
        if TargetClosedError is None and PlaywrightError is None:
            return

        def _is_shutdown_playwright_error(exc: object) -> bool:
            if exc is None:
                return False
            if TargetClosedError is not None and isinstance(exc, TargetClosedError):
                return True
            if not stop_event.is_set():
                return False
            if PlaywrightError is not None and isinstance(exc, PlaywrightError):
                return True
            return "net::ERR_ABORTED" in str(exc)

        def exception_handler(loop, context):  # pragma: no cover - event loop internals
            exception = context.get("exception")
            future = context.get("future")
            if future is not None:
                try:
                    exc = future.exception()
                except Exception:  # pragma: no cover - defensive
                    exc = None
                if _is_shutdown_playwright_error(exc):
                    logger.debug("Ignoring Playwright error during shutdown: %s", exc)
                    return
            if _is_shutdown_playwright_error(exception):
                logger.debug("Ignoring Playwright error during shutdown: %s", exception)
                return
            loop.default_exception_handler(context)

        loop.set_exception_handler(exception_handler)

    def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> list[tuple[str, int, object]]:
        installed: list[tuple[str, int, object]] = []

        def _handle_signal() -> None:
            nonlocal signal_triggered
            signal_triggered = True
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_signal)
                installed.append(("loop", sig, None))
            except NotImplementedError:
                previous = signal.getsignal(sig)

                def _handler(*_args):  # type: ignore[no-untyped-def]
                    _handle_signal()

                signal.signal(sig, _handler)
                installed.append(("signal", sig, previous))
        return installed

    def _restore_signal_handlers(
        loop: asyncio.AbstractEventLoop, installed: list[tuple[str, int, object]]
    ) -> None:
        for kind, sig, previous in installed:
            if kind == "loop":
                loop.remove_signal_handler(sig)
            else:
                signal.signal(sig, previous)

    async def run_executor() -> None:
        loop = asyncio.get_running_loop()
        _install_exception_handler(loop)
        installed = _install_signal_handlers(loop)
        try:
            await executor.run(
                run_id=summary.run.id,
                parallel_agents=summary.parallel_agents,
                log_path=str(ctx.obj["log_file"]),
                stop_signal=stop_event,
            )
        finally:
            _restore_signal_handlers(loop, installed)

    async def run_with_hotkey() -> bool:
        loop = asyncio.get_running_loop()
        hint_timer: Optional[asyncio.TimerHandle] = None
        _install_exception_handler(loop)

        def show_press_hint() -> None:
            nonlocal hint_timer
            logger.info("ESC pressed once; prompting for confirmation.")
            dashboard.show_escape_hint("Press ESC again to stop")
            if hint_timer is not None:
                hint_timer.cancel()
            hint_timer = loop.call_later(1.5, clear_press_hint)

        def clear_press_hint() -> None:
            nonlocal hint_timer
            if hint_timer is not None:
                hint_timer.cancel()
                hint_timer = None
            dashboard.clear_escape_hint()

        def show_exit_hint() -> None:
            nonlocal hint_timer
            if hint_timer is not None:
                hint_timer.cancel()
                hint_timer = None
            logger.info("ESC pressed twice; signalling crawl shutdown.")
            dashboard.show_escape_hint(
                "Exiting. Please wait for whatever it is we happen to be waiting for."
            )

        escape_task = asyncio.create_task(
            monitor_double_escape(
                stop_event,
                timeout=1.5,
                on_single=show_press_hint,
                on_timeout=clear_press_hint,
                on_double=show_exit_hint,
            )
        )
        try:
            await run_executor()
        finally:
            stop_event.set()
            if not escape_task.done():
                escape_task.cancel()

        triggered = False
        try:
            triggered = await escape_task
            return triggered
        except asyncio.CancelledError:
            return False
        finally:
            if hint_timer is not None:
                hint_timer.cancel()
            if not triggered:
                dashboard.clear_escape_hint()

    terminal_state = _capture_terminal_state() if interactive else None
    escape_triggered = False
    try:
        with dashboard:
            if interactive:
                try:
                    escape_triggered = asyncio.run(run_with_hotkey())
                except KeyboardInterrupt:
                    signal_triggered = True
            else:
                try:
                    escape_triggered = asyncio.run(run_executor())
                except KeyboardInterrupt:
                    signal_triggered = True
    finally:
        _restore_terminal(terminal_state)

    if escape_triggered:
        typer.echo("Received double Escape; stopping crawl as requested.")
    if signal_triggered and not escape_triggered:
        typer.echo("Received interrupt; stopping crawl.", err=True)

    database.mark_run_status(
        summary.run.id,
        "stopped" if (escape_triggered or signal_triggered) else "completed",
        completed=True,
    )
    run_record = database.get_run(summary.run.id)
    _write_run_metadata(
        run_record=run_record,
        summary=summary,
        config=config,
        source=source,
        output_dirs=output_dirs,
        database=database,
    )

    report_path = pathlib.Path.cwd() / "tracking" / "status.md"
    write_status_report(output_dirs.metadata, report_path, limit=10)
    _emit_run_exit_summary(database=database, run_id=summary.run.id)
    _emit_runtime_deny_suggestion(executor=executor, source=source)


@app.command()
def status(  # pragma: no cover - placeholder until implementation
    ctx: typer.Context,
    detail: bool = typer.Option(False, "--detail", help="Include detailed per-plugin metrics."),
) -> None:
    """Show current system status (placeholder)."""
    config: Config = ctx.obj["config"]
    logger: logging.Logger = ctx.obj["logger"]
    source: SourceSettings = ctx.obj["selected_source"]
    source_name: str = ctx.obj["selected_source_name"]
    output_dirs: OutputDirs = ctx.obj["output_dirs"]
    log_file: pathlib.Path = ctx.obj["log_file"]

    database = Database(config.storage.path)
    database.initialize()

    overview_counts = database.count_tasks_by_status_for_source(source.name)
    typer.echo(f"Source '{source_name}' overview:")
    if overview_counts:
        total = sum(overview_counts.values())
        finished = overview_counts.get("finished", 0)
        errors = overview_counts.get("error", 0)
        pending = overview_counts.get("pending", 0)
        in_progress = overview_counts.get("in_progress", 0)
        remaining = pending + in_progress
        typer.echo(
            f"  total={total} finished={finished} remaining={remaining} in_progress={in_progress} errors={errors}"
        )
    else:
        typer.echo("  no crawl activity recorded yet.")

    limit = 10 if detail else 5
    runs = database.list_recent_runs(limit=limit, source=source.name)

    if not runs:
        typer.echo("No runs recorded for this source yet.")
        logger.debug("Status command found no runs for source '%s'.", source.name)
        return

    current = runs[0]
    current_counts = database.get_task_status_counts(current.id)
    exceptions_open = database.count_open_exceptions(current.id)
    pending = current_counts.get("pending", 0)
    in_progress = current_counts.get("in_progress", 0)
    finished = current_counts.get("finished", 0)
    errors = current_counts.get("error", 0)

    metadata = _load_run_metadata(output_dirs.metadata, current.id)
    run_info = metadata.get("run", {}) if metadata else {}
    resumed_flag = bool(run_info.get("resumed"))
    depth = run_info.get("depth")
    parallel_agents = run_info.get("parallel_agents")
    seed_urls = run_info.get("seed_urls", []) if isinstance(run_info.get("seed_urls"), list) else []
    queued_seeds = (
        run_info.get("queued_seeds") if isinstance(run_info.get("queued_seeds"), int) else None
    )

    typer.echo("")
    status_label = current.status
    typer.echo(
        f"Current run {current.id} [{status_label}] started {_format_time(current.started_at)}"
    )
    if current.completed_at:
        typer.echo(f"  completed {_format_time(current.completed_at)}")
    if resumed_flag:
        typer.echo("  resumed: yes")
    if depth is not None or parallel_agents is not None:
        typer.echo(
            f"  depth={depth if depth is not None else source.depth} parallel={parallel_agents if parallel_agents is not None else (source.parallel_agents or config.crawler.parallel_agents)}"
        )
    typer.echo(
        f"  queue pending={pending} in_progress={in_progress} finished={finished} errors={errors} exceptions={exceptions_open}"
    )

    if seed_urls:
        preview = ", ".join(seed_urls[:3])
        if queued_seeds and queued_seeds > len(seed_urls[:3]):
            preview += f", … (+{queued_seeds - len(seed_urls[:3])})"
        typer.echo(f"  seeds: {preview}")

    typer.echo(f"  log={_relative_path(log_file)}")

    history = _compute_history(database, runs, current.id, resumed_flag)
    typer.echo("")
    typer.echo("Recent runs:")
    for entry in history:
        icon = entry.get("icon", "")
        run_id = entry.get("run_id", "")
        finished_count = entry.get("finished", 0)
        total = entry.get("total", 0)
        errors_count = entry.get("errors", 0)
        start = entry.get("start", "--")
        end = entry.get("end", "--")
        typer.echo(
            f"  {icon} run {run_id}: {finished_count}/{total} errors={errors_count} {start}–{end}"
        )

    if detail:
        for run in runs:
            counts = database.get_task_status_counts(run.id)
            exceptions = database.count_open_exceptions(run.id)
            typer.echo(
                f"  run {run.id} queue pending={counts.get('pending', 0)} in_progress={counts.get('in_progress', 0)} finished={counts.get('finished', 0)} errors={counts.get('error', 0)} exceptions={exceptions}"
            )

    logger.debug("Status command listed %s runs for source '%s'.", len(runs), source.name)


@app.command()
def version() -> None:
    """Print the Sitesync version."""

    typer.echo(get_version())


def _capture_terminal_state() -> Optional[tuple[str, list[int]]]:
    if os.name == "nt":
        return None
    try:
        import termios
    except Exception:  # pragma: no cover - best effort
        return None
    if sys.stdin.isatty():
        try:
            return ("stdin", list(termios.tcgetattr(sys.stdin.fileno())))
        except termios.error:  # pragma: no cover - best effort
            return None
    try:
        with open("/dev/tty", "r", encoding="utf-8", errors="ignore") as stream:
            return ("tty", list(termios.tcgetattr(stream.fileno())))
    except (OSError, termios.error):  # pragma: no cover - best effort
        return None


def _restore_terminal(state: Optional[tuple[str, list[int]]]) -> None:
    if os.name != "nt":
        try:
            if state is not None:
                import termios

                source, attrs = state
                if source == "stdin" and sys.stdin.isatty():
                    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, attrs)
                    return
                if source == "tty":
                    with open("/dev/tty", "r", encoding="utf-8", errors="ignore") as stream:
                        termios.tcsetattr(stream.fileno(), termios.TCSADRAIN, attrs)
                    return
        except Exception:  # pragma: no cover - best effort
            pass
        try:
            if os.path.exists("/dev/tty"):
                os.system("stty sane </dev/tty >/dev/null 2>&1")
            os.system("stty sane >/dev/null 2>&1")
        except Exception:  # pragma: no cover - best effort
            pass
    try:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()
    except Exception:  # pragma: no cover - best effort
        pass


def _emit_runtime_deny_suggestion(
    *, executor: CrawlExecutor, source: SourceSettings
) -> None:
    runtime_denies = executor.get_runtime_denies()
    if not runtime_denies:
        return

    suggested_domains: Dict[str, Dict[str, list[str]]] = {}
    for domain, rules in source.allowed_domains.items():
        entry: Dict[str, list[str]] = {
            "allow_paths": list(rules.allow_paths),
        }
        merged = list(rules.deny_paths)
        merged.extend(runtime_denies.get(domain, []))
        entry["deny_paths"] = sorted(set(merged))
        suggested_domains[domain] = entry

    for domain, patterns in runtime_denies.items():
        if domain in suggested_domains:
            continue
        suggested_domains[domain] = {
            "allow_paths": [],
            "deny_paths": sorted(patterns),
        }

    suggestion = {
        "sources": [
            {
                "name": source.name,
                "start_urls": list(source.start_urls),
                "allowed_domains": suggested_domains,
            }
        ]
    }

    typer.echo("")
    typer.echo(
        "We hit auth redirects during this crawl. The block below adds deny rules "
        "so future runs skip those login loops and stay on public docs."
    )
    typer.echo("Suggested config update:")
    typer.echo(yaml.safe_dump(suggestion, sort_keys=False).strip())


def _emit_run_exit_summary(*, database: Database, run_id: int) -> None:
    counts = database.get_task_status_counts(run_id)
    pending = counts.get("pending", 0)
    in_progress = counts.get("in_progress", 0)
    finished = counts.get("finished", 0)
    errors = counts.get("error", 0)
    total = sum(counts.values())
    typer.echo("")
    typer.echo(
        f"Run {run_id} summary: finished={finished}/{total} pending={pending} "
        f"in_progress={in_progress} errors={errors}"
    )


def _format_time(timestamp: Optional[str]) -> str:
    if not timestamp:
        return "--"
    try:
        ts = timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%H:%M")
    except ValueError:
        return timestamp or "--"


def _compute_history(
    database: Database,
    runs: List[RunRecord],
    current_run_id: int,
    current_resumed: bool,
) -> List[Dict[str, object]]:
    history: List[Dict[str, object]] = []
    for run in runs:
        counts = database.get_task_status_counts(run.id)
        total = sum(counts.values())
        finished = counts.get("finished", 0)
        errors = counts.get("error", 0)

        if run.status == "stopped":
            icon = "■"
        elif run.completed_at:
            icon = "✓" if errors == 0 else "!"
        elif run.id == current_run_id and current_resumed:
            icon = "↺"
        else:
            icon = "▶"

        history.append(
            {
                "icon": icon,
                "run_id": run.id,
                "finished": finished,
                "total": total,
                "errors": errors,
                "start": _format_time(run.started_at),
                "end": _format_time(run.completed_at),
            }
        )

    return history

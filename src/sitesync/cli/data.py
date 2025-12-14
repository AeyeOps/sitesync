"""Data access commands for querying crawled assets."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from sitesync.storage import (
    AssetRecord,
    Database,
    SourceSummary,
)

from .grep import grep_all_sources, grep_source

# Main data app - shows sources if no subcommand
data_app = typer.Typer(
    help="Query and export crawled data.",
    no_args_is_help=False,
)

# Sub-app for all-sources operations
sources_app = typer.Typer(
    help="Operations across all sources.",
    no_args_is_help=False,
)

# Sub-app for single source operations
source_app = typer.Typer(
    help="Operations on a specific source.",
    no_args_is_help=False,
)


# --- Constants ---

MAX_LINE_DISPLAY = 200  # Max chars to display for grep output on long lines


# --- Helper Functions ---


def _format_time(timestamp: str | None) -> str:
    """Format ISO timestamp for display."""
    if not timestamp:
        return "--"
    try:
        return timestamp[:16].replace("T", " ")
    except (ValueError, IndexError):
        return timestamp or "--"


def _truncate(text: str, max_len: int = 60) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


_BYTES_PER_UNIT = 1024


def _format_bytes(size: int) -> str:
    """Format bytes as human-readable."""
    n: float = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < _BYTES_PER_UNIT:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= _BYTES_PER_UNIT
    return f"{n:.1f} TB"


def _sanitize_filename(url: str) -> str:
    """Convert URL to safe filename component."""
    import re

    name = re.sub(r"^https?://", "", url)
    name = re.sub(r"[^\w\-.]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name[:100]


def _truncate_match_line(
    line: str, pattern: str, case_sensitive: bool = False, context_chars: int = 60
) -> str:
    """
    Truncate a long line to show context around the pattern match.

    If line is short enough, return as-is.
    Otherwise, find the match and show context_chars before and after.
    """
    max_len = context_chars * 2 + len(pattern) + 10  # Allow some slack
    if len(line) <= max_len:
        return line

    # Find the match position
    search_line = line if case_sensitive else line.lower()
    search_pat = pattern if case_sensitive else pattern.lower()
    pos = search_line.find(search_pat)

    if pos == -1:
        # No match found (shouldn't happen), just truncate from start
        return line[:max_len] + "..."

    # Calculate bounds
    start = max(0, pos - context_chars)
    end = min(len(line), pos + len(pattern) + context_chars)

    # Build snippet
    snippet = line[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(line):
        snippet = snippet + "..."

    return snippet


def _get_database(ctx: typer.Context) -> Database:
    """Get or initialize database from context."""
    if ctx.obj is None:
        ctx.obj = {}

    if "database" in ctx.obj:
        return ctx.obj["database"]

    # Fall back to default path if no config
    config = ctx.obj.get("config")
    if config and config.storage and config.storage.path:
        db_path = Path(config.storage.path)
    else:
        db_path = Path("./sitesync.sqlite")

    if not db_path.exists():
        typer.echo("No database found. Run 'sitesync crawl' first.", err=True)
        raise typer.Exit(1)

    database = Database(db_path)
    database.initialize()
    ctx.obj["database"] = database
    return database


def _show_sources_table(database: Database) -> None:
    """Display sources table."""
    sources = database.list_sources()

    if not sources:
        typer.echo("No sources found. Run 'sitesync crawl' to capture data.")
        return

    typer.echo(f"{'SOURCE':<15} {'RUNS':<6} {'ASSETS':<8} {'LAST RUN':<17} {'STATUS'}")
    for src in sources:
        typer.echo(
            f"{_truncate(src.name, 15):<15} {src.run_count:<6} {src.asset_count:<8} "
            f"{_format_time(src.last_run_at):<17} {src.last_status or '--'}"
        )


def _show_source_summary(summary: SourceSummary, database: Database) -> None:
    """Display source summary."""
    # Get run breakdown
    stats = database.get_source_stats(summary.name)
    run_breakdown = ""
    if stats:
        parts = [f"{count} {status}" for status, count in stats.runs_by_status.items()]
        if parts:
            run_breakdown = f" ({', '.join(parts)})"

    typer.echo(f"Source: {summary.name}")
    typer.echo(f"Runs: {summary.run_count}{run_breakdown}")
    typer.echo(f"Assets: {summary.asset_count}")
    typer.echo(f"Last run: {_format_time(summary.last_run_at)} ({summary.last_status or 'none'})")


def _error_source_not_found(name: str, database: Database) -> None:
    """Show error with available sources."""
    typer.echo(f"Error: Source '{name}' not found.", err=True)
    sources = database.list_sources()
    if sources:
        typer.echo("\nAvailable sources:", err=True)
        for src in sources:
            typer.echo(f"  {src.name}", err=True)


# --- Data App Callback ---


@data_app.callback(invoke_without_command=True)
def data_callback(ctx: typer.Context) -> None:
    """Initialize database and show sources if no subcommand."""
    # Skip during help/completion or when subcommand will handle it
    if ctx.resilient_parsing:
        return

    # Only initialize database and show sources when no subcommand
    if ctx.invoked_subcommand is None:
        database = _get_database(ctx)
        _show_sources_table(database)


# --- Sources App (plural - all sources) ---


@sources_app.callback(invoke_without_command=True)
def sources_callback(ctx: typer.Context) -> None:
    """List sources if no subcommand."""
    if ctx.resilient_parsing:
        return

    # Only initialize database when no subcommand
    if ctx.invoked_subcommand is None:
        database = _get_database(ctx)
        _show_sources_table(database)


@sources_app.command("grep")
def sources_grep_cmd(
    ctx: typer.Context,
    pattern: str = typer.Argument(..., help="Search pattern"),
    regex: bool = typer.Option(False, "--regex", "-E", help="Interpret as regex"),
    case_sensitive: bool = typer.Option(False, "--case-sensitive", "-s", help="Case sensitive"),
    raw: bool = typer.Option(False, "--raw", help="Search raw content"),
    context_lines: int = typer.Option(0, "-C", "--context", help="Context lines"),
    files_only: bool = typer.Option(False, "-l", "--files-only", help="Only show URLs"),
    count_only: bool = typer.Option(False, "-c", "--count", help="Only show counts"),
    max_matches: int | None = typer.Option(
        None, "--max-matches", "-m", help="Stop after N matches"
    ),
    format: str = typer.Option("text", "--format", "-f", help="Output format (text/json)"),
) -> None:
    """Search content across ALL sources."""
    database = _get_database(ctx)

    matches = list(
        grep_all_sources(
            database,
            pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            raw=raw,
            context=context_lines,
            max_matches=max_matches,
        )
    )

    if not matches:
        typer.echo("No matches found.")
        return

    if format == "json":
        data = [
            {
                "source": m.source,
                "asset_id": m.asset_id,
                "url": m.url,
                "line_no": m.line_no,
                "line": m.line,
                "context_before": m.context_before,
                "context_after": m.context_after,
            }
            for m in matches
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    if count_only:
        # Count per file
        file_counts: dict[str, int] = {}
        for m in matches:
            key = f"[{m.source}] {m.url}"
            file_counts[key] = file_counts.get(key, 0) + 1
        for key, cnt in file_counts.items():
            typer.echo(f"{key}: {cnt}")
        return

    if files_only:
        seen: set[str] = set()
        for m in matches:
            key = f"[{m.source}] {m.url}"
            if key not in seen:
                typer.echo(key)
                seen.add(key)
        return

    # Full output
    for m in matches:
        if context_lines > 0 and m.context_before:
            for ctx_line in m.context_before:
                typer.echo(f"[{m.source}] {m.url}-{ctx_line}")
        # Truncate long lines to show context around match (skip if regex)
        display_line = m.line
        if not regex:
            display_line = _truncate_match_line(m.line, pattern, case_sensitive)
        elif len(m.line) > MAX_LINE_DISPLAY:
            display_line = m.line[:MAX_LINE_DISPLAY] + "..."
        typer.echo(f"[{m.source}] {m.url}:{m.line_no}: {display_line}")
        if context_lines > 0 and m.context_after:
            for ctx_line in m.context_after:
                typer.echo(f"[{m.source}] {m.url}-{ctx_line}")
            typer.echo("--")

    # Summary
    unique_files = len({(m.source, m.url) for m in matches})
    unique_sources = len({m.source for m in matches})
    typer.echo(f"\n{len(matches)} matches in {unique_files} files across {unique_sources} sources")


# --- Source App (singular - one source) ---


@source_app.callback(invoke_without_command=True)
def source_callback(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Source name"),
) -> None:
    """Validate source and show summary if no subcommand."""
    if ctx.resilient_parsing:
        return

    database = _get_database(ctx)
    summary = database.get_source_summary(name)

    if summary is None:
        _error_source_not_found(name, database)
        raise typer.Exit(1)

    ctx.obj["source_name"] = name
    ctx.obj["source_summary"] = summary

    if ctx.invoked_subcommand is None:
        _show_source_summary(summary, database)


@source_app.command("runs")
def source_runs(
    ctx: typer.Context,
    run_id: int | None = typer.Argument(None, help="Run ID for detail view"),
    all_runs: bool = typer.Option(False, "--all", help="Show all runs"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of runs to show"),
    format: str = typer.Option("table", "--format", "-f", help="Output format (table/json)"),
) -> None:
    """List and inspect crawl runs."""
    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]

    if run_id is not None:
        # Single run detail view
        run = database.get_run(run_id)
        if run is None:
            typer.echo(f"Run {run_id} not found.", err=True)
            raise typer.Exit(code=1)

        counts = database.get_task_status_counts(run_id)
        exceptions_open = database.count_open_exceptions(run_id)

        if format == "json":
            data = {
                "id": run.id,
                "source": run.source,
                "status": run.status,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "label": run.label,
                "task_counts": counts,
                "exceptions_open": exceptions_open,
            }
            typer.echo(json.dumps(data, indent=2))
        else:
            total = sum(counts.values())
            finished = counts.get("finished", 0)
            errors = counts.get("error", 0)
            pending = counts.get("pending", 0)
            in_progress = counts.get("in_progress", 0)

            typer.echo(f"Run {run.id}")
            typer.echo(f"  Source:    {run.source}")
            typer.echo(f"  Status:    {run.status}")
            typer.echo(f"  Started:   {_format_time(run.started_at)}")
            typer.echo(f"  Completed: {_format_time(run.completed_at)}")
            if run.label:
                typer.echo(f"  Label:     {run.label}")
            typer.echo(
                f"  Tasks:     {finished}/{total} "
                f"(pending={pending} in_progress={in_progress} errors={errors})"
            )
            if exceptions_open > 0:
                typer.echo(f"  Exceptions: {exceptions_open} open")
        return

    # List runs
    run_limit = 1000 if all_runs else limit
    runs_list = database.list_recent_runs(limit=run_limit, source=source_name)

    if not runs_list:
        typer.echo("No runs found for this source.", err=True)
        raise typer.Exit(code=0)

    if format == "json":
        data = []
        for run in runs_list:
            counts = database.get_task_status_counts(run.id)
            data.append(
                {
                    "id": run.id,
                    "source": run.source,
                    "status": run.status,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                    "label": run.label,
                    "task_counts": counts,
                }
            )
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(f"{'ID':<6} {'STATUS':<10} {'STARTED':<17} {'FINISHED':<17} {'TASKS'}")
        for run in runs_list:
            counts = database.get_task_status_counts(run.id)
            total = sum(counts.values())
            finished = counts.get("finished", 0)
            errors = counts.get("error", 0)
            task_summary = f"{finished}/{total}"
            if errors > 0:
                task_summary += f" ({errors} err)"
            started = _format_time(run.started_at)
            completed = _format_time(run.completed_at)
            typer.echo(f"{run.id:<6} {run.status:<10} {started:<17} {completed:<17} {task_summary}")


@source_app.command("assets")
def source_assets(
    ctx: typer.Context,
    run_id: int | None = typer.Option(None, "--run", "-r", help="Filter by run ID"),
    asset_type: str | None = typer.Option(None, "--type", "-t", help="Filter by asset type"),
    url_pattern: str | None = typer.Option(None, "--url", "-u", help="URL glob pattern"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Skip N results"),
    format: str = typer.Option(
        "table", "--format", "-f", help="Output format (table/json/csv/paths)"
    ),
    with_paths: bool = typer.Option(False, "--with-paths", help="Include file paths in table"),
) -> None:
    """List, filter, and search assets."""
    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]

    # Determine run ID
    if run_id is None:
        latest_run = database.get_latest_run(source=source_name, statuses=["completed", "stopped"])
        if latest_run is None:
            typer.echo("No completed runs found. Use --run to specify a run ID.", err=True)
            raise typer.Exit(code=1)
        run_id = latest_run.id
        typer.echo(f"Using most recent run: {run_id}", err=True)

    assets_list = database.list_assets(
        run_id=run_id,
        asset_type=asset_type,
        url_pattern=url_pattern,
        limit=limit,
        offset=offset,
    )

    if not assets_list:
        typer.echo("No assets found matching criteria.", err=True)
        raise typer.Exit(code=0)

    if format == "json":
        data = []
        for asset in assets_list:
            data.append(
                {
                    "id": asset.id,
                    "run_id": asset.run_id,
                    "asset_key": asset.asset_key,
                    "asset_type": asset.asset_type,
                    "source_url": asset.source_url,
                    "checksum": asset.checksum,
                    "status": asset.status,
                    "version_count": asset.version_count,
                    "created_at": asset.created_at,
                    "updated_at": asset.updated_at,
                    "latest_raw_path": asset.latest_raw_path,
                    "latest_normalized_path": asset.latest_normalized_path,
                }
            )
        typer.echo(json.dumps(data, indent=2))
    elif format == "csv":
        typer.echo("id,type,asset_key,checksum,versions,updated_at")
        for asset in assets_list:
            checksum_short = asset.checksum[:8] if asset.checksum else ""
            typer.echo(
                f"{asset.id},{asset.asset_type},"
                f'"{asset.asset_key}",{checksum_short},{asset.version_count},{asset.updated_at}'
            )
    elif format == "paths":
        for asset in assets_list:
            path = asset.latest_normalized_path or asset.latest_raw_path
            if path:
                typer.echo(path)
    elif with_paths:
        typer.echo(f"{'ID':<6} {'TYPE':<8} {'KEY':<35} {'CHECKSUM':<10} {'VERS':<4} {'PATH'}")
        for asset in assets_list:
            checksum_short = asset.checksum[:8] + "..." if asset.checksum else ""
            path = asset.latest_normalized_path or asset.latest_raw_path or ""
            typer.echo(
                f"{asset.id:<6} {asset.asset_type:<8} {_truncate(asset.asset_key, 35):<35} "
                f"{checksum_short:<10} {asset.version_count:<4} {_truncate(path, 40)}"
            )
    else:
        hdr = f"{'ID':<6} {'TYPE':<8} {'KEY':<40} {'CHECKSUM':<12} {'VERS':<4} {'UPDATED'}"
        typer.echo(hdr)
        for asset in assets_list:
            checksum_short = asset.checksum[:8] + "..." if asset.checksum else ""
            key = _truncate(asset.asset_key, 40)
            updated = _format_time(asset.updated_at)
            typer.echo(
                f"{asset.id:<6} {asset.asset_type:<8} {key:<40} "
                f"{checksum_short:<12} {asset.version_count:<4} {updated}"
            )


@source_app.command("content")
def source_content(
    ctx: typer.Context,
    asset_id: int | None = typer.Argument(None, help="Asset ID"),
    url: str | None = typer.Option(None, "--url", "-u", help="Asset URL"),
    run_id: int | None = typer.Option(None, "--run", "-r", help="Run ID for URL lookup"),
    raw: bool = typer.Option(False, "--raw", help="Show raw content instead of normalized"),
    version: int | None = typer.Option(None, "--version", "-v", help="Specific version"),
    path_only: bool = typer.Option(False, "--path-only", "-p", help="Output only file path"),
    metadata: bool = typer.Option(False, "--metadata", "-m", help="Show metadata only"),
    no_header: bool = typer.Option(False, "--no-header", help="Suppress header (for piping)"),
) -> None:
    """View asset content."""
    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]

    if asset_id is None and url is None:
        typer.echo("Error: Provide either asset ID or --url", err=True)
        raise typer.Exit(code=1)

    if asset_id is not None and url is not None:
        typer.echo("Error: Provide asset ID or --url, not both", err=True)
        raise typer.Exit(code=1)

    asset: AssetRecord | None = None
    if asset_id is not None:
        asset = database.get_asset(asset_id)
    elif url is not None:
        lookup_run_id = run_id
        if lookup_run_id is None:
            latest_run = database.get_latest_run(
                source=source_name, statuses=["completed", "stopped"]
            )
            if latest_run:
                lookup_run_id = latest_run.id
        asset = database.get_asset_by_url(url, run_id=lookup_run_id)

    if asset is None:
        if asset_id is not None:
            typer.echo(f"Asset {asset_id} not found.", err=True)
        else:
            typer.echo(f"Asset with URL '{url}' not found.", err=True)
        raise typer.Exit(code=1)

    asset_version = database.get_asset_version(asset.id, version=version)
    if asset_version is None:
        if version is not None:
            typer.echo(f"Version {version} not found for asset {asset.id}.", err=True)
        else:
            typer.echo(f"No versions found for asset {asset.id}.", err=True)
        raise typer.Exit(code=1)

    target_path = asset_version.raw_path if raw else asset_version.normalized_path
    if target_path is None:
        target_path = asset_version.normalized_path if raw else asset_version.raw_path

    if path_only:
        if target_path is None:
            typer.echo("No file path recorded for this asset.", err=True)
            raise typer.Exit(code=1)
        file_path = Path(target_path)
        if not file_path.exists():
            typer.echo(f"File not found: {target_path}", err=True)
            raise typer.Exit(code=1)
        typer.echo(target_path)
        return

    if metadata:
        if asset_version.metadata_json:
            try:
                meta = json.loads(asset_version.metadata_json)
                typer.echo(json.dumps(meta, indent=2))
            except json.JSONDecodeError:
                typer.echo(asset_version.metadata_json)
        else:
            typer.echo("{}")
        return

    if not no_header:
        typer.echo(f"=== Asset {asset.id}: {_truncate(asset.asset_key, 60)} ===")
        typer.echo(
            f"Type: {asset.asset_type} | Version: {asset_version.version} | "
            f"Updated: {_format_time(asset_version.created_at)}"
        )
        if target_path:
            typer.echo(f"Path: {target_path}")
        typer.echo("---")

    if target_path is None:
        typer.echo("(no content file recorded)")
        return

    file_path = Path(target_path)
    if not file_path.exists():
        typer.echo(f"(file not found: {target_path})")
        return

    try:
        content_text = file_path.read_text(encoding="utf-8")
        typer.echo(content_text)
    except UnicodeDecodeError:
        typer.echo(f"(binary file: {file_path.stat().st_size} bytes)")
    except OSError as e:
        typer.echo(f"(error reading file: {e})")


@source_app.command("tasks")
def source_tasks(
    ctx: typer.Context,
    run_id: int | None = typer.Option(None, "--run", "-r", help="Filter by run ID"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status"),
    errors: bool = typer.Option(False, "--errors", "-e", help="Show only error tasks"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max results"),
    offset: int = typer.Option(0, "--offset", help="Skip N results"),
    format: str = typer.Option("table", "--format", "-f", help="Output format (table/json)"),
) -> None:
    """Inspect crawl task queue."""
    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]

    if run_id is None:
        latest_run = database.get_latest_run(
            source=source_name, statuses=["completed", "stopped", "running"]
        )
        if latest_run is None:
            typer.echo("No runs found. Use --run to specify a run ID.", err=True)
            raise typer.Exit(code=1)
        run_id = latest_run.id
        typer.echo(f"Using most recent run: {run_id}", err=True)

    filter_status = "error" if errors else status

    tasks_list = database.list_tasks_for_run(
        run_id=run_id,
        status=filter_status,
        limit=limit,
        offset=offset,
    )

    if not tasks_list:
        typer.echo("No tasks found matching criteria.", err=True)
        raise typer.Exit(code=0)

    if format == "json":
        data = []
        for task in tasks_list:
            data.append(
                {
                    "id": task.id,
                    "url": task.url,
                    "depth": task.depth,
                    "status": task.status,
                    "attempt_count": task.attempt_count,
                    "last_error": task.last_error,
                    "lease_owner": task.lease_owner,
                    "next_run_at": task.next_run_at,
                }
            )
        typer.echo(json.dumps(data, indent=2))
    elif errors:
        typer.echo(f"{'ID':<6} {'URL':<50} {'ATTEMPTS':<8} {'ERROR'}")
        for task in tasks_list:
            error_msg = _truncate(task.last_error or "", 40) if task.last_error else ""
            url = _truncate(task.url, 50)
            typer.echo(f"{task.id:<6} {url:<50} {task.attempt_count:<8} {error_msg}")
    else:
        typer.echo(f"{'ID':<6} {'URL':<50} {'STATUS':<12} {'DEPTH':<5} {'ATTEMPTS'}")
        for task in tasks_list:
            typer.echo(
                f"{task.id:<6} {_truncate(task.url, 50):<50} {task.status:<12} "
                f"{task.depth:<5} {task.attempt_count}"
            )


@source_app.command("export")
def source_export(
    ctx: typer.Context,
    output_dir: Path = typer.Argument(..., help="Directory to export files to"),
    run_id: int | None = typer.Option(None, "--run", "-r", help="Filter by run ID"),
    asset_type: str | None = typer.Option(None, "--type", "-t", help="Filter by asset type"),
    url_pattern: str | None = typer.Option(None, "--url", "-u", help="URL glob pattern"),
    raw: bool = typer.Option(False, "--raw", help="Export raw files"),
    with_metadata: bool = typer.Option(False, "--with-metadata", help="Include .meta.json"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview only"),
) -> None:
    """Bulk export assets to directory."""
    import shutil

    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]

    if run_id is None:
        latest_run = database.get_latest_run(source=source_name, statuses=["completed", "stopped"])
        if latest_run is None:
            typer.echo("No completed runs found. Use --run to specify a run ID.", err=True)
            raise typer.Exit(code=1)
        run_id = latest_run.id
        typer.echo(f"Using most recent run: {run_id}", err=True)

    assets_list = database.list_assets(
        run_id=run_id,
        asset_type=asset_type,
        url_pattern=url_pattern,
        limit=10000,
        offset=0,
    )

    if not assets_list:
        typer.echo("No assets found matching criteria.", err=True)
        raise typer.Exit(code=0)

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    exported = 0
    skipped = 0

    for asset in assets_list:
        source_path_str = asset.latest_raw_path if raw else asset.latest_normalized_path
        if source_path_str is None:
            source_path_str = asset.latest_normalized_path if raw else asset.latest_raw_path
        if source_path_str is None:
            skipped += 1
            continue

        source_path = Path(source_path_str)
        if not source_path.exists():
            skipped += 1
            continue

        ext = source_path.suffix or ".txt"
        safe_key = _sanitize_filename(asset.asset_key)
        out_name = f"{asset.id}_{safe_key}{ext}"
        out_path = output_dir / out_name

        if dry_run:
            typer.echo(f"Would export: {source_path} -> {out_path}")
        else:
            try:
                shutil.copy2(source_path, out_path)
                exported += 1
            except OSError as e:
                typer.echo(f"Error copying {source_path}: {e}", err=True)
                skipped += 1
                continue

        if with_metadata:
            meta_path = out_path.with_suffix(out_path.suffix + ".meta.json")
            meta_data = {
                "id": asset.id,
                "asset_key": asset.asset_key,
                "asset_type": asset.asset_type,
                "source_url": asset.source_url,
                "checksum": asset.checksum,
                "version_count": asset.version_count,
                "created_at": asset.created_at,
                "updated_at": asset.updated_at,
            }
            if asset.latest_metadata:
                try:
                    meta_data["metadata"] = json.loads(asset.latest_metadata)
                except json.JSONDecodeError:
                    meta_data["metadata_raw"] = asset.latest_metadata

            if dry_run:
                typer.echo(f"Would write metadata: {meta_path}")
            else:
                try:
                    meta_path.write_text(json.dumps(meta_data, indent=2), encoding="utf-8")
                except OSError as e:
                    typer.echo(f"Error writing metadata {meta_path}: {e}", err=True)

    if dry_run:
        typer.echo(f"Dry run: would export {len(assets_list) - skipped} files, skip {skipped}")
    else:
        typer.echo(f"Exported {exported} files to {output_dir} (skipped {skipped})")


@source_app.command("stats")
def source_stats(
    ctx: typer.Context,
    format: str = typer.Option("table", "--format", "-f", help="Output format (table/json)"),
) -> None:
    """Show detailed statistics for the source."""
    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]

    stats = database.get_source_stats(source_name)
    if stats is None:
        typer.echo(f"Source '{source_name}' not found.", err=True)
        raise typer.Exit(1)

    if format == "json":
        data = {
            "name": stats.name,
            "runs_by_status": stats.runs_by_status,
            "assets_by_type": stats.assets_by_type,
            "tasks_by_status": stats.tasks_by_status,
            "total_raw_bytes": stats.total_raw_bytes,
            "total_normalized_bytes": stats.total_normalized_bytes,
            "first_run_at": stats.first_run_at,
            "last_run_at": stats.last_run_at,
            "avg_duration_seconds": stats.avg_duration_seconds,
        }
        typer.echo(json.dumps(data, indent=2))
        return

    # Table format
    typer.echo(f"Source: {stats.name}")
    typer.echo()

    # Runs
    total_runs = sum(stats.runs_by_status.values())
    typer.echo(f"Runs           {total_runs} total")
    for status, count in sorted(stats.runs_by_status.items()):
        typer.echo(f"  {status:<12} {count}")
    typer.echo()

    # Assets
    total_assets = sum(stats.assets_by_type.values())
    typer.echo(f"Assets         {total_assets} total")
    for atype, count in sorted(stats.assets_by_type.items()):
        pct = (count / total_assets * 100) if total_assets else 0
        typer.echo(f"  {atype:<12} {count:<6} ({pct:.1f}%)")
    typer.echo()

    # Tasks
    total_tasks = sum(stats.tasks_by_status.values())
    typer.echo(f"Tasks          {total_tasks} total")
    for tstatus, count in sorted(stats.tasks_by_status.items()):
        pct = (count / total_tasks * 100) if total_tasks else 0
        typer.echo(f"  {tstatus:<12} {count:<6} ({pct:.1f}%)")
    typer.echo()

    # Storage
    typer.echo("Storage")
    typer.echo(f"  Raw          {_format_bytes(stats.total_raw_bytes)}")
    typer.echo(f"  Normalized   {_format_bytes(stats.total_normalized_bytes)}")
    typer.echo()

    # Timeline
    typer.echo("Timeline")
    typer.echo(f"  First run    {_format_time(stats.first_run_at)}")
    typer.echo(f"  Last run     {_format_time(stats.last_run_at)}")
    if stats.avg_duration_seconds:
        mins = int(stats.avg_duration_seconds // 60)
        secs = int(stats.avg_duration_seconds % 60)
        typer.echo(f"  Avg duration {mins}m {secs}s")


@source_app.command("grep")
def source_grep_cmd(
    ctx: typer.Context,
    pattern: str = typer.Argument(..., help="Search pattern"),
    regex: bool = typer.Option(False, "--regex", "-E", help="Interpret as regex"),
    case_sensitive: bool = typer.Option(False, "--case-sensitive", "-s", help="Case sensitive"),
    raw: bool = typer.Option(False, "--raw", help="Search raw content"),
    context_lines: int = typer.Option(0, "-C", "--context", help="Context lines"),
    files_only: bool = typer.Option(False, "-l", "--files-only", help="Only show URLs"),
    count_only: bool = typer.Option(False, "-c", "--count", help="Only show counts"),
    max_matches: int | None = typer.Option(
        None, "--max-matches", "-m", help="Stop after N matches"
    ),
    format: str = typer.Option("text", "--format", "-f", help="Output format (text/json)"),
) -> None:
    """Search content within this source."""
    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]

    matches = list(
        grep_source(
            database,
            source_name,
            pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            raw=raw,
            context=context_lines,
            max_matches=max_matches,
        )
    )

    if not matches:
        typer.echo("No matches found.")
        return

    if format == "json":
        data = [
            {
                "asset_id": m.asset_id,
                "url": m.url,
                "line_no": m.line_no,
                "line": m.line,
                "context_before": m.context_before,
                "context_after": m.context_after,
            }
            for m in matches
        ]
        typer.echo(json.dumps(data, indent=2))
        return

    if count_only:
        file_counts: dict[str, int] = {}
        for m in matches:
            file_counts[m.url] = file_counts.get(m.url, 0) + 1
        for url, cnt in file_counts.items():
            typer.echo(f"{url}: {cnt}")
        return

    if files_only:
        seen: set[str] = set()
        for m in matches:
            if m.url not in seen:
                typer.echo(m.url)
                seen.add(m.url)
        return

    # Full output
    for m in matches:
        if context_lines > 0 and m.context_before:
            for ctx_line in m.context_before:
                typer.echo(f"{m.url}-{ctx_line}")
        # Truncate long lines to show context around match (skip if regex)
        display_line = m.line
        if not regex:
            display_line = _truncate_match_line(m.line, pattern, case_sensitive)
        elif len(m.line) > MAX_LINE_DISPLAY:
            display_line = m.line[:MAX_LINE_DISPLAY] + "..."
        typer.echo(f"{m.url}:{m.line_no}: {display_line}")
        if context_lines > 0 and m.context_after:
            for ctx_line in m.context_after:
                typer.echo(f"{m.url}-{ctx_line}")
            typer.echo("--")

    # Summary
    unique_files = len({m.url for m in matches})
    typer.echo(f"\n{len(matches)} matches in {unique_files} files")


@source_app.command("delete")
def source_delete(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Delete all data for this source."""
    database: Database = ctx.obj["database"]
    source_name: str = ctx.obj["source_name"]
    summary: SourceSummary = ctx.obj["source_summary"]

    # Preview what will be deleted
    stats = database.get_source_stats(source_name)
    if stats is None:
        typer.echo(f"Source '{source_name}' not found.", err=True)
        raise typer.Exit(1)

    total_bytes = stats.total_raw_bytes + stats.total_normalized_bytes

    if not force:
        typer.echo("This will permanently delete:")
        typer.echo(f"  - {summary.run_count} runs")
        typer.echo(f"  - {summary.asset_count} assets")
        typer.echo(f"  - {_format_bytes(total_bytes)} of files")
        typer.echo()

        confirm = typer.prompt(f"Type '{source_name}' to confirm")
        if confirm != source_name:
            typer.echo("Aborted.", err=True)
            raise typer.Exit(1)

    try:
        result = database.delete_source(source_name)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        typer.echo("Stop the crawl first or wait for completion.", err=True)
        raise typer.Exit(1) from None

    typer.echo(
        f"Deleted {result.runs_deleted} runs, {result.assets_deleted} assets, "
        f"{_format_bytes(result.bytes_freed)} for '{source_name}'."
    )


# --- Register Sub-apps ---

data_app.add_typer(sources_app, name="sources")
data_app.add_typer(source_app, name="source")

"""Markdown status report generator."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def write_status_report(metadata_dir: Path, report_path: Path, limit: int = 10) -> None:
    """Generate a Markdown status report from run metadata files."""

    entries: list[dict[str, Any]] = []

    if not metadata_dir.exists():
        metadata_dir.mkdir(parents=True, exist_ok=True)

    for file_path in sorted(
        metadata_dir.glob("run-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        entries.append(data)
        if len(entries) >= limit:
            break

    report_lines = ["# Sitesync Status", ""]
    generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    report_lines.append(f"Generated: {generated_at}")
    report_lines.append("")

    if not entries:
        report_lines.append("No runs recorded yet.")
    else:
        latest = entries[0]
        report_lines.append("## Latest Run")
        report_lines.extend(_format_entry(latest))
        report_lines.append("")

        if len(entries) > 1:
            report_lines.append("## Recent History")
            for entry in entries[1:]:
                summary = entry.get("run", {})
                run_id = summary.get("id")
                src = summary.get("source")
                started = summary.get("started_at")
                status = summary.get("status")
                report_lines.append(
                    f"- Run {run_id} | source={src} | started={started} | status={status}"
                )
            report_lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines).strip() + "\n", encoding="utf-8")


def _format_entry(entry: dict[str, Any]) -> list[str]:
    run = entry.get("run", {})
    stats = entry.get("stats", {})
    tasks = stats.get("tasks", {})
    lines = [
        f"Run ID: {run.get('id')}",
        f"Source: {run.get('source')}",
        f"Status: {run.get('status')} (resumed={run.get('resumed')})",
        f"Started: {run.get('started_at')} | Completed: {run.get('completed_at')}",
        f"Depth: {run.get('depth')} | Parallel Agents: {run.get('parallel_agents')}",
        "",
        "### Task Summary",
        f"- Pending: {tasks.get('pending', 0)}",
        f"- In Progress: {tasks.get('in_progress', 0)}",
        f"- Finished: {tasks.get('finished', 0)}",
        f"- Errors: {tasks.get('error', 0)}",
        f"- Exceptions Open: {stats.get('exceptions_open', 0)}",
    ]
    return lines

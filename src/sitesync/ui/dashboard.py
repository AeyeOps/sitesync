"""Rich-powered dashboard for live crawl status."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.segment import Segment
from rich.table import Table, box
from rich.text import Text

_MAX_STATUS_TEXT_LEN = 120


def _default_console() -> Console:
    return Console(force_terminal=True, force_interactive=True, soft_wrap=False)


@dataclass(slots=True)
class AgentSnapshot:
    """Represents current state of a crawl agent."""

    name: str
    state: str
    current_url: str
    last_status: str
    fetches: int
    retries: int
    assets: int


@dataclass(slots=True)
class QueueSnapshot:
    """Aggregated queue statistics."""

    pending: int
    in_progress: int
    finished: int
    errors: int
    exceptions_open: int
    throughput_per_minute: float


@dataclass(slots=True)
class RunSnapshot:
    """High-level run information."""

    run_id: int
    source: str
    depth: int
    parallel_agents: int
    elapsed: timedelta
    log_path: str


@dataclass(slots=True)
class Dashboard:
    """Live-updating console dashboard."""

    console: Console = field(default_factory=_default_console)
    enabled: bool = True
    refresh_per_second: float = 4.0
    log_tail_lines: int = 12
    _run_snapshot: RunSnapshot | None = None
    _queue_snapshot: QueueSnapshot | None = None
    _agents: dict[str, AgentSnapshot] = field(default_factory=dict)
    _live: Live | None = field(init=False, default=None)
    _escape_hint: str | None = None
    _overview_counts: dict[str, int] | None = None
    _run_summary: dict[str, object] | None = None
    _history: list[dict[str, object]] = field(default_factory=list)
    _base_url_prefix: str | None = None
    _notices: deque[str] = field(default_factory=lambda: deque(maxlen=2))

    def __enter__(self) -> Dashboard:
        if self.enabled:
            self._live = Live(
                console=self.console,
                refresh_per_second=self.refresh_per_second,
                screen=True,
            )
            self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    def set_run_snapshot(self, snapshot: RunSnapshot) -> None:
        self._run_snapshot = snapshot
        self._refresh()

    def update_queue(self, snapshot: QueueSnapshot) -> None:
        self._queue_snapshot = snapshot
        if self._run_summary is not None:
            counts = {
                "pending": snapshot.pending,
                "in_progress": snapshot.in_progress,
                "finished": snapshot.finished,
                "error": snapshot.errors,
            }
            self.update_run_counts(counts)
        self._refresh()

    def update_agent(self, snapshot: AgentSnapshot) -> None:
        self._agents[snapshot.name] = snapshot
        self._refresh()

    def _refresh(self) -> None:
        if not self.enabled:
            return
        if self._live is None:
            return
        layout = self._render()
        self._live.update(layout, refresh=True)

    def _render(self) -> RenderableType:
        overview_panel = self._render_overview_panel()
        run_panel = self._render_run_panel()
        history_panel = self._render_history_panel()
        agents_table = self._render_agents()

        top_sections = [
            panel for panel in (overview_panel, run_panel, history_panel) if panel is not None
        ]
        top_sections.append(agents_table)

        top_panel = Panel(Group(*top_sections), title="Sitesync", border_style="#005a69")

        if self._agents:
            self._update_log_tail_lines(top_panel)
            log_panel = self._render_log_panel()
            return Group(top_panel, log_panel)

        return Group(top_panel)

    def _render_header(self) -> Table:
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")

        if self._run_snapshot:
            run = self._run_snapshot
            elapsed_str = str(run.elapsed).split(".")[0]
            log_display = run.log_path
            try:
                log_display = str(Path(run.log_path).resolve().relative_to(Path.cwd()))
            except ValueError:
                log_display = run.log_path
            table.add_row(
                f"run {run.run_id} | source={run.source} | depth={run.depth} "
                f"| parallel={run.parallel_agents}",
                f"elapsed={elapsed_str} | log={log_display}",
            )
        else:
            table.add_row("run pending", "")

        if self._queue_snapshot:
            queue = self._queue_snapshot
            table.add_row(
                f"queue pending={queue.pending} in_progress={queue.in_progress} "
                f"finished={queue.finished}",
                f"errors={queue.errors} exceptions={queue.exceptions_open} "
                f"throughput={queue.throughput_per_minute:.1f}/min",
            )
        else:
            table.add_row("queue stats unavailable", "")

        return table

    def _render_log_panel(self) -> Panel:
        if not self._run_snapshot or not self._run_snapshot.log_path:
            lines: list[str] = []
            error_text: Text | None = Text("Log unavailable", style="dim")
        else:
            log_path = Path(self._run_snapshot.log_path)
            try:
                lines = self._tail_file(log_path, self.log_tail_lines)
                error_text = None
            except OSError as exc:
                lines = []
                error_text = Text(f"Unable to read log: {exc}", style="red")

        if error_text is not None:
            content: Group | Text = error_text
        else:
            lines = lines[-self.log_tail_lines :]
            notice_lines = list(self._notices)
            if notice_lines:
                max_notice = min(len(notice_lines), 2, self.log_tail_lines)
                notice_lines = notice_lines[-max_notice:]
                keep = max(self.log_tail_lines - max_notice, 0)
                lines = lines[-keep:] if keep else []
                if len(lines) < keep:
                    pad = [""] * (keep - len(lines))
                    lines = pad + lines
            elif len(lines) < self.log_tail_lines:
                pad = [""] * (self.log_tail_lines - len(lines))
                lines = pad + lines

            table = Table.grid(padding=(0, 0))
            table.add_column(no_wrap=True, overflow="ellipsis", style="grey70")
            for line in lines:
                table.add_row(line)
            if notice_lines:
                for message in notice_lines:
                    table.add_row(Text(message, style="bold yellow"))
            content = table

        return Panel(content, title="Log Tail", border_style="#005a69")

    @staticmethod
    def _tail_file(path: Path, max_lines: int) -> list[str]:
        if max_lines <= 0 or not path.exists():
            return []

        dq: deque[str] = deque(maxlen=max_lines)
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                dq.append(line.rstrip())
        return list(dq)

    def _render_agents(self) -> Table:
        table = Table(
            expand=True,
            box=box.ROUNDED,
            border_style="grey39",
            header_style="bold grey30",
        )
        table.add_column(
            "AGENT", justify="left", ratio=1, min_width=10, no_wrap=True, overflow="ellipsis"
        )
        table.add_column(
            "STATE", justify="left", ratio=1, min_width=12, no_wrap=True, overflow="ellipsis"
        )
        table.add_column(
            "CURRENT URL",
            justify="left",
            ratio=5,
            min_width=68,
            no_wrap=True,
            overflow="ellipsis",
        )
        table.add_column(
            "LAST STATUS", justify="left", ratio=2, min_width=7, no_wrap=True, overflow="ellipsis"
        )
        table.add_column("FETCHES", justify="right", ratio=1, min_width=7, no_wrap=True)
        table.add_column("RETRIES", justify="right", ratio=1, min_width=8, no_wrap=True)
        table.add_column("ASSETS", justify="right", ratio=1, min_width=7, no_wrap=True)

        agents: Iterable[AgentSnapshot] = self._agents.values()
        for agent in sorted(agents, key=lambda snapshot: snapshot.name):
            status_text = agent.last_status or "-"
            status_text = status_text.replace("\r", " ").replace("\n", " ")
            if len(status_text) > _MAX_STATUS_TEXT_LEN:
                status_text = status_text[: _MAX_STATUS_TEXT_LEN - 3] + "..."

            raw_url = agent.current_url or ""
            display_url = raw_url or "-"
            if raw_url and self._base_url_prefix and raw_url.startswith(self._base_url_prefix):
                trimmed = raw_url[len(self._base_url_prefix) :]
                if not trimmed:
                    display_url = "/"
                else:
                    if not trimmed.startswith("/"):
                        trimmed = "/" + trimmed
                    display_url = trimmed

            table.add_row(
                agent.name,
                agent.state,
                display_url,
                status_text,
                str(agent.fetches),
                str(agent.retries),
                str(agent.assets),
            )

        if not self._agents:
            table.add_row("(no agents)", "-", "-", "-", "0", "0", "0")

        if self._overview_counts:
            total = sum(self._overview_counts.values())
            if total:
                finished = self._overview_counts.get("finished", 0)
                errors = self._overview_counts.get("error", 0)
                remaining = self._overview_counts.get("pending", 0) + self._overview_counts.get(
                    "in_progress", 0
                )
                progress = finished / total * 100
                table.add_row(
                    "TOTAL",
                    f"rem={remaining}",
                    f"{finished}/{total} ({progress:.1f}%)",
                    f"errors={errors}",
                    "",
                    "",
                    "",
                    style="bold grey54",
                )

        return table

    def _render_overview_panel(self) -> Panel | None:
        if not self._overview_counts:
            return None

        counts = self._overview_counts
        total = sum(counts.values())
        finished = counts.get("finished", 0)
        errors = counts.get("error", 0)
        pending = counts.get("pending", 0)
        in_progress = counts.get("in_progress", 0)
        remaining = pending + in_progress

        metrics = "  ".join(
            [
                f"TOTAL: {total}",
                f"FINISHED: {finished}",
                f"REMAINING: {remaining}",
                f"IN-PROGRESS: {in_progress}",
                f"ERRORS: {errors}",
            ]
        )

        grid = Table.grid(padding=(0, 2), expand=True)
        grid.add_column(ratio=1, justify="left")
        entries = [Text(metrics, style="white")]
        if self._escape_hint:
            grid.add_column(width=32, justify="right", no_wrap=True)
            entries.append(Text(self._escape_hint, style="bold yellow"))
            grid.add_row(Text(metrics, style="white"), Text(self._escape_hint, style="bold yellow"))
        else:
            grid.add_row(*entries)

        return Panel(grid, title="Crawl Overview", border_style="grey42")

    def _render_run_panel(self) -> Panel | None:
        if not self._run_summary:
            return None

        summary = self._run_summary
        counts: dict[str, int] = summary.get("counts", {})  # type: ignore[assignment]
        pending = counts.get("pending", 0)
        in_progress = counts.get("in_progress", 0)
        finished = counts.get("finished", 0)
        errors = counts.get("error", 0)

        header = f"Run {summary.get('run_id')}"
        if summary.get("resumed"):
            header += " (resumed)"

        info_grid = Table.grid(expand=True)
        info_grid.add_column(justify="left")
        info_grid.add_column(justify="right")
        info_grid.add_row(
            Text(header, style="bold white"),
            Text(f"started {summary.get('start', '--')}", style="grey70"),
        )

        depth = summary.get("depth")
        parallel = summary.get("parallel")
        info_grid.add_row(
            Text(f"depth budget {depth}", style="grey70"),
            Text(f"parallel {parallel}", style="grey70"),
        )

        log_path = summary.get("log_path", "")
        log_display = ""
        if isinstance(log_path, str) and log_path:
            try:
                log_display = str(Path(log_path).resolve().relative_to(Path.cwd()))
            except ValueError:
                log_display = log_path

        info_grid.add_row(
            Text(
                f"pending {pending}   in-progress {in_progress}   "
                f"finished {finished}   errors {errors}",
                style="grey70",
            ),
            Text(f"log {log_display}" if log_display else "", style="grey58"),
        )

        seed_preview = summary.get("seed_preview")
        seed_more = summary.get("seed_more", 0)
        if seed_preview:
            seeds_text = ", ".join(seed_preview)  # type: ignore[arg-type]
            if seed_more:
                seeds_text += f", … (+{seed_more})"
            info_grid.add_row(
                Text(f"seeds: {seeds_text}", style="grey58"), Text("", style="grey70")
            )

        if self._queue_snapshot:
            queue = self._queue_snapshot
            info_grid.add_row(
                Text(
                    f"queue pending {queue.pending}   in-progress {queue.in_progress}   "
                    f"finished {queue.finished}",
                    style="grey58",
                ),
                Text(
                    f"throughput {queue.throughput_per_minute:.1f}/min",
                    style="grey58",
                ),
            )

        return Panel(info_grid, title="Current Run", border_style="grey42")

    def _render_history_panel(self) -> Panel | None:
        if not self._history:
            return None

        grid = Table.grid(padding=(0, 2))
        for entry in self._history:
            icon = entry.get("icon", "")
            run_id = entry.get("run_id", "")
            finished = entry.get("finished", 0)
            total = entry.get("total", 0)
            start = entry.get("start", "--")
            end = entry.get("end", "--")
            text = Text(
                f"{icon} {run_id}  {finished}/{total}  {start}–{end}",
                style="grey70",
            )
            grid.add_row(text)

        return Panel(grid, title="Recent Runs", border_style="grey42")

    @staticmethod
    def _metric_text(label: str, value: int) -> Text:
        return Text(f"{label}: {value}", style="white" if value else "grey58")

    def show_escape_hint(self, message: str) -> None:
        self._escape_hint = message
        self._refresh()

    def clear_escape_hint(self) -> None:
        if self._escape_hint is not None:
            self._escape_hint = None
            self._refresh()

    def add_notice(self, message: str) -> None:
        self._notices.append(message)
        self._refresh()

    def _update_log_tail_lines(self, renderable: RenderableType) -> None:
        if not self.enabled:
            return

        height = getattr(self.console.size, "height", None)
        width = getattr(self.console.size, "width", None)
        if not height or not width:
            return

        options = self.console.options
        segments = list(self.console.render(renderable, options))
        lines_iter = Segment.split_and_crop_lines(segments, width, pad=True)
        lines = list(lines_iter)
        top_height = len(lines)
        available = max(height - top_height - 2, 5)
        self.log_tail_lines = available

    def update_overview(self, counts: dict[str, int]) -> None:
        self._overview_counts = dict(counts)
        self._refresh()

    def update_run_summary(self, summary: dict[str, object]) -> None:
        current_id = self._run_summary.get("run_id") if self._run_summary else None
        new_id = summary.get("run_id")
        if current_id != new_id:
            self._base_url_prefix = None

        self._run_summary = dict(summary)
        self._maybe_update_base_prefix(summary)
        self._refresh()

    def update_history(self, history: list[dict[str, object]]) -> None:
        self._history = list(history)
        self._refresh()

    def update_run_counts(self, counts: dict[str, int]) -> None:
        if self._run_summary is None:
            return

        summary = dict(self._run_summary)
        summary["counts"] = dict(counts)
        self._run_summary = summary
        self._refresh()

    def _maybe_update_base_prefix(self, summary: dict[str, object]) -> None:
        if self._base_url_prefix:
            return

        raw_seeds = summary.get("seed_preview") or summary.get("seed_urls")
        if not raw_seeds or not isinstance(raw_seeds, list):
            return

        for seed in raw_seeds:
            if not isinstance(seed, str):
                continue
            parsed = urlparse(seed)
            if parsed.scheme and parsed.netloc:
                prefix = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
                self._base_url_prefix = prefix
                break

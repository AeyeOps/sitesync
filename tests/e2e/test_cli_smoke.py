"""End-to-end CLI smoke tests.

These tests intentionally stay short and verbose. They exercise:
- interactive config generation (`sitesync init`) with a directory destination
- crawl runs with no queued work (run should still be marked completed + metadata written)
- CLI parsing guardrails for depth/parallel
- a few URL variants against a local HTTP server
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@dataclass(frozen=True)
class CommandResult:
    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class E2EContext:
    root: Path
    base_url: str
    sitesync: tuple[str, ...]
    keep_tmp: bool


@dataclass(frozen=True)
class WorkDir:
    path: Path
    config_path: Path
    output_root: Path
    metadata_dir: Path
    db_path: Path
    log_path: Path
    base_cmd: tuple[str, ...]


def _format_cmd(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in args)


def _combined_output(result: CommandResult) -> str:
    parts: list[str] = []
    if result.stdout:
        parts.append("stdout:\n" + result.stdout.rstrip())
    if result.stderr:
        parts.append("stderr:\n" + result.stderr.rstrip())
    return "\n\n".join(parts).strip()


def run_cli(
    args: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    input_text: str = "",
    timeout_seconds: float = 30.0,
    echo_stdout: bool = True,
    echo_stderr: bool = True,
) -> CommandResult:
    print(f"+ {_format_cmd(args)}", flush=True)
    completed = subprocess.run(
        list(args),
        check=False,
        cwd=str(cwd),
        env=dict(env) if env is not None else None,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )
    if echo_stdout and completed.stdout:
        print(completed.stdout.rstrip(), flush=True)
    if echo_stderr and completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr, flush=True)
    return CommandResult(
        args=tuple(args),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def expect_ok(result: CommandResult) -> None:
    if result.returncode != 0:
        raise AssertionError(
            f"Command failed (rc={result.returncode}): {_format_cmd(result.args)}\n\n"
            f"{_combined_output(result)}"
        )


def expect_fail(result: CommandResult) -> None:
    if result.returncode == 0:
        raise AssertionError(
            f"Command unexpectedly succeeded: {_format_cmd(result.args)}\n\n"
            f"{_combined_output(result)}"
        )


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _extract_error_line(result: CommandResult) -> str:
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    combined = _strip_ansi(combined)
    lines = [line.strip() for line in combined.splitlines() if line.strip()]
    for needle in (
        "Invalid value for",
        "No such option",
        "Error:",
        "Traceback (most recent call last)",
    ):
        for line in lines:
            if needle in line:
                return line.strip("│").strip()
    return (lines[-1] if lines else "").strip("│").strip()


def latest_run_id(db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT COALESCE(MAX(id), 0) FROM runs").fetchone()
        if row is None:
            raise RuntimeError("Unable to find runs table in database.")
        return int(row[0])


def read_run_metadata(metadata_dir: Path, run_id: int) -> dict:
    path = metadata_dir / f"run-{run_id}.json"
    if not path.exists():
        raise RuntimeError(f"Missing metadata file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class _TestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "SitesyncTestHTTP/1.0"

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path in ("/", "/index.html"):
            body = "<html><head><title>Sitesync OK</title></head><body>Hello world</body></html>"
            self.send_response(200)
        elif self.path.startswith("/private"):
            body = "<html><head><title>Denied</title></head><body>Access denied</body></html>"
            self.send_response(403)
        else:
            body = "<html><body>Not found</body></html>"
            self.send_response(404)

        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib API
        message = format % args if args else format
        client = self.client_address[0] if self.client_address else "-"
        command = getattr(self, "command", "-")
        path = getattr(self, "path", "-")
        print(f"[http] {client} {command} {path} -> {message}", flush=True)


@contextlib.contextmanager
def local_http_server() -> Iterable[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=2.0)


def write_config(*, path: Path, output_root: Path, database_path: Path, log_path: Path) -> None:
    yaml_text = f"""\
version: 1
default_source: default

logging:
  path: {log_path}
  level: info

storage:
  path: {database_path}

outputs:
  base_path: {output_root}
  raw_subdir: raw
  normalized_subdir: normalized
  metadata_subdir: runs

crawler:
  parallel_agents: 1
  pages_per_agent: 1
  jitter_seconds: 0.0
  heartbeat_seconds: 10.0
  max_retries: 1
  backoff_min_seconds: 0.1
  backoff_max_seconds: 0.2
  backoff_multiplier: 2.0

sources:
  - name: default
    start_urls: []
    allowed_domains: {{}}
    depth: 1
    fetcher: playwright
    fetcher_options:
      headless: true
      navigation_timeout: 10.0
      wait_until: domcontentloaded
      wait_after_load: 0.1
"""
    path.write_text(yaml_text, encoding="utf-8")


def _assert_playwright_ready() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"Playwright is not importable: {exc}")

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(
            "Playwright browsers are missing or not runnable; "
            f"run `uv run playwright install chromium`.\n{exc}"
        )


def _safe_path_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned or "case"


@pytest.fixture(scope="module")
def e2e_context() -> Iterable[E2EContext]:
    sitesync = (sys.executable, "-m", "sitesync")
    print(f"Sitesync e2e smoke using: {_format_cmd(sitesync)}", flush=True)

    keep_tmp = os.environ.get("SITESYNC_E2E_KEEP_TMP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    root = Path(tempfile.mkdtemp(prefix="sitesync-e2e-"))
    if keep_tmp:
        print(f"Working directory (kept): {root}", flush=True)
    else:
        print(f"Working directory: {root}", flush=True)

    try:
        with local_http_server() as base_url:
            print(f"Local test server: {base_url}", flush=True)
            yield E2EContext(root=root, base_url=base_url, sitesync=sitesync, keep_tmp=keep_tmp)
    finally:
        if not keep_tmp:
            shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def e2e_work_dir(e2e_context: E2EContext, request: pytest.FixtureRequest) -> WorkDir:
    name = _safe_path_component(request.node.name)
    work_dir = e2e_context.root / name
    work_dir.mkdir(parents=True, exist_ok=True)

    output_root = work_dir / "out"
    db_path = work_dir / "sitesync.sqlite"
    log_path = work_dir / "sitesync.log"
    config_path = work_dir / "config.yaml"
    write_config(
        path=config_path,
        output_root=output_root,
        database_path=db_path,
        log_path=log_path,
    )

    metadata_dir = output_root / "runs"
    base_cmd = (*e2e_context.sitesync, "--config", str(config_path))
    return WorkDir(
        path=work_dir,
        config_path=config_path,
        output_root=output_root,
        metadata_dir=metadata_dir,
        db_path=db_path,
        log_path=log_path,
        base_cmd=base_cmd,
    )


@pytest.fixture(scope="module")
def playwright_ready() -> None:
    _assert_playwright_ready()


def test_crawl_no_seed_urls_completes_and_writes_metadata(e2e_work_dir: WorkDir) -> None:
    result = run_cli(
        (*e2e_work_dir.base_cmd, "crawl"),
        cwd=e2e_work_dir.path,
        input_text="",
        timeout_seconds=60.0,
    )
    expect_ok(result)

    run_id = latest_run_id(e2e_work_dir.db_path)
    meta = read_run_metadata(e2e_work_dir.metadata_dir, run_id)
    assert meta.get("run", {}).get("status") == "completed"


@pytest.mark.parametrize(
    ("extra_args", "expected_substrings"),
    [
        (("--depth", "-1"), ("Invalid value for", "--depth")),
        (("--depth", "x"), ("Invalid value for", "--depth")),
        (("--depth", ""), ("Invalid value for", "--depth")),
        (("--parallel", "-1"), ("Invalid value for", "--parallel")),
        (("--parallel", "0"), ("Invalid value for", "--parallel")),
        (("--parallel", "999999"), ("Invalid value for", "--parallel")),
    ],
    ids=[
        "depth-negative",
        "depth-non-int",
        "depth-empty",
        "parallel-negative",
        "parallel-zero",
        "parallel-extravagant",
    ],
)
def test_crawl_rejects_invalid_cli_values(
    e2e_work_dir: WorkDir,
    extra_args: Sequence[str],
    expected_substrings: Sequence[str],
) -> None:
    result = run_cli(
        (*e2e_work_dir.base_cmd, "crawl", *extra_args),
        cwd=e2e_work_dir.path,
        input_text="",
        timeout_seconds=10.0,
        echo_stdout=False,
        echo_stderr=False,
    )
    expect_fail(result)
    message = _extract_error_line(result)
    print(f"expected: {message}", flush=True)
    for needle in expected_substrings:
        assert needle in message


@pytest.mark.parametrize(
    ("label", "url_builder", "assertion"),
    [
        (
            "http-200",
            lambda base: f"{base}/",
            lambda tasks, work: (
                int(tasks.get("finished", 0)) == 1
                and int(tasks.get("error", 0)) == 0
                and any((work.output_root / "raw").glob("*.html"))
                and any((work.output_root / "normalized").glob("*.txt"))
            ),
        ),
        (
            "http-403",
            lambda base: f"{base}/private",
            lambda tasks, work: int(tasks.get("finished", 0)) == 1,
        ),
        (
            "missing-scheme",
            lambda base: base.removeprefix("http://") + "/",
            lambda tasks, work: (
                int(tasks.get("finished", 0)) == 1 or int(tasks.get("error", 0)) == 1
            ),
        ),
        (
            "invalid-url",
            lambda base: "ht!tp://bad",
            lambda tasks, work: int(tasks.get("error", 0)) == 1,
        ),
        (
            "https-unreachable",
            lambda base: "https://127.0.0.1:1/",
            lambda tasks, work: int(tasks.get("error", 0)) == 1,
        ),
    ],
    ids=["http-200", "http-403", "missing-scheme", "invalid-url", "https-unreachable"],
)
def test_crawl_url_variants(
    e2e_context: E2EContext,
    e2e_work_dir: WorkDir,
    playwright_ready: None,
    label: str,
    url_builder,
    assertion,
) -> None:
    start_url = url_builder(e2e_context.base_url)
    print(f"\nCase: crawl {label} -> {start_url}", flush=True)

    result = run_cli(
        (
            *e2e_work_dir.base_cmd,
            "crawl",
            "--start-url",
            start_url,
            "--depth",
            "1",
            "--parallel",
            "1",
        ),
        cwd=e2e_work_dir.path,
        input_text="",
        timeout_seconds=120.0,
    )
    expect_ok(result)

    run_id = latest_run_id(e2e_work_dir.db_path)
    meta = read_run_metadata(e2e_work_dir.metadata_dir, run_id)
    tasks = meta.get("stats", {}).get("tasks", {})
    assert meta.get("run", {}).get("status") == "completed"
    assert assertion(tasks, e2e_work_dir), f"Unexpected task counts for {label}: {tasks}"

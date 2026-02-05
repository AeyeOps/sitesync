"""Playwright-based fetcher implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
import sys
import time
from typing import Any, ClassVar, Optional

from sitesync.core.executor import FetchError, FetchResult, Fetcher, TransientFetchError
from sitesync.storage import TaskRecord


@dataclass(slots=True)
class PlaywrightFetcher(Fetcher):
    """Fetch pages using Playwright."""

    logger: logging.Logger
    browser: str = "chromium"
    headless: bool = True
    navigation_timeout: float = 30.0
    wait_after_load: float = 1.5
    wait_until: str = "networkidle"
    wait_for_selector: Optional[str] = None
    wait_for_selector_timeout: float = 5.0
    raw_dir: Path = field(default_factory=lambda: Path.cwd() / "data" / "raw")
    normalized_dir: Optional[Path] = None
    capture_screenshot: bool = False
    screenshot_format: str = "png"
    _install_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _installed_browsers: ClassVar[set[str]] = set()

    async def fetch(self, task: TaskRecord) -> FetchResult:
        raw_path: Optional[Path] = None
        checksum: Optional[str] = None
        metadata_json: Optional[str] = None
        screenshot_path: Optional[Path] = None
        html: Optional[str] = None
        browser_handle: Optional[Any] = None
        context_handle: Optional[Any] = None
        started_at = time.monotonic()
        self.logger.debug(
            "Fetch start url=%s wait_until=%s timeout=%ss",
            task.url,
            self.wait_until,
            self.navigation_timeout,
        )
        try:
            from playwright.async_api import async_playwright
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise FetchError(
                "Playwright is not installed. Run `uv sync` with dependencies."
            ) from exc

        self.logger.debug("Launching Playwright to fetch %s", task.url)

        try:
            async with async_playwright() as playwright:
                try:
                    browser_type = getattr(playwright, self.browser)
                except AttributeError as exc:  # pragma: no cover - invalid config
                    raise FetchError(f"Unsupported browser type: {self.browser}") from exc

                browser_handle = await self._launch_browser(browser_type)
                context_handle = await browser_handle.new_context()
                page = await context_handle.new_page()
                page_url = task.url

                try:
                    try:
                        response = await page.goto(
                            task.url,
                            wait_until=self.wait_until,
                            timeout=int(self.navigation_timeout * 1000),
                        )
                        status = response.status if response is not None else 0
                        page_url = page.url or task.url
                        self.logger.debug("Fetch goto done url=%s status=%s", page_url, status)
                        self.logger.debug("Fetched %s with status %s", page_url, status)

                        if self.wait_for_selector:
                            try:
                                await page.wait_for_selector(
                                    self.wait_for_selector,
                                    timeout=int(self.wait_for_selector_timeout * 1000),
                                )
                            except PlaywrightTimeoutError:
                                self.logger.warning(
                                    "Selector '%s' not found within %.1fs for %s",
                                    self.wait_for_selector,
                                    self.wait_for_selector_timeout,
                                    task.url,
                                )

                        if self.wait_after_load > 0:
                            await asyncio.sleep(self.wait_after_load)

                        html = await page.content()
                        self.logger.debug(
                            "Fetch content captured url=%s bytes=%s",
                            page_url,
                            len(html) if html is not None else 0,
                        )

                        metadata = {
                            "url": page_url,
                            "status": status,
                            "title": await page.title(),
                        }
                        metadata_json = json.dumps(metadata)

                    except PlaywrightTimeoutError as exc:
                        self.logger.warning(
                            "Fetch timeout url=%s wait_until=%s timeout=%ss",
                            task.url,
                            self.wait_until,
                            self.navigation_timeout,
                        )
                        raise TransientFetchError(f"Timeout while fetching {task.url}") from exc
                    finally:
                        if html is not None:
                            filename = f"{sha256(page_url.encode('utf-8')).hexdigest()}.html"
                            raw_path = self.raw_dir / filename
                            raw_path.write_text(html, encoding="utf-8")

                            checksum = sha256(html.encode("utf-8")).hexdigest()

                        if (
                            html is not None
                            and self.capture_screenshot
                            and self.normalized_dir is not None
                        ):
                            screenshot_name = f"{raw_path.stem}.{self.screenshot_format}"
                            screenshot_path = self.normalized_dir / screenshot_name
                            await page.screenshot(path=str(screenshot_path), full_page=True)
                finally:
                    if context_handle is not None:
                        await context_handle.close()
                    if browser_handle is not None:
                        await browser_handle.close()

        except TransientFetchError:
            raise
        except FetchError:
            raise
        except Exception as exc:  # pylint: disable=broad-except
            msg = str(exc)
            permanent_patterns = ("Download is starting",)
            if any(pattern in msg for pattern in permanent_patterns):
                self.logger.warning(
                    "Fetch permanent failure url=%s error=%s", task.url, exc
                )
                raise FetchError(
                    f"Non-fetchable URL {task.url}: {exc}"
                ) from exc
            self.logger.warning("Fetch failed url=%s error=%s", task.url, exc)
            raise TransientFetchError(f"Playwright error for {task.url}: {exc}") from exc

        elapsed = time.monotonic() - started_at
        self.logger.debug("Fetch complete url=%s elapsed=%.2fs", task.url, elapsed)

        return FetchResult(
            assets_created=1,
            raw_payload_path=str(raw_path) if raw_path else None,
            normalized_payload_path=str(screenshot_path) if screenshot_path else None,
            checksum=checksum,
            asset_type="page",
            metadata_json=metadata_json,
        )

    @classmethod
    def from_options(
        cls, logger: logging.Logger, *, options: Optional[dict[str, Any]] = None
    ) -> "PlaywrightFetcher":
        options = options or {}

        if getattr(sys, "frozen", False):
            browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
            if not browsers_path or browsers_path == "0":
                cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
                browsers_dir = cache_root / "ms-playwright"
                browsers_dir.mkdir(parents=True, exist_ok=True)
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)

        raw_dir_value = options.pop("raw_dir", None)
        normalized_dir_value = options.pop("normalized_dir", None)

        raw_dir = Path(raw_dir_value) if raw_dir_value is not None else Path.cwd() / "data/raw"
        normalized_dir = Path(normalized_dir_value) if normalized_dir_value is not None else None

        raw_dir.mkdir(parents=True, exist_ok=True)
        if normalized_dir is not None:
            normalized_dir.mkdir(parents=True, exist_ok=True)

        return cls(logger=logger, raw_dir=raw_dir, normalized_dir=normalized_dir, **options)

    async def _launch_browser(self, browser_type) -> Any:
        try:
            return await browser_type.launch(headless=self.headless)
        except Exception as exc:  # pylint: disable=broad-except
            if "Executable doesn't exist" in str(exc):
                browser_label = getattr(browser_type, "name", self.browser)
                await self._ensure_browsers_installed(browser_label)
                return await browser_type.launch(headless=self.headless)
            raise

    async def _ensure_browsers_installed(self, browser_name: str) -> None:
        async with self._install_lock:
            if browser_name in self._installed_browsers:
                return

            self.logger.info("Playwright browser '%s' missing; installing...", browser_name)
            try:
                from playwright._impl._driver import compute_driver_executable, get_driver_env
            except ImportError as exc:  # pragma: no cover - runtime dependency
                raise FetchError("Playwright is not installed.") from exc

            try:
                driver_executable, driver_cli = compute_driver_executable()
            except Exception as exc:  # pragma: no cover - defensive
                raise TransientFetchError(
                    "Unable to locate Playwright driver; run `playwright install` manually."
                ) from exc

            process = await asyncio.create_subprocess_exec(
                driver_executable,
                driver_cli,
                "install",
                browser_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=get_driver_env(),
            )
            stdout_bytes, stderr_bytes = await process.communicate()
            stdout = stdout_bytes.decode().strip()
            stderr = stderr_bytes.decode().strip()

            if process.returncode != 0:
                self.logger.error(
                    "Playwright install failed (%s): %s", browser_name, stderr or stdout
                )
                raise TransientFetchError(
                    "Unable to install Playwright browsers automatically; run `playwright install` manually."
                )

            if stdout:
                self.logger.debug("Playwright install output: %s", stdout)
            if stderr:
                self.logger.debug("Playwright install warnings: %s", stderr)

            self._installed_browsers.add(browser_name)
            self.logger.info("Playwright browser '%s' installed successfully.", browser_name)

"""Placeholder fetcher used until Playwright integration is complete."""

from __future__ import annotations

import asyncio

from sitesync.core.executor import Fetcher, FetchResult, TransientFetchError
from sitesync.storage import TaskRecord

_SIMULATED_BACKOFF_THRESHOLD = 3


class NullFetcher(Fetcher):
    """Minimal fetcher that simulates success without network access."""

    async def fetch(self, task: TaskRecord) -> FetchResult:
        await asyncio.sleep(0)
        if task.attempt_count > _SIMULATED_BACKOFF_THRESHOLD:
            raise TransientFetchError("simulated backoff")
        return FetchResult(assets_created=0)

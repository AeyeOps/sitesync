"""Fetcher implementations for Sitesync."""

from .null import NullFetcher
from .playwright import PlaywrightFetcher

__all__ = ["NullFetcher", "PlaywrightFetcher"]

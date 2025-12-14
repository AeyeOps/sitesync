"""Grep utility for searching asset content."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sitesync.storage.db import Database, GrepMatch


def grep_file(
    path: Path,
    pattern: str,
    regex: bool = False,
    case_sensitive: bool = False,
    context: int = 0,
) -> Iterator[tuple[int, str, list[str], list[str]]]:
    """
    Search a file for pattern matches.

    Yields (line_no, line, context_before, context_after) for each match.
    Line numbers are 1-indexed.

    Skips files that fail UTF-8 decode (binary files).
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return

    lines = content.splitlines()
    if not lines:
        return

    # Compile pattern and define match function
    flags = 0 if case_sensitive else re.IGNORECASE
    if regex:
        try:
            compiled = re.compile(pattern, flags)
        except re.error:
            return  # Invalid regex - skip

        def match_fn(line: str) -> bool:
            return compiled.search(line) is not None
    elif case_sensitive:

        def match_fn(line: str) -> bool:
            return pattern in line
    else:
        pattern_lower = pattern.lower()

        def match_fn(line: str) -> bool:
            return pattern_lower in line.lower()

    # Sliding window for context
    before_buffer: deque[str] = deque(maxlen=context) if context > 0 else deque(maxlen=0)

    for i, line in enumerate(lines):
        line_no = i + 1

        if match_fn(line):
            # Gather context before
            ctx_before = list(before_buffer)

            # Gather context after
            ctx_after = []
            if context > 0:
                for j in range(i + 1, min(i + 1 + context, len(lines))):
                    ctx_after.append(lines[j])

            yield (line_no, line, ctx_before, ctx_after)

        if context > 0:
            before_buffer.append(line)


def grep_source(
    database: Database,
    source: str,
    pattern: str,
    regex: bool = False,
    case_sensitive: bool = False,
    raw: bool = False,
    context: int = 0,
    max_matches: int | None = None,
) -> Iterator[GrepMatch]:
    """
    Search all assets in a source for pattern matches.

    Args:
        database: Database instance
        source: Source name to search
        pattern: Search pattern (literal or regex)
        regex: Interpret pattern as regex
        case_sensitive: Case sensitive matching
        raw: Search raw content instead of normalized
        context: Number of context lines before/after
        max_matches: Stop after this many matches

    Yields GrepMatch for each match found.
    """
    from sitesync.storage.db import GrepMatch

    match_count = 0

    for asset_id, url, raw_path, normalized_path in database.get_asset_paths_for_source(source):
        # Select path based on raw flag
        path_str = raw_path if raw else normalized_path
        if not path_str:
            # Fall back to other path if preferred not available
            path_str = normalized_path if raw else raw_path
        if not path_str:
            continue

        path = Path(path_str)
        if not path.exists():
            continue

        for line_no, line, ctx_before, ctx_after in grep_file(
            path, pattern, regex=regex, case_sensitive=case_sensitive, context=context
        ):
            yield GrepMatch(
                source=source,
                asset_id=asset_id,
                url=url,
                path=str(path),
                line_no=line_no,
                line=line,
                context_before=ctx_before,
                context_after=ctx_after,
            )

            match_count += 1
            if max_matches and match_count >= max_matches:
                return


def grep_all_sources(
    database: Database,
    pattern: str,
    regex: bool = False,
    case_sensitive: bool = False,
    raw: bool = False,
    context: int = 0,
    max_matches: int | None = None,
) -> Iterator[GrepMatch]:
    """
    Search all sources for pattern matches.

    Same arguments as grep_source, but searches across all sources.
    """
    match_count = 0

    for source_summary in database.list_sources():
        for match in grep_source(
            database,
            source_summary.name,
            pattern,
            regex=regex,
            case_sensitive=case_sensitive,
            raw=raw,
            context=context,
            max_matches=max_matches - match_count if max_matches else None,
        ):
            yield match
            match_count += 1
            if max_matches and match_count >= max_matches:
                return

"""Keyboard hotkey utilities for the Sitesync CLI."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import Callable
from threading import Event
from typing import TextIO


async def monitor_double_escape(
    stop_event: asyncio.Event,
    *,
    timeout: float = 1.5,
    on_single: Callable[[], None] | None = None,
    on_timeout: Callable[[], None] | None = None,
    on_double: Callable[[], None] | None = None,
) -> bool:
    """Watch for a quick double-press of the Escape key and signal ``stop_event``.

    The watcher runs in a background thread so that the main asyncio loop remains
    responsive. When the user presses Escape twice within ``timeout`` seconds the
    function sets ``stop_event`` (if not already set) and returns ``True``. If the
    terminal is not interactive, the watcher simply waits for ``stop_event`` and
    returns ``False``.
    """

    loop = asyncio.get_running_loop()
    thread_stop = Event()

    stream: TextIO | None = None
    close_stream = False

    if sys.platform != "win32":
        if sys.stdin.isatty():
            stream = sys.stdin
        else:
            try:
                stream = open("/dev/tty", encoding="utf-8", errors="ignore")  # noqa: SIM115
                close_stream = True
            except OSError:
                await stop_event.wait()
                return False

    async def propagate_stop() -> None:
        await stop_event.wait()
        thread_stop.set()

    propagate_task = asyncio.create_task(propagate_stop())

    def _threadsafe(callback: Callable[[], None] | None) -> Callable[[], None] | None:
        if callback is None:
            return None

        def wrapped() -> None:
            loop.call_soon_threadsafe(callback)

        return wrapped

    thread_on_single = _threadsafe(on_single)
    thread_on_timeout = _threadsafe(on_timeout)
    thread_on_double = _threadsafe(on_double)

    def worker() -> bool:
        return _monitor_double_escape(
            thread_stop,
            timeout,
            stream,
            thread_on_single,
            thread_on_timeout,
            thread_on_double,
        )

    try:
        triggered = await loop.run_in_executor(None, worker)
        if triggered:
            loop.call_soon_threadsafe(stop_event.set)
        return triggered
    finally:
        thread_stop.set()
        if close_stream and stream is not None:
            stream.close()
        with contextlib.suppress(asyncio.CancelledError):
            await propagate_task


def _monitor_double_escape(
    stop_flag: Event,
    timeout: float,
    stream: TextIO | None,
    on_single: Callable[[], None] | None,
    on_timeout: Callable[[], None] | None,
    on_double: Callable[[], None] | None,
) -> bool:
    """Blocking helper executed in a background thread."""

    last_press: float | None = None

    if sys.platform == "win32":  # Windows
        try:
            import msvcrt
        except ImportError:  # pragma: no cover - extremely rare
            return False

        while not stop_flag.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch == "\x1b":
                    now = time.monotonic()
                    if last_press is not None and (now - last_press) <= timeout:
                        if on_double is not None:
                            on_double()
                        return True
                    last_press = now
                    if on_single is not None:
                        on_single()
                else:
                    if last_press is not None and on_timeout is not None:
                        on_timeout()
                    last_press = None
            else:
                if last_press is not None and (time.monotonic() - last_press) > timeout:
                    last_press = None
                    if on_timeout is not None:
                        on_timeout()
                time.sleep(0.05)
        return False

    # POSIX terminals
    import select
    import termios
    import tty

    source = stream or sys.stdin
    fd = source.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:  # pragma: no cover - non-interactive
        stop_flag.wait()
        return False

    try:
        tty.setcbreak(fd)

        pending_char: str | None = None

        def _consume_escape_sequence() -> bool:
            """Consume the remainder of an escape sequence (e.g., arrow keys).

            Returns True if additional bytes were consumed, indicating that the
            initial ESC was part of a multi-byte sequence and should not count
            as a standalone Escape key press.
            """

            nonlocal pending_char

            consumed = False
            try:
                # Check quickly for the next byte; many CSI sequences begin with '['.
                if select.select([source], [], [], 0.01)[0]:
                    nxt = source.read(1)
                    if not nxt:
                        return consumed
                    consumed = True
                    if nxt in ("[", "O"):
                        # Consume the rest of the control sequence (digits, semicolons, final byte).
                        while True:
                            if not select.select([source], [], [], 0.01)[0]:
                                break
                            ch_inner = source.read(1)
                            if not ch_inner:
                                break
                            consumed = True
                            # Final byte of CSI sequences is typically in the ASCII range @ to ~.
                            if "@" <= ch_inner <= "~":
                                break
                    else:
                        # Not a CSI sequence; make it available for the next iteration.
                        pending_char = nxt
                        return False
            except (OSError, ValueError):  # pragma: no cover - defensive
                return consumed
            return consumed

        while not stop_flag.is_set():
            rlist, _, _ = select.select([source], [], [], 0.1)
            now = time.monotonic()
            if last_press is not None and (now - last_press) > timeout:
                last_press = None
                if on_timeout is not None:
                    on_timeout()

            if pending_char is None and not rlist:
                continue
            if pending_char is not None:
                ch = pending_char
                pending_char = None
            else:
                ch = source.read(1)
            if ch == "\x1b":
                now = time.monotonic()
                if _consume_escape_sequence():
                    last_press = None
                    if on_timeout is not None:
                        on_timeout()
                    continue
                if last_press is not None and (now - last_press) <= timeout:
                    if on_double is not None:
                        on_double()
                    return True
                last_press = now
                if on_single is not None:
                    on_single()
            else:
                if last_press is not None and on_timeout is not None:
                    on_timeout()
                last_press = None
        return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

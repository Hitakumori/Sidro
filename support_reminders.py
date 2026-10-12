"""Local runtime tracking for voluntary Sidro support reminders.

Schedules random reminder intervals, persists elapsed app time and handles
deferral/restart behavior. It performs no membership checks or analytics.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import random
import tempfile
import time


SUPPORT_URL = "https://www.patreon.com/cw/Hitakumori"
MIN_INTERVAL_SECONDS = 2 * 60 * 60
MAX_INTERVAL_SECONDS = 4 * 60 * 60


def format_usage_time(seconds: float) -> str:
    minutes = max(1, int(seconds // 60))
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " and ".join(parts)


class SupportReminders:
    """Count local runtime across launches; never contact a service or limit actions."""

    def __init__(self, path: Path, *, clock=time.monotonic, interval=random.uniform):
        self.path = Path(path)
        self._clock = clock
        self._interval = interval
        self._last_tick = clock()
        self.total_seconds = 0.0
        self.next_reminder_at = self._next_interval()
        try:
            if self.path.stat().st_size > 4096:
                raise ValueError("Oversized reminder state")
            data = json.loads(self.path.read_text(encoding="utf-8"))
            total, next_at = data["total_seconds"], data["next_reminder_at"]
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value < 0 for value in (total, next_at)):
                raise ValueError("Invalid reminder state")
            self.total_seconds = float(total)
            # A due reminder stays due across restarts, without repeated startup prompts.
            self.next_reminder_at = min(float(next_at), self.total_seconds + MAX_INTERVAL_SECONDS)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            pass

    def _next_interval(self) -> float:
        return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS,
                   float(self._interval(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS))))

    def tick(self, *, can_show: bool = True) -> str | None:
        now = self._clock()
        elapsed = now - self._last_tick
        self._last_tick = now
        # Called every 30 seconds. Do not count a suspended PC or long event-loop stall.
        if 0 <= elapsed <= 90:
            self.total_seconds += elapsed
        message = None
        if can_show and self.total_seconds >= self.next_reminder_at:
            message = (f"You have been using this app for {format_usage_time(self.total_seconds)}, "
                       "please consider supporting me from Patreon 🥹")
            self.next_reminder_at = self.total_seconds + self._next_interval()
        self.save()
        return message

    def save(self) -> None:
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".support-reminder-", suffix=".tmp",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                json.dump({"total_seconds": self.total_seconds,
                           "next_reminder_at": self.next_reminder_at}, handle)
            os.replace(temporary, self.path)
        except OSError:
            # A reminder must never interrupt scanning because local storage is unavailable.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

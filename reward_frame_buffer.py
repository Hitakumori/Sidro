"""Bounded frame handoff between reward capture and OCR workers.

Keeps recent owned frame copies available while OCR is busy, avoiding an
unbounded processing queue in the reward-price workflow.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable

import numpy as np


class RewardFrameBuffer:
    """Capture without OCR; retain at most two owned, immutable frame copies."""

    def __init__(self, grabber_factory: Callable, reward_layout_present: Callable,
                 initial_frame: np.ndarray, interval: float = 0.1):
        self._grabber_factory = grabber_factory
        self._reward_layout_present = reward_layout_present
        self._interval = interval
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._failed = False
        self._latest = initial_frame.copy()
        self._latest_reward = self._latest
        self._thread = threading.Thread(target=self._run, name="reward-capture", daemon=True)

    def start(self):
        self._thread.start()

    def latest_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._failed else self._latest

    def latest_reward_frame(self) -> np.ndarray | None:
        with self._lock:
            return None if self._failed else self._latest_reward

    def stop(self):
        self._stop.set()
        self._thread.join()

    def _run(self):
        grabber = None
        try:
            # Construct MSS's thread-local handle here, not in the OCR thread.
            grabber = self._grabber_factory()
            while not self._stop.is_set():
                started = time.monotonic()
                frame = grabber.capture(preprocess=False, warn_on_failure=False)
                if frame is not None:
                    frame = frame.copy()
                    is_reward = self._reward_layout_present(frame)
                    with self._lock:
                        self._latest = frame
                        if is_reward:
                            self._latest_reward = frame
                # DXGI can return None when the screen has not changed.
                if self._stop.wait(max(0.0, self._interval - (time.monotonic() - started))):
                    break
        except Exception as exc:
            with self._lock:
                self._failed = True
            print(f"[Reward] Background capture failed; final selection needs confirmation: {exc}")
        finally:
            if grabber is not None:
                try:
                    grabber.close()
                except Exception as exc:
                    print(f"[Reward] Background capture cleanup failed: {exc}")

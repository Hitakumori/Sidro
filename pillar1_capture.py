"""
pillar1_capture.py — Screen Capture & Region of Interest
=========================================================
Uses dxcam (Windows Graphics Capture / DXGI) as primary engine for:
  • True Borderless Fullscreen support (bypasses GDI black-frame issue)
  • Hardware-accelerated capture — minimal CPU overhead
  • <5ms latency on modern GPUs

Falls back to `mss` for compatibility / non-DXGI scenarios.
"""

from __future__ import annotations
import time
import threading
import numpy as np
from typing import Optional, Tuple
import cv2

import config

# Type alias
Frame = np.ndarray  # BGR uint8 array
ROI = Tuple[int, int, int, int]  # (left, top, width, height)


# ──────────────────────────────────────────────
# ENGINE ABSTRACTION
# ──────────────────────────────────────────────

class CaptureEngine:
    """Base class — swappable backend."""

    def grab(self, roi: ROI) -> Optional[Frame]:
        raise NotImplementedError

    def close(self):
        pass


class DxCamEngine(CaptureEngine):
    """
    Primary engine using dxcam (DirectX / Windows Graphics Capture).
    Supports borderless fullscreen and hardware-accelerated games.

    Install: pip install dxcam
    """

    _lock = threading.Lock()
    _shared_continuous_region = None
    _shared_continuous_active = False

    def __init__(self):
        import dxcam
        # device_idx=0 targets the primary GPU/monitor
        self._cam = dxcam.create(output_color="BGR")
        print("[Capture] dxcam engine initialized.")

    def grab(self, roi: ROI) -> Optional[Frame]:
        left, top, width, height = roi
        region = (left, top, left + width, top + height)  # dxcam uses (L,T,R,B)
        if getattr(config, "CAPTURE_CONTINUOUS_ENABLED", False):
            try:
                return self._grab_continuous(region)
            except Exception as e:
                print(f"[Capture] continuous dxcam failed ({e}); using one-shot grab.")
        with self._lock:
            frame = self._cam.grab(region=region)
        return frame  # None if no new frame since last grab (vsync)

    def _grab_continuous(self, region: tuple[int, int, int, int]) -> Optional[Frame]:
        with self._lock:
            if DxCamEngine._shared_continuous_region != region:
                if DxCamEngine._shared_continuous_active:
                    self._cam.stop()
                    DxCamEngine._shared_continuous_active = False
                self._cam.start(
                    region=region,
                    target_fps=int(getattr(config, "CAPTURE_FPS_TARGET", 10)),
                    video_mode=True,
                )
                DxCamEngine._shared_continuous_region = region
                DxCamEngine._shared_continuous_active = True

            frame = self._cam.get_latest_frame()
            if frame is None:
                time.sleep(0.02)
                frame = self._cam.get_latest_frame()
            return frame

    def close(self):
        try:
            if DxCamEngine._shared_continuous_active:
                self._cam.stop()
                DxCamEngine._shared_continuous_active = False
                DxCamEngine._shared_continuous_region = None
        except Exception:
            pass
        del self._cam


class MssEngine(CaptureEngine):
    """
    Fallback engine using mss (cross-platform, GDI-based on Windows).
    Works for windowed and most borderless apps, but may black-screen
    on exclusive fullscreen DirectX games.

    Install: pip install mss
    """

    def __init__(self):
        import mss
        self._sct = mss.mss()
        print("[Capture] mss fallback engine initialized.")

    def grab(self, roi: ROI) -> Optional[Frame]:
        left, top, width, height = roi
        monitor = {"left": left, "top": top, "width": width, "height": height}
        raw = self._sct.grab(monitor)
        # mss returns BGRA — drop alpha channel
        frame = np.array(raw)[:, :, :3]
        return frame

    def close(self):
        self._sct.close()


# ──────────────────────────────────────────────
# FRAME GRABBER (public interface)
# ──────────────────────────────────────────────

class FrameGrabber:
    """
    High-level grabber with ROI management and image preprocessing.

    Usage:
        grabber = FrameGrabber()
        frame = grabber.capture()           # single shot
        preprocessed = grabber.capture(preprocess=True)  # OCR-ready
    """

    def __init__(self):
        self._roi: ROI = config.scaled_roi_preset(config.ACTIVE_ROI_PRESET)
        self._engine: CaptureEngine = self._init_engine()
        self._owns_engine = True

    def fork(self) -> FrameGrabber:
        """Create a capture consumer in its calling thread.

        DXGI consumers share the locked camera without owning its lifetime.
        MSS handles must be constructed and closed in the consuming thread.
        """
        consumer = FrameGrabber.__new__(FrameGrabber)
        consumer._roi = self._roi
        if isinstance(self._engine, DxCamEngine):
            consumer._engine = self._engine
            consumer._owns_engine = False
        else:
            consumer._engine = type(self._engine)()
            consumer._owns_engine = True
        return consumer

    # ── Engine selection ──────────────────────

    def _init_engine(self) -> CaptureEngine:
        engine_name = config.CAPTURE_ENGINE.lower()
        if engine_name == "dxcam":
            try:
                return DxCamEngine()
            except Exception as e:
                print(f"[Capture] dxcam failed ({e}), falling back to mss.")
                return MssEngine()
        return MssEngine()

    # ── ROI management ───────────────────────

    def set_roi(self, left: int, top: int, width: int, height: int):
        """Override ROI at runtime (e.g. from calibration UI)."""
        self._roi = (left, top, width, height)
        print(f"[Capture] ROI updated -> {self._roi}")

    def set_roi_preset(self, preset_name: str):
        if preset_name not in config.ROI_PRESETS:
            raise ValueError(f"Unknown preset: {preset_name}. "
                             f"Available: {list(config.ROI_PRESETS.keys())}")
        self._roi = config.scaled_roi_preset(preset_name)
        print(f"[Capture] ROI preset -> '{preset_name}' {self._roi}")

    @property
    def roi(self) -> ROI:
        return self._roi

    # ── Capture ──────────────────────────────

    def capture(self, preprocess: bool = False, warn_on_failure: bool = True) -> Optional[Frame]:
        """
        Grab a single frame from the active ROI.

        Args:
            preprocess: If True, run OCR-preparation pipeline on the frame.
            warn_on_failure: Suppress unchanged-frame warnings for background capture.

        Returns:
            BGR numpy array, or None if capture failed.
        """
        frame = None
        # Retry up to 3 times (dxcam may return None on first grab)
        for _ in range(3):
            frame = self._engine.grab(self._roi)
            if frame is not None:
                break
            time.sleep(0.05)

        if frame is None:
            if warn_on_failure:
                print("[Capture] WARNING: Failed to capture frame.")
            return None

        if preprocess:
            frame = self._preprocess(frame)

        return frame

    # ── Preprocessing for OCR ────────────────

    def _preprocess(self, frame: Frame) -> Frame:
        """
        Prepare a raw frame for OCR:
          1. Upscale (small text reads better at 2×)
          2. Convert to grayscale
          3. Denoise
          4. Adaptive threshold → high-contrast binary image
          5. Optional sharpening

        Returns a BGR frame suitable for the configured OCR backend.
        """
        scale = config.PREPROCESS_SCALE_FACTOR
        if scale != 1.0:
            h, w = frame.shape[:2]
            frame = cv2.resize(
                frame,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_LANCZOS4
            )

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        if config.PREPROCESS_DENOISE:
            gray = cv2.fastNlMeansDenoising(gray, h=10)

        # Adaptive threshold: handles uneven lighting / gradient backgrounds
        binary = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=15, C=8
        )

        if config.PREPROCESS_SHARPEN:
            kernel = np.array([[0, -1, 0],
                                [-1, 5, -1],
                                [0, -1, 0]])
            binary = cv2.filter2D(binary, -1, kernel)

        # Convert back to BGR for consistency (both OCR engines handle it)
        result = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        return result

    # ── Calibration helper ───────────────────

    def show_roi_preview(self):
        """
        Opens a live OpenCV window showing the current ROI capture.
        Press 'q' to close. Use this to calibrate your ROI coordinates.
        """
        print(f"[Capture] ROI Preview — Press 'q' to close. ROI={self._roi}")
        while True:
            frame = self.capture(preprocess=False)
            if frame is not None:
                preview = cv2.resize(frame, (0, 0), fx=0.8, fy=0.8)
                cv2.imshow("ROI Preview (press Q to close)", preview)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        cv2.destroyAllWindows()

    def close(self):
        if self._owns_engine:
            self._engine.close()


# ──────────────────────────────────────────────
# QUICK TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    grabber = FrameGrabber()
    print("Grabbing single frame from ROI...")
    frame = grabber.capture(preprocess=True)
    if frame is not None:
        cv2.imwrite("test_capture.png", frame)
        print("Saved -> test_capture.png")
        grabber.show_roi_preview()
    else:
        print("Capture failed.")
    grabber.close()

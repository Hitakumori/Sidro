"""OCR engine loading, text normalization and catalog-name matching.

Reads names from captured images using the configured OCR engine and matches
recognized text against public item data. RapidOCR is the portable default;
optional PaddleOCR and its runtime health checks use the shared baseline.
"""

from __future__ import annotations
import re
import json
import time
import os
import sys
import requests
import difflib
import warnings
import threading
import subprocess
import asyncio
import unicodedata
import logging
import contextlib
import shutil
import importlib.util
import importlib.metadata
import inspect
import multiprocessing as mp
import traceback
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import numpy as np
import cv2
from thefuzz import process as fuzz_process, fuzz

import config

_SHARED_EASY_READER = None
_SHARED_EASY_READER_KEY = None
_SHARED_EASY_READER_LOCK = threading.Lock()
_SHARED_RAPID_READER = None
_SHARED_RAPID_READER_LOCK = threading.Lock()
_SHARED_PADDLE_READER = None
_SHARED_PADDLE_READER_LOCK = threading.Lock()
_SHARED_PADDLE_READER_ANNOUNCED = False
_SHARED_WINDOWS_ENGINE = None
_SHARED_AUX_ENGINE_LOCK = threading.Lock()
_OCR_WORKERS: dict[str, "OCRWorkerProxy"] = {}
_OCR_WORKERS_LOCK = threading.Lock()


def _hide_ocr_subprocess_windows():
    """Hide backend helper consoles inside the dedicated OCR worker only."""
    if os.name != "nt" or getattr(subprocess.Popen, "_sidro_hidden_ocr_helpers", False):
        return
    original_popen = subprocess.Popen
    signature = inspect.signature(original_popen)

    class _HiddenOCRPopen(original_popen):
        _sidro_hidden_ocr_helpers = True

        def __init__(self, *args, **kwargs):
            # Binding preserves callers that pass creationflags positionally,
            # as well as their existing flags, pipes, and error handling.
            bound = signature.bind(*args, **kwargs)
            bound.arguments["creationflags"] = (
                bound.arguments.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            )
            super().__init__(*bound.args, **bound.kwargs)

    # Paddle imports launch `where nvcc` and `where ccache`. A windowed parent
    # does not hide those console executables; redirecting output is not enough.
    # Installing this in the child leaves the main app/updater launches alone.
    subprocess.Popen = _HiddenOCRPopen


def add_ocr_pack_paths():
    """Expose optional OCR packs installed under user_data/ocr_packs."""
    pack_root = Path(getattr(config, "OCR_PACKS_DIR", config.USER_DATA_DIR / "ocr_packs"))
    for backend in getattr(config, "OCR_ENGINE_CHOICES", ()):
        path = pack_root / str(backend)
        if path.exists():
            path_text = str(path)
            if path_text not in sys.path:
                sys.path.insert(0, path_text)


def is_ocr_backend_available(backend: str) -> tuple[bool, str]:
    """Cheap dependency probe used by Settings. Does not initialize OCR models."""
    add_ocr_pack_paths()
    key = str(backend).strip().lower().replace("-", "").replace("_", "")
    if key == "windows":
        key = "windowsocr"
    probes = {
        "easyocr": ("easyocr", "EasyOCR / PyTorch"),
        "rapidocr": ("rapidocr", "RapidOCR"),
        "paddleocr": ("paddleocr", "PaddleOCR"),
        "windowsocr": ("winrt", "WinRT Windows OCR bindings"),
        "windows": ("winrt", "WinRT Windows OCR bindings"),
    }
    module, label = probes.get(key, (key, key))
    if importlib.util.find_spec(module) is not None:
        from packaging.requirements import Requirement
        requirements = {
            "rapidocr": ("rapidocr==3.9.2", "onnxruntime==1.30.0"),
            "paddleocr": ("paddleocr==3.7.0", "paddlepaddle==3.3.1", "paddlex==3.7.2"),
            "easyocr": ("easyocr>=1.7.1,<2", "torch>=2,<3", "torchvision>=0.15,<1"),
            "windowsocr": tuple(f"winrt-Windows.{name}==3.2.1" for name in (
                "Foundation", "Foundation.Collections", "Globalization", "Graphics.Imaging", "Media.Ocr", "Storage.Streams"
            )),
        }
        for spec in requirements.get(key, ()):
            requirement = Requirement(spec)
            try:
                version = importlib.metadata.version(requirement.name)
            except importlib.metadata.PackageNotFoundError:
                return False, f"Missing version metadata: {requirement.name}; repair required"
            if version not in requirement.specifier:
                return False, f"{requirement.name} {version}; requires {requirement.specifier}"
        return True, label
    return False, label


def validate_ocr_backend(backend: str) -> None:
    """Test initialization and inference in a fresh worker, never a fallback engine."""
    available, detail = is_ocr_backend_available(backend)
    if not available:
        raise RuntimeError(detail)
    worker = OCRWorkerProxy(backend)
    try:
        frame = np.full((96, 320, 3), 255, dtype=np.uint8)
        cv2.putText(frame, "Prime", (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        results = worker.read(frame)
        if not any("prime" in str(row.raw_text).casefold() for row in results):
            raise RuntimeError("OCR loaded but could not read the test word 'Prime'.")
    finally:
        worker.stop()


def _resolve_easyocr_gpu() -> bool:
    mode = getattr(config, "OCR_DEVICE_MODE", "auto")
    if str(mode).strip().lower() in ("", "cpu", "0", "false", "no"):
        return False
    if mode in ("0", "false", "cpu", "no"):
        return False
    if mode in ("1", "true", "gpu", "yes"):
        return True

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        util_raw, memory_raw = result.stdout.strip().splitlines()[0].split(",")[:2]
        util = int(util_raw.strip())
        memory_mb = int(memory_raw.strip())
        if (
            util > getattr(config, "OCR_AUTO_GPU_MAX_UTIL_PERCENT", 75)
            or memory_mb > getattr(config, "OCR_AUTO_GPU_MAX_MEMORY_MB", 5000)
        ):
            print(f"[OCR] GPU busy ({util}% / {memory_mb}MB). Using CPU OCR.")
            return False
        print(f"[OCR] GPU available ({util}% / {memory_mb}MB). Using GPU OCR.")
        return True
    except Exception:
        return False

# ──────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────

@dataclass
class OCRResult:
    raw_text: str
    confidence: float


@dataclass
class MatchedItem:
    display_name: str         # Human-readable: "Ash Prime Neuroptics"
    url_name: str             # API slug: "ash_prime_neuroptics"
    match_score: int          # 0–100 fuzzy confidence
    raw_ocr: str              # Original OCR text before normalization
    alternatives: list[tuple[str, int]] = field(default_factory=list)  # runner-ups


# ──────────────────────────────────────────────
# ITEM LIST CACHE
# ──────────────────────────────────────────────

class ItemsCache:
    """
    Fetches and caches the full warframe.market item list.
    Refreshes automatically when TTL expires.

    Structure stored: { "display_name": "url_name", ... }
    e.g. { "Ash Prime Neuroptics": "ash_prime_neuroptics", ... }
    """

    ITEMS_ENDPOINT = f"{config.WFM_API_BASE_V2}/items"

    def __init__(self):
        self._cache: dict[str, str] = {}   # display_name → url_name
        self._display_names: list[str] = []
        self._load()

    def _load(self):
        cache_path = config.ITEMS_CACHE_PATH
        needs_refresh = True

        if cache_path.exists():
            age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
            if age_hours < config.ITEMS_CACHE_TTL_HOURS:
                needs_refresh = False

        if needs_refresh:
            print("[OCR] Refreshing item list from warframe.market...")
            self._fetch_and_save()
        else:
            print(f"[OCR] Loading item list from cache: {cache_path}")
            with open(cache_path, "r", encoding="utf-8") as f:
                self._cache = json.load(f)

        self._display_names = list(self._cache.keys())
        print(f"[OCR] {len(self._display_names)} items loaded.")

    def _fetch_and_save(self):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Platform": config.WFM_PLATFORM,
            "Language": config.WFM_LANGUAGE,
        }
        try:
            resp = requests.get(self.ITEMS_ENDPOINT, headers=headers, timeout=15)
            resp.raise_for_status()
            self._cache = self._parse_items_response(resp.json())
            if not self._cache:
                raise ValueError("Item manifest response did not contain any parseable items.")
            with open(config.ITEMS_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
            print(f"[OCR] Saved {len(self._cache)} items to {config.ITEMS_CACHE_PATH}")
        except Exception as e:
            print(f"[OCR] ERROR fetching items: {e}")
            if not self._cache and config.ITEMS_CACHE_PATH.exists():
                print("[OCR] Using stale cache as fallback.")
                with open(config.ITEMS_CACHE_PATH, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)

    @staticmethod
    def _parse_items_response(data: dict) -> dict[str, str]:
        """
        Convert warframe.market item manifests into:
            {display_name: slug}

        Supports both current v2 (`data[]`, `slug`, `i18n.en.name`) and
        older v1-style payloads (`payload.items[]`, `item_name`, `url_name`).
        """
        raw_items = data.get("data")
        if raw_items is None:
            raw_items = data.get("payload", {}).get("items", [])

        parsed: dict[str, str] = {}
        for item in raw_items or []:
            slug = item.get("slug") or item.get("url_name")
            name = (
                item.get("i18n", {}).get(config.WFM_LANGUAGE, {}).get("name")
                or item.get("i18n", {}).get("en", {}).get("name")
                or item.get("item_name")
            )
            if name and slug:
                parsed[name] = slug
        return parsed

    def url_name(self, display_name: str) -> Optional[str]:
        return self._cache.get(display_name)

    @property
    def names(self) -> list[str]:
        return self._display_names

    def force_refresh(self):
        self._fetch_and_save()
        self._display_names = list(self._cache.keys())


# ──────────────────────────────────────────────
# OCR ENGINES
# ──────────────────────────────────────────────

class EasyOCREngine:
    """Neural-network OCR for stylized/game fonts."""

    def __init__(self):
        global _SHARED_EASY_READER, _SHARED_EASY_READER_KEY
        import easyocr
        warnings.filterwarnings(
            "ignore",
            message="'pin_memory' argument is set as true.*",
            category=UserWarning,
        )
        use_gpu = _resolve_easyocr_gpu()
        try:
            import torch
            if use_gpu and torch.cuda.is_available():
                torch.backends.cudnn.benchmark = True
                torch.backends.cuda.matmul.allow_tf32 = True
                if hasattr(torch, "set_float32_matmul_precision"):
                    torch.set_float32_matmul_precision("high")
        except Exception:
            pass

        key = (tuple(config.OCR_LANGUAGES), use_gpu)
        with _SHARED_EASY_READER_LOCK:
            if _SHARED_EASY_READER is None or _SHARED_EASY_READER_KEY != key:
                _SHARED_EASY_READER = easyocr.Reader(
                    config.OCR_LANGUAGES,
                    gpu=use_gpu,
                    verbose=False
                )
                _SHARED_EASY_READER_KEY = key

            self._reader = _SHARED_EASY_READER
        device = getattr(self._reader, "device", "gpu" if use_gpu else "cpu")
        print(f"[OCR] EasyOCR engine ready ({device}).")

    def read(self, frame: np.ndarray) -> list[OCRResult]:
        results = self._reader.readtext(
            frame,
            detail=1,
            paragraph=False,
            batch_size=config.OCR_BATCH_SIZE,
            workers=0,
            canvas_size=getattr(config, "OCR_CANVAS_SIZE", 768),
            mag_ratio=1.0,
        )
        # results: [([bbox], text, confidence), ...]
        return [
            OCRResult(raw_text=text, confidence=conf)
            for (_, text, conf) in results
            if conf >= config.OCR_CONFIDENCE_THRESHOLD
        ]

    def read_batch(self, frames: list[np.ndarray], batch_size: int = 16) -> list[list[OCRResult]]:
        if not frames:
            return []

        batched = self._reader.readtext_batched(
            frames,
            detail=1,
            paragraph=False,
            batch_size=batch_size,
            workers=0,
            canvas_size=getattr(config, "OCR_CANVAS_SIZE", 768),
            mag_ratio=1.0,
        )
        return [
            [
                OCRResult(raw_text=text, confidence=conf)
                for (_, text, conf) in result
                if conf >= config.OCR_CONFIDENCE_THRESHOLD
            ]
            for result in batched
        ]

    def read_regions(
        self,
        frame: np.ndarray,
        horizontal_regions: list[list[int]],
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        """
        Fast path for known text regions. This bypasses EasyOCR's CRAFT text
        detector and runs only the recognizer on precomputed line boxes.
        """
        if not horizontal_regions:
            return []

        if frame.ndim == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame

        threshold = (
            config.OCR_CONFIDENCE_THRESHOLD
            if confidence_threshold is None
            else confidence_threshold
        )
        results = self._reader.recognize(
            gray,
            horizontal_list=horizontal_regions,
            free_list=[],
            detail=1,
            paragraph=False,
            batch_size=min(config.OCR_BATCH_SIZE, max(1, len(horizontal_regions))),
            workers=0,
            allowlist=allowlist,
            reformat=False,
        )
        return [
            OCRResult(raw_text=text, confidence=conf)
            for (_, text, conf) in results
            if conf >= threshold
        ]

    def read_whole(
        self,
        frame: np.ndarray,
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        h, w = frame.shape[:2]
        return self.read_regions(
            frame,
            [[0, w, 0, h]],
            allowlist=allowlist,
            confidence_threshold=confidence_threshold,
        )


class RapidOCREngine:
    """
    RapidOCR / ONNX Runtime backend for test builds.

    This keeps the same small interface as EasyOCREngine. RapidOCR does not
    expose EasyOCR's batched-list API, so read_batch() loops through frames.
    """

    def __init__(self):
        global _SHARED_RAPID_READER
        with _SHARED_RAPID_READER_LOCK:
            if _SHARED_RAPID_READER is None:
                from rapidocr import LangDet, ModelType, OCRVersion, RapidOCR
                from rapidocr.inference_engine.base import FileInfo, InferSession
                from rapidocr.utils.typings import EngineType, TaskType

                logging.getLogger("RapidOCR").setLevel(logging.WARNING)
                logging.getLogger("rapidocr").setLevel(logging.WARNING)
                model_root = Path(config.USER_DATA_DIR) / "ocr_models" / "rapidocr"
                model_root.mkdir(parents=True, exist_ok=True)
                def local_model(language, task):
                    return str(model_root / Path(InferSession.get_model_url(
                        FileInfo(EngineType.ONNXRUNTIME, OCRVersion.PPOCRV6, task, language, ModelType.SMALL)
                    )["model_dir"]).name)

                detector_path = local_model(LangDet.CH, TaskType.DET)
                recognizer_path = local_model(LangDet.CH, TaskType.REC)
                bundled_models = Path(__import__("rapidocr").__file__).resolve().parent / "models"
                for target, filename in (
                    (detector_path, "PP-OCRv6_det_small.onnx"),
                    (recognizer_path, "PP-OCRv6_rec_small.onnx"),
                ):
                    if not Path(target).exists():
                        bundled = bundled_models / filename
                        if not bundled.exists():
                            bundled = config.resource_path(f"ocr_models/rapidocr/{filename}")
                        if not bundled.exists():
                            raise FileNotFoundError(f"RapidOCR model is missing: {bundled}")
                        shutil.copy2(bundled, target)
                previous_disable_level = logging.root.manager.disable
                with open(os.devnull, "w", encoding="utf-8") as sink:
                    try:
                        logging.disable(logging.INFO)
                        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                            # v6 uses the shared multilingual recognizer, including English.
                            # Explicit local paths keep startup offline and ignore old v4 models.
                            _SHARED_RAPID_READER = RapidOCR(
                                params={
                                    "Global.model_root_dir": str(bundled_models),
                                    "Det.lang_type": LangDet.CH,
                                    "Det.model_type": ModelType.SMALL,
                                    "Det.ocr_version": OCRVersion.PPOCRV6,
                                    "Det.model_path": detector_path,
                                    "Rec.lang_type": LangDet.CH,
                                    "Rec.model_type": ModelType.SMALL,
                                    "Rec.ocr_version": OCRVersion.PPOCRV6,
                                    "Rec.model_path": recognizer_path,
                                }
                            )
                            self._reader = _SHARED_RAPID_READER
                            self._warmup()
                    finally:
                        logging.disable(previous_disable_level)
            else:
                self._reader = _SHARED_RAPID_READER
        print("[OCR] RapidOCR engine ready (PP-OCRv6 small, onnxruntime).")

    def _warmup(self):
        try:
            warmup = np.ones((48, 220, 3), dtype=np.uint8) * 32
            cv2.putText(
                warmup,
                "Prime",
                (8, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.8,
                (220, 220, 220),
                2,
            )
            self._reader(warmup)
        except Exception:
            pass

    def read(self, frame: np.ndarray) -> list[OCRResult]:
        # read_regions() uses recognizer-only calls; explicitly restore text
        # detection here because RapidOCR can otherwise retain use_det=False.
        return self._results_from_output(
            self._reader(frame, use_det=True, use_cls=False, use_rec=True)
        )

    def read_batch(self, frames: list[np.ndarray], batch_size: int = 16) -> list[list[OCRResult]]:
        return [self.read(frame) for frame in frames]

    def read_regions(
        self,
        frame: np.ndarray,
        horizontal_regions: list[list[int]],
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        if not horizontal_regions:
            return []

        h, w = frame.shape[:2]
        results: list[OCRResult] = []
        for region in horizontal_regions:
            x1, x2, y1, y2 = [int(v) for v in region[:4]]
            x1 = max(0, min(w, x1))
            x2 = max(0, min(w, x2))
            y1 = max(0, min(h, y1))
            y2 = max(0, min(h, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]
            border_value = WindowsOCREngine._background_border_value(crop)
            padded = cv2.copyMakeBorder(
                crop,
                8,
                8,
                8,
                8,
                cv2.BORDER_CONSTANT,
                value=border_value,
            )
            results.extend(self.read_whole(padded, allowlist=allowlist, confidence_threshold=confidence_threshold))
        return results

    def read_whole(
        self,
        frame: np.ndarray,
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        try:
            output = self._reader(frame, use_det=False, use_cls=False, use_rec=True)
        except Exception:
            output = self._reader(frame)
        results = self._results_from_output(output)
        if allowlist:
            allowed = set(allowlist)
            results = [
                OCRResult("".join(ch for ch in item.raw_text if ch in allowed), item.confidence)
                for item in results
            ]
            results = [item for item in results if item.raw_text.strip()]

        threshold = (
            config.OCR_CONFIDENCE_THRESHOLD
            if confidence_threshold is None
            else confidence_threshold
        )
        return [item for item in results if item.confidence >= threshold]

    @staticmethod
    def _results_from_output(output) -> list[OCRResult]:
        texts = getattr(output, "txts", None) or ()
        scores = getattr(output, "scores", None) or ()
        boxes = getattr(output, "boxes", None)

        rows: list[tuple[float, float, str, float]] = []
        for idx, text in enumerate(texts):
            clean = str(text).strip()
            if not clean:
                continue
            try:
                score = float(scores[idx])
            except Exception:
                score = 1.0
            if score < config.OCR_CONFIDENCE_THRESHOLD:
                continue

            x_pos = float(idx)
            y_pos = 0.0
            try:
                box = np.asarray(boxes[idx])
                x_pos = float(np.min(box[:, 0]))
                y_pos = float(np.min(box[:, 1]))
            except Exception:
                pass
            rows.append((y_pos, x_pos, clean, score))

        rows.sort(key=lambda item: (item[0], item[1]))
        return [OCRResult(raw_text=text, confidence=score) for _, _, text, score in rows]


class PaddleOCREngine:
    """
    PaddleOCR backend for dev test builds.

    This is heavier than RapidOCR and Windows OCR, but it is worth testing
    because its recognizer is usually stronger on small/stylized UI text.
    """

    def __init__(self):
        global _SHARED_PADDLE_READER, _SHARED_PADDLE_READER_ANNOUNCED

        # PaddleX otherwise writes downloaded models into the user's global
        # ~/.paddlex cache, which can be unavailable in portable/restricted runs.
        paddle_cache = Path(config.USER_DATA_DIR) / "ocr_models" / "paddlex"
        paddle_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(paddle_cache))
        os.environ.setdefault("FLAGS_use_onednn", "0")
        os.environ.setdefault("FLAGS_use_mkldnn", "0")
        os.environ.setdefault("FLAGS_enable_pir_api", "0")
        os.environ.setdefault("FLAGS_enable_new_ir_in_executor", "0")
        os.environ.setdefault("GLOG_minloglevel", "2")
        os.environ.setdefault("FLAGS_minloglevel", "2")

        with _SHARED_PADDLE_READER_LOCK:
            if _SHARED_PADDLE_READER is None:
                from paddleocr import PaddleOCR

                previous_disable_level = logging.root.manager.disable
                with open(os.devnull, "w", encoding="utf-8") as sink:
                    try:
                        logging.disable(logging.INFO)
                        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                            _SHARED_PADDLE_READER = PaddleOCR(
                                lang=config.OCR_LANGUAGES[0] if config.OCR_LANGUAGES else "en",
                                ocr_version="PP-OCRv6",
                                # PaddleOCR's API default re-enables oneDNN despite
                                # the environment flags above. Paddle 3.3.1's PIR
                                # oneDNN path fails inference on this Windows stack.
                                enable_mkldnn=False,
                                use_doc_orientation_classify=False,
                                use_doc_unwarping=False,
                                use_textline_orientation=False,
                            )
                            self._reader = _SHARED_PADDLE_READER
                            self._warmup()
                    finally:
                        logging.disable(previous_disable_level)
            else:
                self._reader = _SHARED_PADDLE_READER
            if not _SHARED_PADDLE_READER_ANNOUNCED:
                print("[OCR] PaddleOCR engine ready (paddlepaddle).")
                _SHARED_PADDLE_READER_ANNOUNCED = True

    def _warmup(self):
        try:
            warmup = np.ones((56, 260, 3), dtype=np.uint8) * 32
            cv2.putText(
                warmup,
                "Prime",
                (8, 38),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (220, 220, 220),
                2,
            )
            self._reader.predict(warmup)
        except Exception:
            pass

    def read(self, frame: np.ndarray) -> list[OCRResult]:
        return self._results_from_output(self._reader.predict(frame))

    def read_batch(self, frames: list[np.ndarray], batch_size: int = 16) -> list[list[OCRResult]]:
        return [self.read(frame) for frame in frames]

    def read_regions(
        self,
        frame: np.ndarray,
        horizontal_regions: list[list[int]],
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        if not horizontal_regions:
            return []

        h, w = frame.shape[:2]
        results: list[OCRResult] = []
        for region in horizontal_regions:
            x1, x2, y1, y2 = [int(v) for v in region[:4]]
            x1 = max(0, min(w, x1))
            x2 = max(0, min(w, x2))
            y1 = max(0, min(h, y1))
            y2 = max(0, min(h, y2))
            if x2 <= x1 or y2 <= y1:
                continue

            crop = frame[y1:y2, x1:x2]
            border_value = WindowsOCREngine._background_border_value(crop)
            padded = cv2.copyMakeBorder(
                crop,
                10,
                10,
                10,
                10,
                cv2.BORDER_CONSTANT,
                value=border_value,
            )
            results.extend(
                self.read_whole(
                    padded,
                    allowlist=allowlist,
                    confidence_threshold=confidence_threshold,
                )
            )
        return results

    def read_whole(
        self,
        frame: np.ndarray,
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        results = self._results_from_output(self._reader.predict(frame))
        if allowlist:
            allowed = set(allowlist)
            results = [
                OCRResult("".join(ch for ch in item.raw_text if ch in allowed), item.confidence)
                for item in results
            ]
            results = [item for item in results if item.raw_text.strip()]

        threshold = (
            config.OCR_CONFIDENCE_THRESHOLD
            if confidence_threshold is None
            else confidence_threshold
        )
        return [item for item in results if item.confidence >= threshold]

    @staticmethod
    def _results_from_output(output) -> list[OCRResult]:
        rows: list[tuple[float, float, str, float]] = []
        for page in output or []:
            if hasattr(page, "json"):
                try:
                    page = page.json
                except Exception:
                    pass
            if not isinstance(page, dict):
                continue
            if isinstance(page.get("res"), dict):
                page = page["res"]

            texts = page.get("rec_texts") or []
            scores = page.get("rec_scores") or []
            boxes = page.get("rec_boxes") or page.get("rec_polys") or []
            for idx, text in enumerate(texts):
                clean = str(text).strip()
                if not clean:
                    continue
                try:
                    score = float(scores[idx])
                except Exception:
                    score = 1.0
                if score < config.OCR_CONFIDENCE_THRESHOLD:
                    continue

                x_pos = float(idx)
                y_pos = 0.0
                try:
                    box = np.asarray(boxes[idx])
                    if box.ndim == 1 and box.size >= 4:
                        x_pos = float(box[0])
                        y_pos = float(box[1])
                    elif box.ndim >= 2:
                        x_pos = float(np.min(box[:, 0]))
                        y_pos = float(np.min(box[:, 1]))
                except Exception:
                    pass
                rows.append((y_pos, x_pos, clean, score))

        rows.sort(key=lambda item: (item[0], item[1]))
        return [OCRResult(raw_text=text, confidence=score) for _, _, text, score in rows]


class WindowsOCREngine:
    """
    Native Windows OCR via Windows.Media.Ocr.

    This is intentionally API-compatible with EasyOCREngine for testing:
    scanners can call read(), read_batch(), read_regions(), and read_whole()
    without knowing which backend is active.
    """

    def __init__(self):
        self._win = self._load_winrt()
        language_code = config.OCR_LANGUAGES[0] if config.OCR_LANGUAGES else "en"
        try:
            language = self._win["Language"](language_code)
            self._engine = self._win["OcrEngine"].try_create_from_language(language)
        except Exception:
            self._engine = self._win["OcrEngine"].try_create_from_user_profile_languages()
        if self._engine is None:
            raise RuntimeError("Windows OCR engine is unavailable for the configured language.")
        print("[OCR] Windows OCR engine ready.")

    @staticmethod
    def _load_winrt() -> dict[str, object]:
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import (
                BitmapAlphaMode,
                BitmapDecoder,
                BitmapPixelFormat,
                SoftwareBitmap,
            )
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Windows OCR backend needs WinRT Python bindings. "
                "Use Windows OCR Install / Repair in Settings."
            ) from exc

        return {
            "BitmapAlphaMode": BitmapAlphaMode,
            "BitmapDecoder": BitmapDecoder,
            "BitmapPixelFormat": BitmapPixelFormat,
            "DataWriter": DataWriter,
            "InMemoryRandomAccessStream": InMemoryRandomAccessStream,
            "Language": Language,
            "OcrEngine": OcrEngine,
            "SoftwareBitmap": SoftwareBitmap,
        }

    def read(self, frame: np.ndarray) -> list[OCRResult]:
        return asyncio.run(self._read_async(frame))

    def read_batch(self, frames: list[np.ndarray], batch_size: int = 16) -> list[list[OCRResult]]:
        if not frames:
            return []
        return asyncio.run(self._read_many_async(frames))

    def read_regions(
        self,
        frame: np.ndarray,
        horizontal_regions: list[list[int]],
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        if not horizontal_regions:
            return []

        crops: list[np.ndarray] = []
        h, w = frame.shape[:2]
        for region in horizontal_regions:
            x1, x2, y1, y2 = [int(v) for v in region[:4]]
            x1 = max(0, min(w, x1))
            x2 = max(0, min(w, x2))
            y1 = max(0, min(h, y1))
            y2 = max(0, min(h, y2))
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]
            border_value = self._background_border_value(crop)
            crops.append(
                cv2.copyMakeBorder(
                    crop,
                    12,
                    12,
                    12,
                    12,
                    cv2.BORDER_CONSTANT,
                    value=border_value,
                )
            )

        results = [
            item
            for crop_results in self.read_batch(crops)
            for item in crop_results
        ]

        if allowlist:
            allowed = set(allowlist)
            results = [
                OCRResult("".join(ch for ch in item.raw_text if ch in allowed), item.confidence)
                for item in results
            ]
            results = [item for item in results if item.raw_text.strip()]

        threshold = (
            config.OCR_CONFIDENCE_THRESHOLD
            if confidence_threshold is None
            else confidence_threshold
        )
        return [item for item in results if item.confidence >= threshold]

    def read_whole(
        self,
        frame: np.ndarray,
        allowlist: str | None = None,
        confidence_threshold: float | None = None,
    ) -> list[OCRResult]:
        h, w = frame.shape[:2]
        return self.read_regions(
            frame,
            [[0, w, 0, h]],
            allowlist=allowlist,
            confidence_threshold=confidence_threshold,
        )

    @staticmethod
    def _background_border_value(frame: np.ndarray):
        if frame.size == 0:
            return (255, 255, 255)
        if frame.ndim == 2:
            median = int(np.median(frame))
            return median
        median = np.median(frame.reshape(-1, frame.shape[-1]), axis=0)
        values = [int(v) for v in median[:3]]
        return tuple(values)

    async def _read_many_async(self, frames: list[np.ndarray]) -> list[list[OCRResult]]:
        return [await self._read_async(frame) for frame in frames]

    async def _read_async(self, frame: np.ndarray) -> list[OCRResult]:
        software_bitmap = await self._software_bitmap_from_frame(frame)
        ocr_result = await self._engine.recognize_async(software_bitmap)
        results: list[OCRResult] = []
        for line in ocr_result.lines:
            text = str(line.text).strip()
            if text:
                results.append(OCRResult(raw_text=text, confidence=1.0))
        return results

    async def _software_bitmap_from_frame(self, frame: np.ndarray):
        if frame.ndim == 2:
            encode_frame = frame
        else:
            encode_frame = frame[:, :, :3]
        ok, encoded = cv2.imencode(".png", encode_frame)
        if not ok:
            raise RuntimeError("Could not encode frame for Windows OCR.")

        stream = self._win["InMemoryRandomAccessStream"]()
        writer = self._win["DataWriter"](stream)
        writer.write_bytes(bytearray(encoded.tobytes()))
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)

        decoder = await self._win["BitmapDecoder"].create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        try:
            pixel_format = self._win["BitmapPixelFormat"].BGRA8
            alpha_mode = self._win["BitmapAlphaMode"].PREMULTIPLIED
            bitmap = self._win["SoftwareBitmap"].convert(bitmap, pixel_format, alpha_mode)
        except Exception:
            pass
        return bitmap


def _create_local_ocr_engine(engine: str):
    """Construct the actual backend inside its isolated OCR worker process."""
    global _SHARED_WINDOWS_ENGINE
    add_ocr_pack_paths()
    available, detail = is_ocr_backend_available(engine)
    if not available:
        raise RuntimeError(f"{engine}: {detail}. Install/Repair this backend in Settings.")
    if engine == "easyocr":
        return EasyOCREngine()
    if engine == "rapidocr":
        return RapidOCREngine()
    if engine == "paddleocr":
        return PaddleOCREngine()
    if engine == "windowsocr":
        with _SHARED_AUX_ENGINE_LOCK:
            if _SHARED_WINDOWS_ENGINE is None:
                _SHARED_WINDOWS_ENGINE = WindowsOCREngine()
            return _SHARED_WINDOWS_ENGINE
    raise ValueError(f"Unknown OCR engine: {engine}")


def _ocr_worker_main(connection, backend: str):
    """Worker entry point; OCR frameworks and allocator memory stay isolated."""
    try:
        _hide_ocr_subprocess_windows()
        engine = _create_local_ocr_engine(backend)
        connection.send((True, None))
    except BaseException:
        try:
            connection.send((False, traceback.format_exc()))
        finally:
            connection.close()
        return

    try:
        while True:
            try:
                command = connection.recv()
            except EOFError:
                break
            if command is None:
                break
            method_name, args, kwargs = command
            try:
                result = getattr(engine, method_name)(*args, **kwargs)
                connection.send((True, result))
            except BaseException:
                try:
                    connection.send((False, traceback.format_exc()))
                except (BrokenPipeError, EOFError, OSError):
                    break
            finally:
                # This process serves OCR requests indefinitely. Without
                # dropping the loop locals here, `command` and `args` keep the
                # last full-resolution image or batch alive between scans.
                command = None
                method_name = None
                args = ()
                kwargs = {}
                result = None
    finally:
        connection.close()


class OCRWorkerProxy:
    """Synchronous OCR methods backed by an isolated, restartable process."""

    _METHODS = {"read", "read_batch", "read_regions", "read_whole"}
    START_TIMEOUT_SECONDS = 300
    REQUEST_TIMEOUT_SECONDS = 120

    def __init__(self, backend: str):
        self.backend_name = backend
        self._lock = threading.RLock()
        self._process = None
        self._connection = None
        self._ready = False

    @property
    def is_loaded(self) -> bool:
        with self._lock:
            return bool(self._ready and self._process is not None and self._process.is_alive())

    @property
    def process_id(self) -> int | None:
        with self._lock:
            if not self.is_loaded:
                return None
            return int(self._process.pid)

    def _start_locked(self):
        if self._ready and self._process is not None and self._process.is_alive():
            return
        self._stop_locked()
        context = mp.get_context("spawn")
        parent_connection, child_connection = context.Pipe(duplex=True)
        process = context.Process(
            target=_ocr_worker_main,
            args=(child_connection, self.backend_name),
            name=f"Sidro OCR {self.backend_name}",
            daemon=True,
        )
        self._process = process
        self._connection = parent_connection
        try:
            process.start()
            child_connection.close()
            if not parent_connection.poll(self.START_TIMEOUT_SECONDS):
                raise TimeoutError(f"{self.backend_name} OCR initialization timed out. Try loading OCR again.")
            ok, payload = parent_connection.recv()
            if not ok:
                raise RuntimeError(payload or f"{self.backend_name} failed to initialize")
            self._ready = True
        except BaseException:
            self._stop_locked()
            raise

    def _stop_locked(self):
        process = self._process
        connection = self._connection
        self._ready = False
        self._process = None
        self._connection = None
        if connection is not None:
            try:
                connection.send(None)
            except (BrokenPipeError, EOFError, OSError):
                pass
            try:
                connection.close()
            except OSError:
                pass
        if process is not None and process.pid is not None:
            process.join(timeout=1.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=3.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1.0)

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _call(self, method_name: str, *args, **kwargs):
        with self._lock:
            self._start_locked()
            try:
                self._connection.send((method_name, args, kwargs))
                if not self._connection.poll(self.REQUEST_TIMEOUT_SECONDS):
                    raise TimeoutError(f"{self.backend_name} OCR scan timed out. Try scanning again.")
                ok, payload = self._connection.recv()
            except TimeoutError:
                self._stop_locked()
                raise
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._stop_locked()
                raise RuntimeError(f"{self.backend_name} OCR worker stopped unexpectedly: {exc}") from exc
            if not ok:
                raise RuntimeError(f"{self.backend_name} OCR failed:\n{payload}")
            return payload

    def __getattr__(self, name: str):
        if name in self._METHODS:
            return lambda *args, **kwargs: self._call(name, *args, **kwargs)
        raise AttributeError(name)


def create_ocr_engine(context: str | None = None, engine_override: str | None = None):
    add_ocr_pack_paths()
    selected = engine_override or config.OCR_ENGINE
    if context and engine_override is None:
        selected = dict(getattr(config, "OCR_ENGINE_BY_CONTEXT", {}) or {}).get(context, selected)
    backend = str(selected).lower().replace("-", "").replace("_", "")
    if backend == "windows":
        backend = "windowsocr"
    with _OCR_WORKERS_LOCK:
        worker = _OCR_WORKERS.get(backend)
        if worker is None:
            worker = OCRWorkerProxy(backend)
            _OCR_WORKERS[backend] = worker
    # Initializing here makes the explicit load action actually warm the engine.
    with worker._lock:
        worker._start_locked()
    return worker


def loaded_ocr_backends() -> set[str]:
    """Return backends with a live worker holding engine and model memory."""
    with _OCR_WORKERS_LOCK:
        workers = tuple(_OCR_WORKERS.items())
    return {backend for backend, worker in workers if worker.is_loaded}


def ocr_worker_processes() -> dict[str, int]:
    """Return PIDs for live OCR workers, for diagnostics and memory reporting."""
    with _OCR_WORKERS_LOCK:
        workers = tuple(_OCR_WORKERS.items())
    return {
        backend: pid
        for backend, worker in workers
        if (pid := worker.process_id) is not None
    }


def unload_ocr_backends() -> set[str]:
    """Terminate OCR workers, releasing frameworks and allocator arenas."""
    unloaded = loaded_ocr_backends()
    with _OCR_WORKERS_LOCK:
        workers = tuple(_OCR_WORKERS.values())
    for worker in workers:
        worker.stop()
    return unloaded


def create_fallback_ocr_engine(primary_context: str | None = None):
    if not getattr(config, "OCR_FALLBACK_ENABLED", False):
        return None
    selected = str(getattr(config, "OCR_FALLBACK_ENGINE", "")).strip().lower()
    if not selected or selected not in getattr(config, "OCR_ENGINE_CHOICES", ()):
        return None
    primary = str(config.OCR_ENGINE_BY_CONTEXT.get(primary_context, config.OCR_ENGINE)).strip().lower()
    if selected == primary:
        return None
    previous = dict(getattr(config, "OCR_ENGINE_BY_CONTEXT", {}) or {})
    try:
        if primary_context:
            config.OCR_ENGINE_BY_CONTEXT = dict(previous)
            config.OCR_ENGINE_BY_CONTEXT[primary_context] = selected
            return create_ocr_engine(primary_context)
        original = config.OCR_ENGINE
        config.OCR_ENGINE = selected
        try:
            return create_ocr_engine(None)
        finally:
            config.OCR_ENGINE = original
    finally:
        config.OCR_ENGINE_BY_CONTEXT = previous


# ──────────────────────────────────────────────
# TEXT NORMALIZATION
# ──────────────────────────────────────────────

# Common OCR misreads in Warframe's UI font
_OCR_CORRECTIONS = {
    r"(?i)([A-Za-z]{2,})Prime\b": r"\1 Prime",  # stuck word → "AfurisPrime"
    r"(?i)\bPrime([A-Za-z]{2,})": r"Prime \1",
    r"[*•,;:!?\u2018\u2019'\"()\{\}]": " ",
    r"[+\\/]": " ",
    r"(?i)\b(?:b|e)?lueprint\b": "Blueprint",
    r"(?i)\bB\s+ueprint\b": "Blueprint",
    r"(?i)\bYB\s*ueprint\b": "Blueprint",
    r"(?i)\bi?u[eé]?print\b": "Blueprint",
    r"(?i)\bj[uée]?k?print\b": "Blueprint",
    r"(?i)\beprint\b": "Blueprint",
    r"(?i)\bA\s+Ex\b": "Aklex",
    r"(?i)\bPxime\b": "Prime",
    r"(?i)\bIme\b": "Prime",
    r"(?i)([A-Za-z]{2,})frime\b": r"\1 Prime",
    r"(?i)([A-Za-z]{2,})rime\b": r"\1 Prime",
    r"(?i)\bfrime\b": "Prime",
    r"(?i)\brime\b": "Prime",
    r"(?i)(?<=\b)0(?=[a-z])": "O",  # leading zero → O, e.g. "0rthos"
    r"(?i)(?<=[a-z])0(?=[a-z])": "O",  # embedded zero → O, e.g. "Neur0ptics"
    r"\b0\b": "O",       # digit zero → letter O
    r"(?i)\bHv(droid)\b": r"Hy\1",
    r"(?i)\bReciever\b": "Receiver",
    r"\bl\b": "I",       # lowercase L → uppercase I
    r"[|]": "I",         # pipe → I
    r"[\[\]]": "",       # stray brackets
    r"[_]": " ",         # underscores → spaces
    r"\s{2,}": " ",      # collapse multi-spaces
}


def normalize_ocr_text(raw: str) -> str:
    """
    Clean raw OCR output before fuzzy matching:
      • Strip leading/trailing whitespace
      • Title-case (Warframe item names are Title Case)
      • Apply known OCR correction patterns
    """
    text = unicodedata.normalize("NFKD", raw.strip())
    text = text.encode("ascii", "ignore").decode("ascii")
    for pattern, replacement in _OCR_CORRECTIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = text.title()
    return text.strip()


def merge_ocr_lines(results: list[OCRResult]) -> str:
    """
    Combine multi-line OCR output (e.g. 'ASH\nPRIME\nNEUROPTICS') into
    a single candidate string for matching.
    """
    texts = [r.raw_text for r in results]
    return " ".join(texts)


# ──────────────────────────────────────────────
# FUZZY MATCHER
# ──────────────────────────────────────────────

class FuzzyMatcher:
    """
    Matches normalized OCR text against the official item name list.
    Uses `thefuzz` (RapidFuzz backend) for token_sort_ratio to handle
    word-order variations (e.g. 'Prime Ash' vs 'Ash Prime').
    """

    def __init__(self, items_cache: ItemsCache, prime_only: bool = False):
        self._cache = items_cache
        self._prime_only = prime_only
        self._names = (
            [name for name in items_cache.names if _is_prime_inventory_part_name(name)]
            if prime_only
            else items_cache.names
        )
        self._family_names = {
            name.split()[0].lower()
            for name in self._names
            if name.split()
        }

    def match(self, normalized_text: str) -> Optional[MatchedItem]:
        """
        Returns the best-matching item, or None if score is below threshold.
        """
        if not normalized_text.strip():
            return None

        candidates = self._extract_candidates(normalized_text)
        # candidates: [(name, score), ...]

        if not candidates:
            return None

        best_name, best_score = _choose_best_candidate(normalized_text, candidates)

        if best_score < config.FUZZY_MATCH_THRESHOLD:
            print(f"[OCR] Best match '{best_name}' scored {best_score} — below threshold {config.FUZZY_MATCH_THRESHOLD}, skipping.")
            return None

        url = self._cache.url_name(best_name) or ""

        return MatchedItem(
            display_name=best_name,
            url_name=url,
            match_score=best_score,
            raw_ocr=normalized_text,
            alternatives=[(n, s) for n, s in candidates if n != best_name][:config.FUZZY_TOP_N_CANDIDATES - 1]
        )

    def _extract_candidates(self, normalized_text: str) -> list[tuple[str, int]]:
        candidate_texts = [normalized_text]
        prime_retry = _insert_missing_prime(normalized_text) if self._prime_only else None
        if prime_retry and prime_retry.lower() != normalized_text.lower():
            candidate_texts.append(prime_retry)

        by_name: dict[str, int] = {}
        for text in candidate_texts:
            names = self._candidate_names_for_text(text)
            for name, score in fuzz_process.extract(
                text,
                names,
                scorer=fuzz.WRatio,
                limit=max(config.FUZZY_TOP_N_CANDIDATES, 20),
            ):
                by_name[name] = max(by_name.get(name, 0), score)

            if self._prime_only and not names:
                for name, score in self._broad_prime_candidates(text):
                    by_name[name] = max(by_name.get(name, 0), score)

        return sorted(by_name.items(), key=lambda item: item[1], reverse=True)

    def _candidate_names_for_text(self, text: str) -> list[str]:
        if not self._prime_only:
            return self._names

        if "set" in {token.lower() for token in re.findall(r"[A-Za-z0-9]+", text)}:
            return []

        family = _detected_item_family(text, self._family_names)
        if not family:
            family = _fuzzy_detected_item_family(text, self._family_names)
        if not family:
            return []

        restricted = [
            name for name in self._names
            if name.split() and name.split()[0].lower() == family
        ]
        return restricted or self._names

    def _broad_prime_candidates(self, text: str) -> list[tuple[str, int]]:
        if _is_component_only_prime_text(text):
            return []

        results = fuzz_process.extract(
            text,
            self._names,
            scorer=fuzz.WRatio,
            limit=5,
        )
        if not results:
            return []

        top_name, top_score = results[0]
        runner_up_score = results[1][1] if len(results) > 1 else 0
        if top_score < getattr(config, "PRIME_BROAD_FALLBACK_MIN_SCORE", 92):
            return []
        if top_score - runner_up_score < getattr(config, "PRIME_BROAD_FALLBACK_MIN_GAP", 3):
            return []
        return [(top_name, top_score)]


def _is_prime_inventory_part_name(name: str) -> bool:
    padded = f" {name} "
    return " Prime " in padded and not name.lower().endswith(" set")


def _detected_item_family(text: str, known_families: set[str]) -> str | None:
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if token in known_families and token not in {"prime", "blueprint"}:
            return token
    return None


def _fuzzy_detected_item_family(text: str, known_families: set[str]) -> str | None:
    best_family = None
    best_score = 0
    for token in _candidate_family_tokens(text):
        match = fuzz_process.extractOne(
            token,
            known_families,
            scorer=fuzz.WRatio,
        )
        if match is None:
            continue
        family, score = match
        if len(token) <= 3 and not family.startswith(token):
            continue
        if len(token) == 4 and not _safe_short_family_match(token, family, score):
            continue
        if score > best_score:
            best_family = family
            best_score = score

    if best_family and best_score >= getattr(config, "PRIME_FAMILY_FUZZY_THRESHOLD", 75):
        return best_family
    return None


def _candidate_family_tokens(text: str) -> list[str]:
    ignored = _prime_component_tokens()
    tokens: list[str] = []
    fragments: list[str] = []
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if token.endswith("prime") and len(token) > 7:
            token = token[:-5]
        if token.startswith("prime") and len(token) > 7:
            token = token[5:]
        if token in ignored:
            continue
        if len(token) >= 2:
            fragments.append(token)
        if len(token) < 3:
            continue
        tokens.append(token)

    # Windows OCR often splits a family name into fragments:
    # "Bta on Prime Receiver" -> "btaon" -> Braton,
    # "Bto co Prime Receiver" -> "btoco" -> Bronco.
    for left, right in zip(fragments, fragments[1:]):
        combined = f"{left}{right}"
        if 3 <= len(combined) <= 12:
            tokens.append(combined)

    return list(dict.fromkeys(tokens))


def _safe_short_family_match(token: str, family: str, score: int) -> bool:
    if not token or not family:
        return False
    if token in family or family in token:
        return True
    # Four-letter OCR fragments are common, but also dangerous:
    # "ondo" should not become Odonata, while "brat" -> Braton is fine.
    return token[0] == family[0] and score >= 85


def _prime_component_tokens() -> set[str]:
    return {
        "prime", "blueprint", "set", "systems", "chassis", "chasss", "neuroptics",
        "barrel", "receiver", "reciever", "stock", "blade", "handle", "hilt", "link",
        "grip", "limb", "upper", "lower", "string", "disc", "head", "pouch",
        "chain", "gauntlet", "guard", "ornament", "stars", "wings", "cerebrum",
        "carapace", "helm", "boot", "band", "buckle",
    }


def _is_component_only_prime_text(text: str) -> bool:
    return not _candidate_family_tokens(text)


def _first_unknown_family_token(text: str, known_families: set[str]) -> str | None:
    ignored = _prime_component_tokens()
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if len(token) < 3 or token in ignored:
            continue
        if token in known_families:
            return None
        return token
    return None


def _choose_best_candidate(normalized_text: str, candidates: list[tuple[str, int]]) -> tuple[str, int]:
    """
    Prefer a candidate that keeps the first readable word from OCR.

    This prevents partial reads like "Trumna Stock" from resolving to an
    unrelated stock item just because several names tie on fuzzy score.
    """
    best_name, best_score = candidates[0]
    tokens = re.findall(r"[A-Za-z0-9]+", normalized_text)
    if not tokens:
        return best_name, best_score

    first = tokens[0].lower()
    same_prefix = [
        (name, score) for name, score in candidates
        if name.split() and name.split()[0].lower() == first
    ]
    if not same_prefix:
        return best_name, best_score

    prefix_name, prefix_score = max(same_prefix, key=lambda item: item[1])
    if prefix_score >= best_score - 8:
        return prefix_name, prefix_score
    return best_name, best_score


def _insert_missing_prime(normalized_text: str) -> str | None:
    tokens = re.findall(r"[A-Za-z0-9&]+", normalized_text)
    if len(tokens) < 2 or any(token.lower() == "prime" for token in tokens):
        return None
    return " ".join([tokens[0], "Prime", *tokens[1:]])


# ──────────────────────────────────────────────
# MAIN OCR PROCESSOR
# ──────────────────────────────────────────────

class OCRProcessor:
    """
    Orchestrates: Frame → OCR engine → normalize → fuzzy match → MatchedItem
    """

    def __init__(self, context: str | None = None):
        self.items_cache = ItemsCache()
        self.matcher = FuzzyMatcher(self.items_cache)
        self.context = context
        self._engine = self._init_engine()

    def _init_engine(self):
        return create_ocr_engine(self.context)

    def process(self, frame: np.ndarray) -> Optional[MatchedItem]:
        """
        Full pipeline: frame → MatchedItem (or None if nothing recognized).
        """
        # 1. Run OCR
        raw_results = self._engine.read(frame)
        if not raw_results:
            print("[OCR] No text detected in frame.")
            return None

        # 2. Merge multi-line detections
        merged = merge_ocr_lines(raw_results)
        print(f"[OCR] Raw OCR output: '{merged}'")

        # 3. Normalize
        match_input = merged
        if self.context == "single_scan":
            # A selected item crop can include a Warframe stack badge such as x12.
            # Keep it for quantity parsing, but exclude it from the item-name match.
            match_input = re.sub(r"(?:[x×]\s*\d{1,3}|\d{1,3}\s*[x×])", " ", merged, flags=re.IGNORECASE)
        normalized = normalize_ocr_text(match_input)
        print(f"[OCR] Normalized: '{normalized}'")

        # 4. Fuzzy match
        match = self.matcher.match(normalized)
        if match:
            if self.context == "single_scan":
                match.raw_ocr = merged
            print(f"[OCR] Matched: '{match.display_name}' (score={match.match_score})")
        else:
            print("[OCR] No confident match found.")

        return match


# ──────────────────────────────────────────────
# QUICK TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import cv2
    processor = OCRProcessor()

    # Test with a synthetic text image
    test_img = np.ones((100, 600, 3), dtype=np.uint8) * 30
    cv2.putText(test_img, "Ash Prime Neuroptics", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (220, 200, 150), 2)

    result = processor.process(test_img)
    if result:
        print(f"\nResult: {result}")
    else:
        print("\nNo match.")

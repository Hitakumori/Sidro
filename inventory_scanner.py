"""Inventory-grid capture, item recognition and quantity extraction.

Slices the captured grid into label/badge regions, runs OCR and item matching,
then returns structured inventory results for review. Running this module
directly performs a scanner diagnostic and writes local debug images.
"""

from __future__ import annotations
import re
import time
import concurrent.futures
import gc
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import cv2

import config
from pillar1_capture import FrameGrabber
from pillar2_ocr import OCRProcessor, normalize_ocr_text, ItemsCache, FuzzyMatcher


# ──────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────

@dataclass
class InventoryItem:
    display_name: str           # "Akarius Prime Link"
    url_name: str               # "akarius_prime_link"
    quantity: int               # parsed from ✓N badge (1 if no badge)
    match_score: int            # fuzzy match confidence 0-100
    raw_ocr: str                # raw OCR text before normalization
    grid_position: tuple[int, int]  # (col, row) 0-indexed
    selected_for_listing: bool = False


@dataclass
class InventoryScanResult:
    items: list[InventoryItem]
    scan_duration_seconds: float
    frame_captured_at: float   # time.time()
    failed_cells: int          # cells where OCR found nothing useful
    failed_positions: list[tuple[int, int]] | None = None  # (col, row), zero-based


# ──────────────────────────────────────────────
# CARD SLICER
# ──────────────────────────────────────────────

class CardSlicer:
    """
    Splits the full inventory grid frame into individual card sub-images.
    Yields both the icon+label cell and a label-only strip.
    """

    def __init__(
        self,
        rows: int | None = None,
        row_start: int = 0,
        row_y_offset: int | None = None,
        label: str | None = None,
    ):
        self.base_card_w = config.INVENTORY_CARD_WIDTH
        self.base_card_h = config.INVENTORY_CARD_HEIGHT
        self.base_stride_x = getattr(config, "INVENTORY_CARD_STRIDE_X", self.base_card_w)
        self.base_stride_y = getattr(config, "INVENTORY_CARD_STRIDE_Y", self.base_card_h)
        self.base_label_y = getattr(
            config,
            "INVENTORY_LABEL_Y_OFFSET",
            self.base_card_h - config.INVENTORY_LABEL_HEIGHT,
        )
        self.base_label_h = config.INVENTORY_LABEL_HEIGHT
        self.base_label_x_pad = getattr(config, "INVENTORY_LABEL_X_PAD", 0)
        self.cols = config.INVENTORY_GRID_COLS
        self.rows = rows if rows is not None else config.INVENTORY_GRID_ROWS
        self.row_start = row_start
        self.base_row_y_offset = row_y_offset
        self.label = label or f"{self.cols}x{self.rows}"
        self._scale_x = 1.0
        self._scale_y = 1.0
        self._apply_scale(*config.screen_scale())

    def _apply_scale(self, scale_x: float, scale_y: float):
        self._scale_x = max(0.1, float(scale_x))
        self._scale_y = max(0.1, float(scale_y))
        self.card_w = max(1, int(round(self.base_card_w * self._scale_x)))
        self.card_h = max(1, int(round(self.base_card_h * self._scale_y)))
        self.stride_x = max(1, int(round(self.base_stride_x * self._scale_x)))
        self.stride_y = max(1, int(round(self.base_stride_y * self._scale_y)))
        self.label_y = max(0, int(round(self.base_label_y * self._scale_y)))
        self.label_h = max(1, int(round(self.base_label_h * self._scale_y)))
        self.label_x_pad = max(0, int(round(self.base_label_x_pad * self._scale_x)))
        self.row_y_offset = (
            None
            if self.base_row_y_offset is None
            else int(round(self.base_row_y_offset * self._scale_y))
        )

    def configure_for_frame(self, grid_frame: np.ndarray):
        base_width = config.ROI_PRESETS["inventory_sell"][2]
        base_height = config.ROI_PRESETS["inventory_sell"][3]
        frame_h, frame_w = grid_frame.shape[:2]
        scale_x = frame_w / base_width if base_width else 1.0
        scale_y = frame_h / base_height if base_height else scale_x
        self._apply_scale(scale_x, scale_y)

    def _card_y_for_row(self, row: int) -> int:
        if self.row_y_offset is not None:
            return self.row_y_offset + row * self.stride_y
        return (self.row_start + row) * self.stride_y

    def _display_row_for_row(self, row: int) -> int:
        return self.row_start + row

    def slice_labels(self, grid_frame: np.ndarray) -> list[tuple[tuple[int,int], np.ndarray]]:
        """
        Slice the configured label strip from each card.

        Returns:
            List of ((col, row), label_image) tuples.
            label_image is a BGR crop ready for OCR.
        """
        results = []
        frame_h, frame_w = grid_frame.shape[:2]

        for row in range(self.rows):
            for col in range(self.cols):
                card_x = col * self.stride_x
                card_y = self._card_y_for_row(row)
                x1 = card_x + self.label_x_pad
                y1 = card_y + self.label_y
                x2 = card_x + self.card_w - self.label_x_pad
                y2 = y1 + self.label_h

                # Bounds check — partial cards at edges
                if x1 < 0 or y1 < 0 or x2 > frame_w or y2 > frame_h:
                    continue

                label_crop = grid_frame[y1:y2, x1:x2]
                results.append(((col, self._display_row_for_row(row)), label_crop))

        return results

    def slice_quantity_badges(self, grid_frame: np.ndarray) -> list[tuple[tuple[int,int], np.ndarray]]:
        """
        Slice the top-left badge area of each card (where ✓2, ✓3 appear).
        Badge region: top-left of each card. It must include the digit after
        the checkmark circle, so keep it wider than the icon itself.
        """
        results = []
        frame_h, frame_w = grid_frame.shape[:2]
        badge_w = max(1, int(round(92 * self._scale_x)))
        badge_h = max(1, int(round(44 * self._scale_y)))

        for row in range(self.rows):
            for col in range(self.cols):
                x1 = col * self.stride_x
                y1 = self._card_y_for_row(row)
                x2 = min(x1 + badge_w, frame_w)
                y2 = min(y1 + badge_h, frame_h)

                if x1 >= frame_w or y1 >= frame_h:
                    continue

                badge_crop = grid_frame[y1:y2, x1:x2]
                results.append(((col, self._display_row_for_row(row)), badge_crop))

        return results


# ──────────────────────────────────────────────
# QUANTITY PARSER
# ──────────────────────────────────────────────

def parse_quantity_from_badge(
    badge_image: np.ndarray,
    ocr_engine,
    grid_position: Optional[tuple[int, int]] = None,
) -> int:
    """
    Extract quantity from the ✓N badge in the top-left of a card.
    The checkmark icon + number, e.g. "✓2", "✓5".
    Returns 1 if no badge / unreadable.
    """
    digit_crop = _quantity_digit_crop(badge_image)
    if digit_crop.size == 0:
        return 1

    # One tight EasyOCR pass. The previous diagnostic version ran many OCR
    # variants per cell and was far too slow.
    scale = getattr(config, "INVENTORY_BADGE_SCALE", 4.0)
    upscaled = cv2.resize(digit_crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
    upscaled = cv2.copyMakeBorder(
        upscaled, 14, 14, 14, 14,
        cv2.BORDER_CONSTANT,
        value=(0, 0, 0),
    )

    if getattr(config, "OCR_USE_BADGE_RECOGNIZER_ONLY", True) and hasattr(ocr_engine, "read_whole"):
        try:
            raw_results = ocr_engine.read_whole(
                upscaled,
                allowlist="0123456789",
                confidence_threshold=0.15,
            )
            for result in raw_results:
                digits = re.findall(r"\d+", result.raw_text)
                if digits and result.confidence >= 0.15:
                    qty = int(digits[0])
                    if 2 <= qty <= 9999:
                        pos = f" {grid_position}" if grid_position else ""
                        print(f"[Scanner] Qty{pos}: {qty} (conf={result.confidence:.2f})")
                        return qty
        except Exception:
            pass

    if hasattr(ocr_engine, "_reader"):
        try:
            raw_results = ocr_engine._reader.readtext(
                upscaled,
                detail=1,
                paragraph=False,
                allowlist="0123456789",
                text_threshold=0.2,
                low_text=0.1,
                canvas_size=getattr(config, "OCR_DIGIT_CANVAS_SIZE", 320),
                mag_ratio=1.0,
            )
            for _, text, conf in raw_results:
                digits = re.findall(r"\d+", text)
                if digits and conf >= 0.20:
                    qty = int(digits[0])
                    if 2 <= qty <= 9999:
                        pos = f" {grid_position}" if grid_position else ""
                        print(f"[Scanner] Qty{pos}: {qty} (conf={conf:.2f})")
                        return qty
        except Exception:
            pass

    return 1  # No badge = 1 copy


def parse_quantities_from_badges(
    badge_cells: list[tuple[tuple[int, int], np.ndarray]],
    ocr_engine,
) -> dict[tuple[int, int], int]:
    """
    Read the count area of each card, using a batch OCR pass and a
    recognizer-only retry for cells the detector misses.
    """
    qty_map: dict[tuple[int, int], int] = {}
    positions: list[tuple[int, int]] = []
    digit_images: list[np.ndarray] = []
    original_crops: dict[tuple[int, int], np.ndarray] = {}

    for pos, badge_image in badge_cells:
        qty_map[pos] = 1
        # OCR the count area directly. The circle/checkmark detector used to
        # gate this pass was resolution and antialiasing sensitive, causing
        # visible stack counts to be silently reported as 1.
        digit_crop = _quantity_digit_crop(badge_image)
        if digit_crop.size == 0:
            continue
        original_crops[pos] = digit_crop

        scale = getattr(config, "INVENTORY_BADGE_SCALE", 4.0)
        upscaled = cv2.resize(digit_crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
        upscaled = cv2.copyMakeBorder(
            upscaled, 14, 14, 14, 14,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        positions.append(pos)
        digit_images.append(upscaled)

    if not digit_images:
        return qty_map

    if getattr(config, "OCR_USE_BADGE_RECOGNIZER_ONLY", True) and hasattr(ocr_engine, "read_whole"):
        for pos, image in zip(positions, digit_images):
            try:
                raw_results = ocr_engine.read_whole(
                    image,
                    allowlist="0123456789",
                    confidence_threshold=0.15,
                )
            except Exception:
                raw_results = []
            for result in raw_results:
                digits = re.findall(r"\d+", result.raw_text)
                if digits and result.confidence >= 0.15:
                    qty = int(digits[0])
                    if 2 <= qty <= 9999:
                        qty_map[pos] = qty
                        print(f"[Scanner] Qty {pos}: {qty} (conf={result.confidence:.2f})")
                        break
        return qty_map

    if hasattr(ocr_engine, "_reader"):
        try:
            raw_batch = ocr_engine._reader.readtext_batched(
                digit_images,
                detail=1,
                paragraph=False,
                allowlist="0123456789",
                batch_size=min(config.OCR_BATCH_SIZE, len(digit_images)),
                workers=0,
                text_threshold=0.2,
                low_text=0.1,
                canvas_size=getattr(config, "OCR_DIGIT_CANVAS_SIZE", 320),
                mag_ratio=1.0,
            )
        except Exception:
            raw_batch = []
    else:
        raw_batch = []

    for pos, raw_results in zip(positions, raw_batch):
        for _, text, conf in raw_results:
            digits = re.findall(r"\d+", text)
            if digits and conf >= 0.20:
                qty = int(digits[0])
                if 2 <= qty <= 9999:
                    qty_map[pos] = qty
                    print(f"[Scanner] Qty {pos}: {qty} (conf={conf:.2f})")
                    break

    # EasyOCR's detector can miss the small digits even when they are present.
    # Retry only those cells with the engine's recognizer-only path.
    if hasattr(ocr_engine, "read_whole"):
        for pos, crop in original_crops.items():
            if qty_map[pos] != 1:
                continue
            scale = getattr(config, "INVENTORY_BADGE_SCALE", 4.0)
            upscaled = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
            upscaled = cv2.copyMakeBorder(
                upscaled, 14, 14, 14, 14, cv2.BORDER_CONSTANT, value=(0, 0, 0)
            )
            try:
                raw_results = ocr_engine.read_whole(
                    upscaled, allowlist="0123456789", confidence_threshold=0.15
                )
            except Exception:
                raw_results = []
            for result in raw_results:
                digits = re.findall(r"\d+", result.raw_text)
                if digits and result.confidence >= 0.15:
                    qty = int(digits[0])
                    if 2 <= qty <= 9999:
                        qty_map[pos] = qty
                        print(f"[Scanner] Qty {pos}: {qty} (conf={result.confidence:.2f}, fallback)")
                        break

    return qty_map


def _quantity_digit_crop(badge_image: np.ndarray) -> np.ndarray:
    """Return the normalized count-text area from a card's badge crop."""
    h, w = badge_image.shape[:2]
    digit_x1 = min(w, max(0, int(round(w * (34 / 92)))))
    digit_x2 = min(w, max(digit_x1 + 1, int(round(w * (82 / 92)))))
    digit_y2 = min(h, max(1, int(round(h * (40 / 44)))))
    return badge_image[0:digit_y2, digit_x1:digit_x2]


# ──────────────────────────────────────────────
# LABEL IMAGE PREPROCESSOR
# ──────────────────────────────────────────────

def preprocess_label_with_regions(label_image: np.ndarray) -> tuple[np.ndarray, list[list[int]]]:
    """
    Specialized preprocessing for the inventory card label strip.

    The Warframe inventory UI uses gold labels over item art. Pure brightness
    thresholding lets bright item edges leak into OCR, so this pass isolates
    the gold text hue first.
    """
    scale = float(getattr(config, "INVENTORY_LABEL_SCALE", 2.0))
    h, w = label_image.shape[:2]
    img = cv2.resize(label_image, (int(w * scale), int(h * scale)),
                     interpolation=cv2.INTER_LANCZOS4)

    gold_mask = _gold_text_mask(img)

    # Black text on white background tends to be friendlier for EasyOCR on
    # these three-line labels than sparse white-on-black masks.
    result = 255 - gold_mask

    return cv2.cvtColor(result, cv2.COLOR_GRAY2BGR), _extract_text_line_regions(gold_mask)


def preprocess_label_windows_with_regions(label_image: np.ndarray) -> tuple[np.ndarray, list[list[int]]]:
    """
    OCR-friendly label image for Windows.Media.Ocr.

    Windows OCR is fast, but it is less forgiving than EasyOCR on Warframe's
    small gold UI font. Feed it larger black text on a white page and crop
    known text lines instead of asking it to detect the whole card label.
    """
    scale = float(getattr(config, "WINDOWS_OCR_INVENTORY_LABEL_SCALE", 3.0))
    h, w = label_image.shape[:2]
    img = cv2.resize(
        label_image,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_LANCZOS4,
    )

    gold_mask = _gold_text_mask(img)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    gold_mask = cv2.dilate(gold_mask, kernel, iterations=1)

    page = np.full_like(gold_mask, 255)
    page[gold_mask > 0] = 0

    border = max(12, int(6 * scale))
    page = cv2.copyMakeBorder(
        page,
        border,
        border,
        border,
        border,
        cv2.BORDER_CONSTANT,
        value=255,
    )
    region_mask = cv2.copyMakeBorder(
        gold_mask,
        border,
        border,
        border,
        border,
        cv2.BORDER_CONSTANT,
        value=0,
    )
    return cv2.cvtColor(page, cv2.COLOR_GRAY2BGR), _extract_text_line_regions(region_mask)


def preprocess_label_windows_raw(label_image: np.ndarray) -> np.ndarray:
    """
    Primary Windows OCR inventory label image.

    In testing, Windows.Media.Ocr reads full upscaled label strips more reliably
    than tight per-line crops. Keep color information because the native engine
    handles the Warframe gold-on-dark label surprisingly well once enlarged.
    """
    scale = float(getattr(config, "WINDOWS_OCR_INVENTORY_LABEL_SCALE", 3.0))
    h, w = label_image.shape[:2]
    img = cv2.resize(
        label_image,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_LANCZOS4,
    )
    return cv2.copyMakeBorder(
        img,
        16,
        16,
        16,
        16,
        cv2.BORDER_REPLICATE,
    )


def preprocess_label_windows_gray(label_image: np.ndarray) -> np.ndarray:
    """Secondary Windows OCR fallback: enlarged contrast-normalized grayscale."""
    scale = float(getattr(config, "WINDOWS_OCR_INVENTORY_LABEL_SCALE", 3.0))
    h, w = label_image.shape[:2]
    img = cv2.resize(
        label_image,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_LANCZOS4,
    )
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(4, 4))
    gray = clahe.apply(gray)
    gray = cv2.copyMakeBorder(
        gray,
        16,
        16,
        16,
        16,
        cv2.BORDER_REPLICATE,
    )
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def preprocess_label_windows_binary(label_image: np.ndarray) -> np.ndarray:
    """Last Windows OCR fallback: enlarged high-contrast black text on white."""
    scale = float(getattr(config, "WINDOWS_OCR_INVENTORY_LABEL_SCALE", 3.0))
    h, w = label_image.shape[:2]
    img = cv2.resize(
        label_image,
        (int(w * scale), int(h * scale)),
        interpolation=cv2.INTER_LANCZOS4,
    )
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    page = 255 - binary
    page = cv2.copyMakeBorder(
        page,
        16,
        16,
        16,
        16,
        cv2.BORDER_CONSTANT,
        value=255,
    )
    return cv2.cvtColor(page, cv2.COLOR_GRAY2BGR)


def preprocess_label(label_image: np.ndarray) -> np.ndarray:
    return preprocess_label_with_regions(label_image)[0]


def preprocess_label_raw(label_image: np.ndarray) -> np.ndarray:
    """Less aggressive fallback for labels where hue masking clips letters."""
    scale = float(getattr(config, "INVENTORY_LABEL_SCALE", 2.0))
    h, w = label_image.shape[:2]
    img = cv2.resize(label_image, (int(w * scale), int(h * scale)),
                     interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4))
    gray = clahe.apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _gold_text_mask(image: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Gold label text. Keep saturation moderately broad because antialiasing
    # and bloom make some characters pale.
    gold_mask = cv2.inRange(hsv, (12, 25, 80), (52, 255, 255))

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    gold_mask = cv2.morphologyEx(gold_mask, cv2.MORPH_OPEN, kernel, iterations=1)
    gold_mask = cv2.dilate(gold_mask, kernel, iterations=1)
    return gold_mask


def _extract_text_line_regions(mask: np.ndarray) -> list[list[int]]:
    """
    Build line boxes from the already-isolated gold text mask. These boxes let
    EasyOCR skip its expensive detector and run only recognition.
    """
    h, w = mask.shape[:2]
    row_counts = np.count_nonzero(mask, axis=1)
    active_threshold = max(2, int(w * 0.01))

    bands: list[tuple[int, int]] = []
    start: int | None = None
    for y, count in enumerate(row_counts):
        if count >= active_threshold and start is None:
            start = y
        elif count < active_threshold and start is not None:
            bands.append((start, y))
            start = None
    if start is not None:
        bands.append((start, h))

    if not bands:
        return []

    merged: list[tuple[int, int]] = []
    max_gap = max(2, h // 50)
    for y1, y2 in bands:
        if merged and y1 - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], y2)
        else:
            merged.append((y1, y2))

    regions: list[list[int]] = []
    min_height = max(7, h // 24)
    pad_x = max(4, w // 35)
    pad_y = max(2, h // 60)
    for y1, y2 in merged:
        if y2 - y1 < min_height:
            continue
        line = mask[y1:y2, :]
        col_counts = np.count_nonzero(line, axis=0)
        cols = np.where(col_counts > 0)[0]
        if cols.size == 0:
            continue
        x1 = max(0, int(cols[0]) - pad_x)
        x2 = min(w, int(cols[-1]) + pad_x)
        yy1 = max(0, y1 - pad_y)
        yy2 = min(h, y2 + pad_y)
        if x2 - x1 >= 20 and yy2 - yy1 >= min_height:
            regions.append([x1, x2, yy1, yy2])

    return regions


def looks_like_label_text(label_image: np.ndarray) -> bool:
    hsv = cv2.cvtColor(label_image, cv2.COLOR_BGR2HSV)
    gold_mask = cv2.inRange(hsv, (12, 25, 80), (52, 255, 255))
    gold_ratio = float(np.count_nonzero(gold_mask)) / gold_mask.size
    return gold_ratio >= 0.006


# ──────────────────────────────────────────────
# INVENTORY SCANNER (main class)
# ──────────────────────────────────────────────

class InventoryScanner:
    """
    Scans the full Inventory/Sell screen and returns all visible item names.

    Usage:
        scanner = InventoryScanner()
        result = scanner.scan()
        for item in result.items:
            print(f"{item.quantity}x  {item.display_name}  [{item.match_score}%]")
    """

    def __init__(
        self,
        rows: int | None = None,
        row_start: int = 0,
        row_y_offset: int | None = None,
        label: str | None = None,
        enable_capture: bool = True,
        ocr_context: str = "inventory_6x3",
    ):
        self.ocr_context = str(ocr_context)
        self.grabber = FrameGrabber() if enable_capture else None
        if self.grabber is not None:
            self.grabber.set_roi_preset("inventory_sell")

        self.items_cache = ItemsCache()
        self.matcher = FuzzyMatcher(
            self.items_cache,
            prime_only=getattr(config, "INVENTORY_MATCH_PRIME_ONLY", False),
        )
        self.slicer = CardSlicer(
            rows=rows,
            row_start=row_start,
            row_y_offset=row_y_offset,
            label=label,
        )

        # Import OCR engine directly (reuse pillar2 engine)
        from pillar2_ocr import create_ocr_engine
        self._ocr = None
        self._ocr_backend_name = str(
            getattr(config, "OCR_ENGINE_BY_CONTEXT", {}).get(ocr_context, config.OCR_ENGINE)
        )
        self._ocr_backend_key = self._ocr_backend_name.lower().replace("_", "").replace("-", "")
        self._uses_windows_ocr = self._ocr_backend_key.startswith("windows")
        self._uses_rapid_ocr = self._ocr_backend_key.startswith("rapid")
        self._fallback_ocr = None

    def close(self):
        """Release screen capture and per-scanner OCR references."""
        if self.grabber is not None:
            self.grabber.close()
            self.grabber = None
        self._ocr = None
        self._fallback_ocr = None

        print(f"[Scanner] Inventory scanner ready ({self.slicer.label}).")
        print(f"[Scanner] Grid: {self.slicer.cols} cols × "
              f"{self.slicer.rows} rows = "
              f"{self.slicer.cols * self.slicer.rows} cells")

    def scan(self) -> InventoryScanResult:
        """
        Full pipeline: capture → slice → OCR → match → result.
        """
        if self.grabber is None:
            raise RuntimeError("InventoryScanner was created without capture. Use scan_frame(frame).")

        if self._ocr is None:
            from pillar2_ocr import create_ocr_engine
            self._ocr = create_ocr_engine(self.ocr_context)

        t_start = time.time()

        # ── 1. Capture grid ────────────────────
        frame = self.grabber.capture(preprocess=False)  # Raw frame; we preprocess per-cell
        if frame is None:
            raise RuntimeError("Screen capture returned None. Is Warframe running?")

        t_capture = time.time()
        print(f"[Scanner] Frame captured in {(t_capture - t_start)*1000:.0f}ms")

        return self._scan_frame(frame, t_start, t_capture)

    def scan_frame(self, frame: np.ndarray) -> InventoryScanResult:
        """
        Run the normal inventory scanner on an already-loaded ROI frame.
        Used by offline OCR evaluation so tests compare scanner decisions
        against screenshots without changing live capture behavior.
        """
        if frame is None or frame.size == 0:
            raise RuntimeError("scan_frame() received an empty image.")
        if self._ocr is None:
            from pillar2_ocr import create_ocr_engine
            self._ocr = create_ocr_engine(self.ocr_context)
        t_start = time.time()
        t_capture = t_start
        print(f"[Scanner] Frame loaded from image ({frame.shape[1]}x{frame.shape[0]})")
        return self._scan_frame(frame, t_start, t_capture)

    def _scan_frame(
        self,
        frame: np.ndarray,
        t_start: float,
        t_capture: float,
    ) -> InventoryScanResult:
        self.slicer.configure_for_frame(frame)
        # ── 2. Slice into label strips ─────────
        label_cells = self.slicer.slice_labels(frame)
        badge_cells = self.slicer.slice_quantity_badges(frame)

        # Build badge lookup: (col, row) -> quantity
        qty_map = parse_quantities_from_badges(badge_cells, self._ocr)

        # ── 3. OCR each label cell ─────────────
        items: list[InventoryItem] = []
        failed = 0

        def _match_ocr_results(ocr_results, pos, minimum_score: int | None = None):
            col, row = pos
            try:
                if not ocr_results:
                    return None

                merged = " ".join(r.raw_text for r in ocr_results)
                normalized = normalize_ocr_text(merged)
                if len(normalized.strip()) < 4:
                    return None

                matched = self.matcher.match(normalized)
                if matched is None:
                    return None
                if minimum_score is not None and matched.match_score < minimum_score:
                    return None

                qty = qty_map.get((col, row), 1)

                return InventoryItem(
                    display_name=matched.display_name,
                    url_name=matched.url_name,
                    quantity=qty,
                    match_score=matched.match_score,
                    raw_ocr=normalized,
                    grid_position=(col, row),
                )
            except Exception as e:
                print(f"[Scanner] Cell ({col},{row}) error: {e}")
                return None

        positions = [pos for pos, _ in label_cells]
        label_images = [img for _, img in label_cells]
        futures = [None] * len(label_cells)
        ocr_debug: dict[int, dict[str, str]] = {}

        if self._uses_windows_ocr:
            candidate_indices = list(range(len(label_images)))
        else:
            candidate_indices = [
                i for i, img in enumerate(label_images)
                if looks_like_label_text(img)
            ]
        candidate_positions = [positions[i] for i in candidate_indices]
        candidate_images = [label_images[i] for i in candidate_indices]
        primary_images = []

        # Primary pass: isolate gold label text. Because the grid/label boxes
        # are known, skip EasyOCR's expensive detector and recognize only the
        # computed text-line regions.
        if self._uses_windows_ocr:
            prepared_cells = [
                (preprocess_label_windows_raw(img), [])
                for img in candidate_images
            ]
        else:
            prepared_cells = [
                preprocess_label_with_regions(img)
                for img in candidate_images
            ]

        if (
            (
                getattr(config, "OCR_USE_LABEL_RECOGNIZER_ONLY", False)
            )
            and hasattr(self._ocr, "read_regions")
        ):
            primary_results = [
                self._ocr.read_regions(prepared, regions)
                if regions else []
                for prepared, regions in prepared_cells
            ]
        else:
            primary_images = [prepared for prepared, _ in prepared_cells]
            if hasattr(self._ocr, "read_batch"):
                primary_results = self._ocr.read_batch(primary_images, batch_size=config.OCR_BATCH_SIZE)
            else:
                primary_results = [self._ocr.read(img) for img in primary_images]

        windows_raw_by_index: dict[int, str] = {}
        for idx, pos, ocr_results in zip(candidate_indices, candidate_positions, primary_results):
            raw = " ".join(r.raw_text for r in ocr_results).strip()
            ocr_debug.setdefault(idx, {})["primary"] = raw
            if self._uses_windows_ocr:
                ocr_debug.setdefault(idx, {})["raw"] = raw
                if raw:
                    windows_raw_by_index[idx] = raw
            futures[idx] = _match_ocr_results(
                ocr_results,
                pos,
                getattr(config, "OCR_FAST_ACCEPT_THRESHOLD", 90)
                if getattr(config, "OCR_USE_LABEL_RECOGNIZER_ONLY", False)
                else None,
            )

        # Fallback pass only for cells that failed the gold-mask pass.
        # Fallback pass only for cells that failed the primary pass.
        failed_indices = [i for i in candidate_indices if futures[i] is None]
        fallback_preprocessors = []
        fallback_images = []
        fallback_results = []
        next_failed = []
        retry_indices = []
        if failed_indices:
            fallback_preprocessors = (
                [preprocess_label_windows_gray, preprocess_label_windows_binary]
                if self._uses_windows_ocr
                else [preprocess_label_raw]
            )
            for preprocessor in fallback_preprocessors:
                if not failed_indices:
                    break
                fallback_images = [preprocessor(label_images[i]) for i in failed_indices]
                if hasattr(self._ocr, "read_batch"):
                    fallback_results = self._ocr.read_batch(fallback_images, batch_size=config.OCR_BATCH_SIZE)
                else:
                    fallback_results = [self._ocr.read(img) for img in fallback_images]

                next_failed: list[int] = []
                for idx, ocr_results in zip(failed_indices, fallback_results):
                    raw = " ".join(r.raw_text for r in ocr_results).strip()
                    ocr_debug.setdefault(idx, {})[preprocessor.__name__] = raw
                    futures[idx] = _match_ocr_results(ocr_results, positions[idx])
                    if futures[idx] is None:
                        next_failed.append(idx)
                failed_indices = next_failed

            if getattr(config, "OCR_FALLBACK_ENABLED", False):
                min_score = int(getattr(config, "OCR_FALLBACK_MIN_SCORE", 85))
                low_confidence_indices = [
                    i for i in candidate_indices
                    if futures[i] is not None and futures[i].match_score < min_score
                ]
                retry_indices = list(dict.fromkeys([*failed_indices, *low_confidence_indices]))
            else:
                retry_indices = []

            if retry_indices:
                failed_indices = self._retry_failed_with_configured_ocr(
                    retry_indices,
                    label_images,
                    positions,
                    futures,
                    ocr_debug,
                    _match_ocr_results,
                )

            if (
                getattr(config, "INVENTORY_OCR_DEBUG_MISSES", False)
                or (
                    self._uses_windows_ocr
                    and getattr(config, "WINDOWS_OCR_DEBUG_MISSES", True)
                )
            ):
                for idx in failed_indices:
                    info = ocr_debug.get(idx, {})
                    debug_parts = " | ".join(
                        f"{key}='{value}'"
                        for key, value in info.items()
                    )
                    print(
                        f"[Scanner] {self._ocr_backend_name} miss {positions[idx]}: "
                        f"{debug_parts}"
                    )

        if (
            getattr(config, "INVENTORY_OCR_SAVE_DEBUG_SHEET", False)
            or (
                self._uses_windows_ocr
                and getattr(config, "WINDOWS_OCR_SAVE_DEBUG_SHEET", True)
            )
        ):
            self._save_inventory_ocr_debug_sheet(label_images, positions, futures, ocr_debug)

        for result in futures:
            if result is not None:
                items.append(result)
            else:
                failed += 1

        # Sort by grid position (left→right, top→bottom)
        items.sort(key=lambda i: (i.grid_position[1], i.grid_position[0]))

        # Deduplicate (same item appearing in multiple cells — shouldn't happen but just in case)
        seen: set[str] = set()
        deduped: list[InventoryItem] = []
        for item in items:
            if item.url_name not in seen:
                seen.add(item.url_name)
                deduped.append(item)
            else:
                # Merge quantity into existing entry
                for existing in deduped:
                    if existing.url_name == item.url_name:
                        existing.quantity += item.quantity
                        break

        t_end = time.time()
        duration = round(t_end - t_start, 2)
        print(f"[Scanner] ✅ Detected {len(deduped)} items in {duration}s "
              f"({failed} empty/unreadable cells)")

        result = InventoryScanResult(
            items=deduped,
            scan_duration_seconds=duration,
            frame_captured_at=t_capture,
            failed_cells=failed,
            failed_positions=[
                positions[index]
                for index, result in enumerate(futures)
                if result is None
            ],
        )
        # Drop full-screen/ROI images, expanded OCR inputs and result batches
        # promptly instead of keeping their buffers alive through caller UI work.
        del (
            frame,
            label_cells,
            badge_cells,
            label_images,
            candidate_positions,
            candidate_images,
            prepared_cells,
            primary_images,
            primary_results,
            windows_raw_by_index,
            failed_indices,
            fallback_preprocessors,
            fallback_images,
            fallback_results,
            next_failed,
            retry_indices,
            ocr_debug,
            futures,
            qty_map,
            positions,
        )
        gc.collect()
        return result

    def _get_configured_fallback_ocr(self):
        if self._fallback_ocr is None:
            from pillar2_ocr import create_fallback_ocr_engine
            fallback_name = str(getattr(config, "OCR_FALLBACK_ENGINE", "")).strip().lower()
            print(f"[Scanner] OCR fallback: loading {fallback_name} for missed cells...")
            self._fallback_ocr = create_fallback_ocr_engine(self.ocr_context)
        return self._fallback_ocr

    def _retry_failed_with_configured_ocr(
        self,
        failed_indices: list[int],
        label_images: list[np.ndarray],
        positions: list[tuple[int, int]],
        futures: list[InventoryItem | None],
        debug: dict[int, dict[str, str]],
        match_func,
    ) -> list[int]:
        fallback_ocr = self._get_configured_fallback_ocr()
        if fallback_ocr is None:
            return failed_indices
        fallback_label = getattr(
            fallback_ocr,
            "backend_name",
            fallback_ocr.__class__.__name__.replace("Engine", ""),
        )
        minimum_score = int(getattr(config, "OCR_FALLBACK_MIN_SCORE", 85))
        remaining: list[int] = []
        for idx in failed_indices:
            label = label_images[idx]
            prepared, regions = preprocess_label_with_regions(label)
            try:
                if regions and hasattr(fallback_ocr, "read_regions"):
                    ocr_results = fallback_ocr.read_regions(prepared, regions)
                else:
                    ocr_results = fallback_ocr.read(prepared)
            except Exception:
                ocr_results = []

            raw = " ".join(r.raw_text for r in ocr_results).strip()
            debug.setdefault(idx, {})[f"{fallback_label}_mask"] = raw
            result = match_func(ocr_results, positions[idx], minimum_score)
            if result is None:
                try:
                    raw_img = preprocess_label_raw(label)
                    ocr_results = fallback_ocr.read(raw_img)
                except Exception:
                    ocr_results = []
                raw = " ".join(r.raw_text for r in ocr_results).strip()
                debug.setdefault(idx, {})[f"{fallback_label}_raw"] = raw
                result = match_func(ocr_results, positions[idx], minimum_score)

            if result is not None:
                print(f"[Scanner] {fallback_label} recovered {positions[idx]}: {result.display_name}")
                futures[idx] = result
            else:
                if futures[idx] is None:
                    remaining.append(idx)
        return remaining

    def _save_inventory_ocr_debug_sheet(
        self,
        label_images: list[np.ndarray],
        positions: list[tuple[int, int]],
        futures: list[InventoryItem | None],
        debug: dict[int, dict[str, str]],
        filepath: str = "debug_inventory_ocr.png",
    ):
        try:
            cols = self.slicer.cols
            cell_w = 420
            cell_h = 190
            rows = max(1, int(np.ceil(len(label_images) / cols)))
            sheet = np.full((rows * cell_h, cols * cell_w, 3), 28, dtype=np.uint8)

            for idx, (label, pos) in enumerate(zip(label_images, positions)):
                row = idx // cols
                col = idx % cols
                x = col * cell_w
                y = row * cell_h
                cv2.rectangle(sheet, (x, y), (x + cell_w - 1, y + cell_h - 1), (80, 80, 80), 1)

                preview = (
                    preprocess_label_windows_raw(label)
                    if self._uses_windows_ocr
                    else preprocess_label_with_regions(label)[0]
                )
                ph, pw = preview.shape[:2]
                max_w = cell_w - 20
                max_h = 86
                scale = min(max_w / max(1, pw), max_h / max(1, ph))
                preview = cv2.resize(
                    preview,
                    (max(1, int(pw * scale)), max(1, int(ph * scale))),
                    interpolation=cv2.INTER_AREA,
                )
                sheet[y + 8:y + 8 + preview.shape[0], x + 10:x + 10 + preview.shape[1]] = preview

                result = futures[idx]
                status_color = (70, 190, 90) if result else (60, 80, 220)
                status = "OK" if result else "MISS"
                cv2.putText(
                    sheet,
                    f"{pos} {status}",
                    (x + 10, y + 112),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    status_color,
                    2,
                    cv2.LINE_AA,
                )
                if result:
                    lines = [
                        f"match: {result.display_name}",
                        f"score: {result.match_score} raw: {result.raw_ocr}",
                    ]
                else:
                    info = debug.get(idx, {})
                    lines = [
                        f"{key}: {value}"
                        for key, value in info.items()
                    ] or ["no OCR text"]

                for line_idx, text in enumerate(lines[:3]):
                    cv2.putText(
                        sheet,
                        text[:58],
                        (x + 10, y + 134 + line_idx * 18),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.42,
                        (230, 230, 230),
                        1,
                        cv2.LINE_AA,
                    )

            cv2.imwrite(filepath, sheet)
            print(f"[Scanner] OCR debug sheet saved -> {filepath}")
        except Exception as e:
            print(f"[Scanner] OCR debug sheet failed: {e}")

    def _save_windows_ocr_debug_sheet(
        self,
        label_images: list[np.ndarray],
        positions: list[tuple[int, int]],
        futures: list[InventoryItem | None],
        debug: dict[int, dict[str, str]],
        filepath: str = "debug_windows_ocr_inventory.png",
    ):
        self._save_inventory_ocr_debug_sheet(label_images, positions, futures, debug, filepath)

    def save_debug_grid(self, filepath: str = "debug_grid.png"):
        """
        Capture the inventory grid and draw cell boundaries + labels onto it.
        Useful for verifying ROI and grid alignment.
        """
        frame = self.grabber.capture(preprocess=False)
        if frame is None:
            print("[Scanner] No frame captured.")
            return

        debug = frame.copy()
        self.slicer.configure_for_frame(frame)
        cw = self.slicer.card_w
        ch = self.slicer.card_h
        sx = self.slicer.stride_x
        sy = self.slicer.stride_y
        label_y = self.slicer.label_y
        lh = self.slicer.label_h
        label_x_pad = self.slicer.label_x_pad
        cols = self.slicer.cols
        rows = self.slicer.rows

        for row in range(rows):
            for col in range(cols):
                x1 = col * sx
                y1 = self.slicer._card_y_for_row(row)
                x2 = x1 + cw
                y2 = y1 + ch
                lx1 = x1 + label_x_pad
                ly1 = y1 + label_y
                lx2 = x2 - label_x_pad
                ly2 = ly1 + lh

                # Full card border (gold)
                cv2.rectangle(debug, (x1, y1), (x2, y2), (0, 180, 210), 1)
                # Label strip (green)
                cv2.rectangle(debug, (lx1, ly1), (lx2, ly2), (80, 200, 80), 2)
                # Cell label
                cv2.putText(debug, f"({col},{self.slicer._display_row_for_row(row)})", (x1+4, y1+18),
                            cv2.FONT_HERSHEY_PLAIN, 0.9, (200, 200, 200), 1)

        cv2.imwrite(filepath, debug)
        print(f"[Scanner] Debug grid saved -> {filepath}")

    def save_debug_badges(self, filepath: str = "debug_badges.png"):
        """
        Save the badge crops used for quantity OCR as a contact sheet.
        Useful when quantities are read as 1 even though a badge is visible.
        """
        frame = self.grabber.capture(preprocess=False)
        if frame is None:
            print("[Scanner] No frame captured.")
            return

        badge_cells = self.slicer.slice_quantity_badges(frame)
        crops = []
        for (col, row), badge_img in badge_cells:
            preview = cv2.resize(badge_img, (184, 88), interpolation=cv2.INTER_NEAREST)
            # Red box marks the actual digit OCR crop. Left checkmark circle is
            # only used as a quantity-present gate.
            cv2.rectangle(preview, (68, 0), (164, 80), (0, 0, 255), 1)
            cv2.putText(preview, f"{col},{row}", (4, 14),
                        cv2.FONT_HERSHEY_PLAIN, 1.0, (255, 255, 255), 1)
            crops.append(preview)

        if not crops:
            print("[Scanner] No badge crops to save.")
            return

        cols = self.slicer.cols
        rows = []
        for i in range(0, len(crops), cols):
            row_crops = crops[i:i + cols]
            while len(row_crops) < cols:
                row_crops.append(np.zeros_like(crops[0]))
            rows.append(np.hstack(row_crops))
        sheet = np.vstack(rows)
        cv2.imwrite(filepath, sheet)
        print(f"[Scanner] Debug badge crops saved -> {filepath}")


# ──────────────────────────────────────────────
# QUICK TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    scanner = InventoryScanner()

    print("\nSaving debug grid overlay (check debug_grid.png to verify alignment)...")
    scanner.save_debug_grid("debug_grid.png")
    scanner.save_debug_badges("debug_badges.png")

    print("\nRunning full inventory scan...")
    result = scanner.scan()

    print(f"\n{'─'*55}")
    print(f"  SCAN RESULTS  ({result.scan_duration_seconds}s)")
    print(f"{'─'*55}")
    print(f"  {'QTY':<5} {'ITEM NAME':<40} {'SCORE'}")
    print(f"{'─'*55}")
    for item in result.items:
        score_bar = "█" * (item.match_score // 10) + "░" * (10 - item.match_score // 10)
        print(f"  {item.quantity:<5} {item.display_name:<40} {score_bar} {item.match_score}%")
    print(f"{'─'*55}")
    print(f"  Total: {len(result.items)} items | Failed cells: {result.failed_cells}")

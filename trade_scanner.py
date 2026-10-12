"""External screen reader for successful Warframe trades.

Checks the displayed player name, recognizes offered item slots and detects
the trade-success dialog. The caller uses these results to synchronize the
user's corresponding Warframe Market listings.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from thefuzz import process as fuzz_process, fuzz

import config
from name_aliases import name_alias_matches
from inventory_scanner import preprocess_label, preprocess_label_raw, preprocess_label_with_regions
from pillar1_capture import FrameGrabber
from pillar2_ocr import ItemsCache, FuzzyMatcher, normalize_ocr_text


@dataclass
class TradedItem:
    display_name: str
    url_name: str
    slot_index: int
    match_score: int
    raw_ocr: str


@dataclass
class TradeScanResult:
    account_name_seen: str
    name_verified: bool
    offered_items: list[TradedItem]


class TradeScanner:
    """Scans the Warframe Trading Post UI for your offered items."""

    def __init__(self):
        self.grabber = FrameGrabber()
        self.grabber.set_roi_preset("trade_screen")

        self.items_cache = ItemsCache()
        self.matcher = FuzzyMatcher(self.items_cache)

        from pillar2_ocr import create_ocr_engine
        self._ocr = create_ocr_engine("trade_monitor")
        self._title_gate_cache_signature = None
        self._title_gate_cache_at = 0.0
        self._title_gate_cache_result = False
        self._title_gate_cache_text = ""

        print("[Trade] Trade scanner ready.")

    def scan_current_trade(
        self,
        known_items: dict[str, str] | None = None,
        log: bool = True,
    ) -> TradeScanResult:
        frame = self.grabber.capture(preprocess=False)
        if frame is None:
            raise RuntimeError("Screen capture returned None.")
        return self._scan_current_trade_frame(frame, known_items, log=log)

    def _scan_current_trade_frame(
        self,
        frame: np.ndarray,
        known_items: dict[str, str] | None = None,
        log: bool = True,
    ) -> TradeScanResult:
        t0 = time.time()
        if not self._is_trade_screen(frame):
            if log:
                print("[Trade] Trading Post screen not detected.")
                print(f"[Trade] Trade screen scan completed in {time.time() - t0:.2f}s")
            return TradeScanResult(
                account_name_seen="",
                name_verified=False,
                offered_items=[],
            )

        # Warframe renders the local client at the top and the counterparty
        # below. Verify our name only at the top and scan only our six slots.
        row_offset = int(getattr(config, "TRADE_PLAYER_ROW_Y_OFFSET", 466))
        top_name = self._read_account_name(frame)
        bottom_name = self._read_account_name(frame, row_offset)
        account_name_seen = top_name
        name_verified = _trade_name_matches(config.TRADE_MY_NAME, top_name)
        own_side = "top" if name_verified else "unknown"

        offered_items = (
            self._read_slot_items_batch(frame, known_items, row_offset=0)
            if name_verified
            else []
        )

        if log:
            print(
                f"[Trade] Name OCR: self(top)='{top_name}' "
                f"counterparty(bottom)='{bottom_name}' "
                f"own_side={own_side} verified={name_verified}"
            )
            for item in offered_items:
                print(f"[Trade] Slot {item.slot_index + 1}: {item.display_name} "
                      f"({item.match_score}%, OCR='{item.raw_ocr}')")
            if name_verified and not offered_items:
                print("[Trade] No offered item labels detected on your side.")
            print(f"[Trade] Trade screen scan completed in {time.time() - t0:.2f}s")

        return TradeScanResult(
            account_name_seen=account_name_seen,
            name_verified=name_verified,
            offered_items=offered_items,
        )

    def wait_for_completed_trade(
        self,
        initial_trade: TradeScanResult,
        known_items: dict[str, str] | None = None,
        timeout_seconds: int | None = None,
        cancel_event=None,
    ) -> TradeScanResult | None:
        timeout = timeout_seconds or config.TRADE_SUCCESS_TIMEOUT_SECONDS
        poll = float(config.TRADE_SUCCESS_POLL_SECONDS)
        required_success_hits = int(getattr(config, "TRADE_SUCCESS_REQUIRED_HITS", 2))
        deadline = time.time() + timeout
        success_hits = 0
        latest_trade = initial_trade
        last_offer_refresh = 0.0
        queued = ", ".join(item.display_name for item in initial_trade.offered_items)

        print(f"[Trade] Queued offered item(s): {queued}")
        print(f"[Trade] Waiting up to {timeout}s for final successful trade dialog...")

        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                print("[Trade] Trade monitor aborted by user.")
                return None

            frame = self.grabber.capture(preprocess=False)
            if frame is None:
                time.sleep(poll)
                continue

            now = time.time()
            if self._is_success_dialog(frame):
                success_hits += 1
                if success_hits >= required_success_hits:
                    print("[Trade] Success dialog detected; trade is final.")
                    return latest_trade
            else:
                success_hits = 0
                # Live scanning can start as soon as the first slot is filled.
                # Refresh the own-side slots while waiting so later additions
                # are included in the listing sync after the trade completes.
                if now - last_offer_refresh >= max(0.5, poll):
                    last_offer_refresh = now
                    refreshed = self._scan_current_trade_frame(
                        frame,
                        known_items=known_items,
                        log=False,
                    )
                    if refreshed.name_verified and refreshed.offered_items:
                        old_signature = tuple(
                            sorted((item.url_name, item.slot_index) for item in latest_trade.offered_items)
                        )
                        new_signature = tuple(
                            sorted((item.url_name, item.slot_index) for item in refreshed.offered_items)
                        )
                        if new_signature != old_signature:
                            names = ", ".join(item.display_name for item in refreshed.offered_items)
                            print(f"[Trade] Updated offered items while waiting: {names}")
                        latest_trade = refreshed

            time.sleep(poll)

        print("[Trade] Timed out waiting for final success dialog.")
        self.save_success_debug_crop()
        return None

    def _is_trade_screen(self, frame: np.ndarray) -> bool:
        if not getattr(config, "TRADE_REQUIRE_SCREEN_GATE", True):
            return True

        header = _crop_rect(frame, _scaled_rect(getattr(config, "TRADE_SCREEN_HEADER_ROI", (200, 42, 520, 70)), frame))
        own_panel = _crop_rect(frame, _scaled_rect(getattr(config, "TRADE_SCREEN_OWN_PANEL_ROI", (214, 126, 490, 70)), frame))
        if header is None or own_panel is None:
            return False

        header_gold = _gold_ratio(header)
        own_edges = _edge_ratio(own_panel)
        min_gold = float(getattr(config, "TRADE_SCREEN_MIN_GOLD_RATIO", 0.0025))
        min_edges = float(getattr(config, "TRADE_SCREEN_MIN_EDGE_RATIO", 0.006))
        if header_gold < min_gold or own_edges < min_edges:
            return False

        if getattr(config, "TRADE_REJECT_INVENTORY_TITLE", True):
            if self._trade_title_is_inventory(frame):
                return False

        return True

    def _trade_title_is_inventory(self, frame: np.ndarray) -> bool:
        title_crop = _crop_rect(frame, _scaled_rect(getattr(config, "TRADE_SCREEN_TITLE_ROI", (180, 36, 650, 86)), frame))
        if title_crop is None:
            return False

        signature = _image_signature(title_crop)
        now = time.time()
        ttl = float(getattr(config, "TRADE_TITLE_OCR_CACHE_SECONDS", 0.8))
        if (
            self._title_gate_cache_signature == signature
            and now - self._title_gate_cache_at < ttl
        ):
            return self._title_gate_cache_result

        prepared = _preprocess_gold_title_text(title_crop)
        results = self._ocr.read(prepared)
        text = normalize_ocr_text(" ".join(r.raw_text for r in results))
        lowered = text.lower()
        compact = lowered.replace(" ", "")

        rejected = "inventory" in lowered or "inventory" in compact

        self._title_gate_cache_signature = signature
        self._title_gate_cache_at = now
        self._title_gate_cache_result = rejected
        self._title_gate_cache_text = text
        if rejected:
            print(f"[Trade] Header gate rejected inventory screen title: '{text}'")
        return rejected

    def _read_account_name(self, frame: np.ndarray, row_offset: int = 0) -> str:
        x, y, w, h = _scaled_rect(config.TRADE_MY_NAME_ROI, frame)
        y += _scaled_int(row_offset, "y", frame)
        crop = frame[y:y + h, x:x + w]
        if crop.size == 0:
            return ""
        prepared = _preprocess_white_text(crop)
        results = self._ocr.read(prepared)
        return " ".join(r.raw_text for r in results).strip()

    def _read_slot_item(
        self,
        frame: np.ndarray,
        slot_index: int,
        rect: tuple[int, int, int, int],
    ) -> Optional[TradedItem]:
        x, y, w, h = _scaled_rect(rect, frame)
        label_y = y + _scaled_int(config.TRADE_SLOT_LABEL_Y_OFFSET, "y", frame)
        label_h = _scaled_int(config.TRADE_SLOT_LABEL_HEIGHT, "y", frame)
        label_crop = frame[label_y:label_y + label_h, x:x + w]

        if _looks_empty_slot(label_crop):
            return None

        item = self._match_slot_results(slot_index, self._ocr.read(preprocess_label(label_crop)))
        if item is not None:
            return item
        return self._match_slot_results(slot_index, self._ocr.read(preprocess_label_raw(label_crop)))

    def _read_slot_items_batch(
        self,
        frame: np.ndarray,
        known_items: dict[str, str] | None = None,
        row_offset: int = 0,
    ) -> list[TradedItem]:
        jobs: list[tuple[int, np.ndarray, np.ndarray, list[list[int]], np.ndarray]] = []
        for idx, rect in enumerate(config.TRADE_SLOT_RECTS):
            x, y, w, h = _scaled_rect(rect, frame)
            y += _scaled_int(row_offset, "y", frame)
            label_y = y + _scaled_int(config.TRADE_SLOT_LABEL_Y_OFFSET, "y", frame)
            label_h = _scaled_int(config.TRADE_SLOT_LABEL_HEIGHT, "y", frame)
            label_crop = frame[label_y:label_y + label_h, x:x + w]
            card_crop = frame[y:y + h, x:x + w]
            prepared_image, regions = preprocess_label_with_regions(label_crop)
            jobs.append((idx, label_crop, prepared_image, regions, card_crop))

        if not jobs:
            return []

        if hasattr(self._ocr, "read_regions"):
            primary_results = [
                self._ocr.read_regions(image, regions) if regions else []
                for _, _, image, regions, _ in jobs
            ]
        elif hasattr(self._ocr, "read_batch"):
            primary_images = [
                image if regions else preprocess_label_raw(crop)
                for _, crop, image, regions, _ in jobs
            ]
            primary_results = self._ocr.read_batch(primary_images, batch_size=config.OCR_BATCH_SIZE)
        else:
            primary_images = [
                image if regions else preprocess_label_raw(crop)
                for _, crop, image, regions, _ in jobs
            ]
            primary_results = [self._ocr.read(img) for img in primary_images]

        found: dict[int, TradedItem] = {}
        failed: list[tuple[int, np.ndarray, np.ndarray]] = []

        for (slot_index, crop, _, _, card_crop), ocr_results in zip(jobs, primary_results):
            item = self._match_slot_results(slot_index, ocr_results, known_items)
            if item is None:
                failed.append((slot_index, crop, card_crop))
            else:
                found[slot_index] = item

        if failed:
            fallback_images = [preprocess_label_raw(crop) for _, crop, _ in failed]
            if hasattr(self._ocr, "read_batch"):
                fallback_results = self._ocr.read_batch(fallback_images, batch_size=config.OCR_BATCH_SIZE)
            else:
                fallback_results = [self._ocr.read(img) for img in fallback_images]

            unresolved: list[tuple[int, np.ndarray]] = []
            for (slot_index, _, card_crop), ocr_results in zip(failed, fallback_results):
                item = self._match_slot_results(slot_index, ocr_results, known_items)
                if item is not None:
                    found[slot_index] = item
                else:
                    unresolved.append((slot_index, card_crop))

            # Mods and a few special items render their names inside the card
            # tooltip instead of the bottom label strip. Only unresolved slots
            # get this broader, card-local OCR pass.
            for slot_index, card_crop in unresolved:
                ocr_results = self._ocr.read(_preprocess_white_text(card_crop))
                item = self._match_slot_results(slot_index, ocr_results, known_items)
                if item is not None:
                    found[slot_index] = item

        return [found[idx] for idx in sorted(found)]

    def _match_slot_results(
        self,
        slot_index: int,
        ocr_results,
        known_items: dict[str, str] | None = None,
    ) -> Optional[TradedItem]:
        if not ocr_results:
            return None

        merged = " ".join(r.raw_text for r in ocr_results)
        normalized = normalize_ocr_text(merged)
        if len(normalized) < 4 or "Platinum" in normalized:
            return None

        if known_items is not None:
            reverse = {display: url for url, display in known_items.items()}
            candidates = fuzz_process.extract(normalized, list(reverse.keys()), scorer=fuzz.WRatio, limit=3)
            if candidates:
                best_name, best_score = candidates[0]
                runner_up_score = candidates[1][1] if len(candidates) > 1 else 0
                match_gap = best_score - runner_up_score
                if (
                    best_score >= getattr(config, "TRADE_KNOWN_ITEM_MATCH_THRESHOLD", 88)
                    and match_gap >= getattr(config, "TRADE_KNOWN_ITEM_MATCH_MIN_GAP", 4)
                ):
                    return TradedItem(
                        display_name=best_name,
                        url_name=reverse[best_name],
                        slot_index=slot_index,
                        match_score=best_score,
                        raw_ocr=normalized,
                    )

        if getattr(config, "TRADE_DISABLE_GLOBAL_ITEM_MATCH", False):
            return None

        matched = self.matcher.match(normalized)
        if matched is None or matched.match_score < getattr(config, "TRADE_GLOBAL_ITEM_MATCH_THRESHOLD", 94):
            return None
        if matched.alternatives:
            runner_up_score = matched.alternatives[0][1]
            if matched.match_score - runner_up_score < getattr(config, "TRADE_GLOBAL_ITEM_MATCH_MIN_GAP", 4):
                return None
        if not matched.url_name:
            return None
        return TradedItem(
            display_name=matched.display_name,
            url_name=matched.url_name,
            slot_index=slot_index,
            match_score=matched.match_score,
            raw_ocr=normalized,
        )

    def _is_success_dialog(self, frame: np.ndarray) -> bool:
        x, y, w, h = _scaled_rect(config.TRADE_SUCCESS_DIALOG_ROI, frame)
        crop = frame[y:y + h, x:x + w]
        if not _looks_like_center_dialog(crop):
            return False
        if _looks_like_accept_confirm_dialog(crop):
            return False
        fast_result = _looks_like_success_dialog(crop)
        if fast_result is False:
            return False

        require_text = bool(getattr(config, "TRADE_SUCCESS_REQUIRE_TEXT", True))
        if not require_text and fast_result is True:
            return True
        if not require_text and not getattr(config, "TRADE_SUCCESS_USE_OCR_FALLBACK", False):
            return False

        prepared = _preprocess_white_text(crop)
        results = self._ocr.read(prepared)
        text = normalize_ocr_text(" ".join(r.raw_text for r in results))
        lowered = text.lower()
        if any(blocker in lowered for blocker in (
            "accept this trade",
            "are you sure",
            "offering",
            "will receive",
            "cancel",
        )):
            print(f"[Trade] Success dialog rejected by OCR text: '{text}'")
            return False

        success = (
            "trade was successful" in lowered
            or "trade was succesful" in lowered
            or "was successful" in lowered
            or "was succesful" in lowered
        )
        if success:
            print(f"[Trade] Success dialog confirmed by OCR: '{text}'")
        return success

    def save_debug_trade_slots(self, filepath: str = "debug_trade_slots.png"):
        frame = self.grabber.capture(preprocess=False)
        if frame is None:
            print("[Trade] No frame captured.")
            return

        debug = frame.copy()
        row_offset = int(getattr(config, "TRADE_PLAYER_ROW_Y_OFFSET", 466))
        for side, offset, color in (("top", 0, (0, 180, 210)), ("bottom", row_offset, (210, 120, 0))):
            name_rect = list(_scaled_rect(config.TRADE_MY_NAME_ROI, frame))
            name_rect[1] += _scaled_int(offset, "y", frame)
            nx, ny, nw, nh = name_rect
            cv2.rectangle(debug, (nx, ny), (nx + nw, ny + nh), color, 2)
            cv2.putText(debug, f"{side} name", (nx + 4, ny - 4), cv2.FONT_HERSHEY_PLAIN, 1.0, color, 1)
            for idx, rect in enumerate(config.TRADE_SLOT_RECTS):
                x, y, w, h = _scaled_rect(rect, frame)
                y += _scaled_int(offset, "y", frame)
                cv2.rectangle(debug, (x, y), (x + w, y + h), color, 1)
                ly = y + _scaled_int(config.TRADE_SLOT_LABEL_Y_OFFSET, "y", frame)
                cv2.rectangle(
                    debug,
                    (x, ly),
                    (x + w, ly + _scaled_int(config.TRADE_SLOT_LABEL_HEIGHT, "y", frame)),
                    (80, 200, 80),
                    2,
                )
                cv2.putText(debug, f"{side[0]}{idx + 1}", (x + 6, y + 22),
                            cv2.FONT_HERSHEY_PLAIN, 1.2, (255, 255, 255), 1)

        cv2.imwrite(filepath, debug)
        print(f"[Trade] Debug trade slots saved -> {filepath}")

    def save_success_debug_crop(self, filepath: str = "debug_trade_success_timeout.png"):
        frame = self.grabber.capture(preprocess=False)
        if frame is None:
            return
        x, y, w, h = _scaled_rect(config.TRADE_SUCCESS_DIALOG_ROI, frame)
        cv2.imwrite(filepath, frame[y:y + h, x:x + w])
        print(f"[Trade] Success timeout crop saved -> {filepath}")


def summarize_traded_counts(items: list[TradedItem]) -> Counter[str]:
    return Counter(item.url_name for item in items)


def _trade_name_matches(expected: str, observed: str) -> bool:
    """Compare OCR'd trade names while tolerating harmless OCR separators."""
    if name_alias_matches(expected, observed, getattr(config, "OCR_NAME_ALIASES", {})):
        return True
    expected_compact = "".join(ch.casefold() for ch in str(expected) if ch.isalnum())
    observed_compact = "".join(ch.casefold() for ch in str(observed) if ch.isalnum())
    if not expected_compact or not observed_compact:
        return False
    if expected_compact in observed_compact:
        return True
    # Short names are too collision-prone for fuzzy acceptance.
    if len(expected_compact) < 6:
        return False
    return fuzz.partial_ratio(expected_compact, observed_compact) >= 90


def _preprocess_white_text(image: np.ndarray) -> np.ndarray:
    scale = 2.5
    h, w = image.shape[:2]
    img = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LANCZOS4)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4))
    gray = clahe.apply(gray)
    _, binary = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    binary = cv2.dilate(binary, kernel, iterations=1)
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def _preprocess_gold_title_text(image: np.ndarray) -> np.ndarray:
    scale = 2.4
    h, w = image.shape[:2]
    img = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LANCZOS4)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (12, 20, 65), (55, 255, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    gold = cv2.dilate(gold, kernel, iterations=1)
    return cv2.cvtColor(gold, cv2.COLOR_GRAY2BGR)


def _looks_like_center_dialog(crop: np.ndarray) -> bool:
    """
    Cheap gate before OCR. The success/confirm dialogs add a rectangular border
    and bright centered text; normal trade screen crops are much darker/flatter.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    edge_ratio = float(np.count_nonzero(edges)) / edges.size
    bright_ratio = float(np.count_nonzero(gray > 135)) / gray.size
    return edge_ratio > 0.015 and bright_ratio > 0.002


def _looks_like_success_dialog(crop: np.ndarray) -> bool | None:
    """
    Distinguish the final success dialog from the earlier accept-confirm dialog.
    Success has one centered OK button line; confirm has OK + CANCEL.
    Returns None when uncertain so OCR can be used as fallback.
    """
    text_lines = _dialog_white_text_line_count(crop)
    if text_lines >= 3:
        return False

    layout = _dialog_button_layout(crop)
    if layout["left"] and layout["right"]:
        return False
    if layout["count"] == 1 and text_lines <= 2:
        return True
    if layout["count"] >= 2:
        return False
    if text_lines == 1 and _dialog_has_centered_white_text(crop):
        return True
    return None


def _looks_like_accept_confirm_dialog(crop: np.ndarray) -> bool:
    text_lines = _dialog_white_text_line_count(crop)
    if text_lines >= 3:
        return True
    layout = _dialog_button_layout(crop)
    return bool(layout["left"] and layout["right"])


def _dialog_button_layout(crop: np.ndarray) -> dict[str, bool | int]:
    h, w = crop.shape[:2]
    lower = crop[int(h * 0.55):int(h * 0.92), int(w * 0.18):int(w * 0.82)]
    hsv = cv2.cvtColor(lower, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (15, 20, 70), (55, 255, 255))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 2))
    gold = cv2.morphologyEx(gold, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(gold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centers: list[float] = []
    lower_w = max(1, lower.shape[1])
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        if cw >= 80 and 1 <= ch <= 14:
            centers.append((x + cw / 2.0) / lower_w)

    return {
        "count": len(centers),
        "left": any(center < 0.43 for center in centers),
        "center": any(0.43 <= center <= 0.57 for center in centers),
        "right": any(center > 0.57 for center in centers),
    }


def _dialog_white_text_line_count(crop: np.ndarray) -> int:
    """
    Success dialog has one centered text line. The accept-confirm dialog has
    several white text lines, so reject it before button-line heuristics.
    """
    h, w = crop.shape[:2]
    text_area = crop[int(h * 0.10):int(h * 0.56), int(w * 0.12):int(w * 0.88)]
    gray = cv2.cvtColor(text_area, cv2.COLOR_BGR2GRAY)
    mask = gray > 145
    row_counts = np.count_nonzero(mask, axis=1)
    active_rows = row_counts > max(8, int(text_area.shape[1] * 0.012))

    groups = 0
    in_group = False
    group_len = 0
    for active in active_rows:
        if active:
            group_len += 1
            in_group = True
        elif in_group:
            if group_len >= 3:
                groups += 1
            in_group = False
            group_len = 0
    if in_group and group_len >= 3:
        groups += 1
    return groups


def _dialog_has_centered_white_text(crop: np.ndarray) -> bool:
    h, w = crop.shape[:2]
    text_area = crop[int(h * 0.18):int(h * 0.45), int(w * 0.20):int(w * 0.80)]
    gray = cv2.cvtColor(text_area, cv2.COLOR_BGR2GRAY)
    mask = gray > 145
    if float(np.count_nonzero(mask)) / mask.size < 0.006:
        return False

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return False
    center_x = float(xs.mean()) / max(1, text_area.shape[1])
    center_y = float(ys.mean()) / max(1, text_area.shape[0])
    return 0.35 <= center_x <= 0.65 and 0.25 <= center_y <= 0.75


def _looks_empty_slot(label_crop: np.ndarray) -> bool:
    hsv = cv2.cvtColor(label_crop, cv2.COLOR_BGR2HSV)
    gold_mask = cv2.inRange(hsv, (12, 25, 80), (52, 255, 255))
    gold_ratio = float(np.count_nonzero(gold_mask)) / gold_mask.size
    return gold_ratio < 0.004


def _crop_rect(frame: np.ndarray, rect: tuple[int, int, int, int]) -> np.ndarray | None:
    x, y, w, h = rect
    frame_h, frame_w = frame.shape[:2]
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(frame_w, x + w), min(frame_h, y + h)
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def _scaled_int(value: int | float, axis: str, frame: np.ndarray) -> int:
    return config.scaled_int(value, axis, frame.shape)


def _scaled_rect(rect: tuple[int, int, int, int], frame: np.ndarray) -> tuple[int, int, int, int]:
    return config.scaled_rect(rect, frame.shape)


def _gold_ratio(crop: np.ndarray) -> float:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (12, 25, 80), (52, 255, 255))
    return float(np.count_nonzero(gold)) / gold.size


def _edge_ratio(crop: np.ndarray) -> float:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    return float(np.count_nonzero(edges)) / edges.size


def _image_signature(crop: np.ndarray) -> bytes:
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 8), interpolation=cv2.INTER_AREA)
    return small.tobytes()


if __name__ == "__main__":
    scanner = TradeScanner()
    scanner.save_debug_trade_slots()
    result = scanner.scan_current_trade()
    print(result)

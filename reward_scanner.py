"""Reward-card item recognition for the Void Fissure price overlay.

Reads card labels and matches them to Warframe Market item names. Price
requests and on-screen tags are handled by the application and Qt UI.
"""

from __future__ import annotations

import time
import zlib
from dataclasses import dataclass, field

import cv2
import numpy as np
from thefuzz import fuzz

import config
from name_aliases import aliases_for_account, normalize_alias_text
from reward_layout import detect_reward_card_rects, RewardHeadingTemplate
from inventory_scanner import looks_like_label_text, preprocess_label_raw, preprocess_label_with_regions
from pillar1_capture import FrameGrabber
from pillar2_ocr import create_ocr_engine, FuzzyMatcher, ItemsCache, normalize_ocr_text


@dataclass
class RewardItem:
    display_name: str
    url_name: str
    match_score: int
    raw_ocr: str
    slot_index: int
    card_rect: tuple[int, int, int, int]
    selected_by_you: bool = False
    alternatives: list[tuple[str, str, int]] = field(default_factory=list)


@dataclass
class RewardScanResult:
    items: list[RewardItem]
    scan_duration_seconds: float
    selected_slot: int | None = None


@dataclass(frozen=True)
class RewardSelectionResult:
    slot_index: int | None
    # None means capture failed; False means the reward screen is absent.
    screen_present: bool | None


class RewardScanner:
    """Scans the centered 1-4 card Void Fissure reward layouts."""

    def __init__(self):
        self.grabber = FrameGrabber()
        self.grabber.set_roi_preset("manual")
        self.items_cache = ItemsCache()
        self.matcher = FuzzyMatcher(self.items_cache, prime_only=True)
        self._ocr = create_ocr_engine("reward_prices")
        self._slot_ocr_cache: dict[int, tuple[int, float, RewardItem | None]] = {}
        self._selected_name_cache: dict[int, tuple[int, str]] = {}
        self._reward_title_cache: tuple[int, float, bool] | None = None
        self._reward_heading_template = None
        self._reward_card_layout = None
        print("[Reward] Reward scanner ready.")

    def scan(
        self,
        use_fallback: bool = True,
        frame: np.ndarray | None = None,
        detect_selected: bool = True,
    ) -> RewardScanResult:
        started = time.perf_counter()
        capture_started = started
        if frame is None:
            frame = self.grabber.capture(preprocess=False)
        capture_seconds = time.perf_counter() - capture_started
        if frame is None:
            raise RuntimeError("Screen capture returned None. Is Warframe running?")

        slot_cells: list[tuple[int, tuple[int, int, int, int], np.ndarray]] = []
        frame_h, frame_w = frame.shape[:2]
        for slot_idx, rect in enumerate(self._card_rects(frame)):
            x, y, w, h = rect
            pad = _scaled_int(getattr(config, "REWARD_LABEL_X_PAD", 8), "x", frame)
            label_y = y + _scaled_int(getattr(config, "REWARD_LABEL_Y_OFFSET", 178), "y", frame)
            label_h = _scaled_int(getattr(config, "REWARD_LABEL_HEIGHT", 72), "y", frame)
            x1 = max(0, x + pad)
            y1 = max(0, label_y)
            x2 = min(frame_w, x + w - pad)
            y2 = min(frame_h, label_y + label_h)
            if x2 <= x1 or y2 <= y1:
                continue
            slot_cells.append((slot_idx, rect, frame[y1:y2, x1:x2]))

        if not slot_cells:
            duration = round(time.time() - started, 2)
            return RewardScanResult(items=[], scan_duration_seconds=duration, selected_slot=None)

        preprocess_started = time.perf_counter()
        prepared_by_idx = {
            idx: preprocess_label_with_regions(crop)
            for idx, (_slot_idx, _rect, crop) in enumerate(slot_cells)
        }
        preprocess_seconds = time.perf_counter() - preprocess_started

        selected_seconds = 0.0
        selected_slot = None
        if detect_selected:
            selected_started = time.perf_counter()
            selected_slot = self._detect_selected_slot(frame)
            selected_seconds = time.perf_counter() - selected_started
        results_by_slot = [None] * len(slot_cells)
        dirty_indices: list[int] = []
        reused_count = 0
        for idx, (slot_idx, rect, _crop) in enumerate(slot_cells):
            prepared, regions = prepared_by_idx[idx]
            fingerprint = zlib.crc32(np.ascontiguousarray(prepared).view(np.uint8))
            fingerprint = zlib.crc32(repr(regions).encode("ascii"), fingerprint)
            cached = self._slot_ocr_cache.get(slot_idx)
            if (
                cached is not None
                and cached[0] == fingerprint
                and (cached[2] is not None or time.monotonic() < cached[1])
            ):
                cached_item = cached[2]
                if cached_item is not None:
                    results_by_slot[idx] = RewardItem(
                        display_name=cached_item.display_name,
                        url_name=cached_item.url_name,
                        match_score=cached_item.match_score,
                        raw_ocr=cached_item.raw_ocr,
                        slot_index=slot_idx,
                        card_rect=rect,
                        selected_by_you=slot_idx == selected_slot,
                        alternatives=list(cached_item.alternatives),
                    )
                reused_count += 1
            else:
                dirty_indices.append(idx)

        prepared_cells = [prepared_by_idx[idx] for idx in dirty_indices]
        primary_ocr_started = time.perf_counter()
        if hasattr(self._ocr, "read_regions"):
            primary_results = [
                self._ocr.read_regions(
                    prepared,
                    regions,
                    confidence_threshold=getattr(config, "REWARD_OCR_CONFIDENCE_THRESHOLD", 0.20),
                )
                if regions else []
                for prepared, regions in prepared_cells
            ]
        else:
            primary_images = [prepared for prepared, _ in prepared_cells]
            if hasattr(self._ocr, "read_batch"):
                primary_results = self._ocr.read_batch(
                    primary_images,
                    batch_size=min(config.OCR_BATCH_SIZE, len(primary_images)),
                )
            else:
                primary_results = [self._ocr.read(image) for image in primary_images]

        primary_ocr_seconds = time.perf_counter() - primary_ocr_started
        match_started = time.perf_counter()
        for idx, ocr_results in zip(dirty_indices, primary_results):
            slot_idx, rect, _crop = slot_cells[idx]
            matched = self._match_results(
                ocr_results,
                minimum_score=(
                    getattr(config, "REWARD_FAST_ACCEPT_THRESHOLD", 88)
                    if hasattr(self._ocr, "read_regions")
                    else None
                ),
            )
            if matched is not None:
                results_by_slot[idx] = RewardItem(
                    display_name=matched.display_name,
                    url_name=matched.url_name,
                    match_score=matched.match_score,
                    raw_ocr=matched.raw_ocr,
                    slot_index=slot_idx,
                    card_rect=rect,
                    selected_by_you=slot_idx == selected_slot,
                    alternatives=self._matched_alternatives(matched),
                )
        match_seconds = time.perf_counter() - match_started

        fallback_seconds = 0.0
        if use_fallback:
            failed_indices = [idx for idx in dirty_indices if results_by_slot[idx] is None]
            if failed_indices:
                fallback_started = time.perf_counter()
                fallback_images = [
                    preprocess_label_raw(slot_cells[idx][2])
                    for idx in failed_indices
                ]
                if hasattr(self._ocr, "read_batch"):
                    fallback_results = self._ocr.read_batch(
                        fallback_images,
                        batch_size=min(config.OCR_BATCH_SIZE, len(fallback_images)),
                    )
                else:
                    fallback_results = [self._ocr.read(image) for image in fallback_images]

                for idx, ocr_results in zip(failed_indices, fallback_results):
                    slot_idx, rect, _crop = slot_cells[idx]
                    matched = self._match_results(ocr_results)
                    if matched is None:
                        continue
                    results_by_slot[idx] = RewardItem(
                        display_name=matched.display_name,
                        url_name=matched.url_name,
                        match_score=matched.match_score,
                        raw_ocr=matched.raw_ocr,
                        slot_index=slot_idx,
                        card_rect=rect,
                        selected_by_you=slot_idx == selected_slot,
                        alternatives=self._matched_alternatives(matched),
                    )
                fallback_seconds = time.perf_counter() - fallback_started

        for idx in dirty_indices:
            slot_idx, _rect, _crop = slot_cells[idx]
            prepared, regions = prepared_by_idx[idx]
            fingerprint = zlib.crc32(np.ascontiguousarray(prepared).view(np.uint8))
            fingerprint = zlib.crc32(repr(regions).encode("ascii"), fingerprint)
            retry_after = time.monotonic() + 3.0 if results_by_slot[idx] is None else float("inf")
            self._slot_ocr_cache[slot_idx] = (
                fingerprint,
                retry_after,
                results_by_slot[idx],
            )

        # Keep cache bounded to the configured reward slots if layout changes.
        active_slots = {slot_idx for slot_idx, _rect, _crop in slot_cells}
        self._slot_ocr_cache = {
            slot: value for slot, value in self._slot_ocr_cache.items() if slot in active_slots
        }

        duration = round(time.perf_counter() - started, 2)
        items = [item for item in results_by_slot if item is not None]
        print(
            f"[Reward] Detected {len(items)}/{len(slot_cells)} market rewards in {duration}s "
            f"(capture={capture_seconds:.3f}s, selected={selected_seconds:.3f}s, "
            f"preprocess={preprocess_seconds:.3f}s, OCR={primary_ocr_seconds:.3f}s, "
            f"match={match_seconds:.3f}s, fallback={fallback_seconds:.3f}s, "
            f"reused={reused_count}/{len(slot_cells)})"
        )
        return RewardScanResult(
            items=items,
            scan_duration_seconds=duration,
            selected_slot=selected_slot,
        )

    def reward_screen_present(self, frame: np.ndarray | None = None) -> bool:
        """Verify the reward layout and heading without item-name OCR."""
        if frame is None:
            frame = self.grabber.capture(preprocess=False)
        if frame is None:
            return False
        return self._reward_screen_present_in_frame(frame)

    def selected_slot(self, frame: np.ndarray | None = None) -> int | None:
        """Return the selected slot, retaining the slot-only API for callers."""
        return self.selection_status(frame=frame).slot_index

    def selection_status(self, frame: np.ndarray | None = None,
                         *, screen_confirmed: bool = False) -> RewardSelectionResult:
        """Distinguish an unreadable selection from a closed or uncaptured screen."""
        # Only the monitor may supply a frame already validated in this session.
        # Buffered reward frames remain valid after the live screen closes;
        # repeating heading OCR must not invalidate their nickname evidence.
        if screen_confirmed and frame is None:
            return RewardSelectionResult(slot_index=None, screen_present=None)
        if frame is None:
            frame = self.grabber.capture(preprocess=False)
        if frame is None:
            return RewardSelectionResult(slot_index=None, screen_present=None)
        if not screen_confirmed and not self._reward_screen_present_in_frame(frame):
            return RewardSelectionResult(slot_index=None, screen_present=False)
        return RewardSelectionResult(slot_index=self._detect_selected_slot(frame), screen_present=True)

    def _detect_selected_slot_visual(self, frame: np.ndarray) -> int | None:
        frame_h, frame_w = frame.shape[:2]
        scores: list[tuple[int, float]] = []
        marker_w = _scaled_int(getattr(config, "REWARD_SELECTED_MARKER_WIDTH", 96), "x", frame)
        marker_h = _scaled_int(getattr(config, "REWARD_SELECTED_MARKER_HEIGHT", 54), "y", frame)
        marker_y_offset = _scaled_int(getattr(config, "REWARD_SELECTED_MARKER_Y_OFFSET", 244), "y", frame)

        for idx, (x, y, w, h) in enumerate(self._card_rects(frame)):
            cx = x + w // 2
            x1 = max(0, cx - marker_w // 2)
            x2 = min(frame_w, cx + marker_w // 2)
            y1 = max(0, y + marker_y_offset)
            y2 = min(frame_h, y1 + marker_h)
            if x2 <= x1 or y2 <= y1:
                scores.append((idx, 0.0))
                continue

            crop = frame[y1:y2, x1:x2]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            gold = cv2.inRange(hsv, (12, 35, 90), (55, 255, 255))
            white = cv2.inRange(hsv, (0, 0, 145), (180, 95, 255))
            bright = cv2.inRange(hsv, (0, 0, 175), (180, 255, 255))
            score = (
                float(np.count_nonzero(gold))
                + 2.6 * float(np.count_nonzero(white))
                + 0.35 * float(np.count_nonzero(bright))
            )
            scores.append((idx, score))

        if not scores:
            return None

        ranked = sorted(scores, key=lambda item: item[1], reverse=True)
        best_idx, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        min_score = float(getattr(config, "REWARD_SELECTED_MARKER_MIN_SCORE", 35))
        min_ratio = float(getattr(config, "REWARD_SELECTED_MARKER_RATIO", 1.22))

        if best_score < min_score:
            return None
        if second_score > 0 and best_score / second_score < min_ratio:
            return None

        print(f"[Reward] Selected slot detected visually: {best_idx + 1}")
        return best_idx

    def _reward_screen_present_in_frame(self, frame: np.ndarray) -> bool:
        if not self.reward_layout_present(frame):
            return False
        if getattr(self, "_reward_heading_template", None) is not None:
            return True  # reward_layout_present verified the known heading glyphs.
        tx, ty, tw, th = _scaled_rect(getattr(config, "REWARD_TITLE_ROI", (280, 38, 610, 72)), frame)
        title_crop = frame[max(0, ty):min(frame.shape[0], ty + th),
                           max(0, tx):min(frame.shape[1], tx + tw)]
        if not self._has_reward_screen_title(title_crop):
            return False
        self._reward_card_layout = (frame.shape[:2], tuple(self._card_rects(frame)))
        self._reward_heading_template = RewardHeadingTemplate(frame, (tx, ty, tw, th))
        return True

    def reset_session(self):
        self._reward_heading_template = None
        self._reward_card_layout = None

    def _card_rects(self, frame: np.ndarray) -> list[tuple]:
        layout = getattr(self, "_reward_card_layout", None)
        if layout is not None and layout[0] == frame.shape[:2]:
            return list(layout[1])
        return detect_reward_card_rects(
            frame, [_scaled_rect(rect, frame) for rect in getattr(config, "REWARD_CARD_RECTS", [])],
        )

    def reward_layout_present(self, frame: np.ndarray) -> bool:
        """Keep the confirmed heading visible, including during screen transitions."""
        template = getattr(self, "_reward_heading_template", None)
        if template is not None and not template.matches(frame):
            return False
        frame_h, frame_w = frame.shape[:2]
        tx, ty, tw, th = _scaled_rect(getattr(config, "REWARD_TITLE_ROI", (280, 38, 610, 72)), frame)
        tx1, ty1 = max(0, tx), max(0, ty)
        tx2, ty2 = min(frame_w, tx + tw), min(frame_h, ty + th)
        if tx2 <= tx1 or ty2 <= ty1:
            return False
        title_crop = frame[ty1:ty2, tx1:tx2]
        title_hsv = cv2.cvtColor(title_crop, cv2.COLOR_BGR2HSV)
        title_gold = cv2.inRange(title_hsv, (12, 25, 80), (52, 255, 255))
        title_gold_ratio = float(np.count_nonzero(title_gold)) / title_gold.size
        if title_gold_ratio < float(getattr(config, "REWARD_TITLE_GOLD_RATIO_MIN", 0.006)):
            return False
        visible_labels = 0
        rects = self._card_rects(frame)
        for x, y, w, h in rects:
            pad = _scaled_int(getattr(config, "REWARD_LABEL_X_PAD", 8), "x", frame)
            label_y = y + _scaled_int(getattr(config, "REWARD_LABEL_Y_OFFSET", 178), "y", frame)
            label_h = _scaled_int(getattr(config, "REWARD_LABEL_HEIGHT", 72), "y", frame)
            x1 = max(0, x + pad)
            y1 = max(0, label_y)
            x2 = min(frame_w, x + w - pad)
            y2 = min(frame_h, label_y + label_h)
            if x2 <= x1 or y2 <= y1:
                continue
            if looks_like_label_text(frame[y1:y2, x1:x2]):
                visible_labels += 1
        return bool(rects) and visible_labels >= min(2, len(rects))

    def _has_reward_screen_title(self, title_crop: np.ndarray) -> bool:
        """Require the Void Fissure rewards heading, not just gold UI text."""
        prepared, regions = preprocess_label_with_regions(title_crop)
        fingerprint = zlib.crc32(np.ascontiguousarray(prepared).view(np.uint8))
        fingerprint = zlib.crc32(repr(regions).encode("ascii"), fingerprint)
        cached = self._reward_title_cache
        if cached is not None and cached[0] == fingerprint:
            if cached[2] or time.monotonic() < cached[1]:
                return cached[2]

        try:
            if hasattr(self._ocr, "read_regions") and regions:
                results = self._ocr.read_regions(
                    prepared,
                    regions,
                    confidence_threshold=0.15,
                )
            elif hasattr(self._ocr, "read_whole"):
                results = self._ocr.read_whole(prepared, confidence_threshold=0.15)
            else:
                results = self._ocr.read(prepared)
        except Exception as exc:
            print(f"[Reward] Title OCR failed: {exc}")
            results = []

        title_text = _compact(normalize_ocr_text(" ".join(
            str(getattr(result, "raw_text", "") or "") for result in results or []
        )))
        is_reward_title = "fissure" in title_text and "reward" in title_text
        retry_after = float("inf") if is_reward_title else time.monotonic() + 1.5
        self._reward_title_cache = (fingerprint, retry_after, is_reward_title)
        return is_reward_title

    def _detect_selected_slot(self, frame: np.ndarray) -> int | None:
        own_name = str(getattr(config, "TRADE_MY_NAME", "") or "").strip()
        if own_name and getattr(config, "REWARD_SELECTED_SLOT_NAME_ENABLED", True):
            selected = self._detect_selected_slot_from_names(frame, own_name)
            if selected is not None:
                return selected
            if not getattr(config, "REWARD_SELECTED_SLOT_VISUAL_FALLBACK_WITH_NAME", False):
                return None

        if getattr(config, "REWARD_SELECTED_SLOT_VISUAL_ENABLED", True):
            return self._detect_selected_slot_visual(frame)

        return None

    def _detect_selected_slot_from_names(self, frame: np.ndarray, own_name: str) -> int | None:
        frame_h, frame_w = frame.shape[:2]
        name_y_offset = _scaled_int(getattr(config, "REWARD_SELECTED_NAME_Y_OFFSET", 252), "y", frame)
        name_h = _scaled_int(getattr(config, "REWARD_SELECTED_NAME_HEIGHT", 96), "y", frame)
        name_pad = _scaled_int(getattr(config, "REWARD_SELECTED_NAME_X_PAD", 36), "x", frame)
        min_score = int(getattr(config, "REWARD_SELECTED_NAME_MATCH_SCORE", 85))
        min_gap = int(getattr(config, "REWARD_SELECTED_NAME_MATCH_GAP", 6))
        target = _compact(own_name)
        if not target:
            return None
        aliases = aliases_for_account(own_name, getattr(config, "OCR_NAME_ALIASES", {}))

        candidates: list[tuple[int, int, str]] = []
        for idx, (x, y, w, h) in enumerate(self._card_rects(frame)):
            x1 = max(0, x - name_pad)
            x2 = min(frame_w, x + w + name_pad)
            y1 = max(0, y + name_y_offset)
            y2 = min(frame_h, y1 + name_h)
            if x2 <= x1 or y2 <= y1:
                continue

            crop = frame[y1:y2, x1:x2]
            fingerprint = zlib.crc32(np.ascontiguousarray(crop).view(np.uint8))
            cached = self._selected_name_cache.get(idx)
            if cached is not None and cached[0] == fingerprint:
                text = cached[1]
            else:
                text = self._read_name_crop_text(crop)
                self._selected_name_cache[idx] = (fingerprint, text)
            if not text:
                candidates.append((idx, 0, ""))
                continue
            # Match complete player names, preserving separate OCR attempts.
            # partial_ratio treats even "ori" as a perfect match for Hitakumori.
            names = {_compact(token) for token in text.split()}
            names.update(_compact(line) for line in text.splitlines())
            score = max(
                (fuzz.ratio(target, name) for name in names if len(name) >= len(target) * 0.8),
                default=0,
            )
            # Aliases match complete detected lines, never fragments or fuzzy
            # variants. Keep the same ambiguity checks across reward cards.
            if any(normalize_alias_text(line) in aliases for line in text.splitlines()):
                score = 100
            candidates.append((idx, score, text))

        if not candidates:
            return None

        ranked = sorted(candidates, key=lambda item: item[1], reverse=True)
        best_idx, best_score, best_text = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0
        if best_score < min_score or (second_score > 0 and best_score - second_score < min_gap):
            diagnostic = tuple(candidates)
            if getattr(self, "_last_unconfirmed_name_candidates", None) != diagnostic:
                details = "; ".join(
                    f"slot {idx + 1}: {score}% OCR={text!r}" for idx, score, text in candidates
                )
                print(f"[Reward] Own-name selection unconfirmed: {details}")
                self._last_unconfirmed_name_candidates = diagnostic
            return None

        self._last_unconfirmed_name_candidates = None
        print(
            f"[Reward] Selected slot detected from own name: {best_idx + 1} "
            f"(score={best_score}, OCR={best_text!r})"
        )
        return best_idx

    def _read_name_crop_text(self, crop: np.ndarray) -> str:
        try:
            attempts = [preprocess_label_raw(crop), crop]
        except Exception:
            attempts = [crop]

        seen: set[str] = set()
        texts: list[str] = []
        for image in attempts:
            try:
                # This area can contain several player names and selection
                # icons. RapidOCR's read_whole disables detection and squeezes
                # the whole area into one text line, losing parts of the names.
                results = self._ocr.read(image)
            except Exception as e:
                print(f"[Reward] Name OCR attempt failed: {e}")
                continue

            for result in results or []:
                text = str(getattr(result, "raw_text", "") or "").strip()
                if not text:
                    continue
                key = text.casefold()
                if not _compact(text) or key in seen:
                    continue
                seen.add(key)
                texts.append(text)

        return "\n".join(texts)

    def _match_results(self, results, minimum_score: int | None = None):
        if not results:
            return None
        merged = " ".join(result.raw_text for result in results)
        normalized = normalize_ocr_text(merged)
        if len(normalized) < 4:
            return None
        lowered = normalized.lower()
        for rejected in getattr(config, "REWARD_REJECT_TEXT_CONTAINS", ()):
            if rejected and rejected.lower() in lowered:
                print(f"[Reward] Ignoring overlay/chat text in reward crop: '{normalized}'")
                return None
        for ignored in getattr(config, "REWARD_IGNORE_TEXT_CONTAINS", ()):
            if ignored and ignored.lower() in lowered:
                print(f"[Reward] Ignoring non-market reward text: '{normalized}'")
                return None
        matched = self.matcher.match(normalized)
        if matched is None:
            return None
        if minimum_score is not None and matched.match_score < minimum_score:
            return None
        component = matched.display_name.casefold().partition(" prime ")[2]
        if matched.match_score < 100 and any(
            score >= matched.match_score - 2 and name.casefold().partition(" prime ")[2] != component
            for name, score in getattr(matched, "alternatives", []) or []
        ):
            print(f"[Reward] Ambiguous reward component; retrying instead of guessing: '{normalized}'")
            return None
        return matched

    def _matched_alternatives(self, matched) -> list[tuple[str, str, int]]:
        alternatives: list[tuple[str, str, int]] = []
        for name, score in getattr(matched, "alternatives", []) or []:
            url_name = self.items_cache.url_name(name) or ""
            if not url_name:
                continue
            alternatives.append((name, url_name, int(score)))
        return alternatives

    def close(self):
        self.grabber.close()


def _compact(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _scaled_int(value: int | float, axis: str, frame: np.ndarray) -> int:
    return config.scaled_int(value, axis, frame.shape)


def _scaled_rect(rect: tuple[int, int, int, int], frame: np.ndarray) -> tuple[int, int, int, int]:
    return config.scaled_rect(rect, frame.shape)

"""Pixel-based reward-card layout and heading detection.

Finds supported card geometry and screen-transition signals without loading
OCR models. These checks can run on the capture side of the reward workflow.
"""
from __future__ import annotations

import cv2
import numpy as np


def detect_reward_card_rects(frame: np.ndarray, full_layout: list[tuple]) -> list[tuple]:
    """Find the centered 1-4 card layout from its long vertical borders.

    Use the configured four-card geometry as calibration. Choosing the largest
    supported group avoids treating the middle card of a three-card row as solo.
    Without enough border evidence, preserve that calibrated layout.
    """
    if len(full_layout) < 2:
        return full_layout
    frame_h, frame_w = frame.shape[:2]
    width, height = full_layout[0][2:]
    y = full_layout[0][1]
    centers = [x + w / 2 for x, _, w, _ in full_layout]
    center = (centers[0] + centers[-1]) / 2
    step = (centers[-1] - centers[0]) / (len(centers) - 1)
    y1, y2 = max(0, y + 1), min(frame_h, y + height - 2)
    if y2 <= y1 or step <= 0:
        return full_layout
    band = frame[y1:y2]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (12, 25, 25), (52, 255, 255))
    gold_coverage = np.mean(gold > 0, axis=0)
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
    contrast = np.mean(cv2.absdiff(gray[:, 2:], gray[:, :-2]) > 6, axis=0)
    margin = max(2, round(width / 60))
    candidates = []
    for count in range(1, len(full_layout) + 1):
        rects = [(round(center + (i - (count - 1) / 2) * step - width / 2), y, width, height)
                 for i in range(count)]
        strong_edges = 0
        for x, _, w, _ in rects:
            for column in (x, x + w):
                start, end = max(0, column - margin), min(frame_w, column + margin + 1)
                if end <= start or start >= len(contrast):
                    continue
                if (gold_coverage[start:end].max() >= 0.65
                        and contrast[max(0, start - 1):min(len(contrast), end)].max() >= 0.80):
                    strong_edges += 1
        if strong_edges >= max(2, 2 * count - 1):
            candidates.append((strong_edges, count, rects))
    if not candidates:
        return full_layout
    return max(candidates, key=lambda candidate: candidate[:2])[2]


class RewardHeadingTemplate:
    """Recognize the glyphs of a heading already confirmed by OCR this session."""

    def __init__(self, frame: np.ndarray, rect: tuple):
        self._rect = rect
        self._shape = frame.shape[:2]
        self._mask = self._heading_mask(frame)
        self._expanded = cv2.dilate(self._mask, np.ones((3, 3), np.uint8))
        self._count = np.count_nonzero(self._mask)
        self._brightness = self._glyph_brightness(frame)

    def _crop(self, frame):
        x, y, w, h = self._rect
        return frame[max(0, y):min(frame.shape[0], y + h), max(0, x):min(frame.shape[1], x + w)]

    def _glyph_brightness(self, frame):
        crop = self._crop(frame)
        if not crop.size or not self._count:
            return 0.0
        values = np.max(crop, axis=2)
        return float(np.median(values[self._mask > 0]))

    def _heading_mask(self, frame):
        crop = self._crop(frame)
        if not crop.size:
            return np.zeros((1, 1), np.uint8)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        return cv2.inRange(hsv, (12, 25, 80), (52, 255, 255))

    def matches(self, frame: np.ndarray) -> bool:
        if frame.shape[:2] != self._shape or not self._count:
            return False
        current = self._heading_mask(frame)
        current_count = np.count_nonzero(current)
        if current.shape != self._mask.shape or not current_count:
            return False
        expanded = cv2.dilate(current, np.ones((3, 3), np.uint8))
        recall = np.count_nonzero(cv2.bitwise_and(self._mask, expanded)) / self._count
        precision = np.count_nonzero(cv2.bitwise_and(self._expanded, current)) / current_count
        # Keep the last clear frame before the UI fades, while the smaller
        # player names are still readable. Gold background alone is insufficient.
        return (recall >= 0.80 and precision >= 0.65
                and self._glyph_brightness(frame) >= self._brightness * 0.85)

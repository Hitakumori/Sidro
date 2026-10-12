"""Offline evaluator for saved inventory screenshots.

Runs InventoryScanner against a local image and optionally compares the
recognized grid with a ground-truth JSON file. This is a development tool;
private screenshots and evaluation output must not enter the public snapshot.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate inventory OCR against a saved screenshot.")
    parser.add_argument("image", type=Path, help="Full screenshot or already-cropped inventory ROI image.")
    parser.add_argument("--expected", type=Path, help="Optional ground-truth JSON file.")
    parser.add_argument("--out", type=Path, help="Optional JSON output path for raw scan results.")
    parser.add_argument(
        "--ocr",
        choices=["easyocr", "rapidocr", "paddleocr", "windowsocr"],
        default=None,
        help="Override OCR backend for this run.",
    )
    parser.add_argument(
        "--mode",
        choices=["6x3", "bottom6"],
        default="6x3",
        help="Inventory grid mode to evaluate.",
    )
    parser.add_argument(
        "--cropped",
        action="store_true",
        help="Treat image as already cropped to the inventory_sell ROI.",
    )
    return parser.parse_args()


def _normalize_expected_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "empty", "-"}:
        return None
    return text


def _load_expected(
    path: Path,
    *,
    cols: int,
    rows: int,
    row_start: int,
) -> dict[tuple[int, int], str | None]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "cells" in data:
        data = data["cells"]

    expected: dict[tuple[int, int], str | None] = {}
    if isinstance(data, list):
        for idx, value in enumerate(data):
            row = row_start + idx // cols
            col = idx % cols
            if row >= row_start + rows:
                break
            expected[(col, row)] = _normalize_expected_name(value)
        return expected

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str):
                parts = key.replace(";", ",").split(",")
                if len(parts) != 2:
                    raise ValueError(f"Bad expected cell key: {key!r}. Use 'col,row'.")
                col, row = int(parts[0].strip()), int(parts[1].strip())
            else:
                raise ValueError(f"Bad expected cell key: {key!r}. Use 'col,row'.")
            expected[(col, row)] = _normalize_expected_name(value)
        return expected

    raise ValueError("Expected JSON must be either a row-major list or an object with a 'cells' object.")


def _load_frame(image_path: Path, *, config, cropped: bool):
    import cv2

    frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError(f"Could not read image: {image_path}")

    if cropped:
        return frame

    frame_h, frame_w = frame.shape[:2]
    left, top, width, height = config.scaled_rect(
        config.ROI_PRESETS["inventory_sell"],
        frame.shape,
    )
    if frame_w >= left + width and frame_h >= top + height:
        return frame[top:top + height, left:left + width]
    return frame


def _scanner_for_mode(mode: str, *, config, InventoryScanner):
    if mode == "bottom6":
        return InventoryScanner(
            rows=getattr(config, "INVENTORY_BOTTOM_GRID_ROWS", 1),
            row_start=getattr(config, "INVENTORY_BOTTOM_ROW_START", 3),
            row_y_offset=getattr(config, "INVENTORY_BOTTOM_ROW_Y_OFFSET", 590),
            label="bottom 6 eval",
            enable_capture=False,
        )
    return InventoryScanner(label="6x3 eval", enable_capture=False)


def _mode_geometry(mode: str, *, config) -> tuple[int, int, int]:
    cols = int(config.INVENTORY_GRID_COLS)
    if mode == "bottom6":
        return cols, int(getattr(config, "INVENTORY_BOTTOM_GRID_ROWS", 1)), int(getattr(config, "INVENTORY_BOTTOM_ROW_START", 3))
    return cols, int(config.INVENTORY_GRID_ROWS), 0


def _write_output(path: Path, result) -> None:
    payload = {
        "duration_seconds": result.scan_duration_seconds,
        "failed_cells": result.failed_cells,
        "items": [
            {
                "position": list(item.grid_position),
                "name": item.display_name,
                "url_name": item.url_name,
                "quantity": item.quantity,
                "score": item.match_score,
                "raw_ocr": item.raw_ocr,
            }
            for item in result.items
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _print_actual_only(result) -> int:
    print("\nDetected items:")
    for item in result.items:
        col, row = item.grid_position
        print(f"  ({col},{row}) {item.quantity}x {item.display_name} [{item.match_score}%] raw={item.raw_ocr!r}")
    print(f"\nTotal: {len(result.items)} item(s), failed cells: {result.failed_cells}, duration: {result.scan_duration_seconds}s")
    return 0


def _compare(result, expected: dict[tuple[int, int], str | None]) -> int:
    actual = {item.grid_position: item for item in result.items}
    positions = sorted(set(expected) | set(actual), key=lambda pos: (pos[1], pos[0]))

    ok = missing = wrong = unexpected = 0
    print("\nCell comparison:")
    print(f"{'POS':<7} {'STATUS':<10} {'EXPECTED':<38} {'ACTUAL':<38} {'SCORE':<6} RAW")
    print("-" * 120)
    for pos in positions:
        expected_name = expected.get(pos)
        item = actual.get(pos)
        actual_name = item.display_name if item else None

        if expected_name and actual_name == expected_name:
            status = "OK"
            ok += 1
        elif expected_name and actual_name is None:
            status = "MISSING"
            missing += 1
        elif expected_name and actual_name != expected_name:
            status = "WRONG"
            wrong += 1
        elif expected_name is None and actual_name:
            status = "UNEXPECTED"
            unexpected += 1
        else:
            status = "OK"
            ok += 1

        score = f"{item.match_score}%" if item else ""
        raw = item.raw_ocr if item else ""
        pos_text = f"{pos[0]},{pos[1]}"
        print(
            f"{pos_text:<7} {status:<10} "
            f"{(expected_name or '-'): <38.38} "
            f"{(actual_name or '-'): <38.38} "
            f"{score:<6} {raw}"
        )

    failures = missing + wrong + unexpected
    print("-" * 120)
    print(
        f"Summary: ok={ok}, missing={missing}, wrong={wrong}, "
        f"unexpected={unexpected}, duration={result.scan_duration_seconds}s"
    )
    return 1 if failures else 0


def main() -> int:
    args = _parse_args()
    if args.ocr:
        os.environ["TTB_OCR_ENGINE"] = args.ocr

    import config
    from inventory_scanner import InventoryScanner

    cols, rows, row_start = _mode_geometry(args.mode, config=config)
    frame = _load_frame(args.image, config=config, cropped=args.cropped)
    scanner = _scanner_for_mode(args.mode, config=config, InventoryScanner=InventoryScanner)
    result = scanner.scan_frame(frame)

    if args.out:
        _write_output(args.out, result)
        print(f"[Eval] Wrote raw scan output -> {args.out}")

    if not args.expected:
        return _print_actual_only(result)

    expected = _load_expected(args.expected, cols=cols, rows=rows, row_start=row_start)
    return _compare(result, expected)


if __name__ == "__main__":
    raise SystemExit(main())

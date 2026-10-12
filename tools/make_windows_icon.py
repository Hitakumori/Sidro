from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Sidrobot.png"
TARGET = ROOT / "Sidro.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    if not SOURCE.exists() or SOURCE.stat().st_size <= 0:
        print("[Build] Sidrobot.png not found or empty; skipping icon generation.")
        return 0

    image = Image.open(SOURCE).convert("RGBA")
    image.save(TARGET, format="ICO", sizes=SIZES)
    print(f"[Build] Windows icon generated: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

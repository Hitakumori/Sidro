"""Check or regenerate complete OCR repair manifests from the shared lock."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.bundle_release_materials import exact_pins
from tools.check_dependencies import active_requirements, installed_snapshot, provider_name, validate_snapshot


def repair_closure(seeds, versions, requires):
    names = set(seeds)
    for consumer, requirement in active_requirements(requires, versions, seeds):
        names.add(consumer)
        names.add(provider_name(consumer, requirement, versions))
    return {name: versions[name] for name in sorted(names)}


def repair_roots(versions):
    return {
        "requirements-rapidocr-test.txt": ["rapidocr", "onnxruntime", "numpy"],
        "requirements-paddleocr-test.txt": ["paddleocr", "paddlepaddle", "paddlex", "onnxruntime", "numpy"],
        "requirements-windowsocr.txt": sorted(name for name in versions if name.startswith("winrt-windows-")),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Update manifests; default only checks them")
    args = parser.parse_args()
    versions, requires = installed_snapshot()
    errors, _ = validate_snapshot(exact_pins(ROOT / "requirements-release.lock"), versions, requires)
    if errors:
        raise SystemExit("\n".join(errors))
    for filename, seeds in repair_roots(versions).items():
        pins = repair_closure(seeds, versions, requires)
        if args.write:
            header = "# Complete locked repair closure. Install with --no-deps to preserve the\n# single OpenCV provider and transitive versions used by the source/release lock.\n"
            (ROOT / filename).write_text(header + "\n".join(f"{name}=={version}" for name, version in pins.items()) + "\n", encoding="utf-8")
        elif exact_pins(ROOT / filename) != pins:
            raise SystemExit(f"Stale repair manifest: {filename}; run with --write")
        print(f"{filename}: {len(pins)} exact pins verified")

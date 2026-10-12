"""Verify the shared source/release baseline, including PaddleX's OCR extra."""
from __future__ import annotations

import argparse
from collections import deque
import importlib.metadata as metadata
from pathlib import Path
import platform
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.bundle_release_materials import exact_pins

OPENCV_PROVIDERS = {
    "opencv-python", "opencv-python-headless",
    "opencv-contrib-python", "opencv-contrib-python-headless",
}


def provider_name(consumer, requirement, versions):
    name = canonicalize_name(requirement.name)
    # Contrib contains the standard OpenCV modules. Only this reviewed RapidOCR
    # release may substitute its named dependency; never fabricate metadata or
    # accept a missing package for another consumer.
    if (name == "opencv-python" and name not in versions
            and consumer == "rapidocr" and versions.get(consumer) == "3.9.2"):
        return "opencv-contrib-python"
    return name


def active_requirements(requires, versions, roots=None):
    """Walk base dependencies and recursively activated extras with markers."""
    queue = deque((canonicalize_name(name), "") for name in (requires if roots is None else roots))
    visited = set()
    result = []
    while queue:
        consumer, extra = queue.popleft()
        if (consumer, extra) in visited:
            continue
        visited.add((consumer, extra))
        for text in requires.get(consumer, ()):
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate({"extra": extra}):
                continue
            result.append((consumer, requirement))
            name = provider_name(consumer, requirement, versions)
            queue.extend((name, item) for item in ("", *requirement.extras))
    return result


def validate_snapshot(pins, versions, requires):
    errors = []
    substitutions = set()
    for name, expected in pins.items():
        if versions.get(name) != expected:
            errors.append(f"{name}: installed {versions.get(name, 'missing')}; lock requires {expected}")
    for name in sorted(versions.keys() - pins.keys()):
        errors.append(f"Unpinned package in the shared environment: {name}")
    providers = OPENCV_PROVIDERS & versions.keys()
    if providers != {"opencv-contrib-python"}:
        errors.append(f"Expected one OpenCV provider (opencv-contrib-python); found {sorted(providers)}")
    for consumer, requirement in active_requirements(requires, versions):
        name = provider_name(consumer, requirement, versions)
        actual = versions.get(name)
        if actual is None or actual not in requirement.specifier:
            errors.append(f"{consumer} requires {requirement}; {name} is {actual or 'missing'}")
        elif name != canonicalize_name(requirement.name):
            substitutions.add(f"{consumer}: {requirement.name} supplied by {name} {actual}")
    return sorted(set(errors)), sorted(substitutions)


def installed_snapshot():
    versions, requires = {}, {}
    for distribution in metadata.distributions():
        name = canonicalize_name(distribution.metadata["Name"])
        if name in versions:
            raise RuntimeError(f"Duplicate installed distribution metadata: {name}")
        versions[name] = distribution.version
        requires[name] = distribution.requires or []
    return versions, requires


def check_environment(lock=ROOT / "requirements-release.lock"):
    if sys.version_info[:2] != (3, 12) or sys.platform != "win32" or platform.machine().lower() != "amd64":
        raise RuntimeError("This baseline requires Windows x64 and Python 3.12.")
    pins = exact_pins(lock)
    versions, requires = installed_snapshot()
    errors, substitutions = validate_snapshot(pins, versions, requires)
    if errors:
        raise RuntimeError("\n".join(errors))
    import cv2
    expected = ".".join(pins["opencv-contrib-python"].split(".")[:3])
    if cv2.__version__ != expected:
        raise RuntimeError(f"Loaded cv2 {cv2.__version__}; provider metadata requires {expected}")
    environment = Path(sys.prefix).resolve()
    if not Path(cv2.__file__).resolve().is_relative_to(environment):
        raise RuntimeError("cv2 is being loaded outside the selected environment")
    return len(pins), substitutions


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=ROOT / "requirements-release.lock")
    args = parser.parse_args()
    try:
        count, substitutions = check_environment(args.lock)
    except Exception as error:
        print(f"Dependency baseline check failed:\n{error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Dependency baseline verified: {count} exact pins; one OpenCV provider.")
    for substitution in substitutions:
        print(f"Reviewed provider substitution: {substitution}")

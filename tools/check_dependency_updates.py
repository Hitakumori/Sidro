"""Read PyPI's latest stable releases and explain baseline compatibility limits."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.request import urlopen

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.bundle_release_materials import exact_pins
from tools.check_dependencies import active_requirements, installed_snapshot, provider_name


def latest_stable(name):
    with urlopen(f"https://pypi.org/pypi/{name}/json", timeout=45) as response:
        releases = json.load(response)["releases"]
    candidates = [Version(version) for version, files in releases.items()
                  if files and any(not item.get("yanked") for item in files)
                  and not Version(version).is_prerelease and not Version(version).is_devrelease]
    return str(max(candidates))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Save the read-only update report as JSON")
    args = parser.parse_args()
    pins = exact_pins(ROOT / "requirements-release.lock")
    versions, requires = installed_snapshot()
    if any(versions.get(name) != version for name, version in pins.items()):
        raise SystemExit("Run this check from the shared .venv-sidro matching the lock.")
    with ThreadPoolExecutor(max_workers=8) as pool:
        latest = dict(zip(sorted(pins), pool.map(latest_stable, sorted(pins))))
    requirements = active_requirements(requires, versions)
    rows = []
    for name, installed in sorted(pins.items()):
        if Version(latest[name]) <= Version(installed):
            continue
        blockers = sorted({f"{consumer} {versions[consumer]}: {requirement}"
                           for consumer, requirement in requirements
                           if provider_name(consumer, requirement, versions) == name
                           and latest[name] not in requirement.specifier})
        rows.append({"name": name, "baseline": installed, "latest_stable": latest[name], "upstream_constraints": blockers})
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "packages_checked": len(pins), "newer_releases": rows}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Checked {len(pins)} locked packages against PyPI stable releases.")
    for row in rows:
        reasons = "; ".join(row["upstream_constraints"]) or "Review compatibility, wheel availability and source-test this upgrade."
        print(f"{row['name']}: {row['baseline']} -> {row['latest_stable']} | {reasons}")
    raise SystemExit(1 if any(not row["upstream_constraints"] for row in rows) else 0)

"""Copy maintained distribution documents, dependency pins and license notices."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "release_materials"
PROJECT_FILES = ("LICENSE",)
BUNDLED_OCR_BACKENDS = ("rapidocr",)
UNBUNDLED_RUNTIME_MODULES = {
    "paddle", "paddleocr", "paddlepaddle", "paddlex", "winrt",
    "easyocr", "torch", "torchvision", "modelscope", "modelscope-hub",
    "bidi", "python-bidi", "crc32c",
}
MANIFESTS = (
    "requirements.txt", "requirements-release.lock", "requirements-windowsocr.txt",
    "requirements-rapidocr-test.txt", "requirements-paddleocr-test.txt",
)
REQUIRED_MATERIALS = (
    "RELEASE_REQUIREMENTS.md", "THIRD_PARTY_NOTICES.md", "DEPENDENCY_BASELINE.md",
    "licenses/qt/LGPL-3.0-only.txt", "licenses/qt/GPL-3.0-only.txt",
    "licenses/qt/SOURCES_AND_REPLACEMENT.md", "licenses/qt/manifest.json",
    "licenses/supplemental/manifest.json", "sources/manifest.json",
    "licenses/NATIVE_SOURCES_AND_REPLACEMENT.md",
)


def maintained_assets() -> list[dict]:
    assets = []
    for relative, key in (("licenses/supplemental/manifest.json", "notices"),
                          ("sources/manifest.json", "sources")):
        assets.extend(json.loads((MATERIALS / relative).read_text(encoding="utf-8"))[key])
    for item in assets:
        path = Path(item["file"])
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError("Unsafe maintained release material path")
    return assets


def exact_pins(path: Path) -> dict[str, str]:
    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("-r "):
            pins.update(exact_pins(path.parent / line[3:].strip()))
        elif line:
            match = re.fullmatch(r"([\w.-]+)==([^\s;]+)", line)
            if not match:
                raise RuntimeError(f"Expected an exact release dependency pin: {line}")
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            if name in pins and pins[name] != match[2]:
                raise RuntimeError(f"Conflicting dependency pins for {name}")
            pins[name] = match[2]
    return pins


def verify_release_materials(build: Path) -> None:
    for relative in (*REQUIRED_MATERIALS, *PROJECT_FILES, *MANIFESTS,
                     "licenses/python/LICENSE.txt", "DEPENDENCY_INVENTORY.json"):
        path = build / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Missing release distribution material: {relative}")
    for relative in (*REQUIRED_MATERIALS, *PROJECT_FILES, *MANIFESTS):
        original = MATERIALS / relative if relative in REQUIRED_MATERIALS else ROOT / relative
        if (build / relative).read_bytes() != original.read_bytes():
            raise RuntimeError(f"Stale release distribution material: {relative}; run the bundler again")
    manifest = json.loads((build / "licenses/qt/manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["license_sha256"].items():
        actual = hashlib.sha256((build / "licenses/qt" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Qt license text differs from its reviewed upstream copy: {name}")
    inventory = json.loads((build / "DEPENDENCY_INVENTORY.json").read_text(encoding="utf-8"))
    if inventory.get("bundled_ocr_backends") != list(BUNDLED_OCR_BACKENDS):
        raise RuntimeError("Release OCR profile must bundle RapidOCR only")
    for path in (build / "_internal").iterdir():
        name = path.name.lower().replace("_", "-")
        if any(name == module or name.startswith((module + "-", module + "."))
               for module in UNBUNDLED_RUNTIME_MODULES):
            raise RuntimeError(f"Unbundled OCR dependency in release: {path.name}")
    packages = {item["name"]: item for item in inventory["packages"]}
    pins = exact_pins(build / "requirements-release.lock")
    if {name: item["version"] for name, item in packages.items()} != pins:
        raise RuntimeError("Release dependency inventory does not match the bundled lock")
    if (manifest["pyside_version"] != pins["pyside6-essentials"]
            or manifest["shiboken_version"] != pins["shiboken6"]
            or inventory["qt_version"] != manifest["qt_version"]):
        raise RuntimeError("Reviewed Qt versions do not match the dependency inventory")
    sources = (build / "licenses/qt/SOURCES_AND_REPLACEMENT.md").read_text(encoding="utf-8")
    for archive in (
        f"qtbase-everywhere-src-{manifest['qt_version']}.tar.xz",
        f"qtsvg-everywhere-src-{manifest['qt_version']}.tar.xz",
        f"pyside-setup-everywhere-src-{manifest['pyside_version']}.tar.xz",
    ):
        if archive not in sources:
            raise RuntimeError(f"Matching Qt source reference is missing: {archive}")
    for item in packages.values():
        for relative in item["notice_files"]:
            path = build / relative
            if not path.is_file() or path.stat().st_size == 0:
                raise RuntimeError(f"Missing collected dependency notice: {relative}")
    for item in maintained_assets():
        path = build / item["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Missing or altered maintained license/source: {item['file']}")
        if item["name"] in pins and item["version"] != pins[item["name"]]:
            raise RuntimeError(f"Update version-specific license/source: {item['name']}")
        if item["name"] in {"qtbase", "qtsvg", "pyside-shiboken"} and item["version"] != manifest["qt_version"]:
            raise RuntimeError("Update corresponding Qt source archives")
    qt_root = build / "_internal/PySide6"
    actual_dlls = {path.name for path in qt_root.glob("Qt6*.dll")}
    if not actual_dlls or not actual_dlls <= set(manifest["qt_dlls"]):
        raise RuntimeError("Shipped Qt modules differ from reviewed licensing material")


def bundle_release_materials(build: Path) -> dict:
    if not (build / "Sidro.exe").is_file() or not (build / "_internal").is_dir():
        raise RuntimeError("Bundle distribution material after building the portable application")
    manifest = json.loads((MATERIALS / "licenses/qt/manifest.json").read_text(encoding="utf-8"))
    from PySide6.QtCore import qVersion
    actual_versions = (qVersion(), metadata.version("PySide6-Essentials"), metadata.version("shiboken6"))
    expected_versions = tuple(manifest[key] for key in ("qt_version", "pyside_version", "shiboken_version"))
    if actual_versions != expected_versions:
        raise RuntimeError("Update the Qt licensing/source material for the new library versions before building")
    pins = exact_pins(ROOT / "requirements-release.lock")
    distributions = [(name, metadata.distribution(name)) for name in sorted(pins)]
    for name, distribution in distributions:
        if distribution.version != pins[name]:
            raise RuntimeError(f"Installed {name} {distribution.version} differs from pinned {pins[name]}")
    for relative in (*REQUIRED_MATERIALS, *PROJECT_FILES, *MANIFESTS):
        original = MATERIALS / relative if relative in REQUIRED_MATERIALS else ROOT / relative
        destination = build / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    assets = maintained_assets()
    for item in assets:
        original = MATERIALS / item["file"]
        if hashlib.sha256(original.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"Maintained license/source checksum mismatch: {item['file']}")
        destination = build / item["file"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, destination)
    import shapely
    if next(item["version"] for item in assets if item["name"] == "geos") != shapely.geos_version_string:
        raise RuntimeError("Update GEOS corresponding source for the installed Shapely wheel")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("The release interpreter's LICENSE.txt is missing")
    (build / "licenses/python").mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_license, build / "licenses/python/LICENSE.txt")
    packages = []
    for name, distribution in distributions:
        notices = []
        site_root = Path(distribution.locate_file("")).resolve()
        for entry in distribution.files or ():
            relative = Path(str(entry))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            # License-named Python modules (for example license_plate_detection)
            # and generated bytecode are not notices. Bytecode can expose the
            # developer's absolute install path through co_filename.
            if "__pycache__" in relative.parts or relative.suffix.lower() in {
                ".py", ".pyc", ".pyo", ".pyd", ".dll", ".exe", ".so", ".a",
                ".c", ".cpp", ".h", ".hpp", ".rs", ".js", ".bin",
            }:
                continue
            notice_name = relative.name.lower()
            is_notice = any(notice_name == prefix or notice_name.startswith((prefix + ".", prefix + "-"))
                            for prefix in ("license", "licence", "copying", "notice", "copyright", "authors", "credits"))
            is_notice = is_notice or "thirdpartynotice" in re.sub(r"[^a-z]", "", notice_name)
            is_notice = is_notice or any(part.lower() in ("licenses", "licences", "build_licenses") for part in relative.parts)
            if not is_notice:
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(site_root):
                continue
            destination = build / "licenses/dependencies" / name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            notices.append(destination.relative_to(build).as_posix())
        packages.append({
            "name": name, "version": distribution.version,
            "declared_license": distribution.metadata.get("License-Expression") or distribution.metadata.get("License")
                or "; ".join(value for value in distribution.metadata.get_all("Classifier", []) if value.startswith("License ::"))
                or "Not declared",
            "project": f"https://pypi.org/project/{name}/{distribution.version}/",
            "notice_files": sorted(notices),
        })
        packages[-1]["notice_files"].extend(item["file"] for item in assets if item["name"] == name
                                           and item["file"].startswith("licenses/"))
    inventory = {
        "scope": "Pinned build environment, including build tools; not an executable SBOM",
        "bundled_ocr_backends": list(BUNDLED_OCR_BACKENDS),
        "python_version": sys.version.split()[0], "qt_version": actual_versions[0],
        "requirements_lock_sha256": hashlib.sha256((ROOT / "requirements-release.lock").read_bytes()).hexdigest(),
        "packages": packages,
    }
    # Re-bundling must also remove notices copied by older collectors. This is
    # limited to the generated notices directory; never touch installed wheels
    # or the maintained originals under release_materials.
    notice_root = build / "licenses/dependencies"
    expected_notice_root = build.resolve() / "licenses/dependencies"
    if notice_root.resolve() != expected_notice_root or not notice_root.resolve().is_relative_to(build.resolve()):
        raise RuntimeError("Unsafe generated notices cleanup target")
    retained = {relative for package in packages for relative in package["notice_files"]}
    for path in notice_root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise RuntimeError("Links are not allowed in generated notices")
    for path in notice_root.rglob("*"):
        if path.is_file() and path.relative_to(build).as_posix() not in retained:
            path.unlink()
    for path in sorted(notice_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    (build / "DEPENDENCY_INVENTORY.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    verify_release_materials(build)
    return inventory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "dist/Sidro")
    args = parser.parse_args()
    inventory = bundle_release_materials(args.destination)
    print(f"Bundled release documents and license notices for {len(inventory['packages'])} pinned packages.")

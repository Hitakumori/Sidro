"""Release document checks use synthetic packages; no builds or network calls."""
import importlib.util
import json
from pathlib import Path
import runpy
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_materials_helper", ROOT / "tools/bundle_release_materials.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class ReleaseMaterialsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.build = self.root / "dist/Sidro"
        for relative in (*helper.REQUIRED_MATERIALS, *helper.PROJECT_FILES, *helper.MANIFESTS):
            original = helper.MATERIALS / relative if relative in helper.REQUIRED_MATERIALS else ROOT / relative
            destination = self.build / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
        for item in helper.maintained_assets():
            destination = self.build / item["file"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(helper.MATERIALS / item["file"], destination)
        for relative in ("Sidro.exe", "SidroUpdater.exe", "_internal/PySide6/Qt6Core.dll",
                         "licenses/python/LICENSE.txt", "licenses/dependencies/requests/LICENSE"):
            destination = self.build / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("Synthetic fixture", encoding="utf-8")
        pins = helper.exact_pins(ROOT / "requirements-release.lock")
        self.inventory = {"qt_version": "6.11.2", "bundled_ocr_backends": ["rapidocr"], "packages": [
            {"name": name, "version": version,
             "notice_files": ["licenses/dependencies/requests/LICENSE"] if name == "requests" else []}
            for name, version in pins.items()
        ]}
        self.write_inventory()

    def write_inventory(self):
        (self.build / "DEPENDENCY_INVENTORY.json").write_text(json.dumps(self.inventory), encoding="utf-8")

    def test_complete_materials_pass(self):
        helper.verify_release_materials(self.build)

    def test_optional_ocr_runtime_cannot_enter_rapidocr_release(self):
        for name in ("paddle", "paddlepaddle-3.3.1.dist-info", "winrt", "paddlex", "bidi", "crc32c.cp312-win_amd64.pyd"):
            with self.subTest(component=name):
                path = self.build / "_internal" / name
                path.mkdir()
                try:
                    with self.assertRaisesRegex(RuntimeError, "Unbundled OCR dependency"):
                        helper.verify_release_materials(self.build)
                finally:
                    path.rmdir()

    def test_changed_ocr_profile_is_rejected(self):
        self.inventory["bundled_ocr_backends"].append("paddleocr")
        self.write_inventory()
        with self.assertRaisesRegex(RuntimeError, "OCR profile"):
            helper.verify_release_materials(self.build)

    def test_zip_includes_unchanged_project_license(self):
        with patch.dict(sys.modules, {"bundle_release_materials": helper}):
            package = runpy.run_path(str(ROOT / "tools/make_release_zip.py"))
        destination = self.root / "dist/Sidro-windows-v1.0.zip"
        package["make_release_zip"](self.build, destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(archive.read("Sidro/LICENSE"), (ROOT / "LICENSE").read_bytes())

    def test_missing_or_altered_project_license_blocks_zip_and_preserves_archive(self):
        with patch.dict(sys.modules, {"bundle_release_materials": helper}):
            package = runpy.run_path(str(ROOT / "tools/make_release_zip.py"))
        destination = self.root / "dist/Sidro-windows-v1.0.zip"
        destination.write_bytes(b"Previous candidate")
        license_path = self.build / "LICENSE"
        license_path.unlink()
        for state, message in (("missing", "Missing"), ("altered", "Stale")):
            with self.subTest(state=state):
                if state == "altered":
                    license_path.write_text("Incorrect license", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, message + " release distribution material: LICENSE"):
                    package["make_release_zip"](self.build, destination)
                self.assertEqual(destination.read_bytes(), b"Previous candidate")

    def test_missing_notice_prevents_zip_and_preserves_existing_archive(self):
        (self.build / "THIRD_PARTY_NOTICES.md").unlink()
        archive = self.root / "dist/Sidro-windows-v1.0.zip"
        archive.write_bytes(b"Existing archive must remain untouched")
        script = self.root / "tools/make_release_zip.py"
        script.parent.mkdir()
        shutil.copy2(ROOT / "tools/make_release_zip.py", script)
        with patch.dict(sys.modules, {"bundle_release_materials": helper}):
            with self.assertRaisesRegex(RuntimeError, "Missing release distribution material"):
                runpy.run_path(str(script), run_name="__main__")
        self.assertEqual(archive.read_bytes(), b"Existing archive must remain untouched")

    def test_stale_lock_is_rejected(self):
        with (self.build / "requirements-release.lock").open("a", encoding="utf-8") as handle:
            handle.write("\n# Stale copied manifest\n")
        with self.assertRaisesRegex(RuntimeError, "Stale release distribution material"):
            helper.verify_release_materials(self.build)

    def test_private_files_prevent_zip_and_preserve_existing_archive(self):
        script = self.root / "tools/make_release_zip.py"
        script.parent.mkdir()
        shutil.copy2(ROOT / "tools/make_release_zip.py", script)
        archive = self.root / "dist/Sidro-windows-v1.0.zip"
        archive.write_bytes(b"Previous candidate")
        for relative in (".env", "_internal/entitlement_private_key.b64",
                         "_internal/.env.backup", "personal-backup.json",
                         "licenses/dependencies/example/LICENSE.cpython-312.pyc"):
            with self.subTest(file=relative):
                path = self.build / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"Private fixture")
                try:
                    with patch.dict(sys.modules, {"bundle_release_materials": helper}):
                        with self.assertRaisesRegex(RuntimeError, "Private/development|Unreviewed|Generated bytecode"):
                            runpy.run_path(str(script), run_name="__main__")
                    self.assertEqual(archive.read_bytes(), b"Previous candidate")
                finally:
                    path.unlink()

    def test_altered_corresponding_source_is_rejected(self):
        item = next(item for item in helper.maintained_assets() if item["file"].startswith("sources/"))
        (self.build / item["file"]).write_bytes(b"Incorrect source archive")
        with self.assertRaisesRegex(RuntimeError, "altered maintained license/source"):
            helper.verify_release_materials(self.build)

    def test_missing_dependency_notice_is_rejected(self):
        (self.build / "licenses/dependencies/requests/LICENSE").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing collected dependency notice"):
            helper.verify_release_materials(self.build)

    def test_changed_inventory_version_is_rejected(self):
        self.inventory["packages"][0]["version"] = "0.0.0"
        self.write_inventory()
        with self.assertRaisesRegex(RuntimeError, "inventory does not match"):
            helper.verify_release_materials(self.build)

    def test_new_qt_module_requires_reviewed_material(self):
        (self.build / "_internal/PySide6/Qt6WebEngineCore.dll").write_bytes(b"Synthetic DLL")
        with self.assertRaisesRegex(RuntimeError, "Qt modules differ"):
            helper.verify_release_materials(self.build)

    def test_changed_qt_version_requires_matching_source_references(self):
        originals = self.root / "updated-materials"
        shutil.copytree(helper.MATERIALS, originals)
        manifest = json.loads((originals / "licenses/qt/manifest.json").read_text(encoding="utf-8"))
        manifest["qt_version"] = "6.11.3"
        for path in (originals / "licenses/qt/manifest.json", self.build / "licenses/qt/manifest.json"):
            path.write_text(json.dumps(manifest), encoding="utf-8")
        self.inventory["qt_version"] = "6.11.3"
        self.write_inventory()
        with patch.object(helper, "MATERIALS", originals):
            with self.assertRaisesRegex(RuntimeError, "Matching Qt source reference is missing"):
                helper.verify_release_materials(self.build)

    def test_altered_upstream_license_is_rejected_even_if_copied_consistently(self):
        originals = self.root / "reviewed-materials"
        shutil.copytree(helper.MATERIALS, originals)
        for path in (originals / "licenses/qt/LGPL-3.0-only.txt", self.build / "licenses/qt/LGPL-3.0-only.txt"):
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\nUnexpected license change\n")
        with patch.object(helper, "MATERIALS", originals):
            with self.assertRaisesRegex(RuntimeError, "Qt license text differs"):
                helper.verify_release_materials(self.build)

"""Keep release/repair dependency requests and bundle resources aligned."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]


def requirements(path):
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("-r "):
            result.update(requirements(path.parent / line[3:].strip()))
        elif line:
            requirement = Requirement(line)
            result[canonicalize_name(requirement.name)] = str(requirement.specifier)
    return result


class ReleaseContractTests(unittest.TestCase):
    def test_release_and_repair_pins_match(self):
        base = requirements(ROOT / "requirements-release.lock")
        for filename in ("requirements-rapidocr-test.txt", "requirements-paddleocr-test.txt", "requirements-windowsocr.txt"):
            for package, specifier in requirements(ROOT / filename).items():
                with self.subTest(file=filename, package=package):
                    self.assertEqual(base[package], specifier)
                    self.assertTrue(specifier.startswith("=="))

    def test_rapid_runtime_and_release_models_are_v6_small(self):
        runtime = (ROOT / "pillar2_ocr.py").read_text(encoding="utf-8")
        tree = ast.parse(runtime)
        rapid = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RapidOCREngine")
        rapid_code = ast.get_source_segment(runtime, rapid)
        self.assertNotIn("PPOCRV4", rapid_code)
        for filename in ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"):
            self.assertIn(filename, rapid_code)
            self.assertIn(filename, (ROOT / "Sidro.spec").read_text(encoding="utf-8"))
        prep = (ROOT / "tools/prepare_rapidocr_models.py").read_text(encoding="utf-8")
        self.assertIn("OCRVersion.PPOCRV6", prep)
        self.assertIn("ModelType.SMALL", prep)

    def test_builders_ship_all_repair_manifests(self):
        builder = (ROOT / "build_exe.ps1").read_text(encoding="utf-8")
        for manifest in ("requirements-rapidocr-test.txt", "requirements-paddleocr-test.txt", "requirements-windowsocr.txt"):
            self.assertIn(manifest, builder)
        self.assertIn("build_exe.ps1", (ROOT / "build_exe.bat").read_text(encoding="utf-8"))
        self.assertIn(".venv-sidro", builder)
        self.assertIn("check_dependencies.py", builder)
        self.assertNotIn("pip install", builder)

    def test_runtime_backend_pins_match_release(self):
        base = requirements(ROOT / "requirements-release.lock")
        tree = ast.parse((ROOT / "pillar2_ocr.py").read_text(encoding="utf-8"))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "is_ocr_backend_available")
        dictionary = next(node.value for node in ast.walk(function) if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "requirements" for target in node.targets))
        for key, value in zip(dictionary.keys, dictionary.values):
            if isinstance(key, ast.Constant) and key.value in ("rapidocr", "paddleocr"):
                for text in ast.literal_eval(value):
                    requirement = Requirement(text)
                    self.assertEqual(base[canonicalize_name(requirement.name)], str(requirement.specifier))

    def test_bundle_keeps_metadata_needed_by_runtime_checks(self):
        spec = (ROOT / "Sidro.spec").read_text(encoding="utf-8")
        self.assertIn("copy_metadata", spec)
        for package in ("rapidocr", "onnxruntime", "numpy", "opencv-contrib-python", "pyclipper", "shapely"):
            self.assertIn(f'"{package}"', spec)

    def test_rapidocr_defaults_migrate_older_settings_and_preserve_current_choices(self):
        with tempfile.TemporaryDirectory() as temporary:
            settings = Path(temporary) / "app_settings.json"
            contexts = ("single_scan", "inventory_6x3", "inventory_bottom6", "trade_monitor", "reward_prices")
            for version, engines, expected in (
                (4, dict.fromkeys(contexts, "paddleocr"), "rapidocr"),
                (5, dict.fromkeys(contexts, "paddleocr"), "paddleocr"),
                (5, {"inventory_6x3": "paddleocr", "inventory_bottom6": "paddleocr"}, "rapidocr"),
                (5, dict.fromkeys(contexts, "windowsocr"), "rapidocr"),
            ):
                with self.subTest(settings_version=version, engines=engines):
                    settings.write_text(json.dumps({
                        "ocr_settings_version": version,
                        "ocr_engines": engines,
                        "min_listing_price": 15,
                    }), encoding="utf-8")
                    environment = dict(os.environ, TTB_DATA_DIR=temporary)
                    result = subprocess.run([
                        sys.executable, "-B", "-c",
                        "import config, json; print(json.dumps({'engines': config.OCR_ENGINE_BY_CONTEXT, "
                        "'recommendations': config.OCR_RECOMMENDED_BY_CONTEXT, 'min_price': config.MIN_LISTING_PRICE}))",
                    ], cwd=ROOT, env=environment, capture_output=True, text=True, check=True)
                    actual = json.loads(result.stdout)
                    self.assertEqual(actual["engines"], dict.fromkeys(contexts, expected))
                    self.assertEqual(set(actual["recommendations"].values()), {"rapidocr"})
                    self.assertEqual(actual["min_price"], 15)

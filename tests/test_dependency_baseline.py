"""Protect the single OpenCV provider and full activated-extra dependency checks."""
import unittest

from tools.check_dependencies import validate_snapshot
from tools.write_ocr_requirements import repair_closure


class DependencyBaselineTests(unittest.TestCase):
    def setUp(self):
        self.versions = {"rapidocr": "3.9.2", "paddleocr": "3.7.0",
                         "paddlex": "3.7.2", "opencv-contrib-python": "4.10.0.84",
                         "numpy": "2.3.5", "pyclipper": "1.4.0"}
        self.pins = dict(self.versions)
        self.requires = {
            "rapidocr": ["opencv-python>=4.5.1.48", "numpy<3", "pyclipper>=1.2"],
            "paddleocr": ["paddlex[ocr-core]>=3.7,<3.8"],
            "paddlex": ["numpy>=1.24,<2.4", 'opencv-contrib-python==4.10.0.84; extra == "ocr-core"'],
        }

    def test_reviewed_provider_substitution_satisfies_rapid_and_paddle(self):
        errors, substitutions = validate_snapshot(self.pins, self.versions, self.requires)
        self.assertEqual(errors, [])
        self.assertEqual(len(substitutions), 1)

    def test_overlapping_provider_is_rejected_even_with_matching_pins(self):
        self.versions["opencv-python"] = "5.0.0.93"
        self.pins.update(self.versions)
        errors, _ = validate_snapshot(self.pins, self.versions, self.requires)
        self.assertTrue(any("one OpenCV provider" in error for error in errors))

    def test_activated_paddlex_extra_constraint_is_enforced(self):
        self.versions["opencv-contrib-python"] = "5.0.0.93"
        self.pins.update(self.versions)
        errors, _ = validate_snapshot(self.pins, self.versions, self.requires)
        self.assertTrue(any("paddlex requires opencv-contrib-python==4.10.0.84" in error for error in errors))

    def test_missing_transitive_dependency_is_rejected(self):
        del self.versions["pyclipper"]
        errors, _ = validate_snapshot(self.pins, self.versions, self.requires)
        self.assertTrue(any("rapidocr requires pyclipper" in error for error in errors))

    def test_version_drift_is_rejected(self):
        self.versions["rapidocr"] = "3.8.1"
        errors, _ = validate_snapshot(self.pins, self.versions, self.requires)
        self.assertTrue(any("lock requires 3.9.2" in error for error in errors))

    def test_new_rapidocr_cannot_reuse_unreviewed_provider_substitution(self):
        self.versions["rapidocr"] = "4.0.0"
        self.pins.update(self.versions)
        errors, substitutions = validate_snapshot(self.pins, self.versions, self.requires)
        self.assertEqual(substitutions, [])
        self.assertTrue(any("opencv-python is missing" in error for error in errors))

    def test_inactive_optional_extra_is_not_required(self):
        self.requires["paddlex"].append('torch>=2; extra == "genai"')
        errors, _ = validate_snapshot(self.pins, self.versions, self.requires)
        self.assertEqual(errors, [])

    def test_repair_closure_keeps_transitives_and_single_provider(self):
        closure = repair_closure(["paddleocr", "rapidocr"], self.versions, self.requires)
        self.assertEqual(closure, self.pins)
        self.assertNotIn("opencv-python", closure)

    def test_empty_repair_roots_do_not_collect_unrelated_packages(self):
        self.assertEqual(repair_closure([], self.versions, self.requires), {})

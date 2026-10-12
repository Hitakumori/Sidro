import ast
from pathlib import Path
import unittest
from unittest.mock import Mock
import numpy as np
import cv2
from types import SimpleNamespace


class OCRPackValidationTests(unittest.TestCase):
    def load_validator(self, available=True):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "pillar2_ocr.py").read_text(encoding="utf-8"))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "validate_ocr_backend")
        worker = Mock()
        worker.read.return_value = [SimpleNamespace(raw_text="Prime")]
        factory = Mock(return_value=worker)
        scope = {"is_ocr_backend_available": Mock(return_value=(available, "incompatible")),
                 "OCRWorkerProxy": factory, "np": np, "cv2": cv2}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "validator", "exec"), scope)
        return scope["validate_ocr_backend"], worker, factory

    def test_inference_uses_requested_backend_and_stops_worker(self):
        validate, worker, factory = self.load_validator()
        validate("rapidocr")
        factory.assert_called_once_with("rapidocr")
        worker.read.assert_called_once()
        worker.stop.assert_called_once()

    def test_failed_inference_is_not_ready_and_stops_worker(self):
        validate, worker, _ = self.load_validator()
        worker.read.side_effect = RuntimeError("bad model")
        with self.assertRaisesRegex(RuntimeError, "bad model"):
            validate("paddleocr")
        worker.stop.assert_called_once()

    def test_incompatible_version_does_not_start_worker(self):
        validate, _, factory = self.load_validator(False)
        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            validate("rapidocr")
        factory.assert_not_called()

    def test_empty_inference_is_not_ready(self):
        validate, worker, _ = self.load_validator()
        worker.read.return_value = []
        with self.assertRaises(RuntimeError):
            validate("rapidocr")
        worker.stop.assert_called_once()

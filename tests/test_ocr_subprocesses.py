"""OCR helpers stay hidden without changing main-app subprocess behavior."""
import ast
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def load_function(name, namespace):
    tree = ast.parse((ROOT / "pillar2_ocr.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "pillar2_ocr.py", "exec"), namespace)
    return namespace[name]


class OCRSubprocessTests(unittest.TestCase):
    def fixture(self, platform="nt"):
        class Popen:
            def __init__(self, args, bufsize=-1, creationflags=0, **kwargs):
                self.args, self.bufsize, self.creationflags, self.kwargs = args, bufsize, creationflags, kwargs

        module = SimpleNamespace(Popen=Popen, CREATE_NO_WINDOW=0x08000000)
        install = load_function("_hide_ocr_subprocess_windows", {
            "os": SimpleNamespace(name=platform), "subprocess": module, "inspect": inspect,
        })
        return install, module, Popen

    def test_hidden_flag_preserves_existing_flags_and_output_options(self):
        install, module, original = self.fixture()
        install()
        process = module.Popen(["where", "ccache"], creationflags=0x200, stdout="pipe", stderr="pipe")
        self.assertIsInstance(process, original)
        self.assertEqual(process.creationflags, 0x08000200)
        self.assertEqual(process.kwargs, {"stdout": "pipe", "stderr": "pipe"})

    def test_positional_creationflags_are_preserved(self):
        install, module, _ = self.fixture()
        install()
        process = module.Popen(["where", "nvcc"], -1, 0x200)
        self.assertEqual(process.creationflags, 0x08000200)

    def test_install_is_idempotent(self):
        install, module, _ = self.fixture()
        install()
        wrapper = module.Popen
        install()
        self.assertIs(module.Popen, wrapper)

    def test_non_windows_is_unchanged(self):
        install, module, original = self.fixture("posix")
        install()
        self.assertIs(module.Popen, original)

    def test_worker_installs_policy_before_loading_backend(self):
        order = []
        worker = load_function("_ocr_worker_main", {
            "_hide_ocr_subprocess_windows": lambda: order.append("hide"),
            "_create_local_ocr_engine": lambda backend: order.append(backend),
        })
        connection = SimpleNamespace(send=Mock(), recv=Mock(return_value=None), close=Mock())
        worker(connection, "paddleocr")
        self.assertEqual(order, ["hide", "paddleocr"])
        connection.send.assert_called_once_with((True, None))

    @unittest.skipUnless(sys.platform == "win32", "Windows subprocess integration")
    def test_real_hidden_subprocess_keeps_stdout_and_returncode(self):
        original = subprocess.Popen
        execute = original._execute_child
        execute_signature = inspect.signature(execute)
        observed_flags = []

        def trace_execute(process, *args, **kwargs):
            bound = execute_signature.bind(process, *args, **kwargs)
            observed_flags.append(bound.arguments["creationflags"])
            return execute(process, *args, **kwargs)

        install = load_function("_hide_ocr_subprocess_windows", {
            "os": SimpleNamespace(name="nt"), "subprocess": subprocess, "inspect": inspect,
        })
        with patch.object(subprocess, "Popen", original), patch.object(original, "_execute_child", trace_execute):
            install()
            result = subprocess.run(
                [sys.executable, "-c", "print('ocr-helper')"], capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "ocr-helper")
        self.assertEqual(observed_flags, [subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP])
        self.assertIs(subprocess.Popen, original)

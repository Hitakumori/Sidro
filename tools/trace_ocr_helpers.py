"""Trace actual OCR helper creation flags while checking recognition (Windows)."""
import argparse
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("rapidocr", "paddleocr"))
    args = parser.parse_args()
    if sys.platform != "win32":
        raise RuntimeError("This diagnostic checks Windows creation flags.")

    import cv2
    import numpy as np
    import pillar2_ocr

    original_popen = subprocess.Popen
    original_execute = original_popen._execute_child
    execute_signature = inspect.signature(original_execute)
    helpers = []

    def trace_execute(process, *positional, **keywords):
        bound = execute_signature.bind(process, *positional, **keywords)
        helpers.append({
            "command": bound.arguments["args"],
            "creationflags": bound.arguments["creationflags"],
        })
        return original_execute(process, *positional, **keywords)

    # Exercise the production policy and real backend in this isolated process;
    # the regular app installs the same policy inside its dedicated OCR worker.
    with patch.object(subprocess, "Popen", original_popen), \
         patch.object(original_popen, "_execute_child", trace_execute):
        pillar2_ocr._hide_ocr_subprocess_windows()
        engine = pillar2_ocr._create_local_ocr_engine(args.backend)
        frame = np.full((96, 320, 3), 255, dtype=np.uint8)
        cv2.putText(frame, "Prime", (12, 64), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        results = engine.read(frame)
        assert any("prime" in row.raw_text.casefold() for row in results), "Recognition failed"

    assert all(helper["creationflags"] & subprocess.CREATE_NO_WINDOW for helper in helpers)
    print(json.dumps({"backend": args.backend, "recognition_passed": True, "helpers": helpers}, indent=2))

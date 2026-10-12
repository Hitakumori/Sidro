"""Run Settings' OCR validation without opening the UI."""
import multiprocessing
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    backend = sys.argv[1]
    if "--settings" in sys.argv:
        from unittest.mock import patch
        import qt_ui
        app = qt_ui.QApplication.instance() or qt_ui.QApplication([])
        with patch.object(qt_ui.QMessageBox, "information") as message, \
             patch.object(qt_ui.QMessageBox, "question", return_value=qt_ui.QMessageBox.Yes):
            dialog = qt_ui.SettingsDialog()
            if "--repair" in sys.argv:
                dialog._install_ocr_pack(backend)
            else:
                dialog._test_ocr_pack(backend)
            deadline = time.monotonic() + (1650 if "--repair" in sys.argv else 450)
            while dialog._ocr_pack_busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            if dialog._ocr_pack_busy:
                raise TimeoutError("Settings OCR test did not complete")
            print(dialog.ocr_pack_labels[backend].text())
            if dialog._ocr_pack_results.get(backend) is not True:
                raise RuntimeError(message.call_args.args[-1])
            dialog.reject()
    else:
        from pillar2_ocr import validate_ocr_backend
        validate_ocr_backend(backend)
    print(f"{backend}: initialization and recognition passed")

"""Offline source startup/shutdown smoke with isolated local data."""
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == "__main__":
    if not os.environ.get("TTB_DATA_DIR"):
        raise RuntimeError("Set TTB_DATA_DIR to an isolated test directory first.")
    import config
    import main
    from PySide6.QtCore import QTimer
    config.WFM_TOKEN = ""
    config.UPDATE_CHECK_FREQUENCY = "never"
    config.SIDRO_WELCOME_VISUAL_FREQUENCY = "never"
    config.SIDRO_WELCOME_AUDIO_ENABLED = False
    config.ALERT_SOUND_ENABLED = False
    config.REWARD_PRICE_CHECK_ENABLED = False
    config.PRELOAD_OCR_ON_START = False
    with patch.object(main.keyboard, "add_hotkey"), patch.object(main.keyboard, "unhook_all"):
        app = main.App()
        app.open_account_manager()
        app.ui.app.processEvents()
        assert app.ui.dashboard is not None
        QTimer.singleShot(500, app.stop)
        app.run()
        assert app._stop_event.is_set()
    print("Source dashboard startup and idle shutdown passed (network disabled; hotkeys mocked).")

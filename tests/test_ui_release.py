"""Exercise real Qt dialogs with isolated data and mocked market services."""
import inspect
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class UIReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="sidro-ui-test-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name, "QT_QPA_PLATFORM": "offscreen"})
        cls.environment.start()
        import qt_ui
        import account_store
        import main
        cls.ui, cls.stores, cls.main = qt_ui, account_store, main
        cls.app = qt_ui.QApplication.instance() or qt_ui.QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def setUp(self):
        self.messages = patch.object(self.ui.QMessageBox, "information", return_value=self.ui.QMessageBox.Ok)
        self.messages.start()
        self.addCleanup(self.messages.stop)
        self.questions = patch.object(self.ui.QMessageBox, "question", return_value=self.ui.QMessageBox.Yes)
        self.questions.start()
        self.addCleanup(self.questions.stop)

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate(), "Qt background operation did not finish")

    def store(self):
        return self.stores.AccountStore(Path(self.directory.name) / (self.id().split(".")[-1] + ".json"))

    def test_settings_installed_is_not_ready_until_test_succeeds(self):
        with patch.object(self.ui, "is_ocr_backend_available", return_value=(True, "fixture")):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            self.assertIn("not tested", dialog.ocr_pack_labels["rapidocr"].text())
            with patch.object(self.ui, "validate_ocr_backend") as validate:
                dialog._test_ocr_pack("rapidocr")
                self.wait_for(lambda: not dialog._ocr_pack_busy)
                validate.assert_called_once_with("rapidocr")
                self.assertIn("Ready", dialog.ocr_pack_labels["rapidocr"].text())

    def test_settings_test_failure_never_reports_ready(self):
        with patch.object(self.ui, "is_ocr_backend_available", return_value=(True, "fixture")):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            with patch.object(self.ui, "validate_ocr_backend", side_effect=RuntimeError("broken model")):
                dialog._test_ocr_pack("paddleocr")
                self.wait_for(lambda: not dialog._ocr_pack_busy)
                self.assertIn("Test failed", dialog.ocr_pack_labels["paddleocr"].text())

    def test_settings_only_offers_rapid_and_paddle_with_rapid_default(self):
        defaults = dict.fromkeys(self.ui.config.OCR_ENGINE_BY_CONTEXT, "rapidocr")
        with patch.object(self.ui.config, "OCR_ENGINE_BY_CONTEXT", defaults):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            for combo in (dialog.ocr_backend, dialog.ocr_fallback_engine):
                self.assertEqual([combo.itemData(i) for i in range(combo.count())], ["rapidocr", "paddleocr"])
            self.assertEqual(dialog.ocr_backend.currentText(), "RapidOCR")
            self.assertEqual(set(dialog.ocr_pack_labels), {"rapidocr", "paddleocr"})

    def test_settings_save_normalizes_legacy_mixed_choices_to_rapid(self):
        previous = {"single_scan": "rapidocr", "inventory_6x3": "paddleocr", "inventory_bottom6": "paddleocr",
                    "trade_monitor": "rapidocr", "reward_prices": "rapidocr"}
        with patch.object(self.ui.config, "OCR_ENGINE_BY_CONTEXT", previous.copy()):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            dialog._collect_widget_values()
            self.assertEqual(dialog.ocr_backend.currentData(), "rapidocr")
            self.assertEqual(dialog._pending["ocr_engines"], dict.fromkeys(previous, "rapidocr"))

    def test_settings_import_normalizes_mixed_choices_and_preserves_uniform_paddle(self):
        dialog = self.ui.SettingsDialog()
        self.addCleanup(dialog.deleteLater)
        previous = {"single_scan": "rapidocr", "inventory_6x3": "paddleocr", "inventory_bottom6": "paddleocr",
                    "trade_monitor": "paddleocr", "reward_prices": "windowsocr"}
        dialog._pending["ocr_engines"] = previous.copy()
        dialog._pending["ocr_fallback_engine"] = "windowsocr"
        dialog._normalize_pending_after_import()
        dialog._apply_pending_to_widgets()
        dialog._collect_widget_values()
        self.assertEqual(dialog._pending["ocr_engines"], dict.fromkeys(previous, "rapidocr"))
        self.assertEqual(dialog._pending["ocr_fallback_engine"], "rapidocr")
        expected = dict.fromkeys(previous, "paddleocr")
        dialog._pending["ocr_engines"] = expected.copy()
        dialog._normalize_pending_after_import()
        dialog._apply_pending_to_widgets()
        dialog._collect_widget_values()
        self.assertEqual(dialog.ocr_backend.currentData(), "paddleocr")
        self.assertEqual(dialog._pending["ocr_engines"], expected)

    def test_selected_engine_saves_and_reopens_for_every_scan(self):
        path = Path(self.directory.name) / "ocr-settings.json"
        defaults = dict.fromkeys(self.ui.config.OCR_ENGINE_BY_CONTEXT, "rapidocr")
        changed = Mock()
        with patch.object(self.ui.config, "OCR_ENGINE_BY_CONTEXT", defaults), \
             patch.object(self.ui.config, "APP_SETTINGS_PATH", path):
            dialog = self.ui.SettingsDialog(on_ocr_changed=changed)
            self.addCleanup(dialog.deleteLater)
            dialog.ocr_backend.setCurrentIndex(dialog.ocr_backend.findData("paddleocr"))
            dialog.accept()
            expected = dict.fromkeys(defaults, "paddleocr")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["ocr_engines"], expected)
            self.assertEqual(self.ui.config.OCR_ENGINE_BY_CONTEXT, expected)
            changed.assert_called_once()
            reopened = self.ui.SettingsDialog()
            self.addCleanup(reopened.deleteLater)
            self.assertEqual(reopened.ocr_backend.currentData(), "paddleocr")

    def test_settings_cannot_close_during_install_or_test(self):
        dialog = self.ui.SettingsDialog()
        self.addCleanup(dialog.deleteLater)
        dialog._ocr_pack_busy = True
        with patch.object(dialog, "_collect_widget_values") as collect:
            dialog.accept()
            collect.assert_not_called()

    def test_settings_aliases_follow_the_selected_account(self):
        with patch.object(self.ui.config, "WFM_INGAME_NAME", "Valkyr_idiota"), \
             patch.object(self.ui.config, "OCR_NAME_ALIASES", {"valkyr_idiota": ["Valky_diota"], "boto8": ["Bot08"]}):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            self.assertEqual(dialog.name_aliases.toPlainText(), "Valky_diota")
            dialog.name_aliases.setPlainText("Valky_diota\nValky_dlota")
            dialog.ingame_name.setText("BotO8")
            self.assertEqual(dialog.name_aliases.toPlainText(), "Bot08")
            dialog.ingame_name.setText("SomeoneElse")
            self.assertEqual(dialog.name_aliases.toPlainText(), "")
            dialog.ingame_name.setText("Valkyr_idiota")
            self.assertEqual(dialog.name_aliases.toPlainText(), "Valky_diota\nValky_dlota")
            dialog._collect_widget_values()
            self.assertEqual(dialog._pending["wfm_ingame_name"], "Valkyr_idiota")
            self.assertEqual(self.ui.config.OCR_NAME_ALIASES["valkyr_idiota"], ["Valky_diota"])

    def test_settings_alias_editor_is_disabled_without_an_account(self):
        with patch.object(self.ui.config, "WFM_INGAME_NAME", ""):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            self.assertFalse(dialog.name_aliases.isEnabled())
            self.assertIn("Account tab", dialog.name_alias_account.text())
            dialog.ingame_name.setText("BotO8")
            self.assertTrue(dialog.name_aliases.isEnabled())

    def test_settings_imported_aliases_are_not_overwritten_by_old_editor_text(self):
        with patch.object(self.ui.config, "WFM_INGAME_NAME", "Valkyr_idiota"), \
             patch.object(self.ui.config, "OCR_NAME_ALIASES", {"valkyr_idiota": ["OldAlias"]}):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            dialog._pending.update({"wfm_ingame_name": "BotO8", "ocr_name_aliases": {"boto8": ["Bot08"]}})
            dialog._normalize_pending_after_import()
            dialog._apply_pending_to_widgets()
            dialog._collect_widget_values()
            self.assertEqual(dialog.name_aliases.toPlainText(), "Bot08")
            self.assertEqual(dialog._pending["ocr_name_aliases"], {"boto8": ["Bot08"]})

    def test_settings_alias_save_and_reopen_preserve_the_real_name(self):
        path = Path(self.directory.name) / "alias-settings.json"
        with patch.object(self.ui.config, "WFM_INGAME_NAME", "BotO8"), \
             patch.object(self.ui.config, "TRADE_MY_NAME", "BotO8"), \
             patch.object(self.ui.config, "OCR_NAME_ALIASES", {}), \
             patch.object(self.ui.config, "APP_SETTINGS_PATH", path):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            dialog.name_aliases.setPlainText("Bot08\n bot08 ")
            dialog.accept()
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["wfm_ingame_name"], "BotO8")
            self.assertEqual(saved["ocr_name_aliases"], {"boto8": ["Bot08"]})
            self.assertEqual(self.ui.config.TRADE_MY_NAME, "BotO8")
            reopened = self.ui.SettingsDialog()
            self.addCleanup(reopened.deleteLater)
            self.assertEqual(reopened.name_aliases.toPlainText(), "Bot08")
            reopened.name_aliases.clear()
            reopened.accept()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["ocr_name_aliases"], {})

    def test_settings_cancel_does_not_change_live_aliases(self):
        with patch.object(self.ui.config, "WFM_INGAME_NAME", "BotO8"), \
             patch.object(self.ui.config, "OCR_NAME_ALIASES", {}):
            dialog = self.ui.SettingsDialog()
            self.addCleanup(dialog.deleteLater)
            dialog.name_aliases.setPlainText("Bot08")
            dialog.reject()
            self.assertEqual(self.ui.config.OCR_NAME_ALIASES, {})

    def test_store_reload_cannot_overlap_a_mutation(self):
        store = self.store()
        store.queue_reward("test_prime", "Test Prime")
        done = threading.Event()
        def reload():
            store.load()
            done.set()
        with store._lock:
            worker = threading.Thread(target=reload)
            worker.start()
            overlapped = done.wait(0.1)
        worker.join(1)
        self.assertFalse(overlapped, "reload must use the same lock as reward/listing writes")

    def test_ocr_reset_waits_for_active_scan_before_disposing(self):
        runner = self.main.App.__new__(self.main.App)
        runner._ocr_reset_pending = threading.Event()
        runner._ocr_reset_pending.set()
        runner._ocr_warmup_lock = threading.Lock()
        runner._stop_event = Mock()
        runner._stop_event.wait.return_value = False
        runner._ocr_use_is_active = Mock(side_effect=[True, True, False])
        runner._close_scan_windows = Mock()
        runner._schedule_ocr_button_refresh = Mock()
        runner.ui = SimpleNamespace(invoke=Mock(), toast=Mock())

        def dispose():
            self.assertEqual(runner._ocr_use_is_active.call_count, 3)
            self.assertTrue(runner._ocr_warmup_lock.locked())
            self.assertTrue(runner._ocr_reset_pending.is_set())

        runner._dispose_ocr_scanners = Mock(side_effect=dispose)
        with patch("pillar2_ocr.unload_ocr_backends") as unload:
            runner._finish_ocr_reset()
            runner._dispose_ocr_scanners.assert_called_once()
            unload.assert_called_once()
        self.assertEqual(runner.ui.invoke.call_count, 2)
        self.assertFalse(runner._ocr_reset_pending.is_set())

    def test_ocr_reset_shutdown_does_not_dispose_active_scan(self):
        runner = self.main.App.__new__(self.main.App)
        runner._ocr_reset_pending = threading.Event()
        runner._ocr_reset_pending.set()
        runner._stop_event = Mock()
        runner._stop_event.wait.return_value = True
        runner._ocr_use_is_active = Mock(return_value=True)
        runner._close_scan_windows = Mock()
        runner._dispose_ocr_scanners = Mock()
        runner.ui = SimpleNamespace(invoke=Mock())
        with patch("pillar2_ocr.unload_ocr_backends") as unload:
            runner._finish_ocr_reset()
            runner._dispose_ocr_scanners.assert_not_called()
            unload.assert_not_called()
        self.assertFalse(runner._ocr_reset_pending.is_set())

    def test_settings_install_failure_does_not_validate_or_report_ready(self):
        dialog = self.ui.SettingsDialog()
        self.addCleanup(dialog.deleteLater)
        with patch.object(self.ui.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr="pip failed", stdout="")), \
             patch.object(self.ui, "validate_ocr_backend") as validate:
            dialog._install_ocr_pack("rapidocr")
            self.wait_for(lambda: not dialog._ocr_pack_busy)
            validate.assert_not_called()
            self.assertNotIn("Ready", dialog.ocr_pack_labels["rapidocr"].text())

    def dashboard(self, store, delete):
        callbacks = {name: Mock(return_value={}) for name in inspect.signature(self.ui.Dashboard).parameters if name.startswith("on_")}
        callbacks.update(on_delete_listings=delete, on_price_preview=lambda *_: {})
        window = self.ui.Dashboard(store, **callbacks)
        self.addCleanup(window.deleteLater)
        window._notify = Mock()
        return window

    def test_nine_deletions_refresh_actual_dashboard(self):
        store = self.store()
        listings = {}
        for index in range(9):
            store.upsert_listing(f"item_{index}", f"Item {index}", f"order_{index}", quantity=1, platinum=10)
            listings[f"item_{index}"] = self.main.ExistingListing(f"order_{index}", f"item_{index}", f"id_{index}", 10, 1, True)
        runner = self.main.App.__new__(self.main.App)
        runner.api, runner.account_store = Mock(), store
        runner.api.get_existing_sell_listing_map.side_effect = [listings, {}]
        runner.api.delete_order.return_value = True
        dashboard = self.dashboard(store, runner._delete_wfm_listings)
        self.assertEqual(dashboard.table.rowCount(), 9)
        for row in range(9):
            dashboard.table.cellWidget(row, 0).setChecked(True)
        with patch.object(self.main.config, "LISTING_REQUEST_DELAY_MIN_SECONDS", 0), \
             patch.object(self.main.config, "LISTING_REQUEST_DELAY_MAX_SECONDS", 0):
            dashboard._delete_selected()
            self.wait_for(lambda: not dashboard._background_busy)
        self.assertEqual(dashboard.table.rowCount(), 0)
        self.assertFalse(store.listings)
        self.assertEqual(runner.api.delete_order.call_count, 9)

    def test_more_than_100_confirmed_actions_need_no_access_credentials(self):
        store = self.store()
        runner = self.main.App.__new__(self.main.App)
        runner.api, runner.account_store, runner.ui = Mock(), store, Mock()
        runner.api.delete_order.return_value = True
        runner.api.create_sell_order.side_effect = lambda item_id, *_args, **_kwargs: {"id": item_id}
        items, prices = [], {}
        for index in range(150):
            url = f"fixture_{index}"
            action = ("DELETE", "UPDATE", "CREATE")[index % 3]
            item = SimpleNamespace(url_name=url, display_name=f"Fixture {index}", quantity=1,
                                   _listing_action=action, _item_id=f"id_{index}")
            if action != "CREATE":
                item._existing_order_id = f"order_{index}"
            items.append(item)
            prices[url] = SimpleNamespace(suggested_listing_price=10)
        with patch.object(self.main.config, "ENABLE_LIVE_LISTING", True), \
             patch.object(self.main.config, "LISTING_REQUEST_DELAY_MIN_SECONDS", 0), \
             patch.object(self.main.config, "LISTING_REQUEST_DELAY_MAX_SECONDS", 0), \
             patch("builtins.print"):
            applied = runner._post_confirmed_inventory_items(items, prices)
        self.assertEqual(applied, {item.url_name for item in items})
        self.assertEqual(runner.api.delete_order.call_count, 50)
        self.assertEqual(runner.api.update_order_price.call_count, 50)
        self.assertEqual(runner.api.create_sell_order.call_count, 50)
        self.assertEqual(len(store.listings), 100)
        self.assertFalse(hasattr(store, "_usage_store"))

    def test_settings_and_dashboard_have_no_membership_or_quota_controls(self):
        dialog = self.ui.SettingsDialog()
        self.addCleanup(dialog.deleteLater)
        labels = [widget.text().lower() for widget in dialog.findChildren(self.ui.QLabel)]
        buttons = [widget.text().lower() for widget in dialog.findChildren(self.ui.QPushButton)]
        for text in labels + buttons:
            self.assertNotIn("patreon", text)
            self.assertNotIn("redeem", text)
            self.assertNotIn("premium", text)
        dashboard = self.dashboard(self.store(), Mock())
        self.assertFalse(hasattr(dashboard, "usage_card"))
        self.assertFalse(hasattr(dashboard, "support_color_timer"))
        self.assertFalse(any(widget.text() == "Support me" for widget in dashboard.findChildren(self.ui.QPushButton)))

    def test_support_reminder_defers_during_scan_then_shows_dismissible_link(self):
        from support_reminders import SUPPORT_URL, SupportReminders
        now = [100.0]
        reminders = SupportReminders(Path(self.directory.name) / "reminder-test.json",
                                     clock=lambda: now[0], interval=lambda low, high: low)
        reminders.next_reminder_at = reminders.total_seconds + 30
        dashboard = self.dashboard(self.store(), Mock())
        controller = SimpleNamespace(dashboard=dashboard, support_reminders=reminders,
                                     support_reminder_dialog=None)
        with patch.object(dashboard, "isVisible", return_value=True), \
             patch.object(dashboard, "isActiveWindow", return_value=True), \
             patch.object(dashboard, "isMinimized", return_value=False), \
             patch.object(self.ui.QApplication, "activeModalWidget", return_value=None):
            now[0] += 30
            self.ui.UIController._tick_support_reminder(controller, lambda: True)
            self.assertIsNone(controller.support_reminder_dialog)
            self.ui.UIController._tick_support_reminder(controller, lambda: False)
            dialog = controller.support_reminder_dialog
            self.addCleanup(dialog.deleteLater)
            self.addCleanup(dialog.close)
            self.assertFalse(dialog.isModal())
            self.assertTrue(dialog.testAttribute(self.ui.Qt.WA_ShowWithoutActivating))
            links = [label for label in dialog.findChildren(self.ui.QLabel) if SUPPORT_URL in label.text()]
            self.assertEqual(len(links), 1)
            self.assertTrue(links[0].openExternalLinks())
            dismiss = next(button for button in dialog.findChildren(self.ui.QPushButton) if button.text() == "Dismiss")
            dismiss.click()
            self.assertFalse(dialog.isVisible())

    def test_unfocused_dashboard_does_not_show_due_reminder(self):
        dashboard = self.dashboard(self.store(), Mock())
        reminders = Mock()
        reminders.tick.return_value = None
        controller = SimpleNamespace(dashboard=dashboard, support_reminders=reminders,
                                     support_reminder_dialog=None)
        with patch.object(dashboard, "isVisible", return_value=True), \
             patch.object(dashboard, "isActiveWindow", return_value=False):
            self.ui.UIController._tick_support_reminder(controller, lambda: False)
        reminders.tick.assert_called_once_with(can_show=False)

    def test_reward_review_reject_leaves_persisted_queue_untouched(self):
        store = self.store()
        store.queue_reward("test_prime", "Test Prime")
        store.queue_reward("test_prime", "Test Prime")
        item = SimpleNamespace(url_name="test_prime", display_name="Test Prime", quantity=2,
                               _queued_reward_quantity=2, _reward_existing_quantity=0)
        dialog = self.ui.InventoryReviewDialog([item], {}, queued_reward_rows=list(store.get_reward_queue().values()))
        self.addCleanup(dialog.deleteLater)
        dialog.reject()
        self.assertEqual(dialog.selected_items, [])
        self.assertEqual(dialog.removed_queued_rewards, {})
        store.load()
        self.assertEqual(store.get_reward_queue()["test_prime"]["quantity"], 2)

    def test_queue_snapshot_removal_preserves_new_rewards_and_restart(self):
        store = self.store()
        store.queue_reward("test_prime", "Test Prime")
        snapshot = store.get_reward_queue()
        store.queue_reward("test_prime", "Test Prime")
        store.remove_queued_rewards(snapshot)
        reloaded = self.stores.AccountStore(store.path)
        self.assertEqual(reloaded.get_reward_queue()["test_prime"]["quantity"], 1)

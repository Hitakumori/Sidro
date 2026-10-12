"""Verify that the visual redesign retains existing dashboard interactions."""
import inspect
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class DashboardInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="sidro-layout-test-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name, "QT_QPA_PLATFORM": "offscreen"})
        cls.environment.start()
        import qt_ui
        cls.ui = qt_ui
        cls.app = qt_ui.QApplication.instance() or qt_ui.QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def setUp(self):
        ui = self.ui
        self.patches = [
            patch.object(ui.config, "WFM_TOKEN", ""),
            patch.object(ui.config, "FIRST_LAUNCH", False),
            patch.object(ui.config, "REWARD_PRICE_CHECK_ENABLED", False),
            patch.object(ui.Dashboard, "_startup_greeting", lambda *_: None),
            patch.object(ui.Dashboard, "preview_prices", lambda *_: None),
            patch.object(ui, "_save_ui_settings"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        rows = [
            ui.StoredListing("Ash Prime Blueprint", "ash_prime_blueprint", "order-a", platinum=18, quantity=2),
            ui.StoredListing("Adaptation", "adaptation", "order-b", platinum=15, quantity=6),
            ui.StoredListing("Lex Prime Barrel", "lex_prime_barrel", "order-c", platinum=12, quantity=4),
        ]
        store = SimpleNamespace(listings={row.url_name: row for row in rows}, load=lambda: None,
                                listed_rows=lambda: rows)
        self.callbacks = {name: Mock(return_value="fixture") for name in inspect.signature(ui.Dashboard).parameters
                          if name.startswith("on_")}
        self.window = ui.Dashboard(store, **self.callbacks)
        self.window._notify = Mock()
        # Execute inert callbacks synchronously so assertions do not depend on threads.
        self.window._run_background = lambda action, **_kwargs: action()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)

    def button(self, text):
        buttons = [button for button in self.window.findChildren(self.ui.QPushButton)
                   if button.text() == text and button.isVisible()]
        self.assertEqual(len(buttons), 1, f"Expected one visible {text!r} action")
        return buttons[0]

    def test_all_commands_are_reachable_at_minimum_window_size(self):
        self.window.resize(1120, 740)
        self.app.processEvents()
        for text in ("Scanner Help", "Trade Chat", "Recruitment Chat", "Login / Token", "Refresh WFM profile",
                     "Settings", "Review rewards (0)", "Load OCRs", "Show all", "Hide all",
                     "All", "Prime", "Non-Prime", "None", "Preset Help", "Delete selected", "Update selected"):
            with self.subTest(action=text):
                button = self.button(text)
                self.assertGreater(button.width(), 20)
                self.assertTrue(self.window.rect().contains(button.mapTo(self.window, self.ui.QPoint(0, 0))))
                self.assertTrue(self.window.rect().contains(button.mapTo(
                    self.window, self.ui.QPoint(button.width() - 1, button.height() - 1))))

    def test_integrated_title_area_keeps_window_controls_and_taskbar_title(self):
        ui = self.ui
        flags = self.window.windowHandle().flags()
        for flag in (ui.Qt.FramelessWindowHint, ui.Qt.WindowMinimizeButtonHint, ui.Qt.WindowMaximizeButtonHint,
                     ui.Qt.WindowCloseButtonHint, ui.Qt.WindowSystemMenuHint):
            self.assertTrue(flags & flag)
        self.assertFalse(flags & ui.Qt.ExpandedClientAreaHint)
        self.assertEqual(self.window.windowTitle(), "Your best warframe companion!")
        self.assertEqual(self.window.windowHandle().safeAreaMargins().top(), 0)
        self.window.hide()
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.windowHandle().flags() & ui.Qt.FramelessWindowHint)
        for name in ("minimize", "maximize", "close"):
            button = getattr(self.window, f"window_{name}_button")
            self.assertTrue(button.isVisible())
            self.assertEqual(button.accessibleName(), name.capitalize())

    def test_caption_mouse_clicks_maximize_restore_minimize_and_close(self):
        from PySide6.QtTest import QTest
        normal = self.window.geometry()
        for _ in range(3):
            QTest.mouseClick(self.window.window_maximize_button, self.ui.Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(self.window.isMaximized())
            self.assertEqual(self.window.window_maximize_button.accessibleName(), "Restore")
            QTest.mouseClick(self.window.window_maximize_button, self.ui.Qt.LeftButton)
            self.app.processEvents()
            self.assertFalse(self.window.isMaximized())
            self.assertEqual(self.window.geometry(), normal)
            self.assertEqual(self.window.window_maximize_button.accessibleName(), "Maximize")
        QTest.mouseClick(self.window.window_minimize_button, self.ui.Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.window.showNormal()
        self.app.processEvents()
        QTest.mouseClick(self.window.window_close_button, self.ui.Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_header_starts_system_drag(self):
        from PySide6.QtTest import QTest
        handle = self.window.windowHandle()
        with patch.object(handle, "startSystemMove", return_value=True) as move:
            QTest.mousePress(self.window.window_header, self.ui.Qt.LeftButton,
                             pos=self.ui.QPoint(400, 40))
            move.assert_called_once_with()
            QTest.mouseRelease(self.window.window_header, self.ui.Qt.LeftButton)

    def test_single_minimize_click_from_maximized_retains_restore_state(self):
        from PySide6.QtTest import QTest
        for _ in range(4):
            self.window.showMaximized()
            self.app.processEvents()
            QTest.mouseClick(self.window.window_minimize_button, self.ui.Qt.LeftButton)
            self.app.processEvents()
            self.assertTrue(self.window.isMinimized())
            self.assertTrue(self.window.windowState() & self.ui.Qt.WindowMaximized)
            self.window.setWindowState(self.window.windowState() & ~self.ui.Qt.WindowMinimized)
            self.app.processEvents()
            self.assertTrue(self.window.isMaximized())
            self.assertFalse(self.window.isMinimized())

    def test_native_minimize_commits_once_on_release_and_cancels_on_capture_loss(self):
        # Exercise caption routing independently of the Windows display plugin.
        frame = self.ui._WindowsDashboardFrame.__new__(self.ui._WindowsDashboardFrame)
        frame.window, frame.hwnd, frame.pressed = self.window, 1, None
        frame.user32 = Mock()
        button = self.window.window_minimize_button
        clicked = Mock()
        button.clicked.connect(clicked)
        self.window.showMaximized()
        self.app.processEvents()
        point = button.mapTo(self.window, button.rect().center())
        packed = (point.x() & 0xffff) | ((point.y() & 0xffff) << 16)

        def message(code, parameter=0):
            return frame.handle(SimpleNamespace(message=code, wParam=parameter, lParam=packed))

        self.assertEqual(message(0x0084), (True, 8))  # HTMINBUTTON
        self.assertEqual(frame.handle(SimpleNamespace(message=0x0021, wParam=0,
                                                      lParam=(0x00A1 << 16) | 8)), (True, 1))
        for release in (0x0202, 0x00A2):
            self.window.showMaximized()
            self.app.processEvents()
            clicked.reset_mock()
            self.assertEqual(message(0x00A1, 8), (True, 0))
            self.assertTrue(button.isDown())
            self.assertFalse(self.window.isMinimized())
            clicked.assert_not_called()
            self.assertEqual(message(release, 8 if release == 0x00A2 else 0), (True, 0))
            self.app.processEvents()
            self.assertTrue(self.window.isMinimized())
            clicked.assert_called_once()
            self.assertFalse(button.isDown())
            self.assertIsNone(frame.pressed)
            message(release)
            clicked.assert_called_once()
        self.window.showMaximized()
        self.app.processEvents()
        clicked.reset_mock()
        message(0x00A1, 8)
        message(0x0215)
        message(0x0202)
        self.assertFalse(self.window.isMinimized())
        self.assertFalse(button.isDown())
        self.assertIsNone(frame.pressed)
        clicked.assert_not_called()

    def test_header_double_click_maximizes_and_restores(self):
        from PySide6.QtTest import QTest
        normal = self.window.geometry()
        QTest.mouseDClick(self.window.window_header, self.ui.Qt.LeftButton,
                         pos=self.ui.QPoint(400, 40))
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        QTest.mouseDClick(self.window.window_header, self.ui.Qt.LeftButton,
                         pos=self.ui.QPoint(400, 40))
        self.app.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(self.window.geometry(), normal)

    def test_sorting_preserves_edits_selection_and_item_identity(self):
        table = self.window.table
        table.cellWidget(0, 0).setChecked(True)
        table.cellWidget(0, 3).setValue(9)
        table.cellWidget(0, 7).setValue(27)
        table.cellWidget(0, 8).setCurrentText("hidden")
        self.window._sort_inventory_column(3)
        self.assertEqual(table.verticalHeaderItem(0).text(), "ash_prime_blueprint")
        self.assertTrue(table.cellWidget(0, 0).isChecked())
        self.assertEqual(table.cellWidget(0, 3).value(), 9)
        self.assertEqual(table.cellWidget(0, 7).value(), 27)
        self.assertFalse(table.cellWidget(0, 8).currentData())
        self.button("Update selected").click()
        self.callbacks["on_update_prices"].assert_called_once_with(
            {"ash_prime_blueprint": {"price": 27, "quantity": 9, "visible": False}},
            self.ui.config.PRICE_STRATEGY_MOST_COMMON)

    def test_selection_and_search_keep_original_semantics(self):
        self.button("Prime").click()
        self.assertEqual({url for _, url in self.window._selected_rows()}, {"ash_prime_blueprint", "lex_prime_barrel"})
        self.button("Non-Prime").click()
        self.assertEqual(self.window._selected_rows(), [(1, "adaptation")])
        self.window.inventory_search.setText("LEX")
        self.assertTrue(self.window.table.isRowHidden(0))
        self.assertTrue(self.window.table.isRowHidden(1))
        self.assertFalse(self.window.table.isRowHidden(2))
        self.button("All").click()
        self.assertEqual(len(self.window._selected_rows()), 3)
        self.button("None").click()
        self.assertEqual(self.window._selected_rows(), [])

    def test_visibility_commands_retain_confirmation_in_inventory(self):
        with patch.object(self.ui.QMessageBox, "question", return_value=self.ui.QMessageBox.No):
            self.button("Hide all").click()
        self.callbacks["on_update_prices"].assert_not_called()
        self.assertTrue(all(self.window.table.cellWidget(row, 8).currentData() for row in range(3)))
        with patch.object(self.ui.QMessageBox, "question", return_value=self.ui.QMessageBox.Yes):
            self.button("Hide all").click()
            updates, strategy = self.callbacks["on_update_prices"].call_args.args
            self.assertEqual(set(updates), {"ash_prime_blueprint", "adaptation", "lex_prime_barrel"})
            self.assertTrue(all(not values["visible"] for values in updates.values()))
            self.assertEqual(strategy, self.ui.config.PRICE_STRATEGY_MOST_COMMON)
            self.button("Show all").click()
            updates, _ = self.callbacks["on_update_prices"].call_args.args
            self.assertTrue(all(values["visible"] for values in updates.values()))

    def test_scanning_defaults_and_toggle_callbacks_are_unchanged(self):
        self.assertFalse(self.window.reward_check.isChecked())
        self.assertFalse(self.window.trade_scan_check.isChecked())
        self.window.reward_check.click()
        self.callbacks["on_reward_toggle"].assert_called_once_with(True)
        self.window.trade_scan_check.click()
        self.callbacks["on_trade_scan_toggle"].assert_called_once_with(True)
        self.button("Load OCRs").click()
        self.callbacks["on_ocr_warmup"].assert_called_once_with()
        self.window.set_ocr_load_state(True, "Loaded")
        self.button("Unload OCRs").click()
        self.assertEqual(self.callbacks["on_ocr_warmup"].call_count, 2)

    def test_queued_rewards_keep_count_and_callback(self):
        self.assertFalse(self.button("Review rewards (0)").isEnabled())
        self.window.set_reward_queue_count(3)
        self.button("Review rewards (3)").click()
        self.callbacks["on_review_rewards"].assert_called_once_with()

    def test_login_visibility_is_preserved(self):
        self.window._hide_login_button()
        self.assertIsNone(self.window.login_button)
        self.assertFalse(any(button.text() == "Login / Token" and button.isVisible()
                             for button in self.window.findChildren(self.ui.QPushButton)))

    def test_buyer_lookup_and_quantity_price_bounds_are_preserved(self):
        with patch.object(self.window, "_show_buyers") as buyers:
            self.window.table.cellWidget(0, 2).click()
            buyers.assert_called_once_with("ash_prime_blueprint", "Ash Prime Blueprint")
        quantity = self.window.table.cellWidget(0, 3)
        quantity.setValue(999)
        quantity.plus.click()
        self.assertEqual(quantity.value(), 999)
        quantity.setValue(1)
        quantity.minus.click()
        self.assertEqual(quantity.value(), 1)
        price = self.window.table.cellWidget(0, 7)
        price.setValue(99999)
        price.plus.click()
        self.assertEqual(price.value(), 99999)
        price.setValue(self.ui.config.MIN_LISTING_PRICE)
        price.minus.click()
        self.assertEqual(price.value(), self.ui.config.MIN_LISTING_PRICE)


if __name__ == "__main__":
    unittest.main()

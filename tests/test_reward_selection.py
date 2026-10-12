"""Reward selection regressions without capture, OCR models, or account writes."""
import ast
from pathlib import Path
import threading
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, call
import zlib

import numpy as np
from thefuzz import fuzz
from name_aliases import aliases_for_account, normalize_alias_text


ROOT = Path(__file__).resolve().parents[1]


def methods(filename, class_name, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    selected = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=selected, type_ignores=[]), filename, "exec"), namespace)
    return namespace


def rows():
    return [
        {"display_name": "Kompressa Prime Blueprint", "url_name": "kompressa_prime_blueprint", "slot_index": 0},
        {"display_name": "Daikyu Prime Upper Limb", "url_name": "daikyu_prime_upper_limb", "slot_index": 2},
    ]


class RewardNameTests(unittest.TestCase):
    def detect(self, texts):
        namespace = methods("reward_scanner.py", "RewardScanner", {"_detect_selected_slot_from_names"}, {
            "np": np, "zlib": zlib, "fuzz": fuzz,
            "aliases_for_account": aliases_for_account, "normalize_alias_text": normalize_alias_text,
            "config": SimpleNamespace(REWARD_CARD_RECTS=[(i * 240, 0, 240, 240) for i in range(4)]),
            "_scaled_int": lambda value, axis, frame: int(value),
            "_scaled_rect": lambda rect, frame: rect,
            "_compact": lambda text: "".join(ch.lower() for ch in text if ch.isalnum()),
        })
        source = iter(texts)
        scanner = SimpleNamespace(_selected_name_cache={}, _read_name_crop_text=lambda crop: next(source),
                                  _card_rects=lambda frame: namespace["config"].REWARD_CARD_RECTS)
        return namespace["_detect_selected_slot_from_names"](scanner, np.zeros((400, 1100, 3), np.uint8), "Hitakumori")

    def test_short_fragment_cannot_select_an_unpicked_card(self):
        self.assertIsNone(self.detect(["", "", "ori", ""]))

    def test_fragment_cannot_outscore_the_actual_player(self):
        self.assertEqual(self.detect(["ori", "", "Hitakumori", ""]), 2)

    def test_fragment_cannot_outscore_a_minor_name_ocr_error(self):
        self.assertEqual(self.detect(["ori", "", "Hitakumorl", ""]), 2)

    def test_full_name_among_other_players_and_ocr_attempts_is_supported(self):
        self.assertEqual(self.detect(["OtherPlayer", "", "SomeoneElse\nHitakumori\nHitakumorl", ""]), 2)

    def test_multiple_matching_cards_remain_ambiguous(self):
        self.assertIsNone(self.detect(["Hitakumori", "", "Hitakumori", ""]))


class RewardSelectionStatusTests(unittest.TestCase):
    def scanner(self, frame, screen_present=True, slot=None):
        namespace = methods("reward_scanner.py", "RewardScanner", {"selection_status", "selected_slot"}, {
            "np": np, "RewardSelectionResult": SimpleNamespace,
        })
        scanner = SimpleNamespace(
            grabber=SimpleNamespace(capture=Mock(return_value=frame)),
            _reward_screen_present_in_frame=Mock(return_value=screen_present),
            _detect_selected_slot=Mock(return_value=slot),
        )
        for name in ("selection_status", "selected_slot"):
            setattr(scanner, name, MethodType(namespace[name], scanner))
        return scanner

    def test_closed_screen_is_distinct_from_unreadable_player_names(self):
        scanner = self.scanner(object(), screen_present=False)
        result = scanner.selection_status()
        self.assertIs(result.screen_present, False)
        self.assertIsNone(result.slot_index)
        scanner._detect_selected_slot.assert_not_called()
        scanner = self.scanner(object(), screen_present=True)
        result = scanner.selection_status()
        self.assertIs(result.screen_present, True)
        self.assertIsNone(result.slot_index)
        scanner._detect_selected_slot.assert_called_once()

    def test_unavailable_capture_is_distinct_from_unreadable_player_names(self):
        scanner = self.scanner(None)
        result = scanner.selection_status()
        self.assertIsNone(result.screen_present)
        scanner._detect_selected_slot.assert_not_called()
        scanner._reward_screen_present_in_frame.assert_not_called()

    def test_slot_only_api_still_returns_the_detected_slot(self):
        scanner = self.scanner(object(), slot=2)
        self.assertEqual(scanner.selected_slot(), 2)

    def test_confirmed_buffered_frame_does_not_repeat_the_live_screen_gate(self):
        image = object()
        scanner = self.scanner(image, screen_present=False, slot=1)
        result = scanner.selection_status(frame=image, screen_confirmed=True)
        self.assertIs(result.screen_present, True)
        self.assertEqual(result.slot_index, 1)
        scanner._reward_screen_present_in_frame.assert_not_called()
        scanner._detect_selected_slot.assert_called_once_with(image)
        scanner.grabber.capture.assert_not_called()

    def test_confirmed_frame_still_requires_a_readable_unambiguous_nickname(self):
        image = object()
        scanner = self.scanner(image, screen_present=False, slot=None)
        result = scanner.selection_status(frame=image, screen_confirmed=True)
        self.assertIs(result.screen_present, True)
        self.assertIsNone(result.slot_index)
        scanner._detect_selected_slot.assert_called_once_with(image)

    def test_missing_buffered_frame_does_not_recapture_the_closed_screen(self):
        scanner = self.scanner(object(), slot=0)
        result = scanner.selection_status(frame=None, screen_confirmed=True)
        self.assertIsNone(result.screen_present)
        self.assertIsNone(result.slot_index)
        scanner.grabber.capture.assert_not_called()
        scanner._detect_selected_slot.assert_not_called()


class RewardNameReadTests(unittest.TestCase):
    def reader(self, prepare):
        namespace = methods("reward_scanner.py", "RewardScanner", {"_read_name_crop_text"}, {
            "np": np, "preprocess_label_raw": prepare,
            "_compact": lambda text: "".join(ch.lower() for ch in text if ch.isalnum()),
        })
        scanner = SimpleNamespace(_ocr=Mock())
        scanner._read_name_crop_text = MethodType(namespace["_read_name_crop_text"], scanner)
        return scanner

    def test_multiple_player_names_use_detection_and_remain_separate(self):
        crop = np.zeros((96, 312, 3), np.uint8)
        prepared = np.zeros((192, 624, 3), np.uint8)
        scanner = self.reader(Mock(return_value=prepared))
        scanner._ocr.read.side_effect = [
            [SimpleNamespace(raw_text="Hitakumori"), SimpleNamespace(raw_text="OtherPlayer")],
            [SimpleNamespace(raw_text="Hitakumori"), SimpleNamespace(raw_text="SomeoneElse")],
        ]
        text = scanner._read_name_crop_text(crop)
        self.assertEqual(text, "Hitakumori\nOtherPlayer\nSomeoneElse")
        scanner._ocr.read_whole.assert_not_called()
        self.assertIs(scanner._ocr.read.call_args_list[0].args[0], prepared)
        self.assertIs(scanner._ocr.read.call_args_list[1].args[0], crop)

    def test_raw_detection_remains_available_if_preprocessing_fails(self):
        crop = np.zeros((96, 312, 3), np.uint8)
        scanner = self.reader(Mock(side_effect=RuntimeError("preprocessing failed")))
        scanner._ocr.read.return_value = [SimpleNamespace(raw_text="Hitakumori")]
        self.assertEqual(scanner._read_name_crop_text(crop), "Hitakumori")
        scanner._ocr.read.assert_called_once()
        scanner._ocr.read_whole.assert_not_called()

    def test_punctuation_variants_are_retained_for_explicit_aliases(self):
        crop = np.zeros((96, 312, 3), np.uint8)
        scanner = self.reader(Mock(return_value=crop))
        scanner._ocr.read.side_effect = [
            [SimpleNamespace(raw_text="Valkydiota")],
            [SimpleNamespace(raw_text="Valky_diota")],
        ]
        self.assertEqual(scanner._read_name_crop_text(crop), "Valkydiota\nValky_diota")


class RewardTrackingTests(unittest.TestCase):
    def app(self):
        namespace = methods("main.py", "App", {
            "_reward_monitor_loop", "_poll_reward_selection", "_confirm_final_reward_selection",
            "_update_pending_reward_from_rows", "_reset_reward_session_tracking",
            "_merge_reward_scan_result", "_reward_row_without_price", "_show_reward_rows_with_prices",
        }, {"time": time, "threading": threading, "config": SimpleNamespace(
            REWARD_MONITOR_POLL_SECONDS=0, REWARD_ANALYSIS_WINDOW_SECONDS=15, REWARD_ANALYSIS_MAX_ALTERNATES=3,
        )})
        app = SimpleNamespace(
            _pending_reward_pick=None, _pending_reward_slot=None, _prepare_reward_review_async=Mock(),
            _reward_session_started_at=None, _reward_screen_started_at=None,
            _reward_absent_hits=0, _reward_candidates_by_slot={},
            _reward_first_rows_by_slot={}, _last_reward_signature=None, _last_reward_rows=rows(),
            _last_reward_price_signature=((0, "kompressa_prime_blueprint", ()), (2, "daikyu_prime_upper_limb", ())),
            _reward_first_overlay_shown=True, _queue_reward_pick=Mock(), _reward_monitor_lock=threading.Lock(),
            _ocr_reset_pending=threading.Event(), api=object(), ui=Mock(),
            _reward_overlay_remaining_ms=lambda: 1000,
        )
        for name, method in namespace.items():
            if name.startswith("_") and callable(method):
                setattr(app, name, MethodType(method, app))
        return app

    def run_monitor(self, selections, selection_screens=None, reward_rows=None, **final_options):
        app = self.app()
        reward_rows = rows() if reward_rows is None else reward_rows
        app._last_reward_rows = reward_rows
        app._last_reward_price_signature = tuple((row["slot_index"], row["url_name"], ()) for row in reward_rows)
        frame = object()
        final_frame = object() if final_options.get("frame_available", True) else None
        if selection_screens is None:
            selection_screens = [True] * len(selections)
        visible_selections = [slot for slot, present in zip(selections, selection_screens) if present is True]
        final_selection = final_options.get("slot", visible_selections[-1] if visible_selections else None)
        buffer = SimpleNamespace(start=Mock(), stop=Mock(), latest_frame=Mock(return_value=frame))
        buffer.latest_reward_frame = Mock(side_effect=lambda: final_frame if buffer.stop.called else frame)
        scanner = SimpleNamespace(
            grabber=SimpleNamespace(capture=Mock(return_value=frame), fork=Mock()),
            reward_layout_present=Mock(),
            reward_screen_present=Mock(side_effect=[True] * len(selections) + [False, False]),
            scan=Mock(return_value=SimpleNamespace(items=rows())),
        )
        reads = iter(SimpleNamespace(slot_index=slot, screen_present=screen)
                     for slot, screen in zip(selections, selection_screens))
        scanner.selection_status = Mock(side_effect=lambda frame=None, screen_confirmed=False:
            SimpleNamespace(slot_index=final_selection, screen_present=final_options.get("screen_present", True))
            if frame is final_frame else next(reads))
        app._reward_monitor_loop.__func__.__globals__["RewardFrameBuffer"] = Mock(return_value=buffer)
        app._test_frame_buffer = buffer
        app._get_reward_scanner = lambda: scanner
        app._merge_reward_scan_result = lambda result: (reward_rows, reward_rows, (), False)
        app._reward_monitor_stop = SimpleNamespace(
            is_set=Mock(side_effect=[False] * (len(selections) + 2) + [True]), wait=Mock(return_value=False),
        )
        app._reward_monitor_loop()
        return app, scanner

    def test_latest_pick_replaces_an_earlier_pick(self):
        app, scanner = self.run_monitor([0, 2])
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "daikyu_prime_upper_limb")
        self.assertEqual(app._queue_reward_pick.call_count, 1)
        # Name reads use buffered frames, including a final read after closure.
        self.assertEqual(len(scanner.selection_status.call_args_list), 3)
        self.assertTrue(all(read.kwargs["frame"] is not None for read in scanner.selection_status.call_args_list))
        self.assertTrue(all(read.kwargs["screen_confirmed"] for read in scanner.selection_status.call_args_list))
        scanner.grabber.capture.assert_called_once()
        app._test_frame_buffer.stop.assert_called_once()

    def test_switch_during_slow_ocr_is_confirmed_from_the_last_visible_frame(self):
        # The live capture has closed before the second name read. A frame
        # captured while item OCR was busy still contains the final pick.
        app, _ = self.run_monitor([0, None], [True, False], slot=2)
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "daikyu_prime_upper_limb")

    def test_reported_forma_to_vadarya_to_hystrix_sequence_queues_hystrix_once(self):
        reward_rows = [
            {"display_name": "Hystrix Prime Barrel", "url_name": "hystrix_prime_barrel", "slot_index": 1},
            {"display_name": "Vadarya Prime Barrel", "url_name": "vadarya_prime_barrel", "slot_index": 3},
        ]
        app, _ = self.run_monitor([0, 0, 3, 1, 1, 1], reward_rows=reward_rows)
        app._queue_reward_pick.assert_called_once()
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "hystrix_prime_barrel")

    def test_reported_repeated_velox_selection_queues_velox_once(self):
        reward_rows = [{"display_name": "Velox Prime Blueprint", "url_name": "velox_prime_blueprint", "slot_index": 1}]
        app, _ = self.run_monitor([1] * 6, reward_rows=reward_rows)
        app._queue_reward_pick.assert_called_once()
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "velox_prime_blueprint")

    def test_missing_final_frame_cannot_queue_an_earlier_pick(self):
        app, scanner = self.run_monitor([0], frame_available=False)
        app._queue_reward_pick.assert_not_called()
        app.ui.toast.assert_called_once()
        self.assertEqual(scanner.selection_status.call_count, 1)

    def test_false_positive_buffered_layout_cannot_queue_an_earlier_pick(self):
        app, _ = self.run_monitor([0], slot=2, screen_present=False)
        app._queue_reward_pick.assert_not_called()
        app.ui.toast.assert_called_once()

    def test_ignored_final_buffered_pick_clears_an_earlier_pick(self):
        app, _ = self.run_monitor([0], slot=1)
        app._queue_reward_pick.assert_not_called()

    def test_unreadable_final_buffered_pick_clears_an_earlier_pick(self):
        app, _ = self.run_monitor([0], slot=None)
        app._queue_reward_pick.assert_not_called()
        app.ui.toast.assert_called_once()

    def test_closed_recapture_does_not_erase_a_confirmed_selection(self):
        app, _ = self.run_monitor([2, None], [True, False])
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "daikyu_prime_upper_limb")
        app.ui.toast.assert_not_called()

    def test_closed_recapture_preserves_the_latest_switch(self):
        app, _ = self.run_monitor([0, 2, None], [True, True, False])
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "daikyu_prime_upper_limb")
        self.assertEqual(app._queue_reward_pick.call_count, 1)

    def test_unavailable_recapture_does_not_erase_a_confirmed_selection(self):
        app, _ = self.run_monitor([2, None], [True, None])
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "daikyu_prime_upper_limb")

    def test_closed_recapture_cannot_resurrect_an_unconfirmed_selection(self):
        app, _ = self.run_monitor([0, None, None], [True, True, False])
        app._queue_reward_pick.assert_not_called()
        app.ui.toast.assert_called_once()

    def test_unreadable_final_selection_does_not_queue_an_earlier_pick(self):
        app, _ = self.run_monitor([0, None, None])
        app._queue_reward_pick.assert_not_called()
        app.ui.toast.assert_called_once()

    def test_selection_can_recover_after_an_ambiguous_read(self):
        app, _ = self.run_monitor([0, None, 2])
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "daikyu_prime_upper_limb")

    def test_unmarketable_final_pick_clears_earlier_pick(self):
        app, _ = self.run_monitor([0, 1])
        app._queue_reward_pick.assert_not_called()

    def test_overlay_and_queue_share_the_winning_item_identity(self):
        app = self.app()
        sequence = [rows()[1], rows()[0], rows()[0]]
        for row in sequence:
            item = SimpleNamespace(**row, match_score=100, card_rect=(0, 0, 240, 240), alternatives=[])
            item.slot_index = 0
            overlay, best, _, _ = app._merge_reward_scan_result(SimpleNamespace(items=[item]))
            self.assertEqual(overlay[0]["url_name"], best[0]["url_name"])

    def test_price_callback_cannot_restore_an_older_item_snapshot(self):
        app = self.app()
        latest = app._last_reward_rows
        app._show_reward_rows_with_prices([], app._last_reward_price_signature, best_rows=[rows()[0]])
        self.assertIs(app._last_reward_rows, latest)


if __name__ == "__main__":
    unittest.main()

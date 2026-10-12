"""Capture/OCR concurrency regressions without a game, models, or credentials."""
import ast
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from reward_frame_buffer import RewardFrameBuffer
from reward_layout import RewardHeadingTemplate


ROOT = Path(__file__).resolve().parents[1]


def frame(value):
    return np.full((8, 8, 3), value, np.uint8)


def wait_for_value(test, getter, value):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = getter()
        if current is not None and int(current[0, 0, 0]) == value:
            return
        time.sleep(0.001)
    test.fail(f"Timed out waiting for captured frame {value}")


class QueuedGrabber:
    def __init__(self):
        self.frames = queue.Queue()
        self.closed = threading.Event()
        self.capture_threads = set()
        self.closed_thread = None

    def capture(self, **kwargs):
        self.capture_threads.add(threading.get_ident())
        try:
            value = self.frames.get(timeout=0.02)
        except queue.Empty:
            return None
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed_thread = threading.get_ident()
        self.closed.set()


class RewardFrameBufferTests(unittest.TestCase):
    def make_buffer(self, initial=None):
        grabber = QueuedGrabber()
        factory_threads = []

        def factory():
            factory_threads.append(threading.get_ident())
            return grabber

        buffer = RewardFrameBuffer(factory, lambda image: int(image[0, 0, 0]) < 5,
                                   initial if initial is not None else frame(1), interval=0.001)
        buffer.start()
        self.addCleanup(buffer.stop)
        return buffer, grabber, factory_threads

    def test_final_reward_frame_survives_screen_closure_and_slow_ocr(self):
        buffer, grabber, factory_threads = self.make_buffer()
        # The caller is occupied with OCR. Capture advances independently.
        grabber.frames.put(frame(4))
        wait_for_value(self, buffer.latest_reward_frame, 4)
        grabber.frames.put(frame(9))
        wait_for_value(self, buffer.latest_frame, 9)
        buffer.stop()
        self.assertEqual(int(buffer.latest_reward_frame()[0, 0, 0]), 4)
        self.assertEqual(int(buffer.latest_frame()[0, 0, 0]), 9)
        self.assertEqual(grabber.capture_threads, set(factory_threads))
        self.assertNotIn(threading.get_ident(), factory_threads)
        self.assertEqual(grabber.closed_thread, factory_threads[0])

    def test_buffer_owns_copies_and_keeps_only_the_latest_candidate(self):
        initial = frame(1)
        buffer, grabber, _ = self.make_buffer(initial)
        initial[:] = 9
        self.assertEqual(int(buffer.latest_reward_frame()[0, 0, 0]), 1)
        earlier = buffer.latest_reward_frame()
        updated = frame(4)
        grabber.frames.put(updated)
        wait_for_value(self, buffer.latest_reward_frame, 4)
        updated[:] = 9
        self.assertEqual(int(buffer.latest_reward_frame()[0, 0, 0]), 4)
        self.assertEqual(int(earlier[0, 0, 0]), 1)

    def test_capture_error_invalidates_the_earlier_selection(self):
        buffer, grabber, _ = self.make_buffer()
        grabber.frames.put(RuntimeError("fixture capture failure"))
        self.assertTrue(grabber.closed.wait(2))
        self.assertIsNone(buffer.latest_frame())
        self.assertIsNone(buffer.latest_reward_frame())

    def test_new_session_has_no_frames_from_the_previous_session(self):
        old, grabber, _ = self.make_buffer()
        grabber.frames.put(frame(4))
        wait_for_value(self, old.latest_reward_frame, 4)
        old.stop()
        new, _, _ = self.make_buffer()
        self.assertEqual(int(new.latest_reward_frame()[0, 0, 0]), 1)


class RewardMonitorConcurrencyTests(unittest.TestCase):
    def exercise_monitor(self, cancel=False, reset_ocr=False):
        import test_reward_selection as reward_tests
        app = reward_tests.RewardTrackingTests().app()
        reward_rows = [
            {"display_name": "Lavos Prime Chassis Blueprint", "url_name": "lavos_prime_chassis_blueprint", "slot_index": 0},
            {"display_name": "Braton Prime Stock", "url_name": "braton_prime_stock", "slot_index": 3},
        ]
        app._last_reward_rows = reward_rows
        app._last_reward_price_signature = tuple((row["slot_index"], row["url_name"], ()) for row in reward_rows)
        app._merge_reward_scan_result = lambda result: (reward_rows, reward_rows, (), False)
        app._reward_monitor_stop = threading.Event()
        capture = QueuedGrabber()
        buffers = []
        item_ocr_busy, resume_item_ocr = threading.Event(), threading.Event()
        scans = 0

        def scan(**kwargs):
            nonlocal scans
            scans += 1
            if scans == 2:
                item_ocr_busy.set()
                if not resume_item_ocr.wait(2):
                    raise RuntimeError("fixture OCR was not released")
            return SimpleNamespace(items=reward_rows)

        scanner = SimpleNamespace(
            grabber=SimpleNamespace(capture=Mock(return_value=frame(1)), fork=lambda: capture),
            reward_layout_present=lambda image: int(image[0, 0, 0]) < 5,
            reward_screen_present=lambda frame: int(frame[0, 0, 0]) < 5,
            scan=scan,
            selection_status=Mock(side_effect=lambda frame, screen_confirmed=False:
                SimpleNamespace(slot_index=0 if int(frame[0, 0, 0]) == 1 else 3,
                                screen_present=int(frame[0, 0, 0]) < 5)),
        )

        def buffer_factory(*args):
            buffer = RewardFrameBuffer(*args, interval=0.001)
            buffers.append(buffer)
            return buffer

        app._reward_monitor_loop.__func__.__globals__["RewardFrameBuffer"] = buffer_factory
        app._get_reward_scanner = lambda: scanner
        app._queue_reward_pick.side_effect = lambda pick: app._reward_monitor_stop.set()
        monitor = threading.Thread(target=app._reward_monitor_loop, daemon=True)

        def cleanup():
            resume_item_ocr.set()
            app._reward_monitor_stop.set()
            monitor.join(2)

        self.addCleanup(cleanup)
        monitor.start()
        self.assertTrue(item_ocr_busy.wait(2))
        self.assertEqual(app._pending_reward_pick["url_name"], "lavos_prime_chassis_blueprint")
        if reset_ocr:
            app._ocr_reset_pending.set()
        elif cancel:
            app._reward_monitor_stop.set()
        else:
            capture.frames.put(frame(4))
            wait_for_value(self, buffers[0].latest_reward_frame, 4)
            capture.frames.put(frame(9))
            wait_for_value(self, buffers[0].latest_frame, 9)
        resume_item_ocr.set()
        monitor.join(2)
        self.assertFalse(monitor.is_alive())
        self.assertTrue(capture.closed.is_set())
        self.assertIsNone(app._pending_reward_pick)
        self.assertIsNone(app._last_reward_rows)
        scanner.grabber.capture.assert_called_once()
        return app

    def test_slot_one_to_four_switch_and_closure_during_item_ocr_queues_braton(self):
        app = self.exercise_monitor()
        app._queue_reward_pick.assert_called_once()
        self.assertEqual(app._queue_reward_pick.call_args.args[0]["url_name"], "braton_prime_stock")

    def test_disabling_monitor_during_ocr_stops_capture_and_discards_the_pending_pick(self):
        app = self.exercise_monitor(cancel=True)
        app._queue_reward_pick.assert_not_called()

    def test_unloading_ocr_during_a_scan_stops_capture_and_discards_the_pending_pick(self):
        app = self.exercise_monitor(reset_ocr=True)
        app._queue_reward_pick.assert_not_called()


class CaptureConsumerTests(unittest.TestCase):
    def grabber_class(self):
        source = ast.parse((ROOT / "pillar1_capture.py").read_text(encoding="utf-8"))
        capture_classes = [node for node in source.body if isinstance(node, ast.ClassDef)
                           and node.name in {"CaptureEngine", "DxCamEngine", "FrameGrabber"}]
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                                 *capture_classes], type_ignores=[])
        namespace = {"threading": threading}
        exec(compile(ast.fix_missing_locations(module), "pillar1_capture.py", "exec"), namespace)
        return namespace["FrameGrabber"], namespace["DxCamEngine"]

    def test_dxgi_consumer_does_not_close_the_shared_engine(self):
        cls, dx_cls = self.grabber_class()
        engine = dx_cls.__new__(dx_cls)
        engine.close = Mock()
        parent = cls.__new__(cls)
        parent._roi, parent._engine, parent._owns_engine = (0, 0, 1920, 1080), engine, True
        child = parent.fork()
        self.assertIs(child._engine, engine)
        self.assertEqual(child.roi, parent.roi)
        child.close()
        engine.close.assert_not_called()
        parent.close()
        engine.close.assert_called_once()

    def test_mss_consumer_creates_and_closes_its_handle_in_the_capture_thread(self):
        cls, _ = self.grabber_class()

        class ThreadBoundEngine:
            def __init__(self):
                self.created_thread = threading.get_ident()
                self.closed_thread = None

            def close(self):
                self.closed_thread = threading.get_ident()

        parent = cls.__new__(cls)
        parent._roi, parent._engine, parent._owns_engine = (0, 0, 1920, 1080), ThreadBoundEngine(), True
        children = []

        def consume():
            child = parent.fork()
            children.append(child)
            child.close()

        worker = threading.Thread(target=consume)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        child = children[0]
        self.assertIsNot(child._engine, parent._engine)
        self.assertNotEqual(child._engine.created_thread, parent._engine.created_thread)
        self.assertEqual(child._engine.created_thread, child._engine.closed_thread)
        self.assertIsNone(parent._engine.closed_thread)


class RewardLayoutGateTests(unittest.TestCase):
    def test_capture_filter_never_runs_ocr_and_full_gate_still_requires_the_heading(self):
        tree = ast.parse((ROOT / "reward_scanner.py").read_text(encoding="utf-8"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "RewardScanner")
        methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                   and node.name in {"reward_layout_present", "_reward_screen_present_in_frame"}]
        namespace = {"np": np, "cv2": cv2, "RewardHeadingTemplate": RewardHeadingTemplate,
                     "_scaled_rect": lambda rect, image: rect,
                     "_scaled_int": lambda value, axis, image: value,
                     "config": SimpleNamespace(REWARD_TITLE_ROI=(0, 0, 8, 2),
                                               REWARD_CARD_RECTS=[(0, 2, 4, 4), (4, 2, 4, 4)],
                                               REWARD_LABEL_Y_OFFSET=0, REWARD_LABEL_HEIGHT=4, REWARD_LABEL_X_PAD=0),
                     "looks_like_label_text": lambda crop: True}
        exec(compile(ast.Module(body=methods, type_ignores=[]), "reward_scanner.py", "exec"), namespace)
        scanner = SimpleNamespace(_has_reward_screen_title=Mock(return_value=False),
                                  _card_rects=lambda image: namespace["config"].REWARD_CARD_RECTS)
        scanner.reward_layout_present = lambda image: namespace["reward_layout_present"](scanner, image)
        image = frame(0)
        image[:2] = (80, 180, 220)  # Gold heading and visible card labels.
        self.assertTrue(scanner.reward_layout_present(image))
        scanner._has_reward_screen_title.assert_not_called()
        self.assertFalse(namespace["_reward_screen_present_in_frame"](scanner, image))
        scanner._has_reward_screen_title.assert_called_once()
        scanner._has_reward_screen_title.return_value = True
        self.assertTrue(namespace["_reward_screen_present_in_frame"](scanner, image))
        image[:2] = 0
        self.assertFalse(scanner.reward_layout_present(image))


if __name__ == "__main__":
    unittest.main()

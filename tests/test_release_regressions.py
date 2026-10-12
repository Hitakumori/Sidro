"""Isolated release regressions: no credentials, network, or OCR model loading."""
import ast
from pathlib import Path
import threading
from types import SimpleNamespace
import typing
import unittest
from unittest.mock import Mock

import requests


ROOT = Path(__file__).resolve().parents[1]


def load_methods(filename, class_name, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=methods, type_ignores=[]), filename, "exec"), namespace)
    return namespace


class ReleaseRegressions(unittest.TestCase):
    def api_fixture(self, response):
        api_error = type("WFMApiError", (Exception,), {})
        namespace = load_methods("pillar3_api.py", "WarframeMarketAPI",
                                 {"_request", "delete_order", "create_sell_order"}, {
            "Optional": typing.Optional, "requests": requests,
            "time": SimpleNamespace(sleep=lambda _: None),
            "config": SimpleNamespace(MIN_LISTING_PRICE=1, DEFAULT_QUANTITY=1),
            "WFMApiError": api_error, "WFMAuthError": type("AuthError", (Exception,), {}),
            "WFMRateLimitError": type("RateError", (Exception,), {}),
        })
        obj = SimpleNamespace(BASE="https://fixture.invalid", _session_lock=threading.Lock(),
                              _pace_request=lambda: None, _session=SimpleNamespace(request=Mock(return_value=response)))
        obj._request = lambda *args, **kwargs: namespace["_request"](obj, *args, **kwargs)
        return obj, namespace, api_error

    def test_rate_limit_cannot_report_success(self):
        obj, ns, error = self.api_fixture(SimpleNamespace(status_code=429, headers={"Retry-After": "0"}))
        self.assertFalse(ns["delete_order"](obj, "order"))
        with self.assertRaises(error):
            ns["create_sell_order"](obj, "item", 10)
        self.assertEqual(obj._session.request.call_count, 6)

    def test_create_requires_order_identifier(self):
        response = SimpleNamespace(status_code=200, content=b"{}", raise_for_status=lambda: None, json=lambda: {})
        obj, ns, error = self.api_fixture(response)
        with self.assertRaises(error):
            ns["create_sell_order"](obj, "item", 10)
        response.json = lambda: {"data": {"id": "new-order"}}
        self.assertEqual(ns["create_sell_order"](obj, "item", 10)["id"], "new-order")

    def test_scan_timeout_stops_worker(self):
        ns = load_methods("pillar2_ocr.py", "OCRWorkerProxy", {"_call"}, {})
        connection = SimpleNamespace(send=Mock(), poll=Mock(return_value=False), recv=Mock())
        obj = SimpleNamespace(_lock=threading.RLock(), _start_locked=Mock(), _stop_locked=Mock(),
                              _connection=connection, backend_name="fixture", REQUEST_TIMEOUT_SECONDS=0)
        with self.assertRaises(TimeoutError):
            ns["_call"](obj, "read", "frame")
        obj._stop_locked.assert_called_once()
        connection.recv.assert_not_called()


if __name__ == "__main__":
    unittest.main()

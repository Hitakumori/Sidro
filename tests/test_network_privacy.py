"""Exercise real HTTP request preparation with synthetic credentials and offline transport."""
import ast
import json
from pathlib import Path
import re
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

import requests


ROOT = Path(__file__).resolve().parents[1]


def load_network_methods():
    config = SimpleNamespace(APP_NAME="Sidro", APP_VERSION="1.0.0", WFM_PLATFORM="pc",
                             WFM_LANGUAGE="en", WFM_API_BASE_V2="https://api.warframe.market/v2",
                             WARFRAMESTAT_ITEMS_URL="https://api.warframestat.us/items",
                             DUCAT_CACHE_PATH=None)
    namespace = {"requests": requests, "config": config, "time": time, "re": re}
    tree = ast.parse((ROOT / "pillar3_api.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "WarframeMarketAPI")
    cls.body = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in {
        "_build_session", "refresh_ducat_manifest", "_iter_dicts", "_extract_ducats_from_object", "_slugify"}]
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "pillar3_api.py", "exec"), namespace)
    tree = ast.parse((ROOT / "auth_manager.py").read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in {"validate_wfm_token", "_clean_token"}]
    exec(compile(ast.Module(body=functions, type_ignores=[]), "auth_manager.py", "exec"), namespace)
    return namespace


class NetworkPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.namespace = load_network_methods()
        self.netrc = patch("requests.sessions.get_netrc_auth", return_value=None)
        self.netrc.start()
        self.addCleanup(self.netrc.stop)
        self.requests = []

    def response(self, request, status=200, data=None, location=None):
        self.requests.append(request)
        response = requests.Response()
        response.request, response.url, response.status_code = request, request.url, status
        response._content = json.dumps(data if data is not None else {}).encode()
        if location:
            response.headers["Location"] = location
        return response

    def api(self):
        api = self.namespace["WarframeMarketAPI"]()
        api._token, api.PLATFORM, api.LANGUAGE = "fixture", "pc", "en"
        api._session = api._build_session()
        api._session.trust_env = False
        self.addCleanup(api._session.close)
        api._ducat_manifest_checked, api._item_ducat_cache = False, {}
        api._save_item_ducat_cache = Mock()
        return api

    def test_public_ducat_metadata_request_cannot_receive_market_credentials(self):
        api = self.api()
        headers_before = dict(api._session.headers)
        cookies_before = list(api._session.cookies)
        data = [{"name": "Fixture Prime Part", "ducats": 45}]
        def send(_adapter, request, **_kwargs):
            return self.response(request, data=data)
        with patch.object(requests.adapters.HTTPAdapter, "send", autospec=True, side_effect=send):
            api.refresh_ducat_manifest(force=True)
        self.assertEqual(len(self.requests), 1)
        sent = self.requests[0]
        self.assertEqual(sent.url, "https://api.warframestat.us/items?language=en")
        self.assertNotIn("Authorization", sent.headers)
        self.assertNotIn("Cookie", sent.headers)
        self.assertEqual(api._item_ducat_cache, {"fixture_prime_part": 45})
        api._save_item_ducat_cache.assert_called_once_with()
        self.assertEqual(dict(api._session.headers), headers_before)
        self.assertEqual(list(api._session.cookies), cookies_before)

    def test_market_session_keeps_its_authenticated_request_behavior(self):
        api = self.api()
        request = api._session.prepare_request(requests.Request("GET", "https://api.warframe.market/v2/orders/my"))
        self.assertEqual(request.headers["Authorization"], "Bearer fixture")
        self.assertIn("JWT=fixture", request.headers["Cookie"])

    def test_token_validation_rejects_redirect_without_forwarding_cookie(self):
        def send(_adapter, request, **_kwargs):
            if request.url.startswith("https://api.warframe.market/"):
                return self.response(request, 302, location="https://unexpected.invalid/collect")
            return self.response(request, 200)
        with patch.object(requests.adapters.HTTPAdapter, "send", autospec=True, side_effect=send):
            accepted = self.namespace["validate_wfm_token"]("fixture")
        self.assertFalse(accepted)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0].headers["Authorization"], "Bearer fixture")
        self.assertIn("JWT=fixture", self.requests[0].headers["Cookie"])

    def test_token_validation_accepts_only_successful_statuses(self):
        for status, expected in ((200, True), (204, True), (301, False), (401, False), (500, False)):
            with self.subTest(status=status):
                def send(_adapter, request, **_kwargs):
                    return self.response(request, status)
                with patch.object(requests.adapters.HTTPAdapter, "send", autospec=True, side_effect=send):
                    self.assertEqual(self.namespace["validate_wfm_token"]("fixture"), expected)


if __name__ == "__main__":
    unittest.main()

"""Verify exact account aliases without capture, credentials, or OCR models."""
import ast
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
import zlib

import numpy as np
from thefuzz import fuzz

from name_aliases import (
    aliases_for_account, name_alias_matches, normalize_alias_text, sanitize_name_aliases,
)
from settings_validation import sanitize_settings


ROOT = Path(__file__).resolve().parents[1]


class NameAliasTests(unittest.TestCase):
    def setUp(self):
        self.mapping = sanitize_name_aliases({"BotO8": ["Bot08"]})

    def test_exact_case_insensitive_alias(self):
        self.assertTrue(name_alias_matches("BOTO8", "  bOt08  ", self.mapping))

    def test_alias_does_not_match_a_prefix_suffix_or_partial_name(self):
        for observed in ("Bot", "Bot0", "XBot08", "Bot08X", "Bot09", "Other Bot08", "Bot08\nOther"):
            with self.subTest(observed=observed):
                self.assertFalse(name_alias_matches("BotO8", observed, self.mapping))

    def test_punctuation_is_part_of_the_explicit_spelling(self):
        mapping = sanitize_name_aliases({"Valkyr_idiota": ["Valky_diota"]})
        self.assertTrue(name_alias_matches("Valkyr_idiota", "Valky_diota", mapping))
        self.assertFalse(name_alias_matches("Valkyr_idiota", "Valkydiota", mapping))

    def test_aliases_never_transfer_to_another_account(self):
        self.assertFalse(name_alias_matches("SomeoneElse", "Bot08", self.mapping))
        self.assertEqual(aliases_for_account("", self.mapping), frozenset())

    def test_settings_deduplicate_and_reject_invalid_values(self):
        clean = sanitize_settings({"ocr_name_aliases": {" BotO8 ": [
            "Bot08", " bot08 ", "BotO8", "", "---", None, "a" * 65, "Bot\x00", "Bot\n08",
        ], "Other": "not a list", "\x00": ["Other"]}})
        self.assertEqual(clean["ocr_name_aliases"], {"boto8": ["Bot08"]})

    def test_alias_list_is_bounded(self):
        clean = sanitize_name_aliases({"Player": [f"Spelling{i}" for i in range(25)]})
        self.assertEqual(len(clean["player"]), 20)

    def test_invalid_setting_shape_is_empty(self):
        self.assertEqual(sanitize_settings({"ocr_name_aliases": ["Bot08"]})["ocr_name_aliases"], {})

    def trade_match(self, expected, observed, aliases=None):
        tree = ast.parse((ROOT / "trade_scanner.py").read_text(encoding="utf-8"))
        method = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_trade_name_matches")
        ns = {"config": SimpleNamespace(OCR_NAME_ALIASES=aliases or {}), "fuzz": fuzz,
              "name_alias_matches": name_alias_matches}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "trade_scanner.py", "exec"), ns)
        return ns["_trade_name_matches"](expected, observed)

    def test_trade_accepts_a_registered_misread(self):
        self.assertFalse(self.trade_match("BotO8", "Bot08"))
        self.assertTrue(self.trade_match("BotO8", "Bot08", self.mapping))
        valkyr = sanitize_name_aliases({"Valkyr_idiota": ["Valky_diota"]})
        self.assertTrue(self.trade_match("Valkyr_idiota", "Valky_diota", valkyr))

    def test_trade_does_not_fuzz_or_substring_match_aliases(self):
        for observed in ("Bot09", "XBot08", "Bot08X", "Bot", "Other Bot08"):
            with self.subTest(observed=observed):
                self.assertFalse(self.trade_match("BotO8", observed, self.mapping))

    def test_trade_keeps_the_canonical_name_and_owner_checks(self):
        self.assertTrue(self.trade_match("BotO8", "BotO8", self.mapping))
        self.assertFalse(self.trade_match("OtherUser", "Bot08", self.mapping))

    def reward_pick(self, texts, aliases=None, own_name="BotO8"):
        tree = ast.parse((ROOT / "reward_scanner.py").read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RewardScanner")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_detect_selected_slot_from_names")
        ns = {"config": SimpleNamespace(OCR_NAME_ALIASES=aliases or {},
              REWARD_CARD_RECTS=[(i * 240, 0, 240, 240) for i in range(4)]),
              "np": np, "zlib": zlib, "fuzz": fuzz, "aliases_for_account": aliases_for_account,
              "normalize_alias_text": normalize_alias_text,
              "_compact": lambda value: "".join(c.lower() for c in value if c.isalnum()),
              "_scaled_int": lambda value, axis, frame: int(value), "_scaled_rect": lambda rect, frame: rect}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "reward_scanner.py", "exec"), ns)
        text_source = iter(texts)
        scanner = SimpleNamespace(_selected_name_cache={}, _read_name_crop_text=lambda crop: next(text_source),
                                  _card_rects=lambda frame: ns["config"].REWARD_CARD_RECTS)
        with contextlib.redirect_stdout(io.StringIO()):
            return ns[method.name](scanner, np.zeros((400, 1100, 3), np.uint8), own_name)

    def test_reward_accepts_an_alias_among_stacked_player_names(self):
        texts = ["Other", "", "Another\nbOt08\nLast", ""]
        self.assertIsNone(self.reward_pick(texts))
        self.assertEqual(self.reward_pick(texts, self.mapping), 2)

    def test_reward_real_name_and_alias_on_different_cards_are_ambiguous(self):
        self.assertIsNone(self.reward_pick(["BotO8", "", "Bot08", ""], self.mapping))

    def test_reward_two_alias_cards_are_ambiguous(self):
        self.assertIsNone(self.reward_pick(["Bot08", "", "bot08", ""], self.mapping))

    def test_reward_alias_is_not_a_fragment_of_another_detected_name(self):
        for text in ("Other Bot08", "Bot08X", "Bot09", "Bot"):
            with self.subTest(text=text):
                self.assertIsNone(self.reward_pick([text, "", "", ""], self.mapping))

    def test_reward_does_not_use_another_accounts_alias(self):
        self.assertIsNone(self.reward_pick(["Bot08", "", "", ""], self.mapping, own_name="OtherUser"))


if __name__ == "__main__":
    unittest.main()

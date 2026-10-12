"""Market pricing regressions using real methods and offline seller fixtures."""
import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_pricing_api():
    # Avoid config's runtime-data migration and credential loading. Read the
    # actual pricing constants and execute the production methods unchanged.
    config_tree = ast.parse((ROOT / "config.py").read_text(encoding="utf-8"))
    settings = {}
    for node in config_tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and (
                    target.id.startswith("PRICE_")
                    or target.id in {"MIN_LISTING_PRICE", "UNDERCUT_BY_PLATINUM"}
                ):
                    settings[target.id] = ast.literal_eval(node.value)

    tree = ast.parse((ROOT / "pillar3_api.py").read_text(encoding="utf-8"))
    classes = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name in {"MarketOrder", "PriceInfo", "WFMApiError"}:
            classes.append(node)
        elif node.name == "WarframeMarketAPI":
            node.body = [method for method in node.body if isinstance(method, ast.FunctionDef)
                         and method.name in {"get_price_info", "_suggest_listing_price", "_lowest_price_cluster"}]
            classes.append(node)
    namespace = {"__name__": __name__, "dataclass": dataclass, "Counter": Counter,
                 "config": SimpleNamespace(**settings), "time": SimpleNamespace(time=Mock(return_value=1000.0))}
    exec(compile(ast.Module(body=classes, type_ignores=[]), "pillar3_api.py", "exec"), namespace)
    return namespace


class MarketPricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_namespace = load_pricing_api()

    def setUp(self):
        self.api = self.api_namespace["WarframeMarketAPI"]()
        self.api._price_cache = {}
        self.api.get_orders = Mock()
        self.clock = self.api_namespace["time"].time
        self.clock.return_value = 1000.0

    @staticmethod
    def orders(prices):
        return [{"id": f"order-{index}", "platinum": price, "quantity": 1,
                 "type": "sell", "visible": True,
                 "user": {"id": f"seller-{index}", "ingameName": f"Seller{index}", "status": "ingame"}}
                for index, price in enumerate(prices)]

    def price(self, prices, strategy=None):
        self.api.get_orders.return_value = self.orders(prices)
        return self.api.get_price_info("example_prime_part", strategy=strategy)

    def test_reported_7p_outlier_suggests_15p_and_preserves_raw_prices(self):
        info = self.price([7, 15, 15, 60])
        self.assertEqual(info.suggested_listing_price, 15)
        self.assertEqual(info.lowest_sell_price, 7)
        self.assertEqual(info.highest_sell_price, 60)
        self.assertEqual(info.active_sell_count, 4)
        self.assertEqual([order.platinum for order in info.top_5_orders], [7, 15, 15, 60])

    def test_nearby_sellers_support_a_band_without_exact_price_duplicates(self):
        self.assertEqual(self.price([7, 15, 16, 17, 19, 60]).suggested_listing_price, 15)

    def test_multiple_isolated_low_offers_are_skipped(self):
        self.assertEqual(self.price([1, 7, 15, 15, 60]).suggested_listing_price, 15)

    def test_hopeful_high_listings_do_not_override_a_supported_low_band(self):
        prices = [2, 3, 4, 4, 5] + [25] * 20 + [50] * 20
        self.assertEqual(self.price(prices).suggested_listing_price, 4)

    def test_supported_low_band_is_kept_even_if_higher_band_is_larger(self):
        self.assertEqual(self.price([7, 7, 15, 15, 15, 60]).suggested_listing_price, 7)

    def test_order_quantity_does_not_count_as_multiple_sellers(self):
        orders = self.orders([7, 15, 15])
        orders[0]["quantity"] = 999
        self.api.get_orders.return_value = orders
        self.assertEqual(self.api.get_price_info("example_prime_part").suggested_listing_price, 15)

    def test_duplicate_account_orders_do_not_support_the_low_outlier(self):
        for identity in ("id", "_id", "ingameName", "ingame_name"):
            with self.subTest(identity=identity):
                self.api._price_cache.clear()
                orders = self.orders([7, 7, 15, 15])
                for order in orders[:2]:
                    order["user"] = {identity: "SameSeller", "status": "ingame"}
                self.api.get_orders.return_value = orders
                self.assertEqual(self.api.get_price_info("example_prime_part").suggested_listing_price, 15)

    def test_one_account_has_one_vote_at_its_lowest_price(self):
        orders = self.orders([16, 7, 15, 15])
        orders[0]["user"]["id"] = orders[1]["user"]["id"]
        self.api.get_orders.return_value = orders
        self.assertEqual(self.api.get_price_info("example_prime_part").suggested_listing_price, 15)

    def test_existing_presets_keep_their_intended_behavior(self):
        expected = {"lowest_cluster_mode": 15, "lowest_cluster_mode_minus_1": 14,
                    "fast_lowest": 7, "lowest_plus_1": 8, "lowest_plus_2": 9,
                    "lowest_plus_3": 10, "undercut_lowest": 6}
        for strategy, price in expected.items():
            with self.subTest(strategy=strategy):
                self.assertEqual(self.price([7, 15, 15, 60], strategy).suggested_listing_price, price)

    def test_sparse_market_without_supported_band_uses_next_lowest_offer(self):
        self.assertEqual(self.price([7, 15, 60]).suggested_listing_price, 15)

    def test_two_isolated_sellers_do_not_anchor_to_the_minimum(self):
        self.assertEqual(self.price([7, 15]).suggested_listing_price, 15)

    def test_single_seller_keeps_the_only_available_price(self):
        self.assertEqual(self.price([7]).suggested_listing_price, 7)

    def test_minimum_price_is_respected(self):
        self.assertEqual(self.price([1, 1, 15]).suggested_listing_price, 2)

    def test_only_visible_ingame_sell_orders_provide_price_support(self):
        orders = self.orders([7, 7, 7, 7, 7, 15, 15])
        orders[1]["user"]["status"] = "online"
        orders[2]["user"]["status"] = "offline"
        orders[3]["visible"] = False
        orders[4]["type"] = "buy"
        self.api.get_orders.return_value = orders
        info = self.api.get_price_info("example_prime_part")
        self.assertEqual(info.suggested_listing_price, 15)
        self.assertEqual(info.active_sell_count, 3)

    def test_no_eligible_sellers_raises_instead_of_inventing_a_price(self):
        self.api.get_orders.return_value = []
        with self.assertRaises(self.api_namespace["WFMApiError"]):
            self.api.get_price_info("example_prime_part")

    def test_price_cache_reuses_suggestion_and_refreshes_after_expiration(self):
        first = self.price([7, 15, 15, 60])
        self.api.get_orders.return_value = self.orders([7, 19, 19, 60])
        self.assertIs(self.api.get_price_info("example_prime_part"), first)
        self.api.get_orders.assert_called_once()
        self.clock.return_value += self.api_namespace["config"].PRICE_CACHE_TTL_SECONDS
        refreshed = self.api.get_price_info("example_prime_part")
        self.assertEqual(refreshed.suggested_listing_price, 19)
        self.assertEqual(self.api.get_orders.call_count, 2)


if __name__ == "__main__":
    unittest.main()

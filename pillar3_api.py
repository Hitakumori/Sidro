"""Warframe Market API access, item catalogs and suggested listing prices.

Fetches public item/price data and manages the user's listings with their
market token. This module supplies pricing and listing operations to the
app's scan, review and trade-synchronization workflows.
"""

from __future__ import annotations
import json
import re
import time
import threading
from collections import Counter
from typing import Optional
from dataclasses import dataclass

import requests
import websocket

import config


# ──────────────────────────────────────────────
# DATA STRUCTURES
# ──────────────────────────────────────────────

@dataclass
class MarketOrder:
    order_id: str
    item_name: str
    platinum: int
    quantity: int
    order_type: str           # "sell" | "buy"
    user_status: str          # "ingame" | "online" | "offline"
    username: str


@dataclass
class PriceInfo:
    item_url_name: str
    lowest_sell_price: int
    average_sell_price: float
    active_sell_count: int
    suggested_listing_price: int
    top_5_orders: list[MarketOrder]
    highest_sell_price: int = 0


@dataclass
class ExistingListing:
    order_id: str
    item_url_name: str
    item_id: str
    platinum: int
    quantity: int
    visible: bool


# ──────────────────────────────────────────────
# EXCEPTION TYPES
# ──────────────────────────────────────────────

class WFMAuthError(Exception):
    """Raised when the API returns 401 — token is expired or invalid."""
    pass


class WFMRateLimitError(Exception):
    """Raised when the API returns 429 — too many requests."""
    pass


class WFMApiError(Exception):
    """Generic API error."""
    pass


# ──────────────────────────────────────────────
# API CLIENT
# ──────────────────────────────────────────────

class WarframeMarketAPI:
    """
    Official warframe.market REST API client.

    Authentication uses the JWT token from your browser session — no
    OAuth flow, no Selenium, no password handling. The token is passed
    as a cookie + Authorization header (same as the website's own XHR calls).
    """

    BASE = config.WFM_API_BASE_V2
    PLATFORM = config.WFM_PLATFORM
    LANGUAGE = config.WFM_LANGUAGE

    def __init__(self, token: Optional[str] = None):
        self._token = token or config.WFM_TOKEN
        if not self._token:
            raise WFMAuthError(
                "No WFM_TOKEN found. Paste your JWT in the Login / Token dialog."
            )
        self._session = self._build_session()
        self._item_id_cache: dict[str, str] = self._load_item_id_cache()
        self._item_ducat_cache: dict[str, int | None] = self._load_item_ducat_cache()
        self._item_id_manifest_checked = False
        self._ducat_manifest_checked = False
        self._request_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._last_request_at = 0.0
        self._price_cache: dict[tuple[str, str], tuple[float, PriceInfo]] = {}
        self._status_ws = None

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"{config.APP_NAME}/{config.APP_VERSION} (+https://github.com/Hitakumori/Sidro)",
            "Platform": self.PLATFORM,
            "Language": self.LANGUAGE,
            "Crossplay": "true",
            # Use the documented Bearer authorization format and retain the
            # JWT cookie for compatibility with the browser session token.
            "Authorization": f"Bearer {self._token}",
        })
        # Set the JWT cookie (mirrors what your Edge browser session sends)
        for domain in ("warframe.market", ".warframe.market", "api.warframe.market"):
            session.cookies.set("JWT", self._token, domain=domain, path="/")
        return session

    def _load_item_id_cache(self) -> dict[str, str]:
        path = getattr(config, "ITEM_IDS_CACHE_PATH", None)
        if path is None or not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {
                    str(slug): str(item_id)
                    for slug, item_id in data.items()
                    if slug and item_id
                }
        except Exception as e:
            print(f"[API] Could not read item id cache: {e}")
        return {}

    def _save_item_id_cache(self):
        path = getattr(config, "ITEM_IDS_CACHE_PATH", None)
        if path is None:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._item_id_cache, f, ensure_ascii=False, indent=2, sort_keys=True)
        except Exception as e:
            print(f"[API] Could not save item id cache: {e}")

    def _load_item_ducat_cache(self) -> dict[str, int | None]:
        path = getattr(config, "DUCAT_CACHE_PATH", None)
        if path is None or not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            raw_items = data.get("items") if isinstance(data.get("items"), dict) else data
            cache: dict[str, int | None] = {}
            for slug, value in raw_items.items():
                if not slug:
                    continue
                if isinstance(value, dict):
                    value = value.get("ducats")
                cache[str(slug)] = None if value in (None, "") else int(value)
            return cache
        except Exception as e:
            print(f"[API] Could not read ducat cache: {e}")
        return {}

    def _save_item_ducat_cache(self):
        path = getattr(config, "DUCAT_CACHE_PATH", None)
        if path is None:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {"items": dict(sorted(self._item_ducat_cache.items()))},
                    f,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
        except Exception as e:
            print(f"[API] Could not save ducat cache: {e}")

    def refresh_ducat_manifest(self, force: bool = False):
        """
        Populate slug -> ducat value from WarframeStat.us item metadata.

        warframe.market is the price/listing source, but WarframeStat.us exposes
        structured game item metadata, which is a better fit for fixed ducat
        values than scraping wiki pages one by one.
        """
        if self._ducat_manifest_checked and not force:
            return
        self._ducat_manifest_checked = True

        path = getattr(config, "DUCAT_CACHE_PATH", None)
        ttl_hours = getattr(config, "DUCAT_CACHE_TTL_HOURS", 168)
        if (
            not force
            and path is not None
            and path.exists()
            and self._item_ducat_cache
            and (time.time() - path.stat().st_mtime) / 3600 < ttl_hours
        ):
            return

        try:
            # Public metadata is outside warframe.market. Never reuse its
            # authenticated session or forward its Authorization header.
            resp = requests.get(
                getattr(config, "WARFRAMESTAT_ITEMS_URL", "https://api.warframestat.us/items"),
                params={"language": self.LANGUAGE},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[API] Could not refresh ducat manifest: {e}")
            return

        added = 0
        for obj in self._iter_dicts(data):
            ducats = self._extract_ducats_from_object(obj)
            if ducats is None:
                continue
            names = [
                obj.get("name"),
                obj.get("item_name"),
                obj.get("itemName"),
                obj.get("uniqueName"),
            ]
            slugs = [
                obj.get("slug"),
                obj.get("url_name"),
                obj.get("itemSlug"),
                obj.get("item_slug"),
            ]
            for candidate in names:
                if candidate:
                    slugs.append(self._slugify(str(candidate)))

            for slug in slugs:
                if not slug:
                    continue
                slug = str(slug).strip()
                if not slug:
                    continue
                if "/" in slug:
                    slug = slug.rsplit("/", 1)[-1]
                slug = self._slugify(slug.replace("&", " "))
                if not slug:
                    continue
                if self._item_ducat_cache.get(slug) != ducats:
                    self._item_ducat_cache[slug] = ducats
                    added += 1

        if added:
            self._save_item_ducat_cache()
            print(f"[API] Cached {added} ducat values from WarframeStat.us.")

    def _cache_item_ducats(self, item_url_name: str, ducats: int | None):
        if not item_url_name:
            return
        normalized = None if ducats is None else int(ducats)
        if self._item_ducat_cache.get(item_url_name) != normalized:
            self._item_ducat_cache[item_url_name] = normalized
            self._save_item_ducat_cache()

    def _cache_item_id(self, item_url_name: str, item_id: Optional[str]):
        if not item_url_name or not item_id:
            return
        if self._item_id_cache.get(item_url_name) != item_id:
            self._item_id_cache[item_url_name] = item_id
            self._save_item_id_cache()

    def ensure_item_id_manifest(self):
        """
        Populate slug -> item id from the v2 item manifest. This avoids
        hammering /item/{slug} for every visible inventory cell.
        """
        if self._item_id_manifest_checked:
            return

        self._item_id_manifest_checked = True
        path = getattr(config, "ITEM_IDS_CACHE_PATH", None)
        ttl_hours = getattr(config, "ITEM_IDS_CACHE_TTL_HOURS", 168)
        if path is not None and path.exists() and self._item_id_cache:
            age_hours = (time.time() - path.stat().st_mtime) / 3600
            if age_hours < ttl_hours:
                return

        try:
            data = self._request("GET", "/items")
        except WFMApiError as e:
            print(f"[API] Could not refresh item id manifest: {e}")
            return

        raw_items = data.get("data") or data.get("payload", {}).get("items", [])
        added = 0
        added_ducats = 0
        for item in raw_items or []:
            slug = item.get("slug") or item.get("url_name")
            item_id = (
                item.get("id")
                or item.get("_id")
                or item.get("itemId")
                or item.get("item_id")
            )
            if slug and item_id and self._item_id_cache.get(slug) != item_id:
                self._item_id_cache[slug] = item_id
                added += 1
            if slug:
                ducats = self._extract_ducats_from_object(item)
                if ducats is not None and self._item_ducat_cache.get(slug) != ducats:
                    self._item_ducat_cache[slug] = ducats
                    added_ducats += 1

        if added:
            self._save_item_id_cache()
            print(f"[API] Cached {added} item ids from manifest.")
        if added_ducats:
            self._save_item_ducat_cache()
            print(f"[API] Cached {added_ducats} ducat values from manifest.")

    def get_cached_item_id(self, item_url_name: str) -> Optional[str]:
        return self._item_id_cache.get(item_url_name)

    def get_slug_for_item_id(self, item_id: str) -> Optional[str]:
        if not item_id:
            return None
        for slug, cached_id in self._item_id_cache.items():
            if cached_id == item_id:
                return slug
        self.ensure_item_id_manifest()
        for slug, cached_id in self._item_id_cache.items():
            if cached_id == item_id:
                return slug
        return None

    def get_item_ducats(self, item_url_name: str) -> int | None:
        """
        Return the Baro ducat value for a Prime part. The primary source is the
        WarframeStat.us item manifest; warframe.market item detail is only a
        fallback for unusual payload shapes.
        """
        if not item_url_name:
            return None
        normalized_slug = self._slugify(item_url_name)
        cached = self._item_ducat_cache.get(item_url_name)
        if cached is not None:
            return cached
        cached = self._item_ducat_cache.get(normalized_slug)
        if cached is not None:
            return cached

        self.refresh_ducat_manifest()
        cached = self._item_ducat_cache.get(item_url_name)
        if cached is not None:
            return cached
        cached = self._item_ducat_cache.get(normalized_slug)
        if cached is not None:
            return cached

        self.ensure_item_id_manifest()
        cached = self._item_ducat_cache.get(item_url_name)
        if cached is not None:
            return cached
        cached = self._item_ducat_cache.get(normalized_slug)
        if cached is not None:
            return cached

        endpoints = (
            (f"/item/{item_url_name}", self.BASE),
            (f"/items/{item_url_name}", self.BASE),
            (f"/items/{item_url_name}", config.WFM_API_BASE_V1),
        )
        for endpoint, base_url in endpoints:
            try:
                data = self._request("GET", endpoint, base_url=base_url)
            except WFMApiError:
                continue
            ducats = self._extract_ducats(data, item_url_name)
            if ducats is not None:
                self._cache_item_ducats(item_url_name, ducats)
                return ducats

        self._cache_item_ducats(item_url_name, None)
        return None

    @staticmethod
    def _extract_ducats(payload: object, item_url_name: str) -> int | None:
        fallback_values: list[int] = []
        for obj in WarframeMarketAPI._iter_dicts(payload):
            ducats = WarframeMarketAPI._extract_ducats_from_object(obj)
            if ducats is None:
                continue
            slug = (
                obj.get("slug")
                or obj.get("url_name")
                or obj.get("itemSlug")
                or obj.get("item_slug")
                or ""
            )
            name = obj.get("name") or obj.get("item_name") or obj.get("itemName") or ""
            if slug == item_url_name or WarframeMarketAPI._slugify(str(name)) == item_url_name:
                return ducats
            fallback_values.append(ducats)
        unique = set(fallback_values)
        return fallback_values[0] if len(unique) == 1 else None

    @staticmethod
    def _extract_ducats_from_object(obj: dict) -> int | None:
        for key in ("ducats", "ducat", "ducatValue", "ducat_value"):
            value = obj.get(key)
            if value in (None, ""):
                continue
            try:
                ducats = int(value)
            except (TypeError, ValueError):
                continue
            if ducats in {15, 25, 45, 65, 100}:
                return ducats
        return None

    @staticmethod
    def _iter_dicts(value: object):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from WarframeMarketAPI._iter_dicts(child)
        elif isinstance(value, list):
            for child in value:
                yield from WarframeMarketAPI._iter_dicts(child)

    @staticmethod
    def _slugify(name: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")

    def _request(self, method: str, endpoint: str, base_url: Optional[str] = None, **kwargs) -> dict:
        """Central request dispatcher with error handling and auto-retry."""
        url = f"{base_url or self.BASE}{endpoint}"
        for attempt in range(3):
            try:
                # requests.Session is not guaranteed thread-safe. UI searches,
                # inventory price lookups and startup refresh can overlap.
                with self._session_lock:
                    self._pace_request()
                    resp = self._session.request(method, url, timeout=15, **kwargs)
                if resp.status_code == 401:
                    raise WFMAuthError(
                        "Token rejected (401). Your JWT may have expired. "
                        "Re-copy it from Edge DevTools → Cookies."
                    )
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 5))
                    print(f"[API] Rate limited. Waiting {wait}s...")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {}
            except (WFMAuthError, WFMRateLimitError):
                raise
            except requests.exceptions.RequestException as e:
                if attempt == 2:
                    detail = ""
                    response = getattr(e, "response", None)
                    if response is not None:
                        detail = f" | Response: {response.text[:500]}"
                    raise WFMApiError(f"Request failed after 3 attempts: {e}{detail}")
                time.sleep(1)
        raise WFMApiError("Warframe.market remained rate limited after 3 attempts. No change was confirmed.")

    def _pace_request(self):
        interval = max(0.0, float(getattr(config, "API_MIN_REQUEST_INTERVAL_SECONDS", 0.0)))
        if interval <= 0:
            return
        with self._request_lock:
            elapsed = time.time() - self._last_request_at
            if elapsed < interval:
                time.sleep(interval - elapsed)
            self._last_request_at = time.time()

    # ── Profile Status ───────────────────────

    def set_profile_status(self, status: str) -> dict:
        """
        Set warframe.market account status.

        As of the current website bundle, the status switch writes only to the
        browser's localStorage key '@WS/USER/SET_STATUS'. The websocket action
        with the same name now returns 'app.ws.action_not_exists' when sent by
        an external client, and there is no REST status endpoint in the bundle.
        """
        normalized = {
            "offline": "invisible",
            "invisible": "invisible",
            "online": "online",
            "ingame": "ingame",
            "online_in_game": "ingame",
        }.get(status.strip().lower().replace("-", "_").replace(" ", "_"))
        if normalized is None:
            raise WFMApiError(f"Unsupported profile status: {status}")

        raise WFMApiError(
            "warframe.market status changing is not available through the current REST API. "
            "The current site stores status in browser localStorage and the old websocket "
            "action returns app.ws.action_not_exists. Use the website status switch for now, "
            "or provide a fresh 'Copy as cURL (cmd)' from changing status on the site."
        )

    def _drain_status_socket_messages(self, seconds: float) -> list[str]:
        messages: list[str] = []
        deadline = time.time() + max(0.0, seconds)
        if self._status_ws is None:
            return messages
        while time.time() < deadline:
            try:
                self._status_ws.settimeout(max(0.05, min(0.5, deadline - time.time())))
                messages.append(str(self._status_ws.recv()))
            except websocket.WebSocketTimeoutException:
                break
            except Exception as e:
                print(f"[API] Status websocket read failed: {e}")
                break
        return messages

    # ── Price Lookup ──────────────────────────

    def get_orders(self, item_url_name: str) -> list[dict]:
        """Fetch all active orders for a given item."""
        data = self._request("GET", f"/orders/item/{item_url_name}")
        return data.get("data") or data.get("payload", {}).get("orders", [])

    def get_price_info(self, item_url_name: str, strategy: str | None = None) -> PriceInfo:
        """
        Returns a PriceInfo object with current market state and
        the recommended listing price.
        """
        strategy = strategy or config.PRICE_STRATEGY
        cache_key = (item_url_name, strategy)
        cached = self._price_cache.get(cache_key)
        if cached is not None:
            created_at, price_info = cached
            if time.time() - created_at < getattr(config, "PRICE_CACHE_TTL_SECONDS", 300):
                return price_info

        all_orders = self.get_orders(item_url_name)

        # Inventory price references must only use users currently in-game.
        active_sells = [
            o for o in all_orders
            if (o.get("type") or o.get("order_type")) == "sell"
            and o.get("user", {}).get("status") in config.PRICE_STATUS_FILTER
            and o.get("visible", True)
        ]

        if not active_sells:
            raise WFMApiError(
                f"No visible sell orders from online-in-game users found for '{item_url_name}'. "
                "Other online or offline sellers are excluded from Sidro's price references."
            )

        active_sells.sort(key=lambda o: o["platinum"])

        top_5 = [
            MarketOrder(
                order_id=o["id"],
                item_name=item_url_name,
                platinum=o["platinum"],
                quantity=o["quantity"],
                order_type=o.get("type") or o.get("order_type", "sell"),
                user_status=o["user"]["status"],
                username=o["user"].get("ingameName") or o["user"].get("ingame_name", ""),
            )
            for o in active_sells[:5]
        ]

        lowest_price = active_sells[0]["platinum"]
        avg_price = sum(o["platinum"] for o in active_sells[:10]) / min(len(active_sells), 10)

        suggested = self._suggest_listing_price(active_sells, lowest_price, strategy=strategy)

        price_info = PriceInfo(
            item_url_name=item_url_name,
            lowest_sell_price=lowest_price,
            average_sell_price=round(avg_price, 1),
            active_sell_count=len(active_sells),
            suggested_listing_price=suggested,
            top_5_orders=top_5,
            highest_sell_price=max(int(order["platinum"]) for order in active_sells),
        )
        self._price_cache[cache_key] = (time.time(), price_info)
        return price_info

    @staticmethod
    def _suggest_listing_price(
        active_sells: list[dict],
        lowest_price: int,
        strategy: str | None = None,
    ) -> int:
        """
        Price strategy:
          - Default: use the modal price from the lowest cluster backed by
            multiple sellers. Isolated cheap offers should not anchor it.
            Keep supported low clusters ahead of hopeful high listings.
          - If multiple prices are equally common, choose the lower value.
          - Keep a configurable floor to avoid accidental 0p/invalid listings.
        """
        strategy = strategy or config.PRICE_STRATEGY

        if strategy == getattr(config, "PRICE_STRATEGY_FAST_SELL", "fast_lowest"):
            return max(lowest_price, config.MIN_LISTING_PRICE)

        lowest_plus = {
            getattr(config, "PRICE_STRATEGY_LOWEST_PLUS_1", "lowest_plus_1"): 1,
            getattr(config, "PRICE_STRATEGY_LOWEST_PLUS_2", "lowest_plus_2"): 2,
            getattr(config, "PRICE_STRATEGY_LOWEST_PLUS_3", "lowest_plus_3"): 3,
        }
        if strategy in lowest_plus:
            return max(lowest_price + lowest_plus[strategy], config.MIN_LISTING_PRICE)

        if strategy == "undercut_lowest":
            return max(
                lowest_price - config.UNDERCUT_BY_PLATINUM,
                config.MIN_LISTING_PRICE,
            )

        # Count each seller once at their lowest offer. Quantity and duplicate
        # orders from the same account do not provide independent price support.
        seller_prices = {}
        for index, order in enumerate(active_sells):
            user = order.get("user") or {}
            seller = (
                user.get("id") or user.get("_id")
                or user.get("ingameName") or user.get("ingame_name")
            )
            seller_key = ("seller", str(seller).casefold()) if seller else ("order", index)
            price = int(order["platinum"])
            seller_prices[seller_key] = min(price, seller_prices.get(seller_key, price))
        price_counts = Counter(
            WarframeMarketAPI._lowest_price_cluster(list(seller_prices.values()))
        )
        most_common_count = max(price_counts.values())
        tied_prices = [
            price for price, count in price_counts.items()
            if count == most_common_count
        ]
        suggested = min(tied_prices)
        if strategy == getattr(config, "PRICE_STRATEGY_MOST_COMMON_MINUS_1", "lowest_cluster_mode_minus_1"):
            suggested -= 1
        return max(suggested, config.MIN_LISTING_PRICE)

    @staticmethod
    def _lowest_price_cluster(prices: list[int]) -> list[int]:
        """
        Return the lowest price cluster supported by at least two sellers.

        Example:
            2, 3, 4, 4, 5, 25, 30, 50 -> 2, 3, 4, 4, 5
            7, 15, 15, 60 -> 15, 15

        Two guards are used:
          - absolute spread from the lowest price
          - sudden price gap between neighboring listing prices

        If every cluster is a singleton, use the next-lowest offer instead of
        anchoring to the isolated minimum. A lone seller remains the fallback
        when no alternative offer exists.
        """
        if not prices:
            return []

        prices = sorted(prices)
        cluster = [prices[0]]

        for price in prices[1:]:
            if (
                price - cluster[0] > config.PRICE_CLUSTER_MAX_SPREAD
                or price - cluster[-1] > config.PRICE_CLUSTER_MAX_GAP
            ):
                if len(cluster) >= 2:
                    return cluster
                cluster = [price]
            else:
                cluster.append(price)

        if len(cluster) >= 2:
            return cluster
        return [prices[1] if len(prices) > 1 else prices[0]]

    # ── Your Current Listings ─────────────────

    def get_my_orders(self) -> dict:
        """Fetch your own current buy and sell orders."""
        data = self._request("GET", "/orders/my")
        orders = data.get("data", [])
        return {
            "sell_orders": [
                o for o in orders
                if (o.get("type") or o.get("order_type")) == "sell"
            ],
            "buy_orders": [
                o for o in orders
                if (o.get("type") or o.get("order_type")) == "buy"
            ],
            "raw_orders": orders,
        }

    def get_existing_sell_listing_map(self) -> dict[str, ExistingListing]:
        """
        Return your current sell listings keyed by item slug/url_name.
        Also includes item id keys internally for robust duplicate matching.
        """
        my_orders = self.get_my_orders()
        listings: dict[str, ExistingListing] = {}

        for order in my_orders.get("sell_orders", []):
            item = order.get("item") or {}
            item_id = (
                order.get("itemId")
                or order.get("item_id")
                or item.get("id")
                or item.get("_id")
                or ""
            )
            slug = (
                order.get("itemSlug")
                or order.get("item_slug")
                or item.get("slug")
                or item.get("url_name")
                or ""
            )
            if not slug and item_id:
                slug = self.get_slug_for_item_id(item_id) or item_id
            if not slug:
                continue

            listing = ExistingListing(
                order_id=order.get("id", ""),
                item_url_name=slug,
                item_id=item_id,
                platinum=int(order.get("platinum", 0)),
                quantity=int(order.get("quantity", 1)),
                visible=bool(order.get("visible", True)),
            )
            listings[slug] = listing
            if item_id:
                listings[item_id] = listing

        return listings

    # ── Create Listing ────────────────────────

    def create_sell_order(
        self,
        item_id: str,
        platinum: int,
        quantity: int = config.DEFAULT_QUANTITY,
        rank: int = 0,
    ) -> dict:
        """
        Post a new sell listing.

        Args:
            item_id: The item's ID (from /item/{url_name}).
            platinum: Price in platinum.
            quantity: Number of items to list.
            rank: Mod rank (0 for most items; use actual rank for mods).

        Returns:
            The created order payload from the API.
        """
        body = {
            "itemId": item_id,
            "type": "sell",
            "visible": True,
            "platinum": max(config.MIN_LISTING_PRICE, int(platinum)),
            "quantity": max(1, int(quantity)),
        }
        data = self._request("POST", "/order", json=body)
        order = data.get("data") or data.get("payload", {}).get("order", {})
        if not isinstance(order, dict) or not (order.get("id") or order.get("_id")):
            raise WFMApiError("Warframe.market did not return a created order ID. Refresh listings before retrying.")
        print(f"[API] Listing created: {item_id} @ {body['platinum']}p x {body['quantity']}")
        return order

    # ── Fetch Item ID ─────────────────────────

    def get_item_id(self, item_url_name: str) -> Optional[str]:
        """
        warframe.market uses internal item IDs (not url_names) for order creation.
        This fetches the correct ID.
        """
        if item_url_name in self._item_id_cache:
            return self._item_id_cache[item_url_name]

        self.ensure_item_id_manifest()
        if item_url_name in self._item_id_cache:
            return self._item_id_cache[item_url_name]

        data = self._request("GET", f"/item/{item_url_name}")
        item_data = data.get("data", {})
        if item_data.get("id"):
            self._cache_item_id(item_url_name, item_data["id"])
            return item_data["id"]

        items_in_set = data.get("payload", {}).get("item", {}).get("items_in_set", [])
        # The first entry in items_in_set is the tradeable item itself
        for item in items_in_set:
            if item.get("url_name") == item_url_name:
                if item.get("id"):
                    self._cache_item_id(item_url_name, item["id"])
                return item.get("id")
        # Fallback: return first item's ID
        fallback = items_in_set[0].get("id") if items_in_set else None
        if fallback:
            self._cache_item_id(item_url_name, fallback)
        return fallback

    # ── Delete / Update ────────────────────────

    def delete_order(self, order_id: str) -> bool:
        """Delete an existing listing."""
        try:
            self._request("DELETE", f"/order/{order_id}")
            print(f"[API] Order {order_id} deleted.")
            return True
        except WFMApiError as e:
            print(f"[API] Failed to delete order {order_id}: {e}")
            return False

    def update_order_price(
        self,
        order_id: str,
        new_platinum: int,
        quantity: int = 1,
        visible: bool = True,
    ) -> dict:
        """Update the platinum price of an existing listing."""
        body = {
            "visible": bool(visible),
            "platinum": max(config.MIN_LISTING_PRICE, int(new_platinum)),
            "quantity": max(1, int(quantity)),
        }
        data = self._request("PATCH", f"/order/{order_id}", json=body)
        print(f"[API] Order {order_id} updated to {body['platinum']}p x {body['quantity']}")
        return data.get("data") or data.get("payload", {}).get("order", {})

    # ── Full Create-or-Update Flow ─────────────

    def list_item(
        self,
        item_url_name: str,
        platinum: int,
        quantity: int = config.DEFAULT_QUANTITY,
        rank: int = 0,
    ) -> dict:
        """
        High-level convenience method:
          1. Resolve item ID
          2. Check if you already have this listed
          3. Create new listing (or update existing if duplicate)

        Returns the final order dict.
        """
        item_id = self.get_item_id(item_url_name)
        if not item_id:
            raise WFMApiError(f"Could not resolve item ID for '{item_url_name}'")

        # Check for duplicate listings
        my_orders = self.get_my_orders()
        existing = [
            o for o in my_orders.get("sell_orders", [])
            if self._order_matches_item(o, item_url_name, item_id)
        ]
        if existing:
            existing_order = existing[0]
            print(f"[API] Existing listing found (id={existing_order['id']}). Updating price to {platinum}p.")
            return self.update_order_price(existing_order["id"], platinum, quantity)

        return self.create_sell_order(item_id, platinum, quantity, rank)

    @staticmethod
    def _order_matches_item(order: dict, item_url_name: str, item_id: str) -> bool:
        item = order.get("item") or {}
        candidates = {
            order.get("itemId"),
            order.get("item_id"),
            order.get("itemSlug"),
            order.get("item_slug"),
            item.get("id"),
            item.get("_id"),
            item.get("slug"),
            item.get("url_name"),
        }
        return item_url_name in candidates or item_id in candidates


# ──────────────────────────────────────────────
# QUICK TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    api = WarframeMarketAPI()

    test_item = "ash_prime_neuroptics"
    print(f"\nFetching price info for: {test_item}")
    price = api.get_price_info(test_item)
    print(f"  Lowest sell:  {price.lowest_sell_price}p")
    print(f"  Average sell: {price.average_sell_price}p")
    print(f"  Active sellers: {price.active_sell_count}")
    print(f"  Suggested price: {price.suggested_listing_price}p")
    print(f"\n  Top 5 listings:")
    for o in price.top_5_orders:
        print(f"    [{o.user_status}] {o.username}: {o.platinum}p × {o.quantity}")

"""
main.py — Sidro Orchestrator
=================================================
Two scan modes:

  SINGLE ITEM SNIP  (Ctrl+Shift+S)
    Uses Windows Snipping Tool to select one item → review name and amount → list.

  INVENTORY SCAN  (Ctrl+Shift+I)  ← PRIMARY MODE
    Scans the entire Inventory/Sell grid → shows all detected items
    in a batch review UI → tick which to list → confirm all at once.

    Workflow:
      1. Open Warframe Inventory/Sell screen (Prime Parts tab)
      2. Press Ctrl+Shift+I
      3. Review detected items in the overlay window
      4. Check/uncheck items, review suggested prices
      5. Press "List Selected" → posts all confirmed listings
"""

from __future__ import annotations
import sys
import concurrent.futures
import random
import re
import threading
import time
import traceback

import keyboard

import config
from auth_manager import (
    save_wfm_token,
    validate_wfm_token,
)
from account_store import AccountStore
from pillar2_ocr import OCRProcessor, FuzzyMatcher, ItemsCache, normalize_ocr_text
from pillar3_api import ExistingListing, WarframeMarketAPI, WFMAuthError, WFMApiError, PriceInfo
from qt_ui import UIController, UserDecision
from inventory_scanner import InventoryScanner, InventoryItem
from trade_scanner import TradeScanner, summarize_traded_counts
from reward_scanner import RewardScanner
from reward_frame_buffer import RewardFrameBuffer


class App:

    def __init__(self):
        print("\n" + "═" * 58)
        print(f"  ⚔  {config.APP_NAME.upper()}  ⚔")
        print("═" * 58)
        print(f"  Single scan   : {config.HOTKEY_SCAN}")
        print(f"  Inventory scan (6x3): {config.HOTKEY_INVENTORY_SCAN}")
        print(f"  Inventory scan (bottom 6): {config.HOTKEY_INVENTORY_BOTTOM_SCAN}")
        print(f"  Trade monitor : {config.HOTKEY_TRADE_MONITOR}")
        print(f"  Account UI    : {config.HOTKEY_ACCOUNT_UI}")
        print(f"  Reward prices : {config.HOTKEY_REWARD_SCAN}")
        print(f"  Quit          : {config.HOTKEY_QUIT}")
        print("═" * 58 + "\n")

        self.ocr = None
        self.inventory_scanner = None
        self.inventory_bottom_scanner = None
        self.trade_scanner = None
        self.reward_scanner = None
        self.item_search_cache = None
        self.item_search_matcher = None
        self.ui = UIController()
        self.account_store = AccountStore()
        self._queued_reward_picks = self.account_store.get_reward_queue()
        print(
            f"[Reward] Loaded {sum(int(row.get('quantity', 0)) for row in self._queued_reward_picks.values())} "
            f"queued reward(s) from {self.account_store.path}"
        )
        self.ui.set_reward_queue_count(
            sum(int(row.get("quantity", 0)) for row in self._queued_reward_picks.values())
        )
        self.dashboard_window = None

        try:
            self.api = WarframeMarketAPI()
            print("[Main] ✅ API authenticated.")
        except WFMAuthError as e:
            print(f"[Main] ❌ AUTH: {e}")
            self.api = None

        self._running = False
        self._stop_event = threading.Event()
        self._worker_threads: set[threading.Thread] = set()
        self._worker_threads_lock = threading.Lock()
        self._ocr_warmup_lock = threading.Lock()
        self._ocr_reset_pending = threading.Event()
        self._scan_lock = threading.Lock()
        self._inventory_lock = threading.Lock()
        self._inventory_pending: list[str] = []
        self._inventory_pending_lock = threading.Lock()
        self._trade_lock = threading.Lock()
        self._trade_cancel_event: threading.Event | None = None
        self._reward_monitor_stop = threading.Event()
        self._reward_monitor_thread: threading.Thread | None = None
        self._reward_monitor_lock = threading.Lock()
        self._trade_live_scan_stop = threading.Event()
        self._trade_live_scan_thread: threading.Thread | None = None
        self._trade_live_scan_lock = threading.Lock()
        self._last_live_trade_signature: tuple[tuple[str, int], ...] | None = None
        self._last_live_completed_trade_signature: tuple[tuple[str, int], ...] | None = None
        self._live_trade_empty_hits = 0
        self._last_reward_signature: tuple[str, ...] | None = None
        self._last_reward_price_signature: tuple | None = None
        self._last_reward_rows: list[dict] | None = None
        self._reward_absent_hits = 0
        self._reward_screen_started_at: float | None = None
        self._reward_first_overlay_shown = False
        self._reward_session_started_at: float | None = None
        self._reward_candidates_by_slot: dict[int, dict[str, dict]] = {}
        self._pending_reward_pick: dict | None = None
        self._pending_reward_slot: int | None = None
        self._reward_review_cache: dict[str, tuple[InventoryItem, dict[str, PriceInfo], dict]] = {}
        self._reward_review_inflight: set[str] = set()
        self._reward_review_skipped: dict[str, str] = {}
        self._reward_review_lock = threading.Lock()
        self._reward_queue_lock = threading.Lock()
        self._reward_queue_review_lock = threading.Lock()

    def _get_single_scanner(self):
        if self.ocr is None:
            self.ocr = OCRProcessor(context="single_scan")
        elif self.ocr._engine is None:
            self.ocr._engine = self.ocr._init_engine()
        self._schedule_ocr_button_refresh()
        return self.ocr

    def _get_inventory_scanner(self):
        if self.inventory_scanner is None:
            self.inventory_scanner = InventoryScanner(label="6x3", ocr_context="inventory_6x3")
        self._schedule_ocr_button_refresh()
        return self.inventory_scanner

    def _get_inventory_bottom_scanner(self):
        if self.inventory_bottom_scanner is None:
            self.inventory_bottom_scanner = InventoryScanner(
                rows=getattr(config, "INVENTORY_BOTTOM_GRID_ROWS", 1),
                row_start=getattr(config, "INVENTORY_BOTTOM_ROW_START", 3),
                row_y_offset=getattr(config, "INVENTORY_BOTTOM_ROW_Y_OFFSET", 590),
                label="bottom 6",
                ocr_context="inventory_bottom6",
            )
        self._schedule_ocr_button_refresh()
        return self.inventory_bottom_scanner

    def _get_trade_scanner(self):
        if self.trade_scanner is None:
            self.trade_scanner = TradeScanner()
        elif self.trade_scanner._ocr is None:
            from pillar2_ocr import create_ocr_engine
            self.trade_scanner._ocr = create_ocr_engine("trade_monitor")
        self._schedule_ocr_button_refresh()
        return self.trade_scanner

    def _get_reward_scanner(self):
        if self.reward_scanner is None:
            self.reward_scanner = RewardScanner()
        elif self.reward_scanner._ocr is None:
            from pillar2_ocr import create_ocr_engine
            self.reward_scanner._ocr = create_ocr_engine("reward_prices")
        self._schedule_ocr_button_refresh()
        return self.reward_scanner

    def _get_item_search_matcher(self):
        if self.item_search_cache is None:
            self.item_search_cache = ItemsCache()
        if self.item_search_matcher is None:
            self.item_search_matcher = FuzzyMatcher(self.item_search_cache, prime_only=False)
        return self.item_search_cache, self.item_search_matcher

    # ── Single item scan (original flow) ──────

    def scan_and_list(self):
        if self._ocr_reset_pending.is_set():
            return
        if not self._scan_lock.acquire(blocking=False):
            return
        try:
            ocr = self._get_single_scanner()
            selection = self.ui.snip_screen_area()
            if selection is None:
                return
            frame, preview_image = selection

            matched = ocr.process(frame)
            if self._ocr_reset_pending.is_set():
                return
            self._log_ocr_memory("after single-item scan")
            if matched is None:
                self.ui.toast("No item recognized. Snip the item's name and try again.", "warning"); return

            if self.api is None:
                self.ui.toast("❌ Not authenticated.", "error"); return

            try:
                price_info = self.api.get_price_info(matched.url_name)
            except WFMApiError as e:
                self.ui.toast(f"❌ Price lookup failed: {e}", "error"); return

            try:
                existing_listings = self.api.get_existing_sell_listing_map()
                existing_listing = existing_listings.get(matched.url_name)
                if existing_listing is None:
                    item_id = self.api.get_item_id(matched.url_name)
                    existing_listing = existing_listings.get(item_id) if item_id else None
                if existing_listing is not None:
                    existing_listing.item_url_name = matched.url_name
            except WFMApiError as e:
                self.ui.toast(f"Could not check your existing listing: {e}", "error", 6000)
                return

            listing_price = price_info.suggested_listing_price
            quantity = self._quantity_from_ocr(matched.raw_ocr)
            decision, quantity, listing_price, listing_action = self.ui.ask_confirmation(
                matched,
                price_info,
                listing_price,
                quantity=quantity,
                preview_image=preview_image,
                existing_listing=existing_listing,
            )

            if decision == UserDecision.CONFIRMED:
                if listing_action == "SKIP":
                    self.ui.toast(f"Skipped {matched.display_name}.", "info")
                else:
                    self._post_listing(
                        matched.display_name,
                        matched.url_name,
                        listing_price,
                        quantity,
                        existing_listing if listing_action == "UPDATE" else None,
                    )
            elif decision == UserDecision.TIMEOUT:
                self.ui.toast("⏱ Timed out — cancelled.", "warning")
            else:
                self.ui.toast("↩ Cancelled.", "info")
        finally:
            self._scan_lock.release()

    @staticmethod
    def _quantity_from_ocr(raw_text: str) -> int:
        """Use an explicit xN count when OCR sees one; otherwise default to one."""
        text = str(raw_text or "")
        patterns = (r"[x×]\s*(\d{1,3})", r"(\d{1,3})\s*[x×]")
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                quantity = int(match.group(1))
                if 1 <= quantity <= 999:
                    return quantity
        return max(1, int(getattr(config, "DEFAULT_QUANTITY", 1)))

    # ── Inventory batch scan ───────────────────

    def scan_inventory(self, scan_mode: str = "6x3"):
        if self._ocr_reset_pending.is_set():
            return
        if not self._inventory_lock.acquire(blocking=False):
            with self._inventory_pending_lock:
                self._inventory_pending.append(scan_mode)
                queued = len(self._inventory_pending)
            self.ui.toast(f"Inventory scan ({scan_mode}) queued ({queued}).", "info", 3500)
            return
        try:
            scan_label = "bottom 6" if scan_mode == "bottom_6" else "6x3"
            inventory_scanner = (
                self._get_inventory_bottom_scanner()
                if scan_mode == "bottom_6"
                else self._get_inventory_scanner()
            )

            try:
                scan_result = inventory_scanner.scan()
                if self._ocr_reset_pending.is_set():
                    return
                self._log_ocr_memory(f"after {scan_label} OCR scan")
            except RuntimeError as e:
                self.ui.toast(f"❌ {e}", "error")
                return

            if not scan_result.items:
                if scan_mode == "bottom_6":
                    self.ui.toast(
                        "No bottom-row items detected. Scroll so the final row labels are fully visible, then retry.",
                        "warning",
                    )
                else:
                    self.ui.toast("No items detected. Check ROI alignment (run inventory_scanner.py).", "warning")
                return

            if self.api is None:
                self.ui.toast("❌ Not authenticated.", "error")
                return

            self.account_store.upsert_inventory_items(scan_result.items)
            self.ui.alert(
                f"Inventory captured ({scan_label}): {len(scan_result.items)} item(s).",
                "success",
                3500,
                "info",
            )
            print(f"[Main] Fetching prices for {len(scan_result.items)} detected item(s)...")
            price_map: dict[str, PriceInfo] = {}
            failed_prices: list[str] = []

            def _fetch_price(item):
                return item, self.api.get_price_info(item.url_name)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=getattr(config, "PRICE_LOOKUP_WORKERS", 4)
            ) as pool:
                future_map = {
                    pool.submit(_fetch_price, item): item
                    for item in scan_result.items
                }
                completed = 0
                for future in concurrent.futures.as_completed(future_map):
                    item = future_map[future]
                    completed += 1
                    try:
                        fetched_item, price_info = future.result()
                        price_map[fetched_item.url_name] = price_info
                        print(f"[Main] Price {completed}/{len(scan_result.items)}: "
                              f"{fetched_item.display_name} -> {price_info.suggested_listing_price}p")
                    except WFMApiError as e:
                        print(f"[Main] No price for {item.display_name}: {e}")
                        failed_prices.append(item.display_name)
                    except Exception as e:
                        print(f"[Main] Price lookup error for {item.display_name}: {e}")
                        failed_prices.append(item.display_name)

            if failed_prices:
                self.ui.toast(f"⚠ {len(failed_prices)} item(s) have no market data.", "warning", 3000)

            priced_items = [i for i in scan_result.items if i.url_name in price_map]
            listable, saved_for_ducats = self._filter_ducat_save_items(
                priced_items,
                price_map,
                context=f"Inventory scan ({scan_label})",
            )
            if not listable and not saved_for_ducats:
                self.ui.toast("No one-by-one listable items found.", "warning")
                return

            existing_listing_map = {}
            if listable:
                try:
                    self.api.ensure_item_id_manifest()
                    existing_listing_map = self.api.get_existing_sell_listing_map()
                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=getattr(config, "ITEM_ID_LOOKUP_WORKERS", 2)
                    ) as pool:
                        id_futures = {
                            pool.submit(self.api.get_item_id, item.url_name): item
                            for item in listable
                        }
                        for future in concurrent.futures.as_completed(id_futures):
                            item = id_futures[future]
                            item_id = future.result()
                            if item_id:
                                item._item_id = item_id
                                existing = existing_listing_map.get(item_id)
                                if existing is not None:
                                    existing.item_url_name = item.url_name
                                    existing_listing_map[item.url_name] = existing
                    existing_visible = [
                        i for i in listable
                        if i.url_name in existing_listing_map
                    ]
                    self.account_store.upsert_inventory_items(listable)
                    self.account_store.replace_listings(
                        existing_listing_map,
                        {item.url_name: item.display_name for item in listable},
                    )
                    print(f"[Main] Existing listings matched on this page: {len(existing_visible)}")
                except WFMApiError as e:
                    print(f"[Main] Could not fetch existing listings: {e}")
                    existing_listing_map = {}

            print(f"[Main] Opening inventory review for {len(listable)} listable item(s)...")
            confirmed_items = self._show_inventory_review(
                listable,
                price_map,
                existing_listing_map,
                scan_title=f"Inventory scan ({scan_label})",
                saved_ducat_items=saved_for_ducats,
                failed_positions=scan_result.failed_positions or [],
            )

            return self._post_confirmed_inventory_items(confirmed_items, price_map)

        finally:
            self._inventory_lock.release()
            with self._inventory_pending_lock:
                next_scan_mode = self._inventory_pending.pop(0) if self._inventory_pending else None
            if next_scan_mode:
                threading.Thread(
                    target=lambda mode=next_scan_mode: self.scan_inventory(mode),
                    daemon=True,
                ).start()

    def scan_inventory_bottom(self):
        return self.scan_inventory("bottom_6")

    def _ducat_save_decision(
        self,
        item_url_name: str,
        display_name: str,
        price_info: PriceInfo,
    ) -> tuple[bool, str, int | None, int | None]:
        if not getattr(config, "DUCAT_FILTER_ENABLED", True):
            return False, "", None, None
        rules = {
            int(ducats): int(platinum)
            for ducats, platinum in dict(getattr(config, "DUCAT_BULK_PLAT_VALUES", {})).items()
            if int(platinum) >= 0
        }
        if not rules or self.api is None:
            return False, "", None, None

        lowest = int(price_info.lowest_sell_price)
        max_bulk_value = max(rules.values())
        if lowest > max_bulk_value:
            return False, "", None, None

        ducats = self.api.get_item_ducats(item_url_name)
        if ducats is None:
            return False, "", None, None
        bulk_value = rules.get(int(ducats))
        if bulk_value is None:
            return False, "", int(ducats), None

        if lowest <= bulk_value:
            reason = (
                f"{display_name} saved for ducats "
                f"({ducats}d bulk={bulk_value}p, market low={lowest}p)"
            )
            return True, reason, int(ducats), int(bulk_value)
        return False, "", int(ducats), int(bulk_value)

    def _saved_ducat_row(
        self,
        item: InventoryItem,
        price_info: PriceInfo,
        ducats: int | None,
        bulk_value: int | None,
        reason: str,
    ) -> dict:
        return {
            "display_name": item.display_name,
            "url_name": item.url_name,
            "quantity": item.quantity,
            "ducats": ducats,
            "lowest": int(price_info.lowest_sell_price),
            "bulk_value": bulk_value,
            "reason": reason,
        }

    def _filter_ducat_save_items(
        self,
        items: list[InventoryItem],
        price_map: dict[str, PriceInfo],
        context: str,
    ) -> tuple[list[InventoryItem], list[dict]]:
        listable: list[InventoryItem] = []
        saved: list[dict] = []
        for item in items:
            price_info = price_map.get(item.url_name)
            if price_info is None:
                continue
            try:
                should_save, reason, ducats, bulk_value = self._ducat_save_decision(
                    item.url_name,
                    item.display_name,
                    price_info,
                )
            except Exception as e:
                print(f"[Ducat] Could not check {item.display_name}: {e}")
                should_save = False
                reason = ""
            if should_save:
                saved.append(self._saved_ducat_row(item, price_info, ducats, bulk_value, reason))
                continue
            listable.append(item)

        if saved:
            print(f"[Ducat] {context}: saved {len(saved)} item(s) for bulk ducat trading.")
            for row in saved:
                print(f"[Ducat]   {row['reason']}")
            self.ui.alert(
                f"Saved {len(saved)} ducat-value item(s). They will appear in inventory review.",
                "info",
                6000,
                "info",
            )
        return listable, saved

    def _post_confirmed_inventory_items(self, confirmed_items, price_map) -> set[str]:
        if not confirmed_items:
            self.ui.toast("↩ No items selected.", "info")
            return set()

        if not config.ENABLE_LIVE_LISTING:
            print("\n[Main] LIVE LISTING DISABLED. Review plan:")
            for item in confirmed_items:
                action = getattr(item, "_listing_action", "UPDATE")
                if action == "DELETE":
                    print(f"[Main]   DELETE {item.display_name}")
                    continue
                price_info = price_map[item.url_name]
                listing_price = getattr(
                    item,
                    "_override_price",
                    price_info.suggested_listing_price,
                )
                print(f"[Main]   {item.quantity}x {item.display_name} @ {listing_price}p")
            self.ui.toast(
                "Live posting is disabled until the official write API is verified. "
                "Listing plan printed in the terminal.",
                "warning",
                8000,
            )
            return set()

        success, errors = 0, 0
        applied_urls = set()

        for item in confirmed_items:
            action = getattr(item, "_listing_action", "UPDATE")
            try:
                existing_order_id = getattr(item, "_existing_order_id", None)
                if action == "DELETE":
                    if not existing_order_id:
                        raise WFMApiError(f"No existing listing to delete for '{item.url_name}'")
                    if not self.api.delete_order(existing_order_id):
                        raise WFMApiError(f"Delete request failed for '{item.url_name}'")
                    self.account_store.delete_listing(item.url_name)
                    print(f"[Main] ✅ Deleted listing: {item.display_name}")
                    self.ui.toast(f"Deleted listing: {item.display_name}", "success", 3000)
                else:
                    price_info = price_map[item.url_name]
                    listing_price = getattr(item, "_override_price", price_info.suggested_listing_price)
                    if existing_order_id:
                        existing_listing = self.account_store.listings.get(item.url_name)
                        listing_visible = bool(existing_listing.visible) if existing_listing else True
                        self.api.update_order_price(
                            existing_order_id,
                            listing_price,
                            quantity=item.quantity,
                            visible=listing_visible,
                        )
                        item_id = getattr(item, "_item_id", "")
                        self.account_store.upsert_listing(
                            item.url_name,
                            item.display_name,
                            existing_order_id,
                            item_id=item_id,
                            platinum=listing_price,
                            quantity=item.quantity,
                            visible=listing_visible,
                        )
                        print(f"[Main] ✅ Updated: {item.display_name} @ {listing_price}p x {item.quantity}")
                        self.ui.toast(f"Updated {item.display_name}: {listing_price}p x {item.quantity}", "success", 3000)
                    else:
                        item_id = getattr(item, "_item_id", None)
                        if not item_id:
                            item_id = self.api.get_item_id(item.url_name)
                        if not item_id:
                            raise WFMApiError(f"Could not resolve item ID for '{item.url_name}'")
                        order = self.api.create_sell_order(item_id, listing_price, quantity=item.quantity)
                        order_id = order.get("id") or order.get("_id") or ""
                        self.account_store.upsert_listing(
                            item.url_name,
                            item.display_name,
                            order_id,
                            item_id=item_id,
                            platinum=listing_price,
                            quantity=item.quantity,
                            visible=True,
                        )
                        print(f"[Main] ✅ Listed: {item.display_name} @ {listing_price}p x {item.quantity}")
                        self.ui.toast(f"Listed {item.display_name}: {listing_price}p x {item.quantity}", "success", 3000)
                success += 1
                delay = random.uniform(
                    config.LISTING_REQUEST_DELAY_MIN_SECONDS,
                    config.LISTING_REQUEST_DELAY_MAX_SECONDS,
                )
                print(f"[Main] API pacing delay: {delay:.3f}s")
                time.sleep(delay)
            except (WFMApiError, WFMAuthError) as e:
                if (
                    isinstance(e, WFMApiError)
                    and getattr(item, "_from_reward", False)
                    and "exceededOrderLimitSamePrice" in str(e)
                    and self._merge_reward_into_existing_listing(item)
                ):
                    success += 1
                    applied_urls.add(item.url_name)
                    continue
                print(f"[Main] ❌ Failed: {item.display_name}: {e}")
                errors += 1
                continue
            applied_urls.add(item.url_name)

        if errors:
            self.ui.toast(f"⚠ {success} applied, {errors} failed.", "warning", 6000)
        if applied_urls:
            self.ui.refresh_dashboard_profile()
        return applied_urls

    # ── Trade completion monitor ───────────────

    def monitor_trade_and_update_listings(self):
        if self._ocr_reset_pending.is_set():
            return
        if not self._trade_lock.acquire(blocking=False):
            if self._trade_cancel_event is not None:
                self._trade_cancel_event.set()
            self.ui.toast("Previous trade monitor aborted; starting a new scan.", "warning", 3500)
            threading.Timer(0.25, lambda: threading.Thread(target=self.monitor_trade_and_update_listings, daemon=True).start()).start()
            return
        self._trade_cancel_event = threading.Event()
        self._trade_live_scan_lock.acquire()
        try:
            if self.api is None:
                self.ui.toast("❌ Not authenticated.", "error")
                return
            if not str(getattr(config, "TRADE_MY_NAME", "")).strip():
                self.ui.toast(
                    "Set your Warframe name in Settings before using Trade monitor.",
                    "warning",
                    7000,
                )
                return

            trade_scanner = self._get_trade_scanner()
            known_trade_items = self._trade_match_candidates()

            try:
                trade = trade_scanner.scan_current_trade(known_items=known_trade_items)
                self._log_ocr_memory("after trade scan")
            except RuntimeError as e:
                self.ui.toast(f"❌ {e}", "error")
                return

            if not trade.name_verified:
                self.ui.toast(
                    f"⚠ Trade screen name mismatch: '{trade.account_name_seen}'",
                    "warning",
                    6000,
                )
                print(f"[Trade] Expected own name: {config.TRADE_MY_NAME}")
                return

            if not trade.offered_items:
                self.ui.toast("🤷 No offered items detected in your trade slots.", "warning", 5000)
                return

            offered = ", ".join(item.display_name for item in trade.offered_items)
            self.ui.alert(f"Trade screen detected: {offered}. Waiting for success.", "info", 9000, "trade")
            print(f"[Trade] Watching for completion. Offered: {offered}")

            completed_trade = trade_scanner.wait_for_completed_trade(
                trade,
                known_items=known_trade_items,
                cancel_event=self._trade_cancel_event,
            )
            if completed_trade is None:
                self.ui.alert("Final trade success was not detected.", "warning", 6000, "warning")
                return

            self._decrement_listings_after_trade(completed_trade.offered_items)

        finally:
            self._trade_live_scan_lock.release()
            self._trade_lock.release()
            self._trade_cancel_event = None

    def _trade_match_candidates(self) -> dict[str, str]:
        if self.api is not None:
            try:
                self.api.ensure_item_id_manifest()
                existing_map = self.api.get_existing_sell_listing_map()
                self.account_store.replace_listings(existing_map)
                print(f"[Trade] Refreshed WFM listings for matching: {len(self.account_store.listings)} active local row(s).")
            except Exception as e:
                print(f"[Trade] WFM listing refresh skipped before trade scan: {e}")
        candidates = self.account_store.listed_match_candidates()
        print(f"[Trade] Matching against {len(candidates)} active listed item(s).")
        return candidates

    def set_trade_scan_enabled(self, enabled: bool):
        if enabled and self._ocr_reset_pending.is_set():
            return
        if enabled:
            if self._trade_live_scan_thread and self._trade_live_scan_thread.is_alive():
                return
            self._trade_live_scan_stop.clear()
            self._trade_live_scan_thread = threading.Thread(
                target=self._trade_live_scan_loop,
                name="Sidro: Live Trade Scanning",
                daemon=True,
            )
            self._trade_live_scan_thread.start()
            self.ui.toast("Live trade scanning enabled.", "info", 3000)
            return

        self._trade_live_scan_stop.set()
        self._last_live_trade_signature = None
        self._last_live_completed_trade_signature = None
        self._live_trade_empty_hits = 0
        self.ui.toast("Live trade scanning disabled.", "info", 3000)

    def _trade_live_scan_loop(self):
        print("[Trade] Live scanner started.")
        interval = max(0.2, float(getattr(config, "TRADE_LIVE_SCAN_POLL_SECONDS", 1.0)))
        known_items = None
        while not self._trade_live_scan_stop.is_set() and not self._stop_event.is_set():
            if not self._trade_live_scan_lock.acquire(blocking=False):
                self._trade_live_scan_stop.wait(interval)
                continue
            try:
                scanner = self._get_trade_scanner()
                if known_items is None:
                    known_items = self.account_store.listed_match_candidates()
                result = scanner.scan_current_trade(known_items=known_items, log=False)
                if self._ocr_reset_pending.is_set():
                    break
                if not result.name_verified:
                    if self._last_live_completed_trade_signature is None:
                        self._last_live_trade_signature = None
                elif not result.offered_items:
                    if self._last_live_completed_trade_signature is not None:
                        self._live_trade_empty_hits += 1
                        if self._live_trade_empty_hits >= 2:
                            self._last_live_completed_trade_signature = None
                            self._last_live_trade_signature = None
                            self._live_trade_empty_hits = 0
                    else:
                        self._last_live_trade_signature = None
                else:
                    signature = tuple(sorted(
                        (item.url_name, int(item.slot_index))
                        for item in result.offered_items
                    ))
                    if signature != self._last_live_completed_trade_signature:
                        self._last_live_completed_trade_signature = None
                    self._live_trade_empty_hits = 0
                    new_offer = signature != self._last_live_trade_signature
                    if new_offer:
                        self._last_live_trade_signature = signature
                        offered = ", ".join(item.display_name for item in result.offered_items)
                        print(f"[Trade] Live scan detected offered item(s): {offered}")
                        self.ui.toast(f"Trade detected: {offered}", "success", 6000)

                    if new_offer:
                        print("[Trade] Live scan is watching for the successful-trade dialog.")
                        completed_trade = scanner.wait_for_completed_trade(
                            result,
                            known_items=known_items,
                            cancel_event=self._trade_live_scan_stop,
                        )
                        if completed_trade is not None:
                            print("[Trade] Live scan confirmed a successful trade.")
                            print("[Trade] Syncing WFM listings for the completed trade.")
                            try:
                                self._decrement_listings_after_trade(completed_trade.offered_items)
                            except Exception as e:
                                print(f"[Trade] WFM listing sync failed after confirmed trade: {e}")
                                self.ui.alert(
                                    f"Trade succeeded, but WFM listing sync failed: {e}",
                                    "error",
                                    9000,
                                    "warning",
                                )
                            known_items = None
                            # Keep this offer latched while the success dialog
                            # is visible so a later poll cannot sync twice.
                            self._last_live_trade_signature = signature
                            self._last_live_completed_trade_signature = signature
                        else:
                            print("[Trade] Live scan ended without a confirmed successful trade.")
            except Exception as e:
                print(f"[Trade] Live scan skipped: {e}")
            finally:
                self._trade_live_scan_lock.release()
            self._trade_live_scan_stop.wait(interval)
        print("[Trade] Live scanner stopped.")

    def _decrement_listings_after_trade(self, traded_items):
        if not config.ENABLE_LIVE_LISTING:
            self.ui.alert(
                "Trade detected, but listing sync is disabled. Enable ENABLE_LIVE_LISTING in config.py to sync quantities.",
                "warning",
                8000,
                "warning",
            )
            return
        sold_counts = summarize_traded_counts(traded_items)
        self.api.ensure_item_id_manifest()
        existing_map = self.api.get_existing_sell_listing_map()
        self.account_store.replace_listings(existing_map)
        updated, deleted, missing = 0, 0, 0

        for url_name, sold_count in sold_counts.items():
            item_id = self.api.get_item_id(url_name)
            existing = existing_map.get(item_id) or existing_map.get(url_name)

            if existing is None:
                print(f"[Trade] No current listing found for sold item: {url_name}")
                missing += 1
                continue

            remaining = existing.quantity - sold_count
            if remaining <= 0:
                if self.api.delete_order(existing.order_id):
                    print(f"[Trade] Deleted sold-out listing: {url_name}")
                    self.account_store.decrement_after_trade(url_name, sold_count)
                    deleted += 1
                else:
                    print(f"[Trade] Failed to delete sold-out listing: {url_name}")
                    missing += 1
                continue

            try:
                self.api.update_order_price(
                    existing.order_id,
                    existing.platinum,
                    quantity=remaining,
                    visible=existing.visible,
                )
                print(f"[Trade] Reduced listing: {url_name} {existing.quantity} -> {remaining}")
                self.account_store.decrement_after_trade(url_name, sold_count)
                updated += 1
            except (WFMApiError, WFMAuthError) as e:
                print(f"[Trade] Failed to reduce listing for {url_name}: {e}")
                missing += 1

            delay = random.uniform(
                config.LISTING_REQUEST_DELAY_MIN_SECONDS,
                config.LISTING_REQUEST_DELAY_MAX_SECONDS,
            )
            print(f"[Trade] API pacing delay: {delay:.3f}s")
            time.sleep(delay)

        self.ui.alert(
            f"Trade synced: {updated} updated, {deleted} deleted, {missing} missing.",
            "success" if missing == 0 else "warning",
            7000,
            "success" if missing == 0 else "warning",
        )
        if updated or deleted:
            self.ui.refresh_dashboard_profile()

    # ── Void Fissure reward price overlay ─────

    def scan_reward_prices(self):
        if self._ocr_reset_pending.is_set():
            return
        if self.api is None:
            self.ui.toast("❌ Not authenticated.", "error")
            return
        try:
            result = self._get_reward_scanner().scan()
            if self._ocr_reset_pending.is_set():
                return
            self._log_ocr_memory("after reward scan")
            if not result.items:
                self.ui.toast("No reward items detected.", "warning", 4000)
                return

            rows = self._reward_rows_with_prices(result.items)

            self.ui.show_reward_prices(rows)
            self.ui.toast(f"Reward prices shown for {len(rows)} item(s).", "success", 3500)
        except Exception as e:
            print(f"[Reward] ERROR: {e}")
            self.ui.toast(f"Reward price scan failed: {e}", "error", 6000)

    def _reward_rows_with_prices(self, items) -> list[dict]:
        rows = []
        for item in items:
            rows.append(self._reward_row_with_price(self._reward_row_without_price(item)))
        return rows

    def _reward_rows_without_prices(self, items) -> list[dict]:
        return [self._reward_row_without_price(item) for item in items]

    def _reward_row_without_price(self, item) -> dict:
        return {
            "display_name": item.display_name,
            "url_name": item.url_name,
            "price": None,
            "save_for_ducats": False,
            "ducats": None,
            "ducat_bulk_value": None,
            "ducat_reason": "",
            "match_score": item.match_score,
            "card_rect": item.card_rect,
            "slot_index": getattr(item, "slot_index", None),
            "selected_by_you": bool(getattr(item, "selected_by_you", False)),
            "alternates": [],
            "match_alternates": [
                {
                    "display_name": name,
                    "url_name": url_name,
                    "match_score": int(score),
                    "scan_count": 0,
                }
                for name, url_name, score in getattr(item, "alternatives", []) or []
            ],
        }

    def _reward_row_with_price(self, row: dict, include_alternates: bool = True) -> dict:
        priced = dict(row)
        price = None
        save_for_ducats = False
        ducats = None
        ducat_bulk_value = None
        ducat_reason = ""
        try:
            price_info = self.api.get_price_info(priced["url_name"])
            price = price_info.lowest_sell_price
            (
                save_for_ducats,
                ducat_reason,
                ducats,
                ducat_bulk_value,
            ) = self._ducat_save_decision(priced["url_name"], priced["display_name"], price_info)
        except Exception as e:
            print(f"[Reward] Price lookup failed for {priced['display_name']}: {e}")
        priced.update({
            "price": price,
            "save_for_ducats": save_for_ducats,
            "ducats": ducats,
            "ducat_bulk_value": ducat_bulk_value,
            "ducat_reason": ducat_reason,
            "alternates": (
                self._reward_alternates_with_prices(priced.get("alternates") or [])
                if include_alternates
                else [dict(alternate, price=None) for alternate in priced.get("alternates") or []]
            ),
        })
        return priced

    def _reward_alternates_with_prices(self, alternates: list[dict]) -> list[dict]:
        priced_alternates: list[dict] = []
        for alternate in alternates:
            priced = dict(alternate)
            priced["price"] = None
            url_name = str(priced.get("url_name", "")).strip()
            display_name = str(priced.get("display_name", url_name)).strip()
            if not url_name:
                priced_alternates.append(priced)
                continue
            try:
                price_info = self.api.get_price_info(url_name)
                priced["price"] = price_info.lowest_sell_price
            except Exception as e:
                print(f"[Reward] Alternate price lookup failed for {display_name}: {e}")
            priced_alternates.append(priced)
        return priced_alternates

    def _reset_reward_session_tracking(self):
        scanner = getattr(self, "reward_scanner", None)
        if scanner is not None:
            scanner.reset_session()
        self._last_reward_signature = None
        self._last_reward_price_signature = None
        self._last_reward_rows = None
        self._reward_absent_hits = 0
        self._reward_session_started_at = None
        self._reward_screen_started_at = None
        self._reward_first_overlay_shown = False
        self._reward_candidates_by_slot = {}

    def _merge_reward_scan_result(self, result) -> tuple[list[dict], list[dict], tuple, bool]:
        if self._reward_session_started_at is None:
            self._reward_session_started_at = time.monotonic()

        for item in result.items:
            row = self._reward_row_without_price(item)
            slot = row.get("slot_index")
            if slot is None:
                continue
            slot = int(slot)
            url_name = str(row["url_name"])
            slot_candidates = self._reward_candidates_by_slot.setdefault(slot, {})
            candidate = slot_candidates.get(url_name)
            if candidate is None:
                candidate = {
                    "row": dict(row),
                    "count": 0,
                    "best_score": int(row.get("match_score", 0) or 0),
                    "first_seen": time.monotonic(),
                }
                slot_candidates[url_name] = candidate
            candidate["count"] = int(candidate.get("count", 0)) + 1
            if int(row.get("match_score", 0) or 0) > int(candidate.get("best_score", 0) or 0):
                candidate["row"] = dict(row)
                candidate["best_score"] = int(row.get("match_score", 0) or 0)

        best_rows: list[dict] = []
        overlay_rows: list[dict] = []
        max_alternates = int(getattr(config, "REWARD_ANALYSIS_MAX_ALTERNATES", 3))

        for slot in sorted(self._reward_candidates_by_slot):
            candidates = list(self._reward_candidates_by_slot[slot].values())
            if not candidates:
                continue
            ranked = sorted(
                candidates,
                key=lambda item: (
                    int(item.get("count", 0) or 0),
                    int(item.get("best_score", 0) or 0),
                    -float(item.get("first_seen", 0.0) or 0.0),
                ),
                reverse=True,
            )
            best = dict(ranked[0]["row"])
            best["scan_count"] = int(ranked[0].get("count", 0) or 0)
            best["match_score"] = int(ranked[0].get("best_score", best.get("match_score", 0)) or 0)
            best_rows.append(best)

            # The overlay and selected-reward queue must use the same identity.
            primary = dict(best)
            primary_url = str(primary.get("url_name", ""))
            alternates: list[dict] = []
            alternate_urls: set[str] = set()
            for candidate in ranked:
                row = dict(candidate["row"])
                if str(row.get("url_name", "")) == primary_url:
                    continue
                alternate_url = str(row.get("url_name", ""))
                if not alternate_url or alternate_url in alternate_urls:
                    continue
                alternates.append({
                    "display_name": row.get("display_name", ""),
                    "url_name": alternate_url,
                    "match_score": int(candidate.get("best_score", row.get("match_score", 0)) or 0),
                    "scan_count": int(candidate.get("count", 0) or 0),
                })
                alternate_urls.add(alternate_url)
                if len(alternates) >= max_alternates:
                    break
            for alternate in primary.get("match_alternates", []) or []:
                alternate_url = str(alternate.get("url_name", ""))
                if (
                    not alternate_url
                    or alternate_url == primary_url
                    or alternate_url in alternate_urls
                    or len(alternates) >= max_alternates
                ):
                    continue
                alternates.append({
                    "display_name": alternate.get("display_name", ""),
                    "url_name": alternate_url,
                    "match_score": int(alternate.get("match_score", 0) or 0),
                    "scan_count": int(alternate.get("scan_count", 0) or 0),
                })
                alternate_urls.add(alternate_url)
            primary["alternates"] = alternates
            overlay_rows.append(primary)

        best_url_by_slot = {
            int(row.get("slot_index", -1)): str(row.get("url_name", ""))
            for row in best_rows
        }
        signature = tuple(
            (
                int(row.get("slot_index", -1)),
                str(row.get("url_name", "")),
                int(row.get("match_score", 0) or 0),
                int(row.get("scan_count", 0) or 0),
                best_url_by_slot.get(int(row.get("slot_index", -1)), ""),
                tuple(
                    (
                        str(alt.get("url_name", "")),
                        int(alt.get("match_score", 0) or 0),
                        int(alt.get("scan_count", 0) or 0),
                    )
                    for alt in row.get("alternates", [])
                ),
            )
            for row in overlay_rows
        )
        changed = signature != self._last_reward_signature
        return overlay_rows, best_rows, signature, changed

    def _poll_reward_selection(self, scanner, rows: list[dict], frame=None):
        selection = scanner.selection_status(frame=frame, screen_confirmed=True)
        # A transition after item OCR is not a new, ambiguous selection. Keep
        # the latest confirmed pick until the monitor finishes this session.
        if selection.screen_present is not True:
            return
        self._update_pending_reward_from_rows(selection.slot_index, rows)

    def _confirm_final_reward_selection(self, scanner, rows: list[dict], frame):
        # Read names on the last frame validated by this session's capture
        # filter. The live heading may already be gone; it is not selection
        # evidence. Never substitute an earlier pending pick for unreadable names.
        if frame is None:
            self._update_pending_reward_from_rows(None, [])
            return
        print("[Reward] Confirming final selection from buffered reward frame.")
        selection = scanner.selection_status(frame=frame, screen_confirmed=True)
        self._update_pending_reward_from_rows(
            selection.slot_index if selection.screen_present is True else None, rows,
        )

    def _update_pending_reward_from_rows(self, selected_slot: int | None, rows: list[dict]):
        if selected_slot is None:
            if self._pending_reward_pick is not None:
                print("[Reward] Current selection is unconfirmed; cleared the earlier pending reward.")
            self._pending_reward_pick = None
            self._pending_reward_slot = None
            return
        selected = next(
            (row for row in rows if row.get("slot_index") == selected_slot),
            None,
        )
        if selected is not None:
            if (
                self._pending_reward_pick is None
                or self._pending_reward_pick.get("slot_index") != selected_slot
                or self._pending_reward_pick.get("url_name") != selected.get("url_name")
            ):
                self._pending_reward_pick = dict(selected)
                print(
                    "[Reward] Pending selected reward updated: "
                    f"{selected['display_name']}"
                )
                self._prepare_reward_review_async(selected)
            self._pending_reward_slot = selected_slot
            return

        if selected_slot not in {row.get("slot_index") for row in rows}:
            self._pending_reward_pick = None
            self._pending_reward_slot = selected_slot
            print("[Reward] Selected reward is ignored/unmarketable; cleared pending reward.")

    def _show_reward_prices_async(self, items, signature: tuple[str, ...]):
        rows = self._reward_rows_with_prices(items)
        self._show_reward_rows_with_prices(rows, signature)

    def _show_reward_rows_with_prices_async(
        self,
        overlay_rows: list[dict],
        best_rows: list[dict],
        signature: tuple,
    ):
        main_started = time.perf_counter()
        rows = [
            self._reward_row_with_price(row, include_alternates=False)
            for row in overlay_rows
        ]
        print(
            f"[Reward] Main reward prices loaded for {len(rows)} items "
            f"in {time.perf_counter() - main_started:.3f}s"
        )
        self._show_reward_rows_with_prices(rows, signature, best_rows=best_rows)

        alternate_started = time.perf_counter()
        alternate_count = 0
        for row in rows:
            alternates = row.get("alternates") or []
            alternate_count += len(alternates)
            row["alternates"] = self._reward_alternates_with_prices(alternates)
        print(
            f"[Reward] Alternate prices loaded for {alternate_count} reads "
            f"in {time.perf_counter() - alternate_started:.3f}s"
        )
        self._show_reward_rows_with_prices(rows, signature, best_rows=best_rows)

    def _show_reward_rows_with_prices(
        self,
        rows: list[dict],
        signature: tuple,
        best_rows: list[dict] | None = None,
    ):
        if self._last_reward_price_signature != signature:
            return
        duration_ms = self._reward_overlay_remaining_ms()
        if duration_ms <= 0:
            return
        # Only the monitor owns item identities. A delayed price response must
        # not restore an older OCR snapshot used for final selection tracking.
        pending_url = self._pending_reward_pick.get("url_name") if self._pending_reward_pick else None
        if pending_url:
            pending_row = next((row for row in rows if row.get("url_name") == pending_url), None)
            if pending_row and pending_row.get("save_for_ducats"):
                reason = str(pending_row.get("ducat_reason") or "Selected reward saved for ducats.")
                with self._reward_review_lock:
                    self._reward_review_skipped[pending_url] = reason
        self.ui.show_reward_prices(
            rows,
            duration_ms=duration_ms,
        )

    def _show_reward_placeholders(self, rows: list[dict], signature: tuple):
        """Show recognized reward names immediately while market prices load."""
        if self._last_reward_signature != signature:
            return
        duration_ms = self._reward_overlay_remaining_ms()
        if duration_ms <= 0:
            return
        placeholders = []
        for row in rows:
            placeholder = dict(row)
            placeholder.update({
                "price": None,
                "save_for_ducats": False,
                "ducats": None,
                "ducat_bulk_value": None,
                "ducat_reason": "",
                "alternates": [
                    dict(alternate, price=None)
                    for alternate in row.get("alternates", []) or []
                ],
            })
            placeholders.append(placeholder)
        self.ui.show_reward_prices(
            placeholders,
            duration_ms=duration_ms,
        )

    def _reward_overlay_remaining_ms(self) -> int:
        """Keep live reward tags visible until one second before the scan window ends."""
        started_at = self._reward_screen_started_at
        if started_at is None:
            return int(getattr(config, "REWARD_PRICE_OVERLAY_DURATION_MS", 8000))
        analysis_window = float(getattr(config, "REWARD_ANALYSIS_WINDOW_SECONDS", 15.0))
        deadline = started_at + max(0.0, analysis_window - 1.0)
        return max(0, int((deadline - time.monotonic()) * 1000))

    def _reward_to_inventory_item(self, reward: dict) -> InventoryItem:
        item = InventoryItem(
            display_name=reward["display_name"],
            url_name=reward["url_name"],
            quantity=1,
            match_score=int(reward.get("match_score", 100) or 100),
            raw_ocr=str(reward.get("display_name", "")),
            grid_position=(0, 0),
        )
        item._from_reward = True
        return item

    def _prepare_reward_review_async(self, reward: dict):
        url_name = reward.get("url_name")
        if not url_name or self.api is None:
            return
        with self._reward_review_lock:
            if url_name in self._reward_review_cache or url_name in self._reward_review_inflight:
                return
            self._reward_review_inflight.add(url_name)
        threading.Thread(
            target=self._prepare_reward_review_cache,
            args=(dict(reward),),
            daemon=True,
        ).start()

    def _prepare_reward_review_cache(self, reward: dict):
        url_name = reward["url_name"]
        try:
            item = self._reward_to_inventory_item(reward)
            price_info = self.api.get_price_info(item.url_name)
            price_map = {item.url_name: price_info}
            self.api.ensure_item_id_manifest()
            item_id = self.api.get_item_id(item.url_name)
            existing_listing_map = self.api.get_existing_sell_listing_map()
            if item_id:
                item._item_id = item_id
                existing = existing_listing_map.get(item_id)
                if existing is not None:
                    existing.item_url_name = item.url_name
                    existing_listing_map[item.url_name] = existing

            existing = self._find_existing_listing_for_item(item, existing_listing_map)
            if existing is not None:
                item.quantity = max(1, int(existing.quantity) + 1)
                with self._reward_review_lock:
                    self._reward_review_cache[url_name] = (item, price_map, existing_listing_map)
                print(f"[Reward] Existing listing prepared for quantity merge: {item.display_name}")
                return

            should_save, reason, _ducats, _bulk_value = self._ducat_save_decision(
                item.url_name,
                item.display_name,
                price_info,
            )
            if should_save:
                with self._reward_review_lock:
                    self._reward_review_skipped[url_name] = reason
                print(f"[Reward] {reason}")
                return
            with self._reward_review_lock:
                self._reward_review_cache[url_name] = (item, price_map, existing_listing_map)
            print(f"[Reward] Listing review prepared: {item.display_name}")
        except Exception as e:
            print(f"[Reward] Listing review prep failed for {reward.get('display_name', url_name)}: {e}")
        finally:
            with self._reward_review_lock:
                self._reward_review_inflight.discard(url_name)

    def set_reward_monitor_enabled(self, enabled: bool):
        if enabled and self._ocr_reset_pending.is_set():
            return
        if enabled:
            if self._reward_monitor_thread and self._reward_monitor_thread.is_alive():
                return
            self._reward_monitor_stop.clear()
            self._reward_monitor_thread = threading.Thread(target=self._reward_monitor_loop, daemon=True)
            self._reward_monitor_thread.start()
            self.ui.toast("Reward price monitor enabled.", "info", 3000)
            return

        self._reward_monitor_stop.set()
        self.ui.toast("Reward price monitor disabled.", "info", 3000)

    def _reward_monitor_loop(self):
        print("[Reward] Monitor started (buffered nickname confirmation v2).")
        interval = float(getattr(config, "REWARD_MONITOR_POLL_SECONDS", 1.0))
        analysis_window = float(getattr(config, "REWARD_ANALYSIS_WINDOW_SECONDS", 15.0))
        next_scan_at = time.monotonic()
        frame_buffer = None
        while not self._reward_monitor_stop.is_set():
            wait = next_scan_at - time.monotonic()
            if wait > 0:
                if self._reward_monitor_stop.wait(wait):
                    break
            next_scan_at = time.monotonic() + interval
            if self.api is None:
                continue
            if not self._reward_monitor_lock.acquire(blocking=False):
                continue
            try:
                scanner = self._get_reward_scanner()
                # Capture continues independently during heading, item and name
                # OCR. Read its latest live frame without competing for capture.
                capture_started = time.perf_counter()
                frame = frame_buffer.latest_frame() if frame_buffer is not None else None
                if frame is None:
                    frame = scanner.grabber.capture(preprocess=False)
                capture_seconds = time.perf_counter() - capture_started
                if frame is None:
                    continue
                gate_started = time.perf_counter()
                reward_present = scanner.reward_screen_present(frame=frame)
                gate_seconds = time.perf_counter() - gate_started
                if not reward_present:
                    if self._reward_screen_started_at is not None:
                        self._reward_absent_hits += 1
                        if self._reward_absent_hits >= 2:
                            final_frame = None
                            if frame_buffer is not None:
                                frame_buffer.stop()
                                final_frame = frame_buffer.latest_reward_frame()
                                frame_buffer = None
                            if self._last_reward_rows:
                                self._confirm_final_reward_selection(scanner, self._last_reward_rows, final_frame)
                            pending = self._pending_reward_pick
                            selection_unconfirmed = (
                                pending is None
                                and self._pending_reward_slot is None
                                and bool(self._last_reward_rows)
                            )
                            self._pending_reward_pick = None
                            self._pending_reward_slot = None
                            self._reset_reward_session_tracking()
                            if pending is not None:
                                self._queue_reward_pick(pending)
                            elif selection_unconfirmed:
                                print("[Reward] Final selection was unconfirmed; no reward queued.")
                                self.ui.toast(
                                    "Reward selection could not be confirmed; no reward was queued.",
                                    "warning",
                                    6000,
                                )
                    continue

                self._reward_absent_hits = 0
                if self._reward_screen_started_at is None:
                    self._reward_screen_started_at = time.monotonic()
                if frame_buffer is None:
                    new_buffer = RewardFrameBuffer(
                        scanner.grabber.fork, scanner.reward_layout_present, frame,
                    )
                    new_buffer.start()
                    frame_buffer = new_buffer
                print(
                    f"[Reward] Monitor capture={capture_seconds:.3f}s, "
                    f"screen_gate={gate_seconds:.3f}s"
                )
                reward_started_at = self._reward_screen_started_at or self._reward_session_started_at
                analysis_active = (
                    reward_started_at is None
                    or time.monotonic() - reward_started_at < analysis_window
                )
                if analysis_active:
                    result = scanner.scan(
                        use_fallback=True,
                        frame=frame,
                        detect_selected=False,
                    )
                    if self._ocr_reset_pending.is_set():
                        break
                    if result.items:
                        overlay_rows, best_rows, signature, changed = self._merge_reward_scan_result(result)
                        price_signature = tuple(
                            (
                                int(row.get("slot_index", -1)),
                                str(row.get("url_name", "")),
                                tuple(sorted({
                                    str(alternate.get("url_name", ""))
                                    for alternate in row.get("alternates", []) or []
                                    if alternate.get("url_name")
                                })),
                            )
                            for row in overlay_rows
                        )
                        price_changed = price_signature != self._last_reward_price_signature
                        self._last_reward_signature = signature
                        self._last_reward_price_signature = price_signature
                        self._last_reward_rows = best_rows
                        # Confidence and observation counts can change on each
                        # OCR pass. Only redraw placeholders when the actual
                        # reward identities change, or this would overwrite
                        # already-loaded prices with '?'.
                        if price_changed:
                            self._show_reward_placeholders(
                                overlay_rows,
                                signature,
                            )
                            if not self._reward_first_overlay_shown:
                                elapsed = time.monotonic() - (self._reward_screen_started_at or time.monotonic())
                                print(f"[Reward] First reward overlay queued after {elapsed:.3f}s")
                                self._reward_first_overlay_shown = True
                        if price_changed:
                            threading.Thread(
                                target=self._show_reward_rows_with_prices_async,
                                args=(overlay_rows, best_rows, price_signature),
                                daemon=True,
                            ).start()
                        self._poll_reward_selection(
                            scanner, best_rows, frame=frame_buffer.latest_reward_frame(),
                        )
                    elif self._last_reward_rows:
                        self._poll_reward_selection(
                            scanner, self._last_reward_rows, frame=frame_buffer.latest_reward_frame(),
                        )
                    continue

                if self._last_reward_rows:
                    self._poll_reward_selection(scanner, self._last_reward_rows, frame=frame)
            except Exception as e:
                self._update_pending_reward_from_rows(None, [])
                print(f"[Reward] Monitor scan skipped: {e}")
            finally:
                self._reward_monitor_lock.release()
        if frame_buffer is not None:
            frame_buffer.stop()
        self._update_pending_reward_from_rows(None, [])
        self._reset_reward_session_tracking()
        print("[Reward] Monitor stopped.")

    def _prompt_keep_reward(self, reward: dict):
        if self.api is None:
            self.ui.toast("Not authenticated; reward was not listed.", "warning", 5000)
            return
        try:
            url_name = reward["url_name"]
            cached = None
            deadline = time.time() + 1.5
            while time.time() < deadline:
                with self._reward_review_lock:
                    cached = self._reward_review_cache.get(url_name)
                    skipped_reason = self._reward_review_skipped.get(url_name)
                if skipped_reason and getattr(config, "DUCAT_FILTER_ENABLED", True):
                    item = self._reward_to_inventory_item(reward)
                    price_info = self.api.get_price_info(item.url_name)
                    price_map = {item.url_name: price_info}
                    self.api.ensure_item_id_manifest()
                    item_id = self.api.get_item_id(item.url_name)
                    if item_id:
                        item._item_id = item_id
                    existing_listing_map = self.api.get_existing_sell_listing_map()
                    existing = self._find_existing_listing_for_item(item, existing_listing_map)
                    if existing is not None:
                        item.quantity = max(1, int(existing.quantity) + 1)
                        self.ui.alert(
                            f"Reward picked: {reward['display_name']}. Opening listing review.",
                            "success",
                            6000,
                            "success",
                        )
                        confirmed_items = self._show_inventory_review(
                            [item],
                            price_map,
                            existing_listing_map,
                            preselect_single_item=True,
                        )
                        self._post_confirmed_inventory_items(confirmed_items, price_map)
                        return
                    should_save, reason, ducats, bulk_value = self._ducat_save_decision(
                        item.url_name,
                        item.display_name,
                        price_info,
                    )
                    if not should_save:
                        break
                    self.account_store.increment_inventory_item(
                        item.url_name,
                        item.display_name,
                        quantity=1,
                    )
                    self.ui.refresh_dashboard_profile()
                    row = self._saved_ducat_row(item, price_info, ducats, bulk_value, reason or skipped_reason)
                    self.ui.alert(f"Reward saved for ducats: {item.display_name}", "info", 6000, "info")
                    self.ui.show_saved_ducat_items([row], "Reward saved for ducats")
                    return
                if cached is not None or url_name not in self._reward_review_inflight:
                    break
                time.sleep(0.05)

            if cached is not None:
                item, price_map, existing_listing_map = cached
                existing = self._find_existing_listing_for_item(item, existing_listing_map)
                if existing is not None:
                    item.quantity = max(1, int(existing.quantity) + 1)
            else:
                item = self._reward_to_inventory_item(reward)
                price_info = self.api.get_price_info(item.url_name)
                price_map = {item.url_name: price_info}
                self.api.ensure_item_id_manifest()
                item_id = self.api.get_item_id(item.url_name)
                existing_listing_map = self.api.get_existing_sell_listing_map()
                if item_id:
                    item._item_id = item_id
                    existing = existing_listing_map.get(item_id)
                    if existing is not None:
                        existing.item_url_name = item.url_name
                        existing_listing_map[item.url_name] = existing
                existing = self._find_existing_listing_for_item(item, existing_listing_map)
                if existing is not None:
                    item.quantity = max(1, int(existing.quantity) + 1)
                else:
                    should_save, reason, _ducats, _bulk_value = self._ducat_save_decision(
                        item.url_name,
                        item.display_name,
                        price_info,
                    )
                    if should_save:
                        self.account_store.increment_inventory_item(
                            item.url_name,
                            item.display_name,
                            quantity=1,
                        )
                        self.ui.refresh_dashboard_profile()
                        with self._reward_review_lock:
                            self._reward_review_skipped[url_name] = reason
                        row = self._saved_ducat_row(item, price_info, _ducats, _bulk_value, reason)
                        self.ui.alert(f"Reward saved for ducats: {item.display_name}", "info", 6000, "info")
                        self.ui.show_saved_ducat_items([row], "Reward saved for ducats")
                        return

            self.ui.alert(
                f"Reward picked: {reward['display_name']}. Opening listing review.",
                "success",
                6000,
                "success",
            )
            # The selected reward is already the user's kept item. Record it
            # even if they choose not to create or update a market listing.
            self.account_store.increment_inventory_item(
                item.url_name,
                item.display_name,
                quantity=1,
            )
            self.ui.refresh_dashboard_profile()
            confirmed_items = self._show_inventory_review(
                [item],
                price_map,
                existing_listing_map,
                preselect_single_item=True,
            )
            if confirmed_items:
                self._post_confirmed_inventory_items(confirmed_items, price_map)
        except Exception as e:
            print(f"[Reward] Listing review failed: {e}")
            self.ui.toast(f"Reward listing review failed: {e}", "error", 6000)

    def _queue_reward_pick(self, reward: dict):
        """Record a chosen fissure reward without opening a per-item dialog."""
        url_name = str(reward.get("url_name", "")).strip()
        if not url_name:
            return
        display_name = str(reward.get("display_name", url_name)).strip()
        with self._reward_queue_lock:
            self._queued_reward_picks = self.account_store.queue_reward(url_name, display_name)
            total = sum(int(row.get("quantity", 0)) for row in self._queued_reward_picks.values())
        self.ui.set_reward_queue_count(total)
        self.ui.refresh_dashboard_profile()
        print(f"[Reward] Queued picked reward: {display_name} (queue total {total})")

    def review_queued_rewards(self):
        """Open one batch review for rewards collected during a fissure run."""
        if self.api is None:
            self.ui.toast("Not authenticated; queued rewards remain saved locally.", "warning", 5000)
            return
        if not self._reward_queue_review_lock.acquire(blocking=False):
            self.ui.toast("Reward queue review is already open.", "info", 3000)
            return
        with self._reward_queue_lock:
            # Read the persisted store as the source of truth on every open.
            # The dashboard count can outlive or become detached from this
            # convenience cache after background queue operations.
            self._queued_reward_picks = self.account_store.get_reward_queue()
            snapshot = {url: dict(row) for url, row in self._queued_reward_picks.items()}
            total = sum(int(row.get("quantity", 0)) for row in snapshot.values())
        self.ui.set_reward_queue_count(total)
        if not snapshot:
            self._reward_queue_review_lock.release()
            print(f"[Reward] Review requested but {self.account_store.path} has no queued rewards.")
            self.ui.toast("No queued rewards to review.", "info", 3000)
            return

        def worker():
            try:
                items = []
                price_map = {}
                existing_listing_map = self.api.get_existing_sell_listing_map()
                self.api.ensure_item_id_manifest()
                saved_for_ducats = []
                for url_name, row in snapshot.items():
                    item = self._reward_to_inventory_item(row)
                    queued_quantity = max(1, int(row.get("quantity", 1)))
                    item.quantity = queued_quantity
                    item._queued_reward_quantity = queued_quantity
                    item_id = self.api.get_item_id(url_name)
                    if item_id:
                        item._item_id = item_id
                        existing = existing_listing_map.get(item_id)
                        if existing is not None:
                            existing.item_url_name = url_name
                            existing_listing_map[url_name] = existing
                    price_info = self.api.get_price_info(url_name)
                    price_map[url_name] = price_info
                    should_save, reason, ducats, bulk_value = self._ducat_save_decision(
                        url_name, item.display_name, price_info
                    )
                    if should_save:
                        item._saved_for_ducats = True
                        saved_for_ducats.append(
                            self._saved_ducat_row(item, price_info, ducats, bulk_value, reason)
                        )
                    else:
                        existing = self._find_existing_listing_for_item(item, existing_listing_map)
                        item._reward_existing_quantity = max(0, int(existing.quantity)) if existing else 0
                        if existing is not None:
                            item.quantity += max(0, int(existing.quantity))
                    items.append(item)

                confirmed_items, removed_rewards = self._show_inventory_review(
                    items,
                    price_map,
                    existing_listing_map,
                    scan_title="Fissure reward batch",
                    saved_ducat_items=saved_for_ducats,
                    queued_reward_rows=list(snapshot.values()),
                )
                applied_urls = (
                    self._post_confirmed_inventory_items(confirmed_items, price_map)
                    if confirmed_items else set()
                )
                handled_rewards = {}
                for item in confirmed_items:
                    if item.url_name not in applied_urls:
                        continue
                    row = snapshot[item.url_name]
                    consumed = min(int(row["quantity"]), max(0, int(item.quantity) - int(getattr(item, "_reward_existing_quantity", 0))))
                    if consumed:
                        handled_rewards[item.url_name] = {**row, "quantity": consumed}
                listed_count = sum(int(row["quantity"]) for row in handled_rewards.values())
                # Applying a listing consumes only the queued reward quantity
                # used for that successful WFM write. Explicit local removals
                # consume the same snapshot quantity, without double-counting.
                handled_rewards.update(removed_rewards)
                if handled_rewards:
                    with self._reward_queue_lock:
                        self._queued_reward_picks = self.account_store.remove_queued_rewards(handled_rewards)
                        total = sum(int(row.get("quantity", 0)) for row in self._queued_reward_picks.values())
                    self.ui.set_reward_queue_count(total)
                    explicitly_removed_count = sum(
                        int(row.get("quantity", 0))
                        for url_name, row in removed_rewards.items()
                        if url_name not in applied_urls
                    )
                    print(
                        f"[Reward] Consumed {listed_count} queued reward(s) through successful WFM actions; "
                        f"user explicitly removed {explicitly_removed_count} reward(s)."
                    )
                else:
                    print("[Reward] No listing action succeeded and no rewards were removed; queue remains saved.")
            except Exception as e:
                print(f"[Reward] Batch review failed: {e}")
                self.ui.toast(f"Reward batch review failed: {e}", "error", 6000)
            finally:
                self._reward_queue_review_lock.release()

        threading.Thread(target=worker, name="reward-batch-review", daemon=True).start()

    def _find_existing_listing_for_item(self, item: InventoryItem, existing_listing_map=None):
        if self.api is None:
            return None
        if existing_listing_map is None:
            existing_listing_map = self.api.get_existing_sell_listing_map()
        item_id = getattr(item, "_item_id", "") or self.api.get_item_id(item.url_name) or ""
        if item_id:
            item._item_id = item_id

        existing = existing_listing_map.get(item.url_name)
        if existing is None and item_id:
            existing = existing_listing_map.get(item_id)
        if existing is not None:
            existing.item_url_name = item.url_name
            return existing

        for candidate in existing_listing_map.values():
            if not getattr(candidate, "order_id", ""):
                continue
            if candidate.item_url_name == item.url_name or (item_id and candidate.item_id == item_id):
                candidate.item_url_name = item.url_name
                return candidate
        return None

    def _merge_reward_into_existing_listing(self, item: InventoryItem, existing_listing_map=None) -> bool:
        existing = self._find_existing_listing_for_item(item, existing_listing_map)
        if existing is None:
            return False


        new_quantity = max(1, int(existing.quantity) + 1)
        self.api.update_order_price(
            existing.order_id,
            existing.platinum,
            quantity=new_quantity,
            visible=existing.visible,
        )
        item_id = getattr(item, "_item_id", "") or existing.item_id
        self.account_store.increment_inventory_item(
            item.url_name,
            item.display_name,
            quantity=1,
            item_id=item_id,
        )
        self.account_store.upsert_listing(
            item.url_name,
            item.display_name,
            existing.order_id,
            item_id=item_id,
            platinum=existing.platinum,
            quantity=new_quantity,
            visible=existing.visible,
        )
        print(
            f"[Reward] Merged picked reward into existing listing: "
            f"{item.display_name} {existing.quantity} -> {new_quantity}"
        )
        self.ui.alert(
            f"Reward merged into existing listing: {item.display_name} x{new_quantity}",
            "success",
            6000,
            "success",
        )
        return True

    # ── Local Account Manager ─────────────────

    def open_account_manager(self):
        try:
            self.ui.show_dashboard(
                **self._dashboard_callbacks(),
            )
        except Exception:
            print("[Main] ERROR opening account manager:")
            traceback.print_exc()

    def _dashboard_callbacks(self) -> dict:
        return {
            "store": self.account_store,
            "on_refresh": self._refresh_account_store_from_wfm,
            "on_update_prices": self._bulk_update_listing_prices,
            "on_delete_all_listings": self._delete_all_wfm_listings,
            "on_delete_listings": self._delete_wfm_listings,
            "on_delete_listing": self._delete_wfm_listing,
            "on_price_preview": self._preview_listing_prices,
            "on_buyer_search": self._search_market_buyers,
            "on_save_token": self._save_login_token,
            "on_save_settings_token": self._save_settings_token,
            "on_inventory_scan": lambda: self._start_hotkey_thread(
                "Inventory scan (6x3) button", self.scan_inventory
            ),
            "on_inventory_bottom_scan": lambda: self._start_hotkey_thread(
                "Inventory scan (bottom 6) button", self.scan_inventory_bottom
            ),
            "on_trade_monitor": lambda: self._start_hotkey_thread(
                "Trade monitor button", self.monitor_trade_and_update_listings
            ),
            "on_reward_scan": lambda: self._start_hotkey_thread(
                "Reward scan button", self.scan_reward_prices
            ),
            "on_ocr_warmup": self.toggle_ocr_engines,
            "on_reward_toggle": self.set_reward_monitor_enabled,
            "on_trade_scan_toggle": self.set_trade_scan_enabled,
            "on_review_rewards": self.review_queued_rewards,
            "on_hotkeys_changed": self._register_hotkeys,
            "on_ocr_changed": self._reset_ocr_scanners,
        }

    def _reset_ocr_scanners(self):
        if self._ocr_reset_pending.is_set():
            return
        self._ocr_reset_pending.set()
        self._reward_monitor_stop.set()
        self._trade_live_scan_stop.set()
        if self._trade_cancel_event is not None:
            self._trade_cancel_event.set()
        with self._inventory_pending_lock:
            self._inventory_pending.clear()
        dashboard = self.ui.dashboard
        if dashboard is not None:
            dashboard.reward_check.setChecked(False)
            dashboard.trade_scan_check.setChecked(False)
        # Release scan workers waiting for UI decisions, without closing Settings.
        self._close_scan_windows()
        self.ui.toast("Stopping scans and unloading OCR engines…", "info", 3500)
        threading.Thread(target=self._finish_ocr_reset, name="Sidro: OCR settings reset", daemon=True).start()

    def _close_scan_windows(self):
        from PySide6.QtWidgets import QApplication
        for window in QApplication.topLevelWidgets():
            if type(window).__name__ in (
                "InventoryReviewDialog", "ConfirmationDialog", "ScreenSelectionOverlay",
            ):
                window.close()

    def _finish_ocr_reset(self):
        # Native OCR/capture calls cannot safely be killed in a Python thread.
        # Let in-flight calls return before releasing their devices or workers.
        try:
            while self._ocr_use_is_active():
                self.ui.invoke(self._close_scan_windows)
                if self._stop_event.wait(0.1):
                    return
            with self._ocr_warmup_lock:
                self._dispose_ocr_scanners()
                from pillar2_ocr import unload_ocr_backends
                unload_ocr_backends()
            self._schedule_ocr_button_refresh()
            self.ui.toast("Scans stopped. OCR engines unloaded; next scan uses the new settings.", "info", 5000)
        finally:
            self._ocr_reset_pending.clear()

    def _dispose_ocr_scanners(self):
        print("[Main] OCR settings changed. Recreating scanner instances on next use.")
        for attr in (
            "ocr",
            "inventory_scanner",
            "inventory_bottom_scanner",
            "trade_scanner",
            "reward_scanner",
        ):
            scanner = getattr(self, attr, None)
            if scanner is None:
                continue
            try:
                if hasattr(scanner, "close"):
                    scanner.close()
                elif hasattr(scanner, "grabber") and scanner.grabber is not None:
                    scanner.grabber.close()
            except Exception as e:
                print(f"[Main] Scanner cleanup skipped for {attr}: {e}")
            setattr(self, attr, None)

    def _refresh_account_store_from_wfm(self) -> str:
        if self.api is None:
            return "Not authenticated."
        self.api.ensure_item_id_manifest()
        existing_map = self.api.get_existing_sell_listing_map()
        self.account_store.replace_listings(existing_map)
        return f"Refreshed {len({v.order_id for v in existing_map.values() if v.order_id})} WFM sell listings."

    def _save_login_token(self, token: str) -> str:
        if not token.strip():
            return "Paste a JWT token first."
        if not validate_wfm_token(token):
            return "Token was rejected by warframe.market."
        save_wfm_token(token)
        self.api = WarframeMarketAPI(token=config.WFM_TOKEN)
        message = self._refresh_account_store_from_wfm()
        return f"Token saved and authenticated. {message}"

    def _save_settings_token(self, token: str) -> str:
        """Validate settings-provided tokens and replace the active API client."""
        token = str(token or "").strip()
        if not token:
            return ""
        if not validate_wfm_token(token):
            raise ValueError("Token was rejected by warframe.market; the saved token was not changed.")
        save_wfm_token(token)
        self.api = WarframeMarketAPI(token=config.WFM_TOKEN)
        return "Token authenticated."

    def _preview_listing_prices(self, url_names: list[str], strategy: str) -> dict[str, dict[str, int]]:
        if self.api is None:
            return {}

        results: dict[str, dict[str, int]] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=getattr(config, "PRICE_LOOKUP_WORKERS", 4)
        ) as pool:
            future_map = {
                pool.submit(self.api.get_price_info, url_name, strategy): url_name
                for url_name in url_names
            }
            for future in concurrent.futures.as_completed(future_map):
                url_name = future_map[future]
                try:
                    price_info = future.result()
                    results[url_name] = {
                        "lowest": price_info.lowest_sell_price,
                        "price": price_info.suggested_listing_price,
                        "most_common": price_info.suggested_listing_price,
                        "highest": price_info.highest_sell_price,
                    }
                except Exception as e:
                    print(f"[Bulk] Price preview failed for {url_name}: {e}")
        return results

    def _resolve_market_query(self, query: str) -> tuple[str, str, int, object | None]:
        query = str(query or "").strip()
        if not query:
            raise WFMApiError("Enter an item name first.")

        items_cache, matcher = self._get_item_search_matcher()
        query_lower = query.casefold()
        query_slug = query_lower.replace(" ", "_").replace("-", "_")

        display_name = ""
        url_name = ""
        match_score = 100
        matched = None
        for candidate in items_cache.names:
            candidate_url = items_cache.url_name(candidate) or ""
            if candidate.casefold() == query_lower or candidate_url.casefold() == query_slug:
                display_name = candidate
                url_name = candidate_url
                break

        if not url_name:
            matched = matcher.match(normalize_ocr_text(query))
            if matched is None or not matched.url_name:
                raise WFMApiError(f"No market item matched '{query}'.")
            display_name = matched.display_name
            url_name = matched.url_name
            match_score = int(matched.match_score)

        return display_name, url_name, match_score, matched

    def _search_market_buyers(self, url_name: str, display_name: str = "") -> dict:
        if self.api is None:
            raise WFMApiError("Not authenticated.")

        url_name = str(url_name or "").strip()
        display_name = str(display_name or "").strip()
        if not url_name:
            display_name, url_name, _match_score, _matched = self._resolve_market_query(display_name)
        elif not display_name:
            display_name = self.account_store.display_name(url_name) or url_name.replace("_", " ").title()

        all_orders = self.api.get_orders(url_name)
        active_buys = [
            order for order in all_orders
            if (order.get("type") or order.get("order_type")) == "buy"
            and order.get("visible", True)
            and order.get("user", {}).get("status") in ("ingame", "online")
        ]
        if not active_buys:
            raise WFMApiError(f"No active buyers found for '{display_name}'.")

        status_rank = {"ingame": 0, "online": 1}
        active_buys.sort(
            key=lambda order: (
                status_rank.get(order.get("user", {}).get("status"), 9),
                -int(order.get("platinum", 0) or 0),
                -int(order.get("quantity", 1) or 1),
            )
        )

        top_orders = []
        for order in active_buys[:10]:
            username = order.get("user", {}).get("ingameName") or order.get("user", {}).get("ingame_name", "")
            platinum = int(order.get("platinum", 0) or 0)
            quantity = int(order.get("quantity", 1) or 1)
            status = str(order.get("user", {}).get("status", ""))
            whisper = (
                f'/w {username} Hi! I want to sell: "{display_name}" '
                f"for {platinum} platinum."
            )
            top_orders.append({
                "buyer": username,
                "price": platinum,
                "quantity": quantity,
                "status": status,
                "whisper": whisper,
            })

        best = top_orders[0]
        return {
            "display_name": display_name,
            "url_name": url_name,
            "buyer": best["buyer"],
            "price": best["price"],
            "quantity": best["quantity"],
            "status": best["status"],
            "whisper": best["whisper"],
            "top_orders": top_orders,
        }

    def _bulk_update_listing_prices(self, item_updates: dict[str, dict[str, object]], strategy: str) -> str:
        if self.api is None:
            return "Not authenticated."
        if not config.ENABLE_LIVE_LISTING:
            return "Live listing changes are disabled. Enable ENABLE_LIVE_LISTING in config.py to update orders."

        self.api.ensure_item_id_manifest()
        existing_map = self.api.get_existing_sell_listing_map()
        self.account_store.replace_listings(existing_map)

        by_url = {
            listing.item_url_name: listing
            for listing in existing_map.values()
            if listing.order_id and listing.item_url_name in item_updates
        }

        updated, failed = 0, 0
        for url_name, requested in item_updates.items():
            existing = by_url.get(url_name)
            if existing is None:
                print(f"[Bulk] Skipping {url_name}: no active listing.")
                failed += 1
                continue

            try:
                new_price = max(config.MIN_LISTING_PRICE, int(requested["price"]))
                new_quantity = max(1, int(requested["quantity"]))
                new_visible = bool(requested.get("visible", existing.visible))
                self.api.update_order_price(
                    existing.order_id,
                    new_price,
                    quantity=new_quantity,
                    visible=new_visible,
                )
                self.account_store.upsert_listing(
                    url_name,
                    self.account_store.display_name(url_name),
                    existing.order_id,
                    item_id=existing.item_id,
                    platinum=new_price,
                    quantity=new_quantity,
                    visible=new_visible,
                )
                visibility_text = "visible" if new_visible else "hidden"
                print(
                    f"[Bulk] Updated {url_name}: "
                    f"{existing.platinum}p -> {new_price}p, {visibility_text}"
                )
                updated += 1
            except Exception as e:
                print(f"[Bulk] Failed {url_name}: {e}")
                failed += 1

            delay = random.uniform(
                config.LISTING_REQUEST_DELAY_MIN_SECONDS,
                config.LISTING_REQUEST_DELAY_MAX_SECONDS,
            )
            print(f"[Bulk] API pacing delay: {delay:.3f}s")
            time.sleep(delay)

        return f"Bulk update complete: {updated} updated, {failed} skipped/failed."

    def _delete_all_wfm_listings(self) -> str:
        if self.api is None:
            return "Not authenticated."
        if not config.ENABLE_LIVE_LISTING:
            return "Live listing changes are disabled. Enable ENABLE_LIVE_LISTING in config.py to delete orders."

        self.api.ensure_item_id_manifest()
        existing_map = self.api.get_existing_sell_listing_map()
        unique: dict[str, object] = {}
        for listing in existing_map.values():
            if listing.order_id:
                unique[listing.order_id] = listing

        if not unique:
            self.account_store.replace_listings({})
            return "No WFM sell listings to delete."

        deleted, failed = 0, 0
        for index, listing in enumerate(unique.values()):
            try:
                if self.api.delete_order(listing.order_id):
                    self.account_store.delete_listing(listing.item_url_name)
                    deleted += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"[Bulk] Failed deleting {listing.item_url_name}: {e}")
                failed += 1

            delay = random.uniform(
                config.LISTING_REQUEST_DELAY_MIN_SECONDS,
                config.LISTING_REQUEST_DELAY_MAX_SECONDS,
            )
            print(f"[Bulk] Delete pacing delay: {delay:.3f}s")
            time.sleep(delay)

        try:
            refreshed = self.api.get_existing_sell_listing_map()
            self.account_store.replace_listings(refreshed)
        except Exception as e:
            print(f"[Bulk] Listing refresh after delete failed: {e}")

        return f"Delete all listings complete: {deleted} deleted, {failed} skipped/failed."

    def _delete_wfm_listing(self, url_name: str) -> str:
        return self._delete_wfm_listings([url_name])

    def _delete_wfm_listings(self, url_names: list[str]) -> str:
        if self.api is None:
            return "Not authenticated."
        if not config.ENABLE_LIVE_LISTING:
            return "Live listing changes are disabled. Enable ENABLE_LIVE_LISTING in config.py to delete orders."

        requested = []
        seen = set()
        for url_name in url_names:
            url_name = str(url_name or "").strip()
            if not url_name or url_name in seen:
                continue
            seen.add(url_name)
            requested.append(url_name)
        if not requested:
            return "No listings selected."

        self.api.ensure_item_id_manifest()
        existing_map = self.api.get_existing_sell_listing_map()
        self.account_store.replace_listings(existing_map)

        by_url = {
            listing.item_url_name: listing
            for listing in existing_map.values()
            if listing.order_id
        }

        deleted, missing, failed = 0, 0, 0
        for url_name in requested:
            existing = by_url.get(url_name)
            if existing is None:
                self.account_store.delete_listing(url_name)
                missing += 1
                continue

            try:
                if self.api.delete_order(existing.order_id):
                    self.account_store.delete_listing(url_name)
                    deleted += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"[Bulk] Failed deleting {url_name}: {e}")
                failed += 1

            delay = random.uniform(
                config.LISTING_REQUEST_DELAY_MIN_SECONDS,
                config.LISTING_REQUEST_DELAY_MAX_SECONDS,
            )
            print(f"[Bulk] Delete selected pacing delay: {delay:.3f}s")
            time.sleep(delay)

        try:
            refreshed = self.api.get_existing_sell_listing_map()
            self.account_store.replace_listings(refreshed)
        except Exception as e:
            print(f"[Bulk] Listing refresh after selected delete failed: {e}")

        return (
            f"Delete selected complete: {deleted} deleted, "
            f"{missing} already missing, {failed} skipped/failed."
        )

    def _show_inventory_review(
        self,
        items,
        price_map,
        existing_listing_map=None,
        scan_title: str = "Inventory scan",
        saved_ducat_items: list[dict] | None = None,
        failed_positions: list[tuple[int, int]] | None = None,
        preselect_single_item: bool = False,
        queued_reward_rows: list[dict] | None = None,
    ):
        print("[Main] Creating inventory review window...")
        try:
            selected = self.ui.show_inventory_review(
                items,
                price_map,
                existing_listing_map or {},
                scan_title=scan_title,
                saved_ducat_items=saved_ducat_items or [],
                failed_positions=failed_positions or [],
                preselect_single_item=preselect_single_item,
                queued_reward_rows=queued_reward_rows,
            )
            print("[Main] Inventory review window closed.")
            return selected
        except Exception:
            print("[Main] ERROR creating inventory review window:")
            traceback.print_exc()
            return []

    def _post_listing(self, display_name, url_name, platinum, quantity=1, existing_listing=None):
        if not config.ENABLE_LIVE_LISTING:
            print(f"[Main] LIVE LISTING DISABLED. Plan: {display_name} @ {platinum}p")
            self.ui.toast("Live listing changes are disabled. Enable ENABLE_LIVE_LISTING in config.py to post.", "warning", 8000)
            return
        try:
            quantity = max(1, int(quantity))
            if existing_listing is not None:
                self.api.update_order_price(
                    existing_listing.order_id,
                    platinum,
                    quantity=quantity,
                    visible=existing_listing.visible,
                )
                item_id = existing_listing.item_id or self.api.get_item_id(url_name) or ""
                order_id = existing_listing.order_id
                result_message = f"Updated '{display_name}' to {platinum}p x{quantity}"
            else:
                order = self.api.list_item(
                    url_name,
                    platinum,
                    quantity=quantity,
                )
                item_id = self.api.get_item_id(url_name) or ""
                order_id = order.get("id") or order.get("_id") or ""
                result_message = f"Listed {quantity}x '{display_name}' for {platinum}p each"
            self.account_store.upsert_listing(
                url_name,
                display_name,
                order_id,
                item_id=item_id,
                platinum=platinum,
                quantity=quantity,
                visible=existing_listing.visible if existing_listing is not None else True,
            )
            self.ui.refresh_dashboard_profile()
            self.ui.toast(f"✅ {result_message}", "success", 5000)
        except (WFMAuthError, WFMApiError) as e:
            self.ui.toast(f"❌ {e}", "error")

    def _start_hotkey_thread(self, label: str, target):
        if self._ocr_reset_pending.is_set():
            return
        def runner():
            try:
                target()
            except Exception as e:
                print(f"[Main] ERROR in {label}:")
                traceback.print_exc()
                self.ui.toast(f"{label} failed: {e}", "error", 6000)
            finally:
                with self._worker_threads_lock:
                    self._worker_threads.discard(threading.current_thread())

        thread = threading.Thread(target=runner, name=f"Sidro: {label}", daemon=True)
        with self._worker_threads_lock:
            if self._stop_event.is_set():
                return
            self._worker_threads.add(thread)
        thread.start()

    def _register_hotkeys(self):
        keyboard.unhook_all()
        keyboard.add_hotkey(config.HOTKEY_SCAN,
                            lambda: self._start_hotkey_thread("Single scan hotkey", self.scan_and_list))
        keyboard.add_hotkey(config.HOTKEY_INVENTORY_SCAN,
                            lambda: self._start_hotkey_thread("Inventory scan (6x3) hotkey", self.scan_inventory))
        keyboard.add_hotkey(config.HOTKEY_INVENTORY_BOTTOM_SCAN,
                            lambda: self._start_hotkey_thread(
                                "Inventory scan (bottom 6) hotkey",
                                self.scan_inventory_bottom,
                            ))
        keyboard.add_hotkey(config.HOTKEY_TRADE_MONITOR,
                            lambda: self._start_hotkey_thread("Trade monitor hotkey", self.monitor_trade_and_update_listings))
        keyboard.add_hotkey(
            config.HOTKEY_ACCOUNT_UI,
            self.open_account_manager,
        )
        keyboard.add_hotkey(config.HOTKEY_REWARD_SCAN,
                            lambda: self._start_hotkey_thread("Reward scan hotkey", self.scan_reward_prices))
        keyboard.add_hotkey(
            config.HOTKEY_OCR_WARMUP,
            lambda: self._start_hotkey_thread("OCR load toggle hotkey", self.toggle_ocr_engines),
        )
        keyboard.add_hotkey(config.HOTKEY_QUIT,
                            self.stop)
        dashboard = getattr(self.ui, "dashboard", None)
        if dashboard is not None and hasattr(dashboard, "ocr_warmup_button"):
            dashboard.ocr_warmup_button.setToolTip(
                "Load the OCR engines selected in Settings, or unload them to release memory "
                f"({config.HOTKEY_OCR_WARMUP})"
            )
        print(f"[Main]  {config.HOTKEY_SCAN:<22} — select one item (snip)")
        print(f"[Main]  {config.HOTKEY_INVENTORY_SCAN:<22} — inventory scan (6x3)")
        print(f"[Main]  {config.HOTKEY_INVENTORY_BOTTOM_SCAN:<22} — inventory scan (bottom 6)")
        print(f"[Main]  {config.HOTKEY_TRADE_MONITOR:<22} — trade completion monitor")
        print(f"[Main]  {config.HOTKEY_ACCOUNT_UI:<22} — account listing manager")
        print(f"[Main]  {config.HOTKEY_REWARD_SCAN:<22} — reward price overlay")
        print(f"[Main]  {config.HOTKEY_OCR_WARMUP:<22} — load / unload OCR engines")
        print(f"[Main]  {config.HOTKEY_QUIT:<22} — quit\n")

    def run(self):
        self._running = True
        self.ui.app.aboutToQuit.connect(self.stop)
        self._register_hotkeys()
        if getattr(config, "PRELOAD_OCR_ON_START", True):
            threading.Thread(target=self._warm_ocr_engine, daemon=True).start()
        if self.api is not None:
            threading.Thread(target=self._refresh_account_store_on_start, daemon=True).start()
        print("[Main] ✅ Ready. Inventory:", config.HOTKEY_INVENTORY_SCAN,
              "| Bottom 6:", config.HOTKEY_INVENTORY_BOTTOM_SCAN,
              "| Trade monitor:", config.HOTKEY_TRADE_MONITOR,
              "| Account UI:", config.HOTKEY_ACCOUNT_UI,
              "| Reward prices:", config.HOTKEY_REWARD_SCAN, "\n")
        config.REWARD_PRICE_CHECK_ENABLED = False
        self.ui.show_dashboard(**self._dashboard_callbacks())
        from update_ui import UpdateController
        self.update_controller = UpdateController(
            self.ui,
            lambda: self._ocr_use_is_active() or bool(getattr(self.ui.dashboard, '_background_busy', False)),
        )
        self.ui.start_support_reminders(self._ocr_use_is_active)
        self.ui.run()

    def _warm_ocr_engine(self):
        self._load_ocr_engines()

    @staticmethod
    def _canonical_ocr_backend(backend: str) -> str:
        name = str(backend).strip().lower().replace("-", "").replace("_", "")
        return "windowsocr" if name == "windows" else name

    def _configured_ocr_backends(self) -> list[str]:
        engine_by_context = dict(getattr(config, "OCR_ENGINE_BY_CONTEXT", {}) or {})
        contexts = tuple(getattr(config, "OCR_RECOMMENDED_BY_CONTEXT", {})) or tuple(engine_by_context)
        engines = list(dict.fromkeys(
            self._canonical_ocr_backend(
                engine_by_context.get(context, config.OCR_ENGINE)
            )
            for context in contexts
        ))
        if getattr(config, "OCR_FALLBACK_ENABLED", False):
            fallback = self._canonical_ocr_backend(getattr(config, "OCR_FALLBACK_ENGINE", ""))
            if fallback and fallback not in engines:
                engines.append(fallback)
        return engines

    @staticmethod
    def _ocr_process_memory_report(loaded_backends: set[str]) -> str:
        """Report resident memory for Sidro and each isolated OCR worker."""
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            psapi = ctypes.WinDLL("Psapi.dll")
            kernel32 = ctypes.WinDLL("kernel32.dll")
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            def working_set(handle):
                counters = ProcessMemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return None
                return counters.WorkingSetSize / (1024 * 1024)

            rows = []
            own_mb = working_set(kernel32.GetCurrentProcess())
            if own_mb is not None:
                rows.append(f"Sidro={own_mb:.0f} MB")

            if loaded_backends:
                from pillar2_ocr import ocr_worker_processes

                for backend, pid in ocr_worker_processes().items():
                    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)
                    if not handle:
                        continue
                    try:
                        worker_mb = working_set(handle)
                        if worker_mb is not None:
                            rows.append(f"{backend}={worker_mb:.0f} MB")
                    finally:
                        kernel32.CloseHandle(handle)
            return ", ".join(rows)
        except Exception as exc:
            return f"process memory unavailable: {exc}"

    def _log_ocr_memory(self, context: str):
        try:
            from pillar2_ocr import loaded_ocr_backends

            loaded = loaded_ocr_backends()
            print(f"[OCR Memory] {context}: {self._ocr_process_memory_report(loaded)}")
        except Exception as exc:
            print(f"[OCR Memory] {context}: unavailable ({exc})")

    def _schedule_ocr_button_refresh(self):
        try:
            from pillar2_ocr import loaded_ocr_backends
            loaded = loaded_ocr_backends()
            targets = set(self._configured_ocr_backends())
            fully_loaded = bool(targets) and targets.issubset(loaded)
            if targets:
                status = f"Loaded {len(targets & loaded)}/{len(targets)} OCR engines"
            else:
                status = "No OCR engines are configured"
            enabled = not self._ocr_warmup_lock.locked()
            self.ui.invoke(lambda: self._set_ocr_load_ui(fully_loaded, status, enabled=enabled))
        except Exception:
            pass

    def toggle_ocr_engines(self):
        if self._ocr_reset_pending.is_set():
            return
        if not self._ocr_warmup_lock.acquire(blocking=False):
            self.ui.toast("OCR loading or unloading is already running.", "info", 3000)
            return

        try:
            from pillar2_ocr import loaded_ocr_backends
            targets = self._configured_ocr_backends()
            currently_loaded = loaded_ocr_backends()
            should_unload = bool(targets) and set(targets).issubset(currently_loaded)
        except Exception:
            targets = self._configured_ocr_backends()
            should_unload = False

        action = "Unloading" if should_unload else "Loading"
        self.ui.invoke(lambda: self._set_ocr_load_ui(
            should_unload, f"{action} OCR engines…", enabled=False
        ))
        self.ui.toast(f"{action} configured OCR engines…", "info", 3000)

        def load():
            from pillar2_ocr import create_ocr_engine, loaded_ocr_backends
            memory_before = self._ocr_process_memory_report(loaded_ocr_backends())
            if memory_before:
                print(f"[OCR Memory] Before loading: {memory_before}")
            errors: list[str] = []
            for backend in targets:
                try:
                    create_ocr_engine(engine_override=backend)
                    print(f"[Main] OCR ready: {backend}")
                except Exception as e:
                    errors.append(f"{backend}: {e}")
                    print(f"[Main] OCR preparation failed for {backend}: {e}")

            loaded = loaded_ocr_backends()
            memory_report = self._ocr_process_memory_report(loaded)
            fully_loaded = bool(targets) and set(targets).issubset(loaded)
            if errors or not fully_loaded:
                status = f"Loaded {len(set(targets) & loaded)}/{len(targets)} OCR engines"
                self.ui.invoke(lambda: self._set_ocr_load_ui(fully_loaded, status, enabled=True))
                error_text = "; ".join(errors) or "the configured set is not fully loaded"
                self.ui.toast(
                    "Some OCR engines could not be loaded: " + error_text,
                    "error",
                    9000,
                )
            else:
                names = ", ".join(targets)
                self.ui.invoke(lambda: self._set_ocr_load_ui(True, f"Loaded: {names}", enabled=True))
                self.ui.toast(f"OCR engines loaded: {names}.", "success", 5000)
            if memory_report:
                print(f"[OCR Memory] After loading: {memory_report}")

        def unload():
            from pillar2_ocr import loaded_ocr_backends, unload_ocr_backends
            if self._ocr_use_is_active():
                loaded = loaded_ocr_backends()
                fully_loaded = bool(targets) and set(targets).issubset(loaded)
                status = "Stop active scans and the reward monitor before unloading OCR."
                self.ui.invoke(lambda: self._set_ocr_load_ui(fully_loaded, status, enabled=True))
                self.ui.toast(status, "warning", 6500)
                return

            # Scanner instances retain engine wrappers. Drop those references
            # before clearing shared model/runtime objects so their memory can go.
            if self.ocr is not None:
                self.ocr._engine = None
            for scanner in (
                self.inventory_scanner,
                self.inventory_bottom_scanner,
                self.trade_scanner,
                self.reward_scanner,
            ):
                if scanner is None:
                    continue
                if hasattr(scanner, "_ocr"):
                    scanner._ocr = None
                if hasattr(scanner, "_fallback_ocr"):
                    scanner._fallback_ocr = None

            unloaded = unload_ocr_backends()
            remaining = loaded_ocr_backends()
            self.ui.invoke(lambda: self._set_ocr_load_ui(
                False,
                "OCR workers stopped; their runtime and model memory should now be released.",
                enabled=True,
            ))
            self.ui.toast(
                "OCR engines unloaded: " + (", ".join(sorted(unloaded)) or "none were resident"),
                "success",
                5000,
            )
            memory_report = self._ocr_process_memory_report(remaining)
            if memory_report:
                print(f"[OCR Memory] After unloading {sorted(unloaded)}: {memory_report}")

        def runner():
            try:
                if should_unload:
                    unload()
                else:
                    load()
            finally:
                self._ocr_warmup_lock.release()
                with self._worker_threads_lock:
                    self._worker_threads.discard(threading.current_thread())

        thread = threading.Thread(target=runner, name="Sidro: Load or unload OCR", daemon=True)
        with self._worker_threads_lock:
            if self._stop_event.is_set():
                self._ocr_warmup_lock.release()
                return
            self._worker_threads.add(thread)
        thread.start()

    def _load_ocr_engines(self):
        """Startup preload hook; unlike the manual toggle, it only loads."""
        if not self._ocr_warmup_lock.acquire(blocking=False):
            return
        self.ui.invoke(lambda: self._set_ocr_load_ui(False, "Loading OCR engines…", enabled=False))

        def runner():
            try:
                from pillar2_ocr import create_ocr_engine, loaded_ocr_backends
                targets = self._configured_ocr_backends()
                errors = []
                for backend in targets:
                    try:
                        create_ocr_engine(engine_override=backend)
                    except Exception as exc:
                        errors.append(f"{backend}: {exc}")
                loaded = loaded_ocr_backends()
                fully_loaded = bool(targets) and set(targets).issubset(loaded)
                status = (
                    f"Loaded {len(set(targets) & loaded)}/{len(targets)} OCR engines"
                    if errors or not fully_loaded
                    else f"Loaded: {', '.join(targets)}"
                )
                self.ui.invoke(lambda: self._set_ocr_load_ui(fully_loaded, status, enabled=True))
                if errors:
                    self.ui.toast("Some OCR engines could not be loaded: " + "; ".join(errors), "error", 9000)
            finally:
                self._ocr_warmup_lock.release()
                with self._worker_threads_lock:
                    self._worker_threads.discard(threading.current_thread())

        thread = threading.Thread(target=runner, name="Sidro: Startup OCR load", daemon=True)
        with self._worker_threads_lock:
            if self._stop_event.is_set():
                self._ocr_warmup_lock.release()
                return
            self._worker_threads.add(thread)
        thread.start()

    def _ocr_use_is_active(self) -> bool:
        if self._scan_lock.locked() or self._inventory_lock.locked() or self._trade_lock.locked():
            return True
        if self._reward_monitor_thread is not None and self._reward_monitor_thread.is_alive():
            return True
        if self._trade_live_scan_thread is not None and self._trade_live_scan_thread.is_alive():
            return True
        with self._worker_threads_lock:
            threads = tuple(self._worker_threads)
        return any(
            thread is not threading.current_thread()
            and thread.is_alive()
            and any(token in thread.name.lower() for token in ("scan", "monitor"))
            for thread in threads
        )

    def _set_ocr_load_ui(self, loaded: bool, status: str | None, enabled: bool):
        dashboard = getattr(self.ui, "dashboard", None)
        if dashboard is None or not hasattr(dashboard, "ocr_warmup_button"):
            return
        dashboard.ocr_warmup_button.setEnabled(enabled)
        dashboard.set_ocr_load_state(loaded, status)

    def _refresh_account_store_on_start(self):
        try:
            message = self._refresh_account_store_from_wfm()
            print(f"[Main] Account cache: {message}")
            self.ui.refresh_dashboard_profile()
        except Exception as e:
            print(f"[Main] Account cache refresh skipped: {e}")

    def stop(self):
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        self._running = False
        self._reward_monitor_stop.set()
        self._trade_live_scan_stop.set()
        if self._trade_cancel_event is not None:
            self._trade_cancel_event.set()
        keyboard.unhook_all()

        # Do not close capture devices underneath a scan. Some OCR/API backends
        # cannot be interrupted safely; leave cleanup to process teardown when
        # one is still in flight so closing the UI stays responsive.
        with self._worker_threads_lock:
            active_workers = [
                thread for thread in self._worker_threads
                if thread is not threading.current_thread()
            ]
        active_workers = [thread for thread in active_workers if thread.is_alive()]
        reward_monitor_active = (
            self._reward_monitor_thread is not None
            and self._reward_monitor_thread is not threading.current_thread()
            and self._reward_monitor_thread.is_alive()
        )
        trade_live_scan_active = (
            self._trade_live_scan_thread is not None
            and self._trade_live_scan_thread is not threading.current_thread()
            and self._trade_live_scan_thread.is_alive()
        )
        if active_workers or reward_monitor_active or trade_live_scan_active:
            names = [thread.name for thread in active_workers]
            if reward_monitor_active:
                names.append(self._reward_monitor_thread.name)
            if trade_live_scan_active:
                names.append(self._trade_live_scan_thread.name)
            print("[Main] Background work still active at shutdown:", ", ".join(names))
        else:
            for scanner in (
                self.inventory_scanner,
                self.inventory_bottom_scanner,
                self.trade_scanner,
                self.reward_scanner,
            ):
                if scanner is None:
                    continue
                try:
                    if hasattr(scanner, "close"):
                        scanner.close()
                    elif hasattr(scanner, "grabber"):
                        scanner.grabber.close()
                except Exception:
                    pass

        self.ui.stop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if "--check-ocr" in sys.argv:
        import json
        from pillar2_ocr import validate_ocr_backend
        backend = sys.argv[sys.argv.index("--check-ocr") + 1]
        result = {"backend": backend, "ok": False}
        try:
            validate_ocr_backend(backend)
            result["ok"] = True
        except Exception:
            result["error"] = traceback.format_exc()
        (config.USER_DATA_DIR / "ocr_check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        sys.exit(0 if result["ok"] else 1)
    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        app.stop()

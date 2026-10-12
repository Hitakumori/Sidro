"""Local inventory and listing-state cache for Sidro.

Stores scan results and known market listings as JSON so review and trade
matching can use the user's local item state. Runtime account data belongs
in the app's data directory and must not be published with source.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Iterable

import config
from pillar3_api import ExistingListing


@dataclass
class StoredItem:
    display_name: str
    url_name: str
    quantity: int = 0
    item_id: str = ""
    last_seen: float = 0.0


@dataclass
class StoredListing:
    display_name: str
    url_name: str
    order_id: str
    item_id: str = ""
    platinum: int = 0
    quantity: int = 0
    visible: bool = True
    last_synced: float = 0.0


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


class AccountStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config.ACCOUNT_STATE_PATH
        self.items: dict[str, StoredItem] = {}
        self.listings: dict[str, StoredListing] = {}
        self.reward_queue: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self.load()

    @_synchronized
    def load(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[Store] Could not load {self.path}: {e}")
            return

        self.items = {
            url: StoredItem(
                display_name=str(raw.get("display_name") or _title_from_slug(url)),
                url_name=url,
                quantity=int(raw.get("quantity") or 0),
                item_id=str(raw.get("item_id") or ""),
                last_seen=float(raw.get("last_seen") or 0),
            )
            for url, raw in (data.get("items") or {}).items()
            if isinstance(raw, dict)
        }
        self.listings = {
            url: StoredListing(
                display_name=str(raw.get("display_name") or self.display_name(url)),
                url_name=url,
                order_id=str(raw.get("order_id") or ""),
                item_id=str(raw.get("item_id") or ""),
                platinum=int(raw.get("platinum") or 0),
                quantity=int(raw.get("quantity") or 0),
                visible=bool(raw.get("visible", True)),
                last_synced=float(raw.get("last_synced") or 0),
            )
            for url, raw in (data.get("listings") or {}).items()
            if isinstance(raw, dict)
        }
        self.reward_queue = {
            url: {
                "url_name": url,
                "display_name": str(raw.get("display_name") or _title_from_slug(url)),
                "quantity": max(1, int(raw.get("quantity") or 1)),
            }
            for url, raw in (data.get("queued_rewards") or {}).items()
            if isinstance(raw, dict) and int(raw.get("quantity") or 0) > 0
        }

    def save(self):
        tmp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "updated_at": time.time(),
                    "items": {url: item.__dict__ for url, item in sorted(self.items.items())},
                    "listings": {url: listing.__dict__ for url, listing in sorted(self.listings.items())},
                    "queued_rewards": {
                        url: dict(row) for url, row in sorted(self.reward_queue.items())
                    },
                }
                with tempfile.NamedTemporaryFile(
                    "w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as f:
                    tmp_path = Path(f.name)
                    json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.path)
        except Exception as e:
            print(f"[Store] Could not save {self.path}: {e}")
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass


    @_synchronized
    def display_name(self, url_name: str) -> str:
        if url_name in self.items:
            return self.items[url_name].display_name
        if url_name in self.listings:
            return self.listings[url_name].display_name
        return _title_from_slug(url_name)

    @_synchronized
    def upsert_inventory_items(self, items: Iterable[object]):
        now = time.time()
        changed = False
        for item in items:
            url = getattr(item, "url_name", "")
            if not url:
                continue
            current = self.items.get(url)
            quantity = int(getattr(item, "quantity", 0) or 0)
            display_name = str(getattr(item, "display_name", "") or _title_from_slug(url))
            item_id = str(getattr(item, "_item_id", "") or (current.item_id if current else ""))
            self.items[url] = StoredItem(
                display_name=display_name,
                url_name=url,
                quantity=max(0, quantity),
                item_id=item_id,
                last_seen=now,
            )
            changed = True
        if changed:
            self.save()

    @_synchronized
    def increment_inventory_item(
        self,
        url_name: str,
        display_name: str,
        quantity: int = 1,
        item_id: str = "",
    ):
        if not url_name:
            return
        now = time.time()
        quantity = max(1, int(quantity))
        current = self.items.get(url_name)
        if current is None:
            self.items[url_name] = StoredItem(
                display_name=display_name or _title_from_slug(url_name),
                url_name=url_name,
                quantity=quantity,
                item_id=item_id,
                last_seen=now,
            )
        else:
            current.display_name = display_name or current.display_name
            current.quantity = max(0, int(current.quantity)) + quantity
            if item_id:
                current.item_id = item_id
            current.last_seen = now
        self.save()

    @_synchronized
    def get_reward_queue(self) -> dict[str, dict[str, object]]:
        return {url: dict(row) for url, row in self.reward_queue.items()}

    @_synchronized
    def queue_reward(self, url_name: str, display_name: str) -> dict[str, dict[str, object]]:
        """Atomically persist a picked reward and its local inventory count."""
        if not url_name:
            return self.get_reward_queue()
        now = time.time()
        current_item = self.items.get(url_name)
        if current_item is None:
            self.items[url_name] = StoredItem(
                display_name=display_name or _title_from_slug(url_name),
                url_name=url_name,
                quantity=1,
                last_seen=now,
            )
        else:
            current_item.display_name = display_name or current_item.display_name
            current_item.quantity = max(0, int(current_item.quantity)) + 1
            current_item.last_seen = now

        queued = self.reward_queue.get(url_name)
        if queued is None:
            queued = {
                "url_name": url_name,
                "display_name": display_name or _title_from_slug(url_name),
                "quantity": 0,
            }
            self.reward_queue[url_name] = queued
        queued["display_name"] = display_name or queued["display_name"]
        queued["quantity"] = int(queued.get("quantity", 0)) + 1
        self.save()
        return {key: dict(row) for key, row in self.reward_queue.items()}

    @_synchronized
    def remove_queued_rewards(self, reviewed: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
        """Remove only the reviewed quantities, retaining rewards queued meanwhile."""
        for url_name, row in reviewed.items():
            current = self.reward_queue.get(url_name)
            if current is None:
                continue
            remaining = int(current.get("quantity", 0)) - int(row.get("quantity", 0))
            if remaining > 0:
                current["quantity"] = remaining
            else:
                self.reward_queue.pop(url_name, None)
        self.save()
        return {key: dict(row) for key, row in self.reward_queue.items()}

    @_synchronized
    def upsert_listing(
        self,
        url_name: str,
        display_name: str,
        order_id: str,
        item_id: str = "",
        platinum: int = 0,
        quantity: int = 0,
        visible: bool = True,
    ):
        if not url_name:
            return
        now = time.time()
        display = display_name or self.display_name(url_name)
        self.listings[url_name] = StoredListing(
            display_name=display,
            url_name=url_name,
            order_id=order_id,
            item_id=item_id,
            platinum=int(platinum),
            quantity=max(0, int(quantity)),
            visible=bool(visible),
            last_synced=now,
        )
        if url_name not in self.items:
            self.items[url_name] = StoredItem(
                display_name=display,
                url_name=url_name,
                quantity=max(0, int(quantity)),
                item_id=item_id,
                last_seen=now,
            )
        elif item_id:
            self.items[url_name].item_id = item_id
        self.save()

    @_synchronized
    def upsert_listings(
        self,
        listing_map: dict[str, ExistingListing],
        display_names_by_url: dict[str, str] | None = None,
    ):
        display_names_by_url = display_names_by_url or {}
        seen_orders: set[str] = set()
        changed = False
        now = time.time()

        for listing in listing_map.values():
            if not listing.order_id or listing.order_id in seen_orders:
                continue
            seen_orders.add(listing.order_id)
            url = listing.item_url_name
            if not url:
                continue
            display = display_names_by_url.get(url) or self.display_name(url)
            self.listings[url] = StoredListing(
                display_name=display,
                url_name=url,
                order_id=listing.order_id,
                item_id=listing.item_id,
                platinum=int(listing.platinum),
                quantity=int(listing.quantity),
                visible=bool(listing.visible),
                last_synced=now,
            )
            if url not in self.items:
                self.items[url] = StoredItem(
                    display_name=display,
                    url_name=url,
                    quantity=int(listing.quantity),
                    item_id=listing.item_id,
                    last_seen=now,
                )
            elif listing.item_id:
                self.items[url].item_id = listing.item_id
            changed = True

        if changed:
            self.save()

    @_synchronized
    def replace_listings(
        self,
        listing_map: dict[str, ExistingListing],
        display_names_by_url: dict[str, str] | None = None,
    ):
        """
        Authoritative WFM sync: replace the local listing table with the current
        sell orders returned by the account endpoint.
        """
        previous_items = self.items
        self.listings = {}
        self.items = previous_items
        self.upsert_listings(listing_map, display_names_by_url)
        if not listing_map:
            self.save()

    @_synchronized
    def delete_listing(self, url_name: str):
        if self.listings.pop(url_name, None) is not None:
            self.save()

    @_synchronized
    def decrement_after_trade(self, url_name: str, count: int = 1):
        count = max(1, int(count))
        if url_name in self.items:
            self.items[url_name].quantity = max(0, self.items[url_name].quantity - count)
            self.items[url_name].last_seen = time.time()
        if url_name in self.listings:
            remaining = self.listings[url_name].quantity - count
            if remaining <= 0:
                del self.listings[url_name]
            else:
                self.listings[url_name].quantity = remaining
                self.listings[url_name].last_synced = time.time()
        self.save()

    @_synchronized
    def listed_match_candidates(self) -> dict[str, str]:
        return {
            url: listing.display_name
            for url, listing in self.listings.items()
            if listing.visible and listing.quantity > 0
        }

    @_synchronized
    def all_match_candidates(self) -> dict[str, str]:
        candidates = {
            url: item.display_name
            for url, item in self.items.items()
            if item.quantity > 0
        }
        candidates.update(self.listed_match_candidates())
        return candidates

    @_synchronized
    def listed_rows(self) -> list[StoredListing]:
        return sorted(self.listings.values(), key=lambda row: row.display_name.lower())

    @_synchronized
    def item_rows(self) -> list[StoredItem]:
        return sorted(self.items.values(), key=lambda row: row.display_name.lower())


def _title_from_slug(slug: str) -> str:
    return slug.replace("_", " ").title()

"""Older CustomTkinter inventory-review interface.

Contains item rows, selection controls and listing callbacks for the earlier
UI. The current portable application's inventory dialogs live in qt_ui.py.
"""

from __future__ import annotations
from typing import Callable

import customtkinter as ctk

import config
from inventory_scanner import InventoryItem
from pillar3_api import ExistingListing, PriceInfo


# ──────────────────────────────────────────────
# COLOR PALETTE (Warframe dark theme)
# ──────────────────────────────────────────────
C_BG      = "#14141A"
C_BG2     = "#1E1E26"
C_BG3     = "#252530"
C_GOLD    = "#C8A96E"
C_GOLD2   = "#E8C97E"
C_TEXT    = "#E2DAC8"
C_MUTED   = "#6A6560"
C_GREEN   = "#5DBB63"
C_RED     = "#C0392B"
C_BLUE    = "#4A9CC8"
C_ROW_A   = "#1A1A22"
C_ROW_B   = "#1E1E28"
C_HEADER  = "#0F0F15"


def _is_prime_item(display_name: str, url_name: str = "") -> bool:
    text = f" {display_name} {url_name} ".lower().replace("_", " ")
    return " prime " in text


class ItemRow:
    """Represents one row in the checklist."""

    def __init__(
        self,
        parent_frame,
        item: InventoryItem,
        price_info: PriceInfo,
        row_idx: int,
        existing_listing: ExistingListing | None = None,
    ):
        self.item = item
        self.price_info = price_info
        self.existing_listing = existing_listing

        bg = C_ROW_A if row_idx % 2 == 0 else C_ROW_B

        self.frame = ctk.CTkFrame(parent_frame, fg_color=bg, corner_radius=0, height=38)
        self.frame.pack(fill="x", pady=1)
        self.frame.pack_propagate(False)
        self.frame.columnconfigure(1, weight=1)

        # Checkbox
        self.var = ctk.BooleanVar(value=existing_listing is None)
        self.checkbox = ctk.CTkCheckBox(
            self.frame, variable=self.var, text="",
            width=28, checkbox_width=18, checkbox_height=18,
            fg_color=C_GOLD, hover_color=C_GOLD2,
            checkmark_color=C_BG,
        )
        self.checkbox.pack(side="left", padx=(8, 4))

        # Score indicator dot
        score = item.match_score
        dot_color = C_GREEN if score >= 85 else (C_GOLD if score >= 70 else C_RED)
        ctk.CTkLabel(self.frame, text="●", text_color=dot_color,
                     font=ctk.CTkFont(size=10), width=14).pack(side="left", padx=(0, 6))

        # Item name + current listing state
        name_text = item.display_name
        if existing_listing is not None:
            name_text += f"  [listed {existing_listing.quantity}x @ {existing_listing.platinum}p]"
        ctk.CTkLabel(
            self.frame,
            text=name_text,
            font=ctk.CTkFont(size=12),
            text_color=C_MUTED if existing_listing else C_TEXT,
            anchor="w", width=300,
        ).pack(side="left", padx=(0, 8))

        # Editable quantity. Badge OCR can be wrong, so let the user correct it.
        self.qty_var = ctk.StringVar(value=str(item.quantity))
        ctk.CTkEntry(
            self.frame,
            textvariable=self.qty_var,
            width=36, height=26,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=C_BG3,
            text_color=C_TEXT,
            border_color=C_MUTED,
            border_width=1,
            justify="center",
        ).pack(side="left", padx=(0, 8))

        # Market lowest price / existing state
        lowest = price_info.lowest_sell_price
        ctk.CTkLabel(
            self.frame,
            text=f"Low: {lowest}p",
            font=ctk.CTkFont(size=11),
            text_color=C_MUTED,
            width=60,
        ).pack(side="left", padx=(0, 6))

        # Editable listing price
        initial_price = (
            str(existing_listing.platinum)
            if existing_listing is not None
            else str(price_info.suggested_listing_price)
        )
        self.price_var = ctk.StringVar(value=initial_price)
        price_entry = ctk.CTkEntry(
            self.frame,
            textvariable=self.price_var,
            width=48, height=26,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color=C_BG3,
            text_color=C_GOLD,
            border_color=C_GOLD,
            border_width=1,
            justify="center",
        )
        price_entry.pack(side="left", padx=(0, 4))

        ctk.CTkLabel(self.frame, text="p", text_color=C_GOLD,
                     font=ctk.CTkFont(size=11)).pack(side="left", padx=(0, 8))

        # Existing listings default to SKIP. User can explicitly switch to UPDATE.
        self.action_var = ctk.StringVar(value="UPDATE" if existing_listing is None else "SKIP")
        values = ["UPDATE"] if existing_listing is None else ["SKIP", "UPDATE"]
        action = ctk.CTkOptionMenu(
            self.frame,
            variable=self.action_var,
            values=values,
            width=78,
            height=26,
            fg_color=C_BG3,
            button_color=C_BG3,
            button_hover_color=C_BG2,
            text_color=C_GOLD if existing_listing is None else C_MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        )
        action.pack(side="left", padx=(0, 6))

    @property
    def is_selected(self) -> bool:
        return self.var.get() and self.action_var.get() == "UPDATE"

    @property
    def listing_price(self) -> int:
        try:
            return max(config.MIN_LISTING_PRICE, int(self.price_var.get()))
        except ValueError:
            return self.price_info.suggested_listing_price

    @property
    def listing_quantity(self) -> int:
        try:
            return max(1, min(int(self.qty_var.get()), 999))
        except ValueError:
            return self.item.quantity


# ──────────────────────────────────────────────
# MAIN WINDOW
# ──────────────────────────────────────────────

class InventoryReviewWindow(ctk.CTkToplevel):
    """
    Scrollable checklist of all detected inventory items.
    User selects which to list and reviews/edits prices.
    """

    def __init__(
        self,
        parent,
        items: list[InventoryItem],
        price_map: dict[str, PriceInfo],
        on_confirm: Callable[[list[InventoryItem]], None],
        on_cancel: Callable[[], None],
        existing_listing_map: dict[str, ExistingListing] | None = None,
    ):
        super().__init__(parent)
        self.items = items
        self.price_map = price_map
        self.existing_listing_map = existing_listing_map or {}
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self._rows: list[ItemRow] = []
        self.price_mode_var = ctk.StringVar(value="Balanced")

        self._setup_window()
        self._build_ui()

    def _setup_window(self):
        self.title("Warframe Market — Inventory Review")
        self.configure(fg_color=C_BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", config.UI_WINDOW_ALPHA)
        self.resizable(True, True)

        # Size and position: left-center of screen
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = 820
        h = min(sh - 120, 120 + len(self.items) * 40 + 80)
        x = max(20, sw - w - 40)
        y = max(40, (sh - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(760, 300)
        self.deiconify()
        self.lift()
        self.focus_force()
        self.grab_set()

    def _build_ui(self):
        # ── Header ─────────────────────────────
        header = ctk.CTkFrame(self, fg_color=C_HEADER, corner_radius=0, height=50)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text=f"⚔  INVENTORY SCAN  —  {len(self.items)} ITEMS DETECTED",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color=C_GOLD,
        ).pack(side="left", padx=16)

        # Select all / none buttons
        btn_frame = ctk.CTkFrame(header, fg_color="transparent")
        btn_frame.pack(side="right", padx=12)

        ctk.CTkOptionMenu(
            btn_frame,
            variable=self.price_mode_var,
            values=["Balanced", "Fast Sell", "Lowest +1p", "Lowest +2p", "Lowest +3p"],
            width=132,
            height=26,
            fg_color=C_BG3,
            button_color=C_BG3,
            button_hover_color=C_BG2,
            text_color=C_GOLD,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda _value: self._apply_pricing_mode(),
        ).pack(side="left", padx=(0, 6))

        ctk.CTkButton(btn_frame, text="All", width=46, height=26,
                      fg_color=C_BG3, hover_color=C_BG2, text_color=C_GOLD,
                      font=ctk.CTkFont(size=11),
                      command=self._select_all).pack(side="left", padx=2)
        ctk.CTkButton(btn_frame, text="Prime", width=58, height=26,
                      fg_color=C_BG3, hover_color=C_BG2, text_color=C_GOLD,
                      font=ctk.CTkFont(size=11),
                      command=self._select_prime).pack(side="left", padx=2)
        ctk.CTkButton(btn_frame, text="None", width=46, height=26,
                      fg_color=C_BG3, hover_color=C_BG2, text_color=C_MUTED,
                      font=ctk.CTkFont(size=11),
                      command=self._deselect_all).pack(side="left", padx=2)

        # ── Column headers ─────────────────────
        col_header = ctk.CTkFrame(self, fg_color=C_BG2, corner_radius=0, height=28)
        col_header.pack(fill="x")
        col_header.pack_propagate(False)

        headers = [("", 50), ("ITEM NAME", 330), ("QTY", 42), ("MARKET", 70), ("PRICE", 70), ("ACTION", 90)]
        for label, width in headers:
            ctk.CTkLabel(col_header, text=label, text_color=C_GOLD,
                         font=ctk.CTkFont(size=10, weight="bold"),
                         width=width, anchor="w").pack(side="left", padx=(4, 0))

        # ── Scrollable item list ───────────────
        scroll_frame = ctk.CTkScrollableFrame(
            self, fg_color=C_BG, scrollbar_button_color=C_GOLD,
            scrollbar_button_hover_color=C_GOLD2,
        )
        scroll_frame.pack(fill="both", expand=True, padx=0, pady=0)

        for i, item in enumerate(self.items):
            price_info = self.price_map.get(item.url_name)
            if price_info is None:
                continue
            row = ItemRow(
                scroll_frame,
                item,
                price_info,
                row_idx=i,
                existing_listing=self.existing_listing_map.get(item.url_name),
            )
            self._rows.append(row)

        # ── Stats bar ──────────────────────────
        stats_frame = ctk.CTkFrame(self, fg_color=C_BG2, corner_radius=0, height=30)
        stats_frame.pack(fill="x")
        stats_frame.pack_propagate(False)

        self._stats_label = ctk.CTkLabel(
            stats_frame, text=self._stats_text(),
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=C_MUTED,
        )
        self._stats_label.pack(side="left", padx=14)

        # Update stats when checkboxes change
        for row in self._rows:
            row.var.trace_add("write", lambda *_: self._refresh_stats())
            row.action_var.trace_add("write", lambda *_: self._refresh_stats())

        # ── Action buttons ─────────────────────
        btn_bar = ctk.CTkFrame(self, fg_color=C_BG, corner_radius=0, height=56)
        btn_bar.pack(fill="x")
        btn_bar.pack_propagate(False)

        ctk.CTkButton(
            btn_bar,
            text="✖  CANCEL",
            fg_color=C_RED, hover_color="#A93226",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=140, height=36,
            command=self._cancel,
        ).pack(side="right", padx=(8, 14), pady=10)

        self._confirm_btn = ctk.CTkButton(
            btn_bar,
            text=f"✔  LIST SELECTED  ({self._selected_count()})",
            fg_color=C_GREEN, hover_color="#4AA851",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            width=220, height=36,
            command=self._confirm,
        )
        self._confirm_btn.pack(side="right", padx=4, pady=10)

        self.bind("<Escape>", lambda e: self._cancel())
        self.focus_force()

    def _apply_pricing_mode(self):
        mode = self.price_mode_var.get()
        for row in self._rows:
            lowest = row.price_info.lowest_sell_price
            if mode == "Fast Sell":
                price = lowest
            elif mode == "Lowest +1p":
                price = lowest + 1
            elif mode == "Lowest +2p":
                price = lowest + 2
            elif mode == "Lowest +3p":
                price = lowest + 3
            else:
                price = row.price_info.suggested_listing_price
            row.price_var.set(str(max(config.MIN_LISTING_PRICE, int(price))))
        self._refresh_stats()

    # ── Helpers ────────────────────────────────

    def _select_all(self):
        for row in self._rows:
            row.var.set(True)
        self._refresh_stats()

    def _select_prime(self):
        for row in self._rows:
            row.var.set(_is_prime_item(row.item.display_name, row.item.url_name))
        self._refresh_stats()

    def _deselect_all(self):
        for row in self._rows:
            row.var.set(False)
        self._refresh_stats()

    def _selected_count(self) -> int:
        return sum(1 for r in self._rows if r.is_selected)

    def _total_value(self) -> int:
        return sum(r.listing_price * r.listing_quantity for r in self._rows if r.is_selected)

    def _stats_text(self) -> str:
        n = self._selected_count()
        val = self._total_value()
        return f"  {n} / {len(self._rows)} selected  •  Est. total: {val} platinum"

    def _refresh_stats(self):
        self._stats_label.configure(text=self._stats_text())
        self._confirm_btn.configure(text=f"✔  LIST SELECTED  ({self._selected_count()})")

    # ── Actions ────────────────────────────────

    def _confirm(self):
        selected: list[InventoryItem] = []
        for row in self._rows:
            if row.is_selected:
                item = row.item
                # Inject the (possibly user-edited) price back into the item
                # and quantity back into the item before main.py posts/prints.
                item._override_price = row.listing_price
                item._existing_order_id = (
                    row.existing_listing.order_id if row.existing_listing else None
                )
                item._review_action = row.action_var.get()
                item.quantity = row.listing_quantity
                selected.append(item)

        self.on_confirm(selected)
        self.destroy()

    def _cancel(self):
        self.on_cancel()
        self.destroy()


if __name__ == "__main__":
    dummy_items = [
        InventoryItem(
            display_name=f"Test Prime Part {i}",
            url_name=f"test_prime_part_{i}",
            quantity=(i % 5) + 1,
            match_score=95,
            raw_ocr=f"Test Prime Part {i}",
            grid_position=(i % 6, i // 6),
        )
        for i in range(18)
    ]
    dummy_prices = {
        item.url_name: PriceInfo(
            item_url_name=item.url_name,
            lowest_sell_price=2 + (i % 4),
            average_sell_price=4.0,
            active_sell_count=10,
            suggested_listing_price=2 + (i % 4),
            top_5_orders=[],
        )
        for i, item in enumerate(dummy_items)
    }
    dummy_existing = {
        dummy_items[1].url_name: ExistingListing(
            order_id="test-order-1",
            item_url_name=dummy_items[1].url_name,
            item_id="test-item-1",
            platinum=7,
            quantity=2,
            visible=True,
        ),
        dummy_items[4].url_name: ExistingListing(
            order_id="test-order-4",
            item_url_name=dummy_items[4].url_name,
            item_id="test-item-4",
            platinum=12,
            quantity=1,
            visible=True,
        ),
    }

    root = ctk.CTk()
    root.withdraw()

    def _confirm(items):
        print("confirmed", len(items))
        root.quit()

    def _cancel():
        print("cancelled")
        root.quit()

    InventoryReviewWindow(
        root,
        items=dummy_items,
        price_map=dummy_prices,
        on_confirm=_confirm,
        on_cancel=_cancel,
        existing_listing_map=dummy_existing,
    )
    root.mainloop()

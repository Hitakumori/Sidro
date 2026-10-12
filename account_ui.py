"""
account_ui.py - Local account inventory/listing manager.
"""

from __future__ import annotations

from typing import Callable
import threading

import customtkinter as ctk

import config
from auth_manager import BrowserProfile
from account_store import AccountStore, StoredListing


C_BG = "#14141A"
C_BG2 = "#1E1E26"
C_BG3 = "#252530"
C_HEADER = "#0F0F15"
C_GOLD = "#C8A96E"
C_GOLD2 = "#E8C97E"
C_TEXT = "#E2DAC8"
C_MUTED = "#7A7470"
C_GREEN = "#5DBB63"
C_RED = "#C0392B"
C_BLUE = "#4A9CC8"


def _is_prime_item(display_name: str, url_name: str = "") -> bool:
    text = f" {display_name} {url_name} ".lower().replace("_", " ")
    return " prime " in text


class AccountRow:
    def __init__(self, parent, listing: StoredListing, row_idx: int):
        self.listing = listing
        self.market_lowest: int | None = None
        self.market_suggested: int | None = None
        bg = "#1A1A22" if row_idx % 2 == 0 else "#1E1E28"
        self.frame = ctk.CTkFrame(parent, fg_color=bg, corner_radius=0, height=36)
        self.frame.pack(fill="x", pady=1)
        self.frame.pack_propagate(False)

        self.var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            self.frame,
            variable=self.var,
            text="",
            width=30,
            checkbox_width=18,
            checkbox_height=18,
            fg_color=C_GOLD,
            hover_color=C_GOLD2,
            checkmark_color=C_BG,
        ).pack(side="left", padx=(8, 4))

        ctk.CTkLabel(
            self.frame,
            text=listing.display_name,
            width=280,
            anchor="w",
            text_color=C_TEXT,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(0, 8))

        self.quantity_var = ctk.StringVar(value=str(listing.quantity))
        ctk.CTkEntry(
            self.frame,
            textvariable=self.quantity_var,
            width=70,
            height=26,
            fg_color=C_BG3,
            border_color=C_MUTED,
            border_width=1,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=12, weight="bold"),
            justify="center",
        ).pack(side="left", padx=(0, 8))

        self.lowest_var = ctk.StringVar(value="...")
        ctk.CTkLabel(
            self.frame,
            textvariable=self.lowest_var,
            width=70,
            text_color=C_MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(0, 8))

        self.price_var = ctk.StringVar(value=str(listing.platinum))
        ctk.CTkEntry(
            self.frame,
            textvariable=self.price_var,
            width=70,
            height=26,
            fg_color=C_BG3,
            border_color=C_GOLD,
            border_width=1,
            text_color=C_GOLD,
            font=ctk.CTkFont(size=12, weight="bold"),
            justify="center",
        ).pack(side="left", padx=(0, 8))

        ctk.CTkLabel(
            self.frame,
            text="visible" if listing.visible else "hidden",
            width=70,
            text_color=C_GREEN if listing.visible else C_MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(side="left", padx=(0, 8))

    @property
    def selected(self) -> bool:
        return bool(self.var.get())

    @property
    def quantity(self) -> int:
        try:
            return max(1, min(999, int(self.quantity_var.get())))
        except ValueError:
            return max(1, self.listing.quantity)

    @property
    def price(self) -> int:
        try:
            return max(config.MIN_LISTING_PRICE, int(self.price_var.get()))
        except ValueError:
            return max(config.MIN_LISTING_PRICE, self.listing.platinum)

    def set_price(self, price: int):
        self.price_var.set(str(max(config.MIN_LISTING_PRICE, int(price))))

    def set_lowest(self, price: int | None):
        self.market_lowest = price
        self.lowest_var.set("..." if price is None else f"{price}p")

    def set_market_prices(self, lowest: int | None, suggested: int | None):
        self.market_lowest = lowest
        self.market_suggested = suggested
        self.lowest_var.set("..." if lowest is None else f"{lowest}p")
        if suggested is not None:
            self.set_price(suggested)


class AccountManagerWindow(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        store: AccountStore,
        on_refresh: Callable[[], str],
        on_update_prices: Callable[[dict[str, dict[str, int]], str], str],
        on_price_preview: Callable[[list[str], str], dict[str, dict[str, int]]],
        on_status_change: Callable[[str], str],
        on_save_token: Callable[[str], str],
        on_import_token: Callable[[BrowserProfile], str],
        on_discover_profiles: Callable[[], list[BrowserProfile]],
        on_inventory_scan: Callable[[], None] | None = None,
        on_trade_monitor: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self.store = store
        self.on_refresh = on_refresh
        self.on_update_prices = on_update_prices
        self.on_price_preview = on_price_preview
        self.on_status_change = on_status_change
        self.on_save_token = on_save_token
        self.on_import_token = on_import_token
        self.on_discover_profiles = on_discover_profiles
        self.on_inventory_scan = on_inventory_scan
        self.on_trade_monitor = on_trade_monitor
        self.rows: list[AccountRow] = []
        self._background_busy = False

        self._setup_window()
        self._build_ui()

    def _setup_window(self):
        self.title("Sidro")
        self.configure(fg_color=C_BG)
        self.attributes("-topmost", False)
        self.resizable(True, True)

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = 960
        h = min(780, sh - 120)
        self.geometry(f"{w}x{h}+{max(20, sw - w - 40)}+{max(40, (sh - h) // 2)}")
        self.minsize(680, 360)
        self.deiconify()
        self.lift()
        self.focus_force()

    def _build_ui(self):
        header = ctk.CTkFrame(self, fg_color=C_HEADER, corner_radius=0, height=54)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text="Sidro",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=C_GOLD,
        ).pack(side="left", padx=16)

        self.mode_var = ctk.StringVar(value="Most Common")
        ctk.CTkOptionMenu(
            header,
            variable=self.mode_var,
            values=[
                "Most Common",
                "Most Common -1",
                "Quicksell",
                "Lowest +1p",
                "Lowest +2p",
                "Lowest +3p",
            ],
            width=146,
            height=28,
            fg_color=C_BG3,
            button_color=C_BG3,
            button_hover_color=C_BG2,
            text_color=C_GOLD,
            command=lambda _value: self._apply_pricing_mode(fetch_missing=True),
        ).pack(side="right", padx=(6, 14))

        self.market_status_var = ctk.StringVar(value="Online in-game")
        ctk.CTkButton(
            header,
            text="Set status",
            width=88,
            height=28,
            fg_color=C_GREEN,
            hover_color="#4AA851",
            command=self._set_market_status,
        ).pack(side="right", padx=(4, 0))
        ctk.CTkOptionMenu(
            header,
            variable=self.market_status_var,
            values=["Offline", "Online", "Online in-game"],
            width=132,
            height=28,
            fg_color=C_BG3,
            button_color=C_BG3,
            button_hover_color=C_BG2,
            text_color=C_GOLD,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            header,
            text="Refresh your warframe.market profile",
            width=218,
            height=28,
            fg_color=C_BLUE,
            hover_color="#3379A8",
            command=self._refresh_from_wfm,
        ).pack(side="right", padx=4)

        ctk.CTkButton(
            header,
            text="Login / Token",
            width=116,
            height=28,
            fg_color=C_BG3,
            hover_color=C_BG2,
            text_color=C_GOLD,
            command=self._open_token_window,
        ).pack(side="right", padx=4)

        controls = ctk.CTkFrame(self, fg_color=C_BG2, corner_radius=0, height=38)
        controls.pack(fill="x")
        controls.pack_propagate(False)

        ctk.CTkButton(
            controls,
            text="Inventory scan",
            width=118,
            height=26,
            fg_color=C_BLUE,
            hover_color="#3379A8",
            command=self._start_inventory_scan,
        ).pack(side="left", padx=(12, 4), pady=6)
        ctk.CTkButton(
            controls,
            text="Trade monitor",
            width=118,
            height=26,
            fg_color=C_GOLD,
            hover_color=C_GOLD2,
            text_color=C_BG,
            command=self._start_trade_monitor,
        ).pack(side="left", padx=4, pady=6)

        self.sound_var = ctk.BooleanVar(value=getattr(config, "ALERT_SOUND_ENABLED", True))
        ctk.CTkCheckBox(
            controls,
            variable=self.sound_var,
            text="Sound alerts",
            width=112,
            checkbox_width=17,
            checkbox_height=17,
            fg_color=C_GOLD,
            hover_color=C_GOLD2,
            checkmark_color=C_BG,
            text_color=C_TEXT,
            command=self._toggle_sound_alerts,
        ).pack(side="left", padx=(8, 4), pady=6)

        self.sound_style_var = ctk.StringVar(value=getattr(config, "ALERT_SOUND_STYLE", "Random"))
        ctk.CTkOptionMenu(
            controls,
            variable=self.sound_style_var,
            values=["Random", "Void Chime", "Trade Ping", "Soft Pulse", "Credit Tick"],
            width=118,
            height=26,
            fg_color=C_BG3,
            button_color=C_BG3,
            button_hover_color=C_BG2,
            text_color=C_GOLD,
            command=lambda _value: self._set_sound_style(),
        ).pack(side="left", padx=4, pady=6)

        self.sound_volume_var = ctk.DoubleVar(value=float(getattr(config, "ALERT_SOUND_VOLUME", 0.45)))
        ctk.CTkSlider(
            controls,
            variable=self.sound_volume_var,
            from_=0.0,
            to=1.0,
            width=90,
            height=16,
            progress_color=C_GOLD,
            button_color=C_GOLD2,
            button_hover_color=C_GOLD,
            command=lambda _value: self._set_sound_volume(),
        ).pack(side="left", padx=(4, 10), pady=6)

        ctk.CTkButton(
            controls,
            text="All",
            width=54,
            height=26,
            fg_color=C_BG3,
            hover_color=C_BG,
            text_color=C_GOLD,
            command=lambda: self._set_all(True),
        ).pack(side="left", padx=(10, 4), pady=6)
        ctk.CTkButton(
            controls,
            text="Prime",
            width=62,
            height=26,
            fg_color=C_BG3,
            hover_color=C_BG,
            text_color=C_GOLD,
            command=self._select_prime,
        ).pack(side="left", padx=4, pady=6)
        ctk.CTkButton(
            controls,
            text="None",
            width=60,
            height=26,
            fg_color=C_BG3,
            hover_color=C_BG,
            text_color=C_MUTED,
            command=lambda: self._set_all(False),
        ).pack(side="left", padx=4, pady=6)

        self.status_var = ctk.StringVar(value=self._summary_text())
        ctk.CTkLabel(
            controls,
            textvariable=self.status_var,
            text_color=C_MUTED,
            font=ctk.CTkFont(family="Consolas", size=11),
        ).pack(side="left", padx=14)

        self.update_btn = ctk.CTkButton(
            controls,
            text="Update selected items",
            width=160,
            height=28,
            fg_color=C_GREEN,
            hover_color="#4AA851",
            command=self._update_selected,
        )
        self.update_btn.pack(side="right", padx=12, pady=5)

        col_header = ctk.CTkFrame(self, fg_color=C_BG3, corner_radius=0, height=28)
        col_header.pack(fill="x")
        col_header.pack_propagate(False)
        for label, width in [
            ("", 42),
            ("ITEM", 296),
            ("Quantity", 86),
            ("LOWEST", 82),
            ("PRICE", 82),
            ("STATE", 86),
        ]:
            ctk.CTkLabel(
                col_header,
                text=label,
                width=width,
                anchor="w",
                text_color=C_GOLD,
                font=ctk.CTkFont(size=10, weight="bold"),
            ).pack(side="left", padx=(4, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=C_BG,
            scrollbar_button_color=C_GOLD,
            scrollbar_button_hover_color=C_GOLD2,
        )
        self.list_frame.pack(fill="both", expand=True)
        self._rebuild_rows()
        self.after(300, self._preview_prices)

        self.bind("<Escape>", lambda _e: self.destroy())

    def _summary_text(self) -> str:
        listed = len(self.store.listings)
        known = len(self.store.items)
        return f"{listed} listed  |  {known} known items"

    def _rebuild_rows(self):
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.rows.clear()
        for idx, listing in enumerate(self.store.listed_rows()):
            self.rows.append(AccountRow(self.list_frame, listing, idx))
        self.status_var.set(self._summary_text())

    def _set_all(self, value: bool):
        for row in self.rows:
            row.var.set(value)

    def _select_prime(self):
        for row in self.rows:
            row.var.set(_is_prime_item(row.listing.display_name, row.listing.url_name))

    def _start_inventory_scan(self):
        if self.on_inventory_scan is not None:
            self.on_inventory_scan()

    def _start_trade_monitor(self):
        if self.on_trade_monitor is not None:
            self.on_trade_monitor()

    def _toggle_sound_alerts(self):
        config.ALERT_SOUND_ENABLED = bool(self.sound_var.get())
        self.status_var.set(f"Sound alerts {'enabled' if config.ALERT_SOUND_ENABLED else 'disabled'}.")

    def _set_sound_style(self):
        config.ALERT_SOUND_STYLE = self.sound_style_var.get()
        self.status_var.set(f"Alert sound: {config.ALERT_SOUND_STYLE}.")

    def _set_sound_volume(self):
        config.ALERT_SOUND_VOLUME = float(self.sound_volume_var.get())

    def _selected_urls(self) -> list[str]:
        return [row.listing.url_name for row in self.rows if row.selected]

    def _mode(self) -> str:
        return {
            "Most Common -1": config.PRICE_STRATEGY_MOST_COMMON_MINUS_1,
            "Quicksell": config.PRICE_STRATEGY_FAST_SELL,
            "Lowest +1p": config.PRICE_STRATEGY_LOWEST_PLUS_1,
            "Lowest +2p": config.PRICE_STRATEGY_LOWEST_PLUS_2,
            "Lowest +3p": config.PRICE_STRATEGY_LOWEST_PLUS_3,
        }.get(self.mode_var.get(), config.PRICE_STRATEGY_MOST_COMMON)

    def _price_from_mode(self, row: AccountRow) -> int | None:
        mode = self.mode_var.get()
        if mode == "Most Common":
            return row.market_suggested
        if mode == "Most Common -1":
            if row.market_suggested is None:
                return None
            return max(config.MIN_LISTING_PRICE, row.market_suggested - 1)
        if row.market_lowest is None:
            return None
        offsets = {
            "Quicksell": 0,
            "Lowest +1p": 1,
            "Lowest +2p": 2,
            "Lowest +3p": 3,
        }
        if mode in offsets:
            return row.market_lowest + offsets[mode]
        return row.market_suggested

    def _apply_pricing_mode(self, fetch_missing: bool = False):
        missing_market_data = False
        for row in self.rows:
            price = self._price_from_mode(row)
            if price is None:
                missing_market_data = True
                continue
            row.set_price(price)
        if missing_market_data and fetch_missing:
            self._preview_prices()
        else:
            self.status_var.set(f"Applied {self.mode_var.get()} using loaded market prices.")

    def _run_background(self, action: Callable[[], str]):
        if self._background_busy:
            self.status_var.set("An action is already running. Please wait for it to finish.")
            return
        self._background_busy = True
        self.update_btn.configure(state="disabled")
        self.status_var.set("Working...")

        def worker():
            try:
                message = action()
            except Exception as e:
                message = f"ERROR: {e}"

            def finish():
                self._background_busy = False
                self.store.load()
                self._rebuild_rows()
                self.status_var.set(message)
                self.update_btn.configure(state="normal")

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_from_wfm(self):
        self._run_background(self.on_refresh)

    def _set_market_status(self):
        status = {
            "Offline": "offline",
            "Online": "online",
            "Online in-game": "ingame",
        }.get(self.market_status_var.get(), "ingame")
        self._run_background(lambda: self.on_status_change(status))

    def _open_token_window(self):
        TokenImportWindow(
            self,
            on_save_token=self.on_save_token,
            on_import_token=self.on_import_token,
            on_discover_profiles=self.on_discover_profiles,
        )

    def _preview_prices(self):
        urls = [row.listing.url_name for row in self.rows]
        if not urls:
            return
        self.status_var.set("Fetching market prices...")

        def worker():
            try:
                prices = self.on_price_preview(urls, config.PRICE_STRATEGY_MOST_COMMON)
                message = f"Loaded market prices for {len(prices)} item(s)."
            except Exception as e:
                prices = {}
                message = f"ERROR: {e}"

            def finish():
                by_url = {row.listing.url_name: row for row in self.rows}
                for url, info in prices.items():
                    row = by_url.get(url)
                    if row is not None:
                        row.set_market_prices(info["lowest"], info["price"])
                self._apply_pricing_mode(fetch_missing=False)
                self.status_var.set(message)

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _update_selected(self):
        selected = {
            row.listing.url_name: {
                "price": row.price,
                "quantity": row.quantity,
            }
            for row in self.rows
            if row.selected
        }
        if not selected:
            self.status_var.set("No rows selected.")
            return
        mode = self._mode()
        self._run_background(lambda: self.on_update_prices(selected, mode))


class TokenImportWindow(ctk.CTkToplevel):
    def __init__(
        self,
        parent,
        on_save_token: Callable[[str], str],
        on_import_token: Callable[[BrowserProfile], str],
        on_discover_profiles: Callable[[], list[BrowserProfile]],
    ):
        super().__init__(parent)
        self.on_save_token = on_save_token
        self.on_import_token = on_import_token
        self.on_discover_profiles = on_discover_profiles
        self.profiles: list[BrowserProfile] = []

        self.title("warframe.market Login")
        self.configure(fg_color=C_BG)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.geometry("620x360")
        self.grab_set()

        ctk.CTkLabel(
            self,
            text="warframe.market login token",
            text_color=C_GOLD,
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(16, 8))

        self.status_var = ctk.StringVar(
            value="Paste JWT directly, or close your browser and import from a browser profile."
        )
        ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            text_color=C_MUTED,
            font=ctk.CTkFont(size=12),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(0, 10))

        self.token_box = ctk.CTkTextbox(
            self,
            width=584,
            height=92,
            fg_color=C_BG2,
            text_color=C_TEXT,
            border_color=C_MUTED,
            border_width=1,
            wrap="word",
        )
        self.token_box.pack(padx=18, pady=(0, 10))

        button_row = ctk.CTkFrame(self, fg_color="transparent")
        button_row.pack(fill="x", padx=18, pady=(0, 12))

        ctk.CTkButton(
            button_row,
            text="Save pasted JWT",
            width=150,
            height=30,
            fg_color=C_GREEN,
            hover_color="#4AA851",
            command=self._save_pasted,
        ).pack(side="left")

        ctk.CTkButton(
            button_row,
            text="Find browser logins",
            width=160,
            height=30,
            fg_color=C_BLUE,
            hover_color="#3379A8",
            command=self._find_profiles,
        ).pack(side="left", padx=8)

        self.profile_var = ctk.StringVar(value="No browser profiles loaded")
        self.profile_menu = ctk.CTkOptionMenu(
            self,
            variable=self.profile_var,
            values=["No browser profiles loaded"],
            width=584,
            height=30,
            fg_color=C_BG3,
            button_color=C_BG3,
            button_hover_color=C_BG2,
            text_color=C_GOLD,
        )
        self.profile_menu.pack(padx=18, pady=(0, 10))

        ctk.CTkButton(
            self,
            text="Import selected browser token",
            width=220,
            height=32,
            fg_color=C_GOLD,
            hover_color=C_GOLD2,
            text_color=C_BG,
            command=self._import_selected,
        ).pack(anchor="e", padx=18, pady=(0, 14))

    def _run(self, action: Callable[[], str]):
        self.status_var.set("Working...")

        def worker():
            try:
                message = action()
            except Exception as e:
                message = f"ERROR: {e}"
            self.after(0, lambda: self.status_var.set(message))

        threading.Thread(target=worker, daemon=True).start()

    def _save_pasted(self):
        token = self.token_box.get("1.0", "end").strip()
        self._run(lambda: self.on_save_token(token))

    def _find_profiles(self):
        try:
            self.profiles = self.on_discover_profiles()
            labels = [profile.label for profile in self.profiles] or ["No browser profiles found"]
            self.profile_menu.configure(values=labels)
            self.profile_var.set(labels[0])
            self.status_var.set(f"Found {len(self.profiles)} browser profile(s).")
        except Exception as e:
            self.status_var.set(f"ERROR: {e}")

    def _import_selected(self):
        label = self.profile_var.get()
        profile = next((p for p in self.profiles if p.label == label), None)
        if profile is None:
            self.status_var.set("Select a browser profile first.")
            return
        self._run(lambda: self.on_import_token(profile))

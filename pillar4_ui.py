"""Older CustomTkinter confirmation dialogs and UI controller.

Contains the earlier snip review, toast and event-loop interface.
The current portable application's Dashboard and dialogs live in qt_ui.py.
"""

from __future__ import annotations
import math
import random
import struct
import threading
import winsound
import wave
from enum import Enum, auto
from pathlib import Path
from typing import Optional, Callable

import customtkinter as ctk

import config
from pillar2_ocr import MatchedItem
from pillar3_api import PriceInfo

# ──────────────────────────────────────────────
# ENUMS
# ──────────────────────────────────────────────

class UserDecision(Enum):
    CONFIRMED = auto()
    CANCELLED = auto()
    TIMEOUT = auto()


# ──────────────────────────────────────────────
# CONFIRMATION DIALOG
# ──────────────────────────────────────────────

class ConfirmationDialog(ctk.CTkToplevel):
    """
    Modal-ish popup window for user confirmation before posting a listing.
    Stays on top of the game window. Dismisses on Y/N keypress.
    """

    # Warframe gold / dark palette
    COLOR_GOLD = config.UI_COLOR_ACCENT       # "#C8A96E"
    COLOR_BG = "#1A1A1F"
    COLOR_BG2 = "#24242B"
    COLOR_TEXT = "#E8E0D0"
    COLOR_MUTED = "#7A7470"
    COLOR_GREEN = "#5DBB63"
    COLOR_RED = "#C0392B"
    COLOR_BLUE = "#3D8BC0"

    def __init__(
        self,
        parent,
        matched_item: MatchedItem,
        price_info: PriceInfo,
        on_confirm: Callable[[], None],
        on_cancel: Callable[[], None],
        listing_price: int,
    ):
        super().__init__(parent)
        self.matched_item = matched_item
        self.price_info = price_info
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.listing_price = listing_price
        self._decision: Optional[UserDecision] = None

        self._setup_window()
        self._build_ui()
        self._bind_keys()

    def _setup_window(self):
        self.title("Warframe Market Assistant")
        self.configure(fg_color=self.COLOR_BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", config.UI_WINDOW_ALPHA)
        self.resizable(False, False)

        # Center-right of screen (doesn't cover game UI center)
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        w, h = 420, 580
        x = screen_w - w - 40
        y = (screen_h - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.grab_set()  # Make modal

    def _build_ui(self):
        pad = {"padx": 16, "pady": 6}

        # ── Header ─────────────────────────────
        header = ctk.CTkFrame(self, fg_color=self.COLOR_BG2, corner_radius=0)
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text="⚔  MARKET ASSISTANT",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color=self.COLOR_GOLD,
        ).pack(pady=10)

        # ── OCR Detection ──────────────────────
        self._section(self, "DETECTED ITEM")

        ctk.CTkLabel(
            self,
            text=self.matched_item.display_name,
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=self.COLOR_TEXT,
            wraplength=380,
        ).pack(**pad)

        # OCR raw text (muted, for debugging)
        ctk.CTkLabel(
            self,
            text=f'OCR: "{self.matched_item.raw_ocr}"',
            font=ctk.CTkFont(family="Consolas", size=10),
            text_color=self.COLOR_MUTED,
        ).pack(pady=(0, 2))

        # Match confidence bar
        score = self.matched_item.match_score
        score_color = self.COLOR_GREEN if score >= 85 else (self.COLOR_GOLD if score >= 70 else self.COLOR_RED)
        ctk.CTkLabel(
            self,
            text=f"Match confidence: {score}%",
            font=ctk.CTkFont(size=11),
            text_color=score_color,
        ).pack(pady=(0, 8))

        # ── Price Panel ────────────────────────
        self._section(self, "MARKET PRICES")

        price_frame = ctk.CTkFrame(self, fg_color=self.COLOR_BG2, corner_radius=8)
        price_frame.pack(fill="x", padx=16, pady=4)

        self._price_row(price_frame, "Lowest active sell:", f"{self.price_info.lowest_sell_price} platinum")
        self._price_row(price_frame, "10-order average:", f"{self.price_info.average_sell_price} platinum")
        self._price_row(price_frame, "Active sellers:", str(self.price_info.active_sell_count))

        # Listing price (big, highlighted)
        self._section(self, "YOUR LISTING")
        listing_frame = ctk.CTkFrame(self, fg_color="#2A2A18", corner_radius=8)
        listing_frame.pack(fill="x", padx=16, pady=4)
        ctk.CTkLabel(
            listing_frame,
            text=f"→  {self.listing_price} platinum",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=self.COLOR_GOLD,
        ).pack(pady=10)

        # ── Top 5 Competing Orders ─────────────
        self._section(self, "TOP COMPETITORS")
        orders_frame = ctk.CTkFrame(self, fg_color=self.COLOR_BG2, corner_radius=8)
        orders_frame.pack(fill="x", padx=16, pady=4)

        for i, order in enumerate(self.price_info.top_5_orders):
            status_icon = "🟢" if order.user_status == "ingame" else "🟡"
            row_bg = "#1E1E24" if i % 2 == 0 else self.COLOR_BG2
            row = ctk.CTkFrame(orders_frame, fg_color=row_bg, corner_radius=0)
            row.pack(fill="x")
            ctk.CTkLabel(
                row,
                text=f"  {status_icon} {order.username[:20]:<20} {order.platinum}p × {order.quantity}",
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=self.COLOR_TEXT,
                anchor="w",
            ).pack(fill="x", padx=8, pady=2)

        # ── Action Buttons ─────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=16, pady=(12, 16))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        ctk.CTkButton(
            btn_frame,
            text="✔  CONFIRM  [Y]",
            fg_color=self.COLOR_GREEN,
            hover_color="#4AA851",
            text_color="white",
            font=ctk.CTkFont(weight="bold", size=13),
            command=self._confirm,
            corner_radius=6,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            btn_frame,
            text="✖  CANCEL  [N]",
            fg_color=self.COLOR_RED,
            hover_color="#A93226",
            text_color="white",
            font=ctk.CTkFont(weight="bold", size=13),
            command=self._cancel,
            corner_radius=6,
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))

    def _section(self, parent, title: str):
        ctk.CTkLabel(
            parent,
            text=f"  {title}",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self.COLOR_GOLD,
            anchor="w",
        ).pack(fill="x", padx=16, pady=(10, 2))

        ctk.CTkFrame(parent, height=1, fg_color=self.COLOR_GOLD).pack(
            fill="x", padx=16, pady=(0, 4)
        )

    def _price_row(self, parent, label: str, value: str):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=2)
        ctk.CTkLabel(row, text=label, text_color=self.COLOR_MUTED,
                     font=ctk.CTkFont(size=11), anchor="w").pack(side="left")
        ctk.CTkLabel(row, text=value, text_color=self.COLOR_TEXT,
                     font=ctk.CTkFont(size=11, weight="bold"), anchor="e").pack(side="right")

    def _bind_keys(self):
        self.bind("<KeyPress-y>", lambda e: self._confirm())
        self.bind("<KeyPress-Y>", lambda e: self._confirm())
        self.bind("<KeyPress-n>", lambda e: self._cancel())
        self.bind("<KeyPress-N>", lambda e: self._cancel())
        self.bind("<Escape>", lambda e: self._cancel())
        self.focus_force()

    def _confirm(self):
        self._decision = UserDecision.CONFIRMED
        self.on_confirm()
        self.destroy()

    def _cancel(self):
        self._decision = UserDecision.CANCELLED
        self.on_cancel()
        self.destroy()


# ──────────────────────────────────────────────
# NOTIFICATION TOASTS
# ──────────────────────────────────────────────

class ToastNotification(ctk.CTkToplevel):
    """Small non-blocking status notification (auto-dismisses)."""

    COLOR_BG = "#1A1A1F"
    COLOR_GOLD = config.UI_COLOR_ACCENT

    STATUS_COLORS = {
        "success": "#5DBB63",
        "error": "#C0392B",
        "info": "#3D8BC0",
        "warning": "#E67E22",
    }

    def __init__(self, parent, message: str, status: str = "info", duration_ms: int = 3500):
        super().__init__(parent)
        color = self.STATUS_COLORS.get(status, "#3D8BC0")

        self.configure(fg_color=self.COLOR_BG)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.92)
        self.overrideredirect(True)  # No window decorations

        self.update_idletasks()
        sw = self.winfo_screenwidth()
        w, h = 360, 64
        self.geometry(f"{w}x{h}+{sw - w - 40}+40")

        # Left color bar
        ctk.CTkFrame(self, width=5, fg_color=color, corner_radius=0).pack(
            side="left", fill="y"
        )

        ctk.CTkLabel(
            self,
            text=message,
            font=ctk.CTkFont(size=12),
            text_color="#E8E0D0",
            wraplength=330,
            justify="left",
        ).pack(side="left", padx=12, pady=8)

        self.after(duration_ms, self.destroy)


# ──────────────────────────────────────────────
# MAIN UI CONTROLLER
# ──────────────────────────────────────────────

class UIController:
    """
    Manages the customtkinter root window and spawns dialogs.
    The root window is hidden (acts as a host for Toplevel dialogs).

    Usage:
        ui = UIController()
        decision = ui.ask_confirmation(matched_item, price_info, listing_price)
        ui.toast("Listing posted!", "success")
        ui.run()   # blocks — call in main thread
    """

    def __init__(self):
        ctk.set_appearance_mode(config.UI_THEME)
        ctk.set_default_color_theme("dark-blue")

        self.root = ctk.CTk()
        self.root.withdraw()  # Hidden host window
        self.root.title("WFM Assistant (host)")
        self.root.attributes("-topmost", False)

        self._pending_confirmation: Optional[threading.Event] = None
        self._last_decision: Optional[UserDecision] = None

    def ask_confirmation(
        self,
        matched_item: MatchedItem,
        price_info: PriceInfo,
        listing_price: int,
    ) -> UserDecision:
        """
        Shows the confirmation dialog and blocks until user responds.
        Safe to call from any thread — dispatches to main thread via `after`.
        """
        event = threading.Event()
        decision_holder: list[UserDecision] = []

        def _show():
            def _on_confirm():
                decision_holder.append(UserDecision.CONFIRMED)
                event.set()

            def _on_cancel():
                decision_holder.append(UserDecision.CANCELLED)
                event.set()

            ConfirmationDialog(
                self.root, matched_item, price_info,
                on_confirm=_on_confirm,
                on_cancel=_on_cancel,
                listing_price=listing_price,
            )

        self.root.after(0, _show)
        event.wait(timeout=60)  # 60s auto-timeout

        if not decision_holder:
            return UserDecision.TIMEOUT
        return decision_holder[0]

    def toast(self, message: str, status: str = "info", duration_ms: int = 3500):
        """Show a brief non-blocking notification."""
        def _show():
            ToastNotification(self.root, message, status, duration_ms)
        self.root.after(0, _show)

    def alert(self, message: str, status: str = "info", duration_ms: int = 5000, sound: str = "info"):
        """Visible toast plus a lightweight Windows sound cue."""
        self.toast(message, status, duration_ms)
        self.beep(sound)

    @staticmethod
    def beep(sound: str = "info"):
        if not getattr(config, "ALERT_SOUND_ENABLED", True):
            return

        def _play():
            try:
                path = _generated_alert_path(sound)
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            except Exception:
                try:
                    winsound.MessageBeep()
                except Exception:
                    pass

        threading.Thread(target=_play, daemon=True).start()

    def run(self):
        """Start the tkinter event loop (call from main thread)."""
        self.root.mainloop()

    def stop(self):
        self.root.quit()


def _generated_alert_path(event: str) -> Path:
    style = getattr(config, "ALERT_SOUND_STYLE", "Random")
    if style == "Random":
        style = random.choice(["Void Chime", "Trade Ping", "Soft Pulse", "Credit Tick"])
    volume = max(0.0, min(1.0, float(getattr(config, "ALERT_SOUND_VOLUME", 0.45))))
    safe_name = f"{style.lower().replace(' ', '_')}_{event}_{int(volume * 100)}.wav"
    out_dir = Path("generated_sounds")
    out_dir.mkdir(exist_ok=True)
    path = out_dir / safe_name
    if not path.exists():
        _write_alert_wav(path, style, event, volume)
    return path


def _write_alert_wav(path: Path, style: str, event: str, volume: float):
    patterns = {
        "Void Chime": [(392, 0.09), (587, 0.10), (880, 0.16)],
        "Trade Ping": [(660, 0.06), (990, 0.07), (1320, 0.08)],
        "Soft Pulse": [(330, 0.12), (440, 0.14)],
        "Credit Tick": [(1046, 0.04), (1318, 0.04), (1568, 0.08)],
    }
    event_shift = {
        "success": 1.12,
        "trade": 1.0,
        "warning": 0.72,
        "error": 0.55,
        "info": 0.92,
    }.get(event, 1.0)

    sample_rate = 44100
    samples: list[int] = []
    for freq, seconds in patterns.get(style, patterns["Void Chime"]):
        freq *= event_shift
        tone_samples = int(sample_rate * seconds)
        for i in range(tone_samples):
            t = i / sample_rate
            attack = min(1.0, i / max(1, int(sample_rate * 0.012)))
            release = min(1.0, (tone_samples - i) / max(1, int(sample_rate * 0.035)))
            env = min(attack, release)
            wave_a = math.sin(2 * math.pi * freq * t)
            wave_b = 0.28 * math.sin(2 * math.pi * freq * 1.5 * t)
            value = int(32767 * volume * env * 0.45 * (wave_a + wave_b))
            samples.append(max(-32767, min(32767, value)))
        samples.extend([0] * int(sample_rate * 0.035))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))


# ──────────────────────────────────────────────
# QUICK TEST
# ──────────────────────────────────────────────

if __name__ == "__main__":
    from pillar2_ocr import MatchedItem
    from pillar3_api import PriceInfo, MarketOrder

    # Dummy data for UI preview
    dummy_item = MatchedItem(
        display_name="Ash Prime Neuroptics",
        url_name="ash_prime_neuroptics",
        match_score=94,
        raw_ocr="ASH PRIME NEUROPT1CS",
        alternatives=[("Ash Prime Systems", 72), ("Ash Neuroptics", 65)],
    )
    dummy_price = PriceInfo(
        item_url_name="ash_prime_neuroptics",
        lowest_sell_price=45,
        average_sell_price=52.3,
        active_sell_count=18,
        suggested_listing_price=44,
        top_5_orders=[
            MarketOrder("id1", "ash_prime_neuroptics", 45, 1, "sell", "ingame", "TennoPlayer1"),
            MarketOrder("id2", "ash_prime_neuroptics", 47, 2, "sell", "online", "VoidTrader99"),
            MarketOrder("id3", "ash_prime_neuroptics", 50, 1, "sell", "online", "PrimeHunter"),
            MarketOrder("id4", "ash_prime_neuroptics", 52, 1, "sell", "ingame", "LotusFollower"),
            MarketOrder("id5", "ash_prime_neuroptics", 55, 3, "sell", "online", "WarframeFan"),
        ]
    )

    ui = UIController()

    def _test_flow():
        import time; time.sleep(0.3)
        decision = ui.ask_confirmation(dummy_item, dummy_price, listing_price=44)
        print(f"\nUser decision: {decision}")
        if decision == UserDecision.CONFIRMED:
            ui.toast("✅ Listing posted for 44p!", "success")
        else:
            ui.toast("❌ Listing cancelled.", "error")
        ui.root.after(3000, ui.stop)

    import threading
    threading.Thread(target=_test_flow, daemon=True).start()
    ui.run()

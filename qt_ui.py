"""
qt_ui.py - PySide6 UI layer for Sidro.
"""

from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes
from html import escape as html_escape
from collections import deque
import queue
import json
import os
import threading
import tempfile
import shutil
import subprocess
import weakref
import cv2
import numpy as np
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QEventLoop, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QGraphicsDropShadowEffect,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyleOptionButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

import config
from account_store import AccountStore, StoredListing
from alert_sound import generated_styles, play_alert, play_voice_line
from inventory_scanner import InventoryItem
from support_reminders import SUPPORT_URL, SupportReminders
from settings_validation import MAX_SETTINGS_BYTES, sanitize_settings
from name_aliases import normalize_alias_text, sanitize_name_aliases
from pillar2_ocr import MatchedItem, is_ocr_backend_available, validate_ocr_backend
from pillar3_api import ExistingListing, PriceInfo


C_BG = "#12161D"
C_BG2 = "#1C2029"
C_BG3 = "#272D38"
C_HEADER = "#10141A"
C_GOLD = "#C8A96E"
C_GOLD2 = "#E8C97E"
C_TEXT = "#E4E7EE"
C_MUTED = "#929AAB"
C_GREEN = "#38A94D"
C_RED = "#C83A32"
C_BLUE = "#2587C7"

_THEMED_BUTTONS: weakref.WeakSet[QPushButton] = weakref.WeakSet()
_UI_ICONS: dict[tuple[str, str], QIcon] = {}


def _ui_icon(name: str, color: str) -> QIcon:
    """Draw small native icons without additional assets or Qt modules."""
    key = (name, color)
    if key in _UI_ICONS:
        return _UI_ICONS[key]
    pixmap = QPixmap(96, 96)
    pixmap.setDevicePixelRatio(4)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)

    def lines(points, close=False):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        if close:
            path.closeSubpath()
        painter.drawPath(path)

    if name == "brand":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        lines([(12, 1), (22, 12), (18, 20), (15, 23), (18, 13), (12, 7),
               (6, 13), (9, 23), (6, 20), (2, 12)], True)
        lines([(12, 11), (16, 15), (12, 22), (8, 15)], True)
    elif name == "help":
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        font = QFont("Segoe UI")
        font.setPixelSize(17)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(3, 1, 18, 21), Qt.AlignCenter, "?")
    elif name == "chat":
        lines([(5, 4), (19, 4), (21, 6), (21, 15), (19, 17), (10, 17),
               (5, 21), (5, 17), (3, 15), (3, 6)], True)
    elif name == "key":
        painter.drawEllipse(QRectF(3, 3, 8, 8))
        lines([(10, 10), (20, 20), (22, 18), (19, 15), (17, 17)])
    elif name == "refresh":
        painter.drawArc(QRectF(4, 4, 16, 16), 30 * 16, 145 * 16)
        painter.drawArc(QRectF(4, 4, 16, 16), 210 * 16, 145 * 16)
        lines([(19, 3), (20, 8), (15, 8)])
        lines([(5, 21), (4, 16), (9, 16)])
    elif name == "settings":
        painter.drawEllipse(QRectF(6, 6, 12, 12))
        painter.drawEllipse(QRectF(9, 9, 6, 6))
        for start, end in (((12, 2), (12, 6)), ((12, 18), (12, 22)),
                           ((2, 12), (6, 12)), ((18, 12), (22, 12)),
                           ((5, 5), (7, 7)), ((17, 17), (19, 19)),
                           ((5, 19), (7, 17)), ((17, 7), (19, 5))):
            lines([start, end])
    elif name == "heart":
        path = QPainterPath(QPointF(12, 21))
        path.cubicTo(10, 18, 2, 13, 2, 7)
        path.cubicTo(2, 1, 10, 1, 12, 7)
        path.cubicTo(14, 1, 22, 1, 22, 7)
        path.cubicTo(22, 13, 14, 18, 12, 21)
        painter.drawPath(path)
    elif name == "gift":
        painter.drawRect(QRectF(4, 10, 16, 11))
        painter.drawRect(QRectF(3, 7, 18, 4))
        lines([(12, 7), (12, 21)])
        painter.drawEllipse(QRectF(6, 2, 6, 5))
        painter.drawEllipse(QRectF(12, 2, 6, 5))
    elif name == "inventory":
        lines([(12, 2), (21, 7), (21, 17), (12, 22), (3, 17), (3, 7)], True)
        lines([(3, 7), (12, 12), (21, 7)])
        lines([(12, 12), (12, 22)])
    elif name == "delete":
        lines([(4, 6), (20, 6)])
        lines([(6, 6), (7, 21), (17, 21), (18, 6)])
        lines([(9, 6), (9, 3), (15, 3), (15, 6)])
        lines([(10, 10), (10, 17)])
        lines([(14, 10), (14, 17)])
    elif name == "check":
        lines([(4, 12), (9, 17), (20, 6)])
    elif name == "clock":
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        lines([(12, 6), (12, 12), (17, 15)])
    elif name == "search":
        painter.drawEllipse(QRectF(3, 3, 13, 13))
        lines([(15, 15), (21, 21)])
    elif name == "minimize":
        lines([(5, 12), (19, 12)])
    elif name == "maximize":
        painter.drawRect(QRectF(5, 5, 14, 14))
    elif name == "restore":
        lines([(9, 5), (19, 5), (19, 15), (16, 15)])
        painter.drawRect(QRectF(5, 9, 10, 10))
    elif name == "close":
        lines([(6, 6), (18, 18)])
        lines([(18, 6), (6, 18)])
    painter.end()
    icon = QIcon(pixmap)
    _UI_ICONS[key] = icon
    return icon


class _StyledCheckBox(QCheckBox):
    """Keep Qt checkbox behavior and accessibility; paint the theme indicator."""

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(rect, QColor(C_BG))
        checked = self.checkState() != Qt.Unchecked
        painter.setOpacity(1.0 if self.isEnabled() else 0.45)
        painter.setPen(QPen(QColor(C_GOLD if checked else C_MUTED), 1))
        painter.setBrush(QColor(C_GOLD if checked else C_BG2))
        box = QRectF(rect).adjusted(1, 1, -1, -1)
        painter.drawRoundedRect(box, 2, 2)
        if checked:
            painter.setPen(QPen(QColor(C_HEADER), 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            if self.checkState() == Qt.PartiallyChecked:
                painter.drawLine(box.topLeft() + QPointF(3, box.height() / 2),
                                 box.topRight() + QPointF(-3, box.height() / 2))
            else:
                path = QPainterPath(box.topLeft() + QPointF(box.width() * .2, box.height() * .5))
                path.lineTo(box.topLeft() + QPointF(box.width() * .43, box.height() * .73))
                path.lineTo(box.topLeft() + QPointF(box.width() * .8, box.height() * .27))
                painter.drawPath(path)


def _theme_role_for_color(value: str) -> str | None:
    normalized = str(value).strip().lower()
    for role, current in {
        "Background": C_BG,
        "Panel": C_BG2,
        "Control": C_BG3,
        "Header": C_HEADER,
        "Accent": C_GOLD,
        "Accent hover": C_GOLD2,
        "Text": C_TEXT,
        "Muted text": C_MUTED,
        "Success": C_GREEN,
        "Danger": C_RED,
        "Info": C_BLUE,
    }.items():
        if normalized == str(current).strip().lower():
            return role
    return None


def _active_theme_palette() -> dict[str, str]:
    return {
        "Background": C_BG,
        "Panel": C_BG2,
        "Control": C_BG3,
        "Header": C_HEADER,
        "Accent": C_GOLD,
        "Accent hover": C_GOLD2,
        "Text": C_TEXT,
        "Muted text": C_MUTED,
        "Success": C_GREEN,
        "Danger": C_RED,
        "Info": C_BLUE,
    }

DEFAULT_UI_COLORS = {
    "Background": C_BG,
    "Panel": C_BG2,
    "Control": C_BG3,
    "Header": C_HEADER,
    "Accent": C_GOLD,
    "Accent hover": C_GOLD2,
    "Text": C_TEXT,
    "Muted text": C_MUTED,
    "Success": C_GREEN,
    "Danger": C_RED,
    "Info": C_BLUE,
}


def _load_color_settings():
    global C_BG, C_BG2, C_BG3, C_HEADER, C_GOLD, C_GOLD2, C_TEXT, C_MUTED, C_GREEN, C_RED, C_BLUE
    colors = dict(DEFAULT_UI_COLORS)
    colors.update(getattr(config, "UI_COLORS", {}) or {})
    C_BG = colors["Background"]
    C_BG2 = colors["Panel"]
    C_BG3 = colors["Control"]
    C_HEADER = colors["Header"]
    C_GOLD = colors["Accent"]
    C_GOLD2 = colors["Accent hover"]
    C_TEXT = colors["Text"]
    C_MUTED = colors["Muted text"]
    C_GREEN = colors["Success"]
    C_RED = colors["Danger"]
    C_BLUE = colors["Info"]


_load_color_settings()


SIDRO_VISUALS = {
    "robot": ("Sidro robot", "Sidrobot.png"),
    "maid": ("Sidro Maid", "Sidro.png"),
}


def _sidro_visual_asset() -> str:
    key = str(getattr(config, "SIDRO_VISUAL_CHARACTER", "robot")).strip().lower()
    return SIDRO_VISUALS.get(key, SIDRO_VISUALS["robot"])[1]


def _app_icon() -> QIcon:
    for filename in ("Sidro.ico", "Sidrobot.png", "Sidro.png"):
        path = config.resource_path(filename)
        try:
            if path.exists() and path.stat().st_size > 0:
                icon = QIcon(str(path))
                if not icon.isNull():
                    return icon
        except OSError:
            continue
    return QIcon()


class UserDecision:
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class _Invoker(QObject):
    call = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.call.connect(self._execute, Qt.ConnectionType.QueuedConnection)

    def _execute(self, fn):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()


class LogStream(QObject):
    text_written = Signal(str)

    def __init__(self, target, max_chunks: int = 4000):
        super().__init__()
        self.target = target
        self.buffer = deque(maxlen=max_chunks)

    def write(self, text: str):
        if self.target is not None:
            try:
                self.target.write(text)
            except Exception:
                pass
        if not text:
            return
        self.buffer.append(text)
        self.text_written.emit(text)

    def flush(self):
        if self.target is not None:
            try:
                self.target.flush()
            except Exception:
                pass

    def current_text(self) -> str:
        return "".join(self.buffer)

    def clear(self):
        self.buffer.clear()


_LOG_STREAM: LogStream | None = None


def _install_log_capture() -> LogStream:
    global _LOG_STREAM
    if isinstance(sys.stdout, LogStream):
        _LOG_STREAM = sys.stdout
        return _LOG_STREAM
    if _LOG_STREAM is None:
        _LOG_STREAM = LogStream(sys.stdout)
    sys.stdout = _LOG_STREAM
    sys.stderr = _LOG_STREAM
    return _LOG_STREAM


def _button(text: str, color: str | None = None, text_color: str | None = None) -> QPushButton:
    color = C_BG3 if color is None else color
    text_color = C_TEXT if text_color is None else text_color
    button = QPushButton(text)
    button.setCursor(Qt.PointingHandCursor)
    icon_name = {
        "Scanner Help": "help", "Trade Chat": "chat", "Recruitment Chat": "chat",
        "Login / Token": "key", "Refresh WFM profile": "refresh", "Settings": "settings",
        "Preset Help": "help", "Delete": "delete",
        "Delete selected": "delete", "Update selected": "check", "💬": "chat",
    }.get(text)
    if text.startswith("Review rewards ("):
        icon_name = "gift"
    if icon_name:
        button.setProperty("sidro_icon", icon_name)
        button.setIconSize(QSize(16, 16))
        if text == "💬":
            button.setText("")
            button.setAccessibleName("Show active buyers")
    button.setProperty("sidro_theme_background_role", _theme_role_for_color(color))
    button.setProperty("sidro_theme_text_role", _theme_role_for_color(text_color))
    button.setProperty("sidro_theme_background_fallback", str(color))
    button.setProperty("sidro_theme_text_fallback", str(text_color))
    button.setProperty("sidro_theme_managed", True)
    _THEMED_BUTTONS.add(button)
    _refresh_themed_button(button)
    return button


def _refresh_themed_button(button: QPushButton):
    if not button.property("sidro_theme_managed"):
        return
    palette = _active_theme_palette()
    background_role = button.property("sidro_theme_background_role")
    text_role = button.property("sidro_theme_text_role")
    background = palette.get(background_role, button.property("sidro_theme_background_fallback"))
    foreground = palette.get(text_role, button.property("sidro_theme_text_fallback"))
    top = QColor(background).lighter(110).name()
    border = C_BG3 if background_role in {"Background", "Panel", "Control", "Header"} else QColor(background).lighter(120).name()
    hover = QColor(background).lighter(128).name()
    padding = "0" if button.property("sidro_stepper") else "8px 10px"
    icon_name = button.property("sidro_icon")
    if icon_name:
        button.setIcon(_ui_icon(icon_name, foreground))
    button.setStyleSheet(
        f"""
        QPushButton {{
            background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1, stop: 0 {top}, stop: 1 {background});
            color: {foreground};
            border: 1px solid {border};
            border-radius: 4px;
            padding: {padding};
            font-weight: 600;
        }}
        QPushButton:hover {{ background: {hover}; border-color: {C_GOLD}; }}
        QPushButton:pressed {{ background: {QColor(background).darker(115).name()}; }}
        QPushButton:focus {{ border-color: {C_GOLD}; }}
        QPushButton:disabled {{ color: {C_MUTED}; background: {C_BG2}; border-color: {C_BG3}; }}
        """
    )


def _refresh_themed_buttons():
    for button in tuple(_THEMED_BUTTONS):
        _refresh_themed_button(button)


def _is_prime_item(display_name: str, url_name: str = "") -> bool:
    text = f" {display_name} {url_name} ".lower().replace("_", " ")
    return " prime " in text


def _save_ui_settings():
    data = {
        "alert_sound_enabled": bool(getattr(config, "ALERT_SOUND_ENABLED", True)),
        "alert_sound_style": str(getattr(config, "ALERT_SOUND_STYLE", "Random")),
        "alert_sound_volume": float(getattr(config, "ALERT_SOUND_VOLUME", 0.45)),
        "alert_sound_custom_path": str(getattr(config, "ALERT_SOUND_CUSTOM_PATH", "")),
        "wfm_ingame_name": str(getattr(config, "WFM_INGAME_NAME", "")),
        "ocr_name_aliases": sanitize_name_aliases(getattr(config, "OCR_NAME_ALIASES", {})),
        "min_listing_price": int(getattr(config, "MIN_LISTING_PRICE", 2)),
        "visual_notifications_enabled": bool(getattr(config, "VISUAL_NOTIFICATIONS_ENABLED", True)),
        "notification_corner": str(getattr(config, "NOTIFICATION_CORNER", "top_left")),
        "sidro_notification_mascot": bool(getattr(config, "SIDRO_NOTIFICATION_MASCOT", False)),
        "sidro_visual_character": str(getattr(config, "SIDRO_VISUAL_CHARACTER", "robot")),
        "sidro_welcome_visual_frequency": str(getattr(config, "SIDRO_WELCOME_VISUAL_FREQUENCY", "once_a_day")),
        "sidro_welcome_audio_enabled": bool(getattr(config, "SIDRO_WELCOME_AUDIO_ENABLED", True)),
        "update_check_frequency": getattr(config, "UPDATE_CHECK_FREQUENCY", "daily"),
        "sidro_welcome_visual_last_shown": str(getattr(config, "SIDRO_WELCOME_VISUAL_LAST_SHOWN", "")),
        "ui_font_family": str(getattr(config, "UI_FONT_FAMILY", "Segoe UI")),
        "ui_font_size": int(getattr(config, "UI_FONT_SIZE", 12)),
        "ui_table_text_alignment": str(getattr(config, "UI_TABLE_TEXT_ALIGNMENT", "left")),
        "ui_colors": dict(getattr(config, "UI_COLORS", {})),
        "trade_chat_messages": [
            str(message)
            for message in list(getattr(config, "TRADE_CHAT_MESSAGES", [""]) or [""])
        ],
        "recruitment_chat_messages": [
            str(message)
            for message in list(getattr(config, "RECRUITMENT_CHAT_MESSAGES", [""]) or [""])
        ],
        "ocr_engines": dict(getattr(config, "OCR_ENGINE_BY_CONTEXT", {})),
        "ocr_fallback_enabled": bool(getattr(config, "OCR_FALLBACK_ENABLED", False)),
        "ocr_fallback_engine": str(getattr(config, "OCR_FALLBACK_ENGINE", "rapidocr")),
        "ocr_fallback_min_score": int(getattr(config, "OCR_FALLBACK_MIN_SCORE", 85)),
        "ocr_settings_version": int(getattr(config, "OCR_SETTINGS_VERSION", 1)),
        "ducat_filter_enabled": bool(getattr(config, "DUCAT_FILTER_ENABLED", True)),
        "ducat_bulk_plat_values": {
            str(ducats): int(platinum)
            for ducats, platinum in dict(getattr(config, "DUCAT_BULK_PLAT_VALUES", {})).items()
        },
        "reward_price_check_enabled": bool(getattr(config, "REWARD_PRICE_CHECK_ENABLED", True)),
        "hotkeys": {
            "scan": str(getattr(config, "HOTKEY_SCAN", "ctrl+shift+s")),
            "inventory_scan": str(getattr(config, "HOTKEY_INVENTORY_SCAN", "ctrl+shift+i")),
            "inventory_bottom_scan": str(
                getattr(config, "HOTKEY_INVENTORY_BOTTOM_SCAN", "ctrl+shift+b")
            ),
            "trade_monitor": str(getattr(config, "HOTKEY_TRADE_MONITOR", "ctrl+shift+t")),
            "account_ui": str(getattr(config, "HOTKEY_ACCOUNT_UI", "ctrl+shift+m")),
            "reward_scan": str(getattr(config, "HOTKEY_REWARD_SCAN", "ctrl+shift+r")),
            "ocr_warmup": str(getattr(config, "HOTKEY_OCR_WARMUP", "ctrl+shift+o")),
            "quit": str(getattr(config, "HOTKEY_QUIT", "ctrl+shift+q")),
        },
    }
    tmp_path: Path | None = None
    try:
        config.APP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=config.APP_SETTINGS_PATH.parent,
            prefix=f".{config.APP_SETTINGS_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = Path(f.name)
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config.APP_SETTINGS_PATH)
        config.OCR_SETTINGS_NEEDS_SAVE = False
    except Exception as e:
        print(f"[Settings] Could not save {config.APP_SETTINGS_PATH}: {e}")
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _prepare_table(table: QTableWidget, widths: list[int], stretch_column: int | None = None):
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionsMovable(False)
    header = table.horizontalHeader()
    font_size = int(getattr(config, "UI_FONT_SIZE", 12))
    scale = max(1.0, font_size / 12)
    header.setSectionResizeMode(QHeaderView.Fixed)
    if stretch_column is not None and 0 <= stretch_column < len(widths):
        header.setSectionResizeMode(stretch_column, QHeaderView.Stretch if font_size <= 16 else QHeaderView.Interactive)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.verticalHeader().setDefaultSectionSize(max(38, int(getattr(config, "UI_FONT_SIZE", 12)) + 26))
    for i, width in enumerate(widths):
        table.setColumnWidth(i, round(width * scale))


def _table_text_alignment():
    value = str(getattr(config, "UI_TABLE_TEXT_ALIGNMENT", "left")).strip().lower()
    if value == "center":
        return Qt.AlignCenter
    if value == "right":
        return Qt.AlignRight | Qt.AlignVCenter
    return Qt.AlignLeft | Qt.AlignVCenter


def _table_item(text: object) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text))
    item.setTextAlignment(_table_text_alignment())
    return item


def _hotkey_choices() -> list[str]:
    keys = (
        [chr(code) for code in range(ord("a"), ord("z") + 1)]
        + [str(i) for i in range(10)]
        + [f"f{i}" for i in range(1, 13)]
        + ["space", "tab", "esc", "enter"]
    )
    modifiers = [
        "",
        "ctrl+",
        "shift+",
        "alt+",
        "ctrl+shift+",
        "ctrl+alt+",
        "shift+alt+",
        "ctrl+shift+alt+",
    ]
    return [f"{modifier}{key}" for modifier in modifiers for key in keys]


class NumberEditor(QWidget):
    valueChanged = Signal(int)

    def __init__(self, minimum: int, maximum: int, value: int):
        super().__init__()
        self.minimum = minimum
        self.maximum = maximum
        self.setObjectName("NumberEditor")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.minus = _button("-", C_BG3, C_TEXT)
        self.plus = _button("+", C_BG3, C_TEXT)
        self.edit = QLineEdit(str(value))
        self.applyAlignment()
        self.minus.setFixedWidth(24)
        self.plus.setFixedWidth(24)
        for button in (self.minus, self.plus):
            button.setProperty("sidro_stepper", True)
            button.setMinimumHeight(max(30, int(getattr(config, "UI_FONT_SIZE", 12)) + 14))
            _refresh_themed_button(button)
        self.edit.setMinimumWidth(32)
        self.edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.minus.clicked.connect(lambda: self.setValue(self.value() - 1))
        self.plus.clicked.connect(lambda: self.setValue(self.value() + 1))
        self.edit.editingFinished.connect(lambda: self.setValue(self.value()))
        layout.addWidget(self.minus)
        layout.addWidget(self.edit)
        layout.addWidget(self.plus)

    def value(self) -> int:
        try:
            return max(self.minimum, min(self.maximum, int(self.edit.text())))
        except ValueError:
            return self.minimum

    def setValue(self, value: int):
        value = max(self.minimum, min(self.maximum, int(value)))
        self.edit.setText(str(value))
        self.valueChanged.emit(value)

    def applyAlignment(self):
        self.edit.setAlignment(_table_text_alignment())


class CopyOnClickTextEdit(QTextEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setStyleSheet(
            f"""
            QTextEdit {{
                background: {C_BG3};
                color: {C_TEXT};
                border: 1px solid #4a4650;
                padding: 4px;
            }}
            """
        )

def _apply_app_style(app: QApplication):
    font_family = getattr(config, "UI_FONT_FAMILY", "Segoe UI")
    font_size = int(getattr(config, "UI_FONT_SIZE", 12))
    title_size = max(font_size + 3, 13)
    border = QColor(C_BG3).lighter(132).name()
    panel_top = QColor(C_BG2).lighter(110).name()
    app.setStyleSheet(
        f"""
        QWidget {{
            background: {C_BG};
            color: {C_TEXT};
            font-family: {font_family};
            font-size: {font_size}px;
        }}
        QMainWindow, QDialog {{ background: {C_BG}; }}
        QFrame#Header {{ background: {C_HEADER}; }}
        QFrame#Panel, QFrame#Toolbar, QFrame#InventoryStatus {{
            background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                stop: 0 {panel_top}, stop: 1 {C_BG2});
            border: 1px solid {border};
            border-radius: 5px;
        }}
        QWidget#ToolbarGroup, QWidget#InventoryPage, QWidget#NumberEditor {{ background: transparent; }}
        QLabel {{ background: transparent; }}
        QLabel#Title {{ color: {C_GOLD}; font-size: {title_size}px; font-weight: 700; }}
        QLabel#Muted {{ color: {C_MUTED}; }}
        QLabel#BrandTitle {{ color: {C_GOLD}; font-size: {font_size + 28}px; font-weight: 700; }}
        QLabel#BrandSubtitle {{ color: {C_GOLD}; font-size: {font_size + 1}px; }}
        QLabel#SelectionFeedback {{ color: {C_GOLD}; font-size: {font_size + 2}px; font-weight: 600; }}
        QPushButton[sidro_caption="true"] {{
            background: transparent; border: 0; border-radius: 3px; padding: 0;
        }}
        QPushButton[sidro_caption="true"]:hover,
        QPushButton[sidro_caption_hover="true"] {{ background: {C_BG3}; }}
        QPushButton[sidro_caption="true"]:pressed {{ background: {border}; }}
        QPushButton#CaptionClose:hover, QPushButton#CaptionClose:pressed {{ background: {C_RED}; }}
        QPushButton[sidro_caption="true"]:focus {{ border: 1px solid {C_GOLD}; }}
        QTableWidget {{
            background: {C_BG};
            alternate-background-color: {C_BG2};
            gridline-color: {C_BG3};
            border: 1px solid {border};
            selection-background-color: {C_BG3};
            selection-color: {C_TEXT};
        }}
        QTableWidget::item {{ padding: 6px 8px; }}
        QHeaderView::section {{
            background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                stop: 0 {QColor(C_BG3).lighter(110).name()}, stop: 1 {C_BG3});
            color: {C_GOLD};
            border: 0;
            border-right: 1px solid {border};
            border-bottom: 1px solid {border};
            padding: 10px 8px;
            font-weight: 600;
        }}
        QLabel#StatusBanner {{
            background: {C_BG2};
            border: 1px solid {border};
            border-left: 3px solid {C_GOLD};
            border-radius: 4px;
            padding: 7px 10px;
            color: {C_TEXT};
        }}
        QLabel#InventorySummary {{ color: {C_GOLD}; font-size: {font_size + 2}px; }}
        QFrame#Divider {{ background: {border}; border: none; }}
        QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background: {C_BG2};
            color: {C_TEXT};
            border: 1px solid {border};
            border-radius: 4px;
            padding: 7px 8px;
        }}
        QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border-color: {C_GOLD};
        }}
        QLineEdit:disabled, QComboBox:disabled {{ color: {C_MUTED}; background: {C_BG}; }}
        QWidget#NumberEditor QLineEdit {{
            background: {C_BG3}; border-radius: 0; padding: 4px 2px;
        }}
        QComboBox::drop-down {{ border: none; width: 20px; }}
        QComboBox QAbstractItemView, QListWidget {{
            background: {C_BG2}; color: {C_TEXT}; border: 1px solid {border};
            selection-background-color: {C_BG3}; selection-color: {C_GOLD};
        }}
        QListWidget::item {{ padding: 7px; }}
        QCheckBox {{ color: {C_TEXT}; spacing: 8px; background: transparent; }}
        QCheckBox::indicator {{ width: 18px; height: 18px; }}
        QScrollArea {{ border: none; background: transparent; }}
        QScrollBar:vertical {{ background: {C_BG}; width: 10px; margin: 0; }}
        QScrollBar:horizontal {{ background: {C_BG}; height: 10px; margin: 0; }}
        QScrollBar::handle {{ background: {border}; border-radius: 4px; min-width: 24px; min-height: 24px; }}
        QScrollBar::handle:hover {{ background: {C_MUTED}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        QTabWidget::pane {{
            border: 1px solid {border};
            border-top: 1px solid {C_GOLD};
            margin-top: 0;
        }}
        QTabBar::tab {{
            background: {C_BG3};
            color: {C_TEXT};
            border: 1px solid {border};
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            padding: 12px 24px;
            min-width: 120px;
            margin-right: 6px;
            font-weight: 600;
        }}
        QTabBar::tab:selected {{
            background: qlineargradient(x1: 0, y1: 0, x2: 0, y2: 1,
                stop: 0 {C_GOLD2}, stop: 1 {C_GOLD});
            color: {C_HEADER};
            border-color: {C_GOLD};
        }}
        QTabBar::tab:hover {{
            border-color: {C_GOLD};
        }}
        """
    )
    _refresh_themed_buttons()
    for widget in app.allWidgets():
        if isinstance(widget, QLabel) and widget.property("sidro_icon"):
            widget.setPixmap(_ui_icon(widget.property("sidro_icon"), C_GOLD).pixmap(widget.size()))
        if isinstance(widget, QTabWidget) and widget.property("sidro_dashboard_tabs"):
            widget.setTabIcon(0, _ui_icon("inventory", C_HEADER if widget.currentIndex() == 0 else C_TEXT))
        if isinstance(widget, QPushButton) and widget.property("sidro_caption_icon"):
            widget.setIcon(_ui_icon(widget.property("sidro_caption_icon"), C_TEXT))


class Toast(QFrame):
    COLORS = {
        "success": C_GREEN,
        "error": C_RED,
        "warning": "#E67E22",
        "info": C_BLUE,
    }

    def __init__(self, message: str, status: str, duration_ms: int):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        try:
            flags |= Qt.WindowTransparentForInput
        except AttributeError:
            pass
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setObjectName("ToastRoot")
        self.setStyleSheet(
            """
            QFrame#ToastRoot {
                background: transparent;
                border: none;
            }
            QLabel#ToastMascot {
                background: transparent;
            }
            """
        )

        screen = QApplication.primaryScreen().availableGeometry()
        mascot_enabled = bool(getattr(config, "SIDRO_NOTIFICATION_MASCOT", False))
        mascot_label: QLabel | None = None
        mascot_width = 0
        mascot_height = 0
        if mascot_enabled:
            pixmap = QPixmap(str(config.resource_path(_sidro_visual_asset())))
            if not pixmap.isNull():
                mascot_height = int(max(128, min(screen.height() * 0.2, 260)))
                mascot_width = int(mascot_height * 0.78)
                mascot_label = QLabel()
                mascot_label.setObjectName("ToastMascot")
                mascot_label.setFixedSize(mascot_width, mascot_height)
                mascot_label.setAlignment(Qt.AlignCenter)
                mascot_label.setAttribute(Qt.WA_TransparentForMouseEvents)
                mascot_label.setPixmap(
                    pixmap.scaled(
                        mascot_width,
                        mascot_height,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(8)

        panel = QFrame()
        panel.setObjectName("Panel")
        panel.setFixedSize(380, 76)
        panel_layout = QHBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 12, 0)
        bar = QFrame()
        bar.setFixedWidth(5)
        bar.setStyleSheet(f"background: {self.COLORS.get(status, C_BLUE)};")
        label = QLabel(message)
        label.setWordWrap(True)
        label.setFixedWidth(330)
        panel_layout.addWidget(bar)
        panel_layout.addWidget(label)

        corner = str(getattr(config, "NOTIFICATION_CORNER", "top_left"))
        if mascot_label is not None and "right" not in corner:
            root_layout.addWidget(mascot_label)
        root_layout.addWidget(panel, 0, Qt.AlignVCenter)
        if mascot_label is not None and "right" in corner:
            root_layout.addWidget(mascot_label)

        width = 380 + (mascot_width + 8 if mascot_label is not None else 0)
        height = max(76, mascot_height)
        self.resize(width, height)
        QTimer.singleShot(duration_ms, self.close)

    def place(self, index: int):
        screen = QApplication.primaryScreen().availableGeometry()
        margin = 24
        gap = 8
        offset = index * (self.height() + gap)
        corner = str(getattr(config, "NOTIFICATION_CORNER", "top_left"))
        if "right" in corner:
            x = screen.right() - self.width() - margin
        else:
            x = screen.left() + margin
        if "bottom" in corner:
            y = screen.bottom() - self.height() - margin - offset
        else:
            y = screen.top() + 32 + offset
        self.move(x, y)


class SidroGreeting(QFrame):
    def __init__(self, message: str, duration_ms: int = 6500):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        try:
            flags |= Qt.WindowTransparentForInput
        except AttributeError:
            pass
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setStyleSheet(
            f"""
            QFrame {{
                background: transparent;
                border: none;
            }}
            QLabel#GreetingSubtitle {{
                color: white;
                background: transparent;
                font-size: 30px;
                font-weight: 800;
                letter-spacing: 0;
            }}
            """
        )

        self._pixmap = QPixmap(str(config.resource_path(_sidro_visual_asset())))
        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setAttribute(Qt.WA_TransparentForMouseEvents)

        self.subtitle = QLabel(message, self)
        self.subtitle.setObjectName("GreetingSubtitle")
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setWordWrap(True)
        self.subtitle.setAttribute(Qt.WA_TransparentForMouseEvents)
        shadow = QGraphicsDropShadowEffect(self.subtitle)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 230))
        self.subtitle.setGraphicsEffect(shadow)

        QTimer.singleShot(duration_ms, self.close)

    def place_near(self, parent: QWidget):
        self.place_fullscreen()

    def place_fullscreen(self):
        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)
        self.image.setGeometry(0, 0, self.width(), self.height())
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                int(self.width() * 0.98),
                int(self.height() * 0.9),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self.image.setPixmap(scaled)
        else:
            self.image.setText(config.APP_NAME)

        subtitle_width = min(self.width() - 96, 1300)
        subtitle_height = 92
        subtitle_x = int((self.width() - subtitle_width) / 2)
        subtitle_y = self.height() - subtitle_height - 54
        self.subtitle.setGeometry(subtitle_x, subtitle_y, subtitle_width, subtitle_height)


class RewardPriceTag(QFrame):
    def __init__(
        self,
        label: str,
        price: int | None,
        match_score: int,
        x: int,
        y: int,
        duration_ms: int,
    ):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        price_text = "?" if price is None else f"{price}p"
        score = max(0, min(100, int(match_score or 0)))
        if score >= int(getattr(config, "REWARD_CONFIDENCE_OK_SCORE", 92)):
            confidence_color = C_GREEN
        elif score >= int(getattr(config, "REWARD_CONFIDENCE_WARN_SCORE", 82)):
            confidence_color = C_GOLD2
        else:
            confidence_color = C_RED
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        dot = QFrame()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background: {confidence_color}; border-radius: 6px;")
        price_label = QLabel(price_text)
        price_label.setAlignment(Qt.AlignCenter)
        price_label.setStyleSheet(f"color: {C_GOLD2}; font-size: 22px; font-weight: 800;")
        score_label = QLabel(f"{score}%")
        score_label.setObjectName("Muted")
        score_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(dot)
        top.addStretch()
        top.addWidget(price_label)
        top.addStretch()
        top.addWidget(score_label)
        name_label = QLabel(label)
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setWordWrap(True)
        name_label.setStyleSheet(f"color: {C_TEXT}; font-size: 14px;")
        name_label.setFixedWidth(170)
        layout.addLayout(top)
        layout.addWidget(name_label)
        self.resize(190, 72)
        self.move(x, y)
        QTimer.singleShot(duration_ms, self.close)


class RewardAlternateTag(QFrame):
    def __init__(self, alternates: list[dict], x: int, y: int, duration_ms: int):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        title = QLabel("Other reads")
        title.setObjectName("Muted")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color: {C_MUTED}; font-size: 13px; font-weight: 600;")
        layout.addWidget(title)
        for alt in alternates[: int(getattr(config, "REWARD_ANALYSIS_MAX_ALTERNATES", 3))]:
            name = str(alt.get("display_name", "")).strip()
            price = alt.get("price")
            price_text = "?" if price is None else f"{int(price)}p"
            score = int(alt.get("match_score", 0) or 0)
            count = int(alt.get("scan_count", 0) or 0)
            label = QLabel(
                f'<span style="color:{C_GOLD2}; font-weight:700">{html_escape(price_text)}</span>'
                f'&nbsp;&nbsp;{html_escape(name)}&nbsp;&nbsp;{score}% x{count}'
            )
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {C_TEXT}; font-size: 14px;")
            label.setFixedWidth(220)
            layout.addWidget(label)
        self.resize(230, 58 + 38 * max(0, min(len(alternates), 3) - 1))
        self.move(x, y)
        QTimer.singleShot(duration_ms, self.close)


class LogViewerDialog(QDialog):
    def __init__(self, stream: LogStream, parent=None):
        super().__init__(parent)
        self.stream = stream
        self.setWindowTitle("Logs")
        self.resize(900, 560)
        layout = QVBoxLayout(self)

        self.text = QTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(stream.current_text())
        self.text.moveCursor(QTextCursor.End)
        layout.addWidget(self.text)

        footer = QHBoxLayout()
        clear = _button("Clear")
        close = _button("Close", C_GREEN, "white")
        clear.clicked.connect(self._clear)
        close.clicked.connect(self.close)
        footer.addStretch()
        footer.addWidget(clear)
        footer.addWidget(close)
        layout.addLayout(footer)

        self.stream.text_written.connect(self._append)

    def _append(self, text: str):
        self.text.moveCursor(QTextCursor.End)
        self.text.insertPlainText(text)
        self.text.moveCursor(QTextCursor.End)

    def _clear(self):
        self.stream.clear()
        self.text.clear()


class ConfirmationDialog(QDialog):
    def __init__(
        self,
        matched_item: MatchedItem,
        price_info: PriceInfo,
        listing_price: int,
        quantity: int = 1,
        preview_image: QImage | None = None,
        existing_listing: ExistingListing | None = None,
    ):
        super().__init__()
        self.setWindowTitle("Review selected item")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.result_value = UserDecision.CANCELLED
        self.quantity = max(1, int(quantity))
        self.existing_listing = existing_listing
        self.selected_action = "SKIP"
        self.setObjectName("SnipReview")
        font_size = int(getattr(config, "UI_FONT_SIZE", 12))
        available = self.screen().availableGeometry()
        self.setMinimumWidth(min(max(560, 560 + (font_size - 12) * 20), available.width() - 40))
        control_height = max(36, font_size + 24)
        self.setStyleSheet(
            f"""
            QFrame#SnipCard {{
                background: {C_BG2};
                border: 1px solid {C_BG3};
                border-radius: 8px;
            }}
            QLabel#SnipTitle {{
                color: {C_GOLD};
                font-size: {font_size + 8}px;
                font-weight: 700;
            }}
            QLabel#SnipSection {{ font-weight: 600; }}
            QLabel#SnipPrice {{ font-size: {font_size + 6}px; font-weight: 600; }}
            QLabel#SnipSuggestion {{
                color: {C_GOLD};
                font-size: {font_size + 6}px;
                font-weight: 600;
            }}
            QLabel#SnipConfidence {{ color: {C_GOLD}; }}
            QComboBox, QLineEdit {{ border-radius: 4px; padding: 7px; }}
            QPushButton#SnipDetailsToggle {{
                background: transparent;
                color: {C_TEXT};
                border: none;
                padding: 4px 0;
                text-align: left;
            }}
            QPushButton#SnipDetailsToggle:hover {{ color: {C_GOLD}; }}
            QPushButton#SnipDetailsToggle:focus {{ border: 1px solid {C_GOLD}; }}
            QScrollArea#SnipScroll {{ background: transparent; border: none; }}
            QScrollBar:vertical {{ background: {C_BG}; width: 8px; margin: 0; }}
            QScrollBar::handle:vertical {{
                background: {C_BG3}; min-height: 24px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {C_MUTED}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
            """
        )
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)
        self.review_content = QWidget()
        layout = QVBoxLayout(self.review_content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        scroll = QScrollArea()
        scroll.setObjectName("SnipScroll")
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.review_content)
        self.review_scroll = scroll
        root_layout.addWidget(scroll, 1)

        item_card = QFrame()
        item_card.setObjectName("SnipCard")
        item_layout = QHBoxLayout(item_card)
        item_layout.setContentsMargins(16, 16, 16, 16)
        item_layout.setSpacing(16)
        has_preview = preview_image is not None and not preview_image.isNull()
        if has_preview:
            preview = QLabel()
            preview.setAlignment(Qt.AlignCenter)
            preview.setFixedSize(144, 88)
            preview.setPixmap(
                QPixmap.fromImage(preview_image).scaled(
                    144, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )
            preview.setToolTip("Expand Scan details below to inspect the captured image.")
            item_layout.addWidget(preview)

        item_text = QVBoxLayout()
        item_text.setSpacing(6)
        detected = QLabel("Identified item")
        detected.setObjectName("Muted")
        item_text.addWidget(detected)
        title = QLabel(matched_item.display_name)
        title.setObjectName("SnipTitle")
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        item_text.addWidget(title)
        confidence = QLabel(f"{matched_item.match_score}% match confidence")
        confidence.setObjectName("SnipConfidence")
        item_text.addWidget(confidence)
        item_layout.addLayout(item_text, 1)
        layout.addWidget(item_card)

        market_card = QFrame()
        market_card.setObjectName("SnipCard")
        market_layout = QGridLayout(market_card)
        market_layout.setContentsMargins(16, 12, 16, 12)
        market_layout.setHorizontalSpacing(16)
        market_layout.setVerticalSpacing(4)
        for column, (label, value, object_name) in enumerate((
            ("Lowest seller", price_info.lowest_sell_price, "SnipPrice"),
            ("Market suggestion", price_info.suggested_listing_price, "SnipSuggestion"),
        )):
            caption = QLabel(label)
            caption.setObjectName("Muted")
            amount = QLabel(f"{value}p")
            amount.setObjectName(object_name)
            market_layout.addWidget(caption, 0, column)
            market_layout.addWidget(amount, 1, column)
            market_layout.setColumnStretch(column, 1)
        layout.addWidget(market_card)

        listing_card = QFrame()
        listing_card.setObjectName("SnipCard")
        listing_layout = QVBoxLayout(listing_card)
        listing_layout.setContentsMargins(16, 16, 16, 16)
        listing_layout.setSpacing(12)
        heading = QLabel("Listing settings")
        heading.setObjectName("SnipSection")
        listing_layout.addWidget(heading)
        self.action_combo = QComboBox()
        if existing_listing is not None:
            self.action_combo.addItem("Skip existing listing", "SKIP")
            self.action_combo.addItem("Update existing listing", "UPDATE")
            existing_note = QLabel(
                f"Already listed: {existing_listing.quantity}x at {existing_listing.platinum}p"
                f"  ·  {'visible' if existing_listing.visible else 'hidden'}"
            )
        else:
            self.action_combo.addItem("Create new listing", "CREATE")
            self.action_combo.addItem("Skip this item", "SKIP")
            existing_note = QLabel("No existing listing found for this item.")
        existing_note.setObjectName("Muted")
        existing_note.setWordWrap(True)
        listing_layout.addWidget(existing_note)

        self.price_preset = QComboBox()
        if existing_listing is not None:
            self.price_preset.addItem("Current listing")
        self.price_preset.addItems(
            ["Most Common", "Most Common -1", "Quicksell", "Lowest +1p", "Lowest +2p", "Lowest +3p"]
        )
        self.quantity_editor = NumberEditor(1, 999, self.quantity)
        self.quantity_editor.setToolTip("Number of copies to include in this listing")
        initial_price = existing_listing.platinum if existing_listing is not None else listing_price
        self.price_editor = NumberEditor(
            config.MIN_LISTING_PRICE,
            99999,
            max(config.MIN_LISTING_PRICE, int(initial_price)),
        )
        self.price_editor.setToolTip("Price per copy, in platinum")
        for editor, name in ((self.quantity_editor, "Quantity"), (self.price_editor, "Price")):
            editor.layout().setSpacing(4)
            editor.edit.setMinimumWidth(64)
            editor.edit.setMaximumWidth(16777215)
            editor.edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            editor.edit.setAlignment(Qt.AlignCenter)
            editor.edit.setAccessibleName(name)
            editor.edit.setMinimumHeight(control_height)
            for button, operation in ((editor.minus, "Decrease"), (editor.plus, "Increase")):
                button.setFixedSize(control_height, control_height)
                button.setAutoDefault(False)
                button.setAccessibleName(f"{operation} {name.lower()}")
            editor.setFocusProxy(editor.edit)
        self.price_preset.currentTextChanged.connect(lambda _text: self._apply_price_preset(price_info))
        self.price_preset.setCurrentText("Current listing" if existing_listing is not None else "Most Common")

        fields = QGridLayout()
        fields.setHorizontalSpacing(16)
        fields.setVerticalSpacing(6)
        fields.setColumnStretch(0, 1)
        fields.setColumnStretch(1, 1)
        for row, column, text, widget in (
            (0, 0, "Action", self.action_combo),
            (0, 1, "Selling preset", self.price_preset),
            (3, 0, "Quantity", self.quantity_editor),
            (3, 1, "Price per copy (platinum)", self.price_editor),
        ):
            label = QLabel(text)
            label.setWordWrap(True)
            label.setBuddy(widget)
            widget.setAccessibleName(text)
            widget.setMinimumHeight(control_height)
            fields.addWidget(label, row, column)
            fields.addWidget(widget, row + 1, column)
        fields.setRowMinimumHeight(2, 6)
        listing_layout.addLayout(fields)
        self.listing_summary = QLabel()
        self.listing_summary.setWordWrap(True)
        listing_layout.addWidget(self.listing_summary)
        layout.addWidget(listing_card)

        self.details_toggle = QPushButton("▸ Scan details")
        self.details_toggle.setObjectName("SnipDetailsToggle")
        self.details_toggle.setCursor(Qt.PointingHandCursor)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAutoDefault(False)
        self.details_toggle.toggled.connect(self._toggle_scan_details)
        layout.addWidget(self.details_toggle)
        self.scan_details = QFrame()
        self.scan_details.setObjectName("SnipCard")
        details_layout = QVBoxLayout(self.scan_details)
        details_layout.setContentsMargins(16, 12, 16, 12)
        details_layout.setSpacing(10)
        if has_preview:
            full_preview = QLabel()
            full_preview.setAlignment(Qt.AlignCenter)
            full_preview.setPixmap(QPixmap.fromImage(preview_image).scaled(
                480, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation
            ))
            details_layout.addWidget(full_preview)
        details = QLabel(f"Name read as: {matched_item.raw_ocr}")
        details.setObjectName("Muted")
        details.setTextFormat(Qt.PlainText)
        details.setWordWrap(True)
        details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details.setMinimumWidth(0)
        details.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        details_layout.addWidget(details)
        self.scan_details.hide()
        layout.addWidget(self.scan_details)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.confirm_button = _button("Skip item", C_BG3, C_TEXT)
        cancel = _button("Cancel", C_BG3, C_TEXT)
        for button in (cancel, self.confirm_button):
            button.setMinimumHeight(max(40, control_height))
            button.setAutoDefault(False)
        cancel.setMinimumWidth(100)
        self.confirm_button.setMinimumWidth(220)
        self.confirm_button.clicked.connect(self._confirm)
        self.action_combo.currentIndexChanged.connect(self._update_action_button)
        self.quantity_editor.valueChanged.connect(self._update_listing_summary)
        self.price_editor.valueChanged.connect(self._update_listing_summary)
        self.quantity_editor.edit.textChanged.connect(self._update_listing_summary)
        self.price_editor.edit.textChanged.connect(self._update_listing_summary)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        buttons.addWidget(self.confirm_button)
        root_layout.addLayout(buttons)
        layout.addStretch()
        self._update_action_button()
        self.resize(self.minimumWidth(), 1)
        self._resize_review()
        self.quantity_editor.edit.setFocus()

    def _resize_review(self):
        self.review_content.layout().activate()
        margins = self.layout().contentsMargins()
        footer = self.layout().itemAt(1).sizeHint().height()
        height = (
            self.review_content.sizeHint().height() + margins.top() + margins.bottom()
            + self.layout().spacing() + footer
        )
        self.resize(self.width(), min(height, self.screen().availableGeometry().height() - 80))

    def _toggle_scan_details(self, expanded: bool):
        self.scan_details.setVisible(expanded)
        self.details_toggle.setText("▾ Scan details" if expanded else "▸ Scan details")
        self._resize_review()
        if expanded:
            self.review_scroll.ensureWidgetVisible(self.scan_details, 0, 12)

    def _update_listing_summary(self, _value=None):
        if self.action_combo.currentData() == "SKIP":
            self.listing_summary.setText("This item will be skipped.")
            return
        quantity = self.quantity_editor.value()
        price = self.price_editor.value()
        self.listing_summary.setText(
            f"{quantity} {'copy' if quantity == 1 else 'copies'}  ·  {price}p each  ·  {quantity * price:,}p total"
        )

    def _apply_price_preset(self, price_info: PriceInfo):
        mode = self.price_preset.currentText()
        if mode == "Current listing" and self.existing_listing is not None:
            price = self.existing_listing.platinum
        elif mode == "Quicksell":
            price = price_info.lowest_sell_price
        elif mode == "Most Common -1":
            price = price_info.suggested_listing_price - 1
        elif mode == "Lowest +1p":
            price = price_info.lowest_sell_price + 1
        elif mode == "Lowest +2p":
            price = price_info.lowest_sell_price + 2
        elif mode == "Lowest +3p":
            price = price_info.lowest_sell_price + 3
        else:
            price = price_info.suggested_listing_price
        self.price_editor.setValue(max(config.MIN_LISTING_PRICE, int(price)))

    def _update_action_button(self, _index: int = -1):
        action = self.action_combo.currentData()
        if action == "UPDATE":
            self.confirm_button.setText("Update existing listing")
            background, foreground = C_BLUE, "white"
        elif action == "CREATE":
            self.confirm_button.setText("Create listing")
            background, foreground = C_GREEN, "white"
        else:
            self.confirm_button.setText("Skip item")
            background, foreground = C_BG3, C_TEXT
        self.confirm_button.setProperty("sidro_theme_background_role", _theme_role_for_color(background))
        self.confirm_button.setProperty("sidro_theme_text_role", _theme_role_for_color(foreground))
        self.confirm_button.setProperty("sidro_theme_background_fallback", background)
        self.confirm_button.setProperty("sidro_theme_text_fallback", foreground)
        _refresh_themed_button(self.confirm_button)
        self._update_listing_summary()

    def _confirm(self):
        self.quantity = self.quantity_editor.value()
        self.selected_action = str(self.action_combo.currentData() or "SKIP")
        self.result_value = UserDecision.CONFIRMED
        self.accept()


class ScreenSelectionOverlay(QDialog):
    """Fullscreen drag-to-select overlay backed by a frozen screen image."""

    def __init__(self, screen, screenshot: QPixmap):
        super().__init__(None)
        self.screen = screen
        self.screenshot = screenshot
        self.selected_image: QImage | None = None
        self._origin: QPoint | None = None
        self._selection = QRect()
        self.setWindowTitle("Select one item")
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.setModal(True)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.screenshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 105))

        selection = self._selection.normalized().intersected(self.rect())
        if selection.width() > 0 and selection.height() > 0:
            ratio = self.screenshot.devicePixelRatio()
            crop_rect = QRect(
                round(selection.x() * ratio),
                round(selection.y() * ratio),
                round(selection.width() * ratio),
                round(selection.height() * ratio),
            )
            painter.drawPixmap(selection, self.screenshot, crop_rect)
            painter.setPen(QPen(QColor(C_BLUE), 2))
            painter.drawRect(selection)

        banner = QRect(0, 0, self.width(), 52)
        painter.fillRect(banner, QColor(16, 16, 22, 225))
        painter.setPen(QColor(C_TEXT))
        painter.setFont(QFont("Segoe UI", 14, QFont.Bold))
        painter.drawText(
            banner,
            Qt.AlignCenter,
            "Drag around one item — include its name and quantity badge.  •  Esc cancels",
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.reject()
            return
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._selection = QRect(self._origin, self._origin)
            self.update()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        self._selection = QRect(self._origin, event.position().toPoint()).normalized()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        self._selection = QRect(self._origin, event.position().toPoint()).normalized()
        self._origin = None
        selection = self._selection.intersected(self.rect())
        if selection.width() < 24 or selection.height() < 18:
            self._selection = QRect()
            self.update()
            return

        ratio = self.screenshot.devicePixelRatio()
        crop_rect = QRect(
            round(selection.x() * ratio),
            round(selection.y() * ratio),
            round(selection.width() * ratio),
            round(selection.height() * ratio),
        )
        self.selected_image = self.screenshot.toImage().copy(crop_rect)
        self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class InventoryReviewDialog(QDialog):
    def __init__(
        self,
        items: list[InventoryItem],
        price_map: dict[str, PriceInfo],
        existing_listing_map: dict[str, ExistingListing] | None = None,
        scan_title: str = "Inventory scan",
        saved_ducat_items: list[dict] | None = None,
        failed_positions: list[tuple[int, int]] | None = None,
        preselect_single_item: bool = False,
        parent: QWidget | None = None,
        queued_reward_rows: list[dict] | None = None,
    ):
        super().__init__(parent)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.items = items
        self.price_map = price_map
        self.existing_listing_map = existing_listing_map or {}
        self.is_reward_queue_review = queued_reward_rows is not None
        self.saved_ducat_items = saved_ducat_items or []
        self.failed_positions = failed_positions or []
        self.selected_items: list[InventoryItem] = []
        self.queued_reward_rows = [dict(row) for row in queued_reward_rows or []]
        self.removed_queued_rewards: dict[str, dict] = {}
        self.setWindowTitle("Reward Queue Review" if self.is_reward_queue_review else "Inventory Review")
        if self.is_reward_queue_review:
            available = self.screen().availableGeometry() if self.screen() is not None else None
            if available is None:
                dialog_width, dialog_height = 1060, 740
            else:
                dialog_width = max(720, min(1060, available.width() - 64))
                dialog_height = max(560, available.height() - 48)
            self.resize(dialog_width, dialog_height)
            self.setMinimumSize(min(860, dialog_width), min(620, dialog_height))
        else:
            self.resize(940, 760)

        layout = QVBoxLayout(self)
        self.tabs = None if self.is_reward_queue_review else QTabWidget()
        if self.tabs is not None:
            layout.addWidget(self.tabs, 1)

        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        header = QHBoxLayout()
        detected_count = len(items) if self.is_reward_queue_review else len(items) + len(self.saved_ducat_items)
        title = QLabel(f"{scan_title} - {detected_count} items detected")
        title.setObjectName("Title")
        self.mode = QComboBox()
        self.mode.addItems(["Most Common", "Most Common -1", "Quicksell", "Lowest +1p", "Lowest +2p", "Lowest +3p"])
        self.mode.currentTextChanged.connect(self._apply_prices)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(QLabel("Selling Preset:"))
        header.addWidget(self.mode)
        review_layout.addLayout(header)

        if self.is_reward_queue_review:
            listing_note = QLabel(
                "Queued reward quantities are added to the WFM quantity. A successful listing action consumes those queued units; skipped or failed items remain. You can also remove rewards from Sidro locally."
            )
            listing_note.setObjectName("Muted")
            listing_note.setWordWrap(True)
            review_layout.addWidget(listing_note)

        self.table = QTableWidget(0, 10 if self.is_reward_queue_review else 8)
        self.table.setHorizontalHeaderLabels(
            ["List", "Reward", "Queued", "WFM Qty", "Lowest", "Common", "Highest", "Price", "WFM Action", "Remove locally"]
            if self.is_reward_queue_review
            else ["Use", "Item", "Quantity", "Lowest", "Most Common", "Highest", "Price", "Action"]
        )
        self.table.setAlternatingRowColors(True)
        _prepare_table(
            self.table,
            [42, 240, 62, 96, 62, 75, 66, 96, 96, 110]
            if self.is_reward_queue_review
            else [42, 340, 96, 70, 100, 78, 96, 92],
        )
        review_layout.addWidget(self.table, 1)
        self._build_rows()
        if preselect_single_item and self.table.rowCount() == 1:
            self.table.cellWidget(0, 0).setChecked(True)
        if not items and not self.is_reward_queue_review:
            empty_note = QLabel("No items need one-by-one listing. Review the Saved for Ducats tab.")
            empty_note.setObjectName("Muted")
            review_layout.insertWidget(1, empty_note)
        if self.failed_positions:
            missed = ", ".join(
                f"row {row + 1}, col {col + 1}"
                for col, row in self.failed_positions
            )
            missed_note = QLabel(f"Empty or unreadable slots ({len(self.failed_positions)}): {missed}")
            missed_note.setObjectName("Muted")
            missed_note.setWordWrap(True)
            review_layout.insertWidget(1, missed_note)

        selection_bar = QHBoxLayout()
        selection_bar.addWidget(QLabel("Select:"))
        self.selection_feedback = QLabel("0 selected")
        self.selection_feedback.setObjectName("Muted")
        all_btn = _button("All")
        prime_btn = _button("Prime")
        non_prime_btn = _button("Non-Prime")
        unlisted_btn = _button("Select unlisted")
        none_btn = _button("None")
        update_all = _button("UPDATE", C_BLUE, "white")
        delete_all = _button("DELETE", C_RED, "white")
        skip_all = _button("SKIP")
        if self.is_reward_queue_review:
            delete_all.hide()
        all_btn.clicked.connect(lambda: self._set_all(True))
        prime_btn.clicked.connect(lambda _checked=False: self._select_prime())
        non_prime_btn.clicked.connect(lambda _checked=False: self._select_non_prime())
        unlisted_btn.clicked.connect(self._select_unlisted)
        none_btn.clicked.connect(lambda: self._set_all(False))
        update_all.clicked.connect(lambda: self._set_all_actions("UPDATE"))
        delete_all.clicked.connect(lambda: self._set_all_actions("DELETE"))
        skip_all.clicked.connect(lambda: self._set_all_actions("SKIP"))
        selection_bar.addWidget(all_btn)
        selection_bar.addWidget(prime_btn)
        selection_bar.addWidget(non_prime_btn)
        selection_bar.addWidget(unlisted_btn)
        selection_bar.addWidget(none_btn)
        selection_bar.addWidget(self.selection_feedback)
        selection_bar.addWidget(QLabel("Set WFM actions:" if self.is_reward_queue_review else "Set all actions:"))
        selection_bar.addWidget(update_all)
        if not self.is_reward_queue_review:
            selection_bar.addWidget(delete_all)
        selection_bar.addWidget(skip_all)
        review_layout.addLayout(selection_bar)
        if self.is_reward_queue_review:
            queue_actions = QHBoxLayout()
            queue_actions.addWidget(QLabel("Local reward queue actions do not change Warframe.market."))
            queue_actions.addStretch()
            remove_rewards = _button("Remove selected locally", C_RED, "white")
            remove_rewards.clicked.connect(self._remove_selected_queued_rewards)
            queue_actions.addWidget(remove_rewards)
            review_layout.addLayout(queue_actions)
            layout.addWidget(review_page, 1)
        else:
            self.tabs.addTab(review_page, f"Listings ({len(items)})")

        if self.saved_ducat_items and not self.is_reward_queue_review:
            ducat_page = QWidget()
            ducat_layout = QVBoxLayout(ducat_page)
            note = QLabel("These detected items were saved for bulk ducat trading instead of one-by-one listings.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            ducat_layout.addWidget(note)
            table = QTableWidget(0, 5)
            table.setHorizontalHeaderLabels(["Item", "Ducats", "Lowest", "Bulk value", "Reason"])
            table.setAlternatingRowColors(True)
            _prepare_table(table, [320, 80, 80, 100, 260])
            for item in self.saved_ducat_items:
                row = table.rowCount()
                table.insertRow(row)
                table.setItem(row, 0, _table_item(str(item.get("display_name", ""))))
                table.setItem(row, 1, _table_item(f"{item.get('ducats', '?')}d"))
                table.setItem(row, 2, _table_item(f"{item.get('lowest', '?')}p"))
                table.setItem(row, 3, _table_item(f"{item.get('bulk_value', '?')}p"))
                table.setItem(row, 4, _table_item(str(item.get("reason", ""))))
            ducat_layout.addWidget(table, 1)
            self.tabs.addTab(ducat_page, f"Saved for Ducats ({len(self.saved_ducat_items)})")

        footer = QHBoxLayout()
        footer.addStretch()
        confirm = _button("Apply selected" if items else "Done", C_GREEN, "white")
        cancel = _button("Close", C_BG3)
        confirm.clicked.connect(self._confirm)
        cancel.clicked.connect(self.reject)
        footer.addWidget(confirm)
        footer.addWidget(cancel)
        layout.addLayout(footer)

    def _remove_selected_queued_rewards(self):
        selected = {}
        for row in range(self.table.rowCount()):
            if not self.table.cellWidget(row, 9).isChecked():
                continue
            url_name = self.table.verticalHeaderItem(row).text()
            reward = next((item for item in self.queued_reward_rows if item.get("url_name") == url_name), None)
            if reward is not None:
                selected[url_name] = reward
        if not selected:
            return
        answer = QMessageBox.question(
            self,
            "Remove local rewards?",
            f"Remove {sum(int(row.get('quantity', 0)) for row in selected.values())} reward(s) from Sidro's local queue? This does not change Warframe.market listings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.removed_queued_rewards.update(selected)
        self.queued_reward_rows = [
            row for row in self.queued_reward_rows if row.get("url_name") not in selected
        ]
        for row in reversed(range(self.table.rowCount())):
            if self.table.verticalHeaderItem(row).text() in selected:
                self.table.removeRow(row)
        self.selection_feedback.setText(f"0 of {self.table.rowCount()} selected")

    def _build_rows(self):
        self.table.setRowCount(0)
        for item in self.items:
            price = self.price_map.get(item.url_name)
            if price is None:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            existing = self.existing_listing_map.get(item.url_name)

            use = _StyledCheckBox()
            use.setChecked(False)
            use.toggled.connect(self._update_selection_feedback)
            self.table.setCellWidget(row, 0, use)
            name = item.display_name
            if not self.is_reward_queue_review:
                col, grid_row = item.grid_position
                name += f"  [row {grid_row + 1}, col {col + 1}]"
            if existing:
                name += f"  [listed {existing.quantity}x @ {existing.platinum}p]"
            self.table.setItem(row, 1, _table_item(name))
            if self.is_reward_queue_review:
                queued_quantity = max(1, int(getattr(item, "_queued_reward_quantity", item.quantity)))
                self.table.setItem(row, 2, _table_item(str(queued_quantity)))
                qty = NumberEditor(1, 999, item.quantity)
                self.table.setCellWidget(row, 3, qty)
                self.table.setItem(row, 4, _table_item(f"{price.lowest_sell_price}p"))
                self.table.setItem(row, 5, _table_item(f"{price.suggested_listing_price}p"))
                self.table.setItem(row, 6, _table_item(f"{price.highest_sell_price}p"))
                sell_price = NumberEditor(
                    config.MIN_LISTING_PRICE,
                    99999,
                    existing.platinum if existing else price.suggested_listing_price,
                )
                self.table.setCellWidget(row, 7, sell_price)
                action = QComboBox()
                if getattr(item, "_saved_for_ducats", False):
                    action.addItem("SAVED FOR DUCATS")
                    action.setEnabled(False)
                    use.setEnabled(False)
                    qty.setEnabled(False)
                    sell_price.setEnabled(False)
                else:
                    action.addItems(["SKIP", "UPDATE"] if existing else ["UPDATE"])
                    action.setCurrentText("SKIP" if existing else "UPDATE")
                self.table.setCellWidget(row, 8, action)
                self.table.setCellWidget(row, 9, _StyledCheckBox())
            else:
                qty = NumberEditor(1, 999, item.quantity)
                self.table.setCellWidget(row, 2, qty)
                self.table.setItem(row, 3, _table_item(f"{price.lowest_sell_price}p"))
                self.table.setItem(row, 4, _table_item(f"{price.suggested_listing_price}p"))
                self.table.setItem(row, 5, _table_item(f"{price.highest_sell_price}p"))
                sell_price = NumberEditor(
                    config.MIN_LISTING_PRICE,
                    99999,
                    existing.platinum if existing else price.suggested_listing_price,
                )
                self.table.setCellWidget(row, 6, sell_price)
                action = QComboBox()
                action.addItems(["SKIP", "UPDATE", "DELETE"] if existing else ["UPDATE"])
                action.setCurrentText("SKIP" if existing else "UPDATE")
                self.table.setCellWidget(row, 7, action)
            self.table.setVerticalHeaderItem(row, QTableWidgetItem(item.url_name))
        self._update_selection_feedback()

    def _apply_prices(self):
        mode = self.mode.currentText()
        for row in range(self.table.rowCount()):
            url = self.table.verticalHeaderItem(row).text()
            info = self.price_map[url]
            if mode == "Quicksell":
                price = info.lowest_sell_price
            elif mode == "Most Common -1":
                price = info.suggested_listing_price - 1
            elif mode == "Lowest +1p":
                price = info.lowest_sell_price + 1
            elif mode == "Lowest +2p":
                price = info.lowest_sell_price + 2
            elif mode == "Lowest +3p":
                price = info.lowest_sell_price + 3
            else:
                price = info.suggested_listing_price
            price_column = 7 if self.is_reward_queue_review else 6
            self.table.cellWidget(row, price_column).setValue(max(config.MIN_LISTING_PRICE, int(price)))

    def _set_all(self, value: bool):
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, 0).setChecked(value)
        self._update_selection_feedback()

    def _select_prime(self):
        items_by_url = {item.url_name: item for item in self.items}
        for row in range(self.table.rowCount()):
            url = self.table.verticalHeaderItem(row).text()
            item = items_by_url.get(url)
            display_name = item.display_name if item is not None else ""
            self.table.cellWidget(row, 0).setChecked(_is_prime_item(display_name, url))
        self._update_selection_feedback()

    def _select_non_prime(self):
        items_by_url = {item.url_name: item for item in self.items}
        for row in range(self.table.rowCount()):
            url = self.table.verticalHeaderItem(row).text()
            item = items_by_url.get(url)
            display_name = item.display_name if item is not None else ""
            self.table.cellWidget(row, 0).setChecked(not _is_prime_item(display_name, url))
        self._update_selection_feedback()

    def _update_selection_feedback(self, _checked: bool = False):
        if not hasattr(self, "selection_feedback"):
            return
        selected = sum(
            bool(self.table.cellWidget(row, 0).isChecked())
            for row in range(self.table.rowCount())
        )
        total = self.table.rowCount()
        self.selection_feedback.setText(f"{selected} of {total} selected")

    def _select_unlisted(self):
        for row in range(self.table.rowCount()):
            url = self.table.verticalHeaderItem(row).text()
            is_unlisted = url not in self.existing_listing_map
            self.table.cellWidget(row, 0).setChecked(is_unlisted)
            action = self.table.cellWidget(row, 8 if self.is_reward_queue_review else 7)
            if is_unlisted and action.findText("UPDATE") >= 0:
                action.setCurrentText("UPDATE")

    def _set_all_actions(self, action_text: str):
        for row in range(self.table.rowCount()):
            action = self.table.cellWidget(row, 8 if self.is_reward_queue_review else 7)
            if action.findText(action_text) >= 0:
                action.setCurrentText(action_text)
                if action_text in {"UPDATE", "DELETE"}:
                    self.table.cellWidget(row, 0).setChecked(True)
            elif action_text == "DELETE":
                self.table.cellWidget(row, 0).setChecked(False)

    def _confirm(self):
        by_url = {item.url_name: item for item in self.items}
        selected: list[InventoryItem] = []
        delete_count = 0
        for row in range(self.table.rowCount()):
            use = self.table.cellWidget(row, 0).isChecked()
            action_column = 8 if self.is_reward_queue_review else 7
            action = self.table.cellWidget(row, action_column).currentText()
            if not use or action not in {"UPDATE", "DELETE"}:
                continue
            url = self.table.verticalHeaderItem(row).text()
            item = by_url[url]
            item._listing_action = action
            item.quantity = self.table.cellWidget(row, 3 if self.is_reward_queue_review else 2).value()
            item._override_price = self.table.cellWidget(row, 7 if self.is_reward_queue_review else 6).value()
            existing = self.existing_listing_map.get(url)
            if existing:
                item._existing_order_id = existing.order_id
                item._item_id = existing.item_id
            if action == "DELETE":
                delete_count += 1
            selected.append(item)
        if delete_count:
            answer = QMessageBox.question(
                self,
                "Delete selected listings?",
                f"This will permanently delete {delete_count} selected warframe.market sell listing(s). Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.selected_items = selected
        self.accept()


class SavedDucatDialog(QDialog):
    def __init__(self, rows: list[dict], title: str = "Saved for ducats"):
        super().__init__()
        self.setWindowTitle(title)
        self.resize(760, 360)
        layout = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setObjectName("Title")
        layout.addWidget(heading)
        note = QLabel("These items were not opened for one-by-one listing because their market value is at or below your ducat bulk value.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["Item", "Ducats", "Lowest", "Bulk value", "Action"])
        table.setAlternatingRowColors(True)
        _prepare_table(table, [320, 80, 80, 100, 120])
        layout.addWidget(table)

        for item in rows:
            row = table.rowCount()
            table.insertRow(row)
            table.setItem(row, 0, _table_item(str(item.get("display_name", ""))))
            table.setItem(row, 1, _table_item(f"{item.get('ducats', '?')}d"))
            table.setItem(row, 2, _table_item(f"{item.get('lowest', '?')}p"))
            table.setItem(row, 3, _table_item(f"{item.get('bulk_value', '?')}p"))
            table.setItem(row, 4, _table_item("SAVEDUCAT"))

        footer = QHBoxLayout()
        close = _button("Close", C_GREEN, "white")
        close.clicked.connect(self.accept)
        footer.addStretch()
        footer.addWidget(close)
        layout.addLayout(footer)


class ChatMessagesDialog(QDialog):
    def __init__(
        self,
        messages: list[str],
        on_changed: Callable[[list[str]], None],
        on_copy: Callable[[str, str, str], bool],
        chat_name: str = "Trade",
        parent=None,
    ):
        super().__init__(parent)
        self.messages = [str(message) for message in messages]
        if not self.messages:
            self.messages = [""]
        self.on_changed = on_changed
        self.on_copy = on_copy
        self.chat_name = str(chat_name)
        self.editors: list[CopyOnClickTextEdit] = []
        self.setWindowTitle(f"{self.chat_name} Chat")
        available = self.screen().availableGeometry() if self.screen() is not None else None
        if available is None:
            dialog_width, dialog_height = 1060, 740
        else:
            dialog_width = max(720, min(1060, available.width() - 64))
            dialog_height = max(560, available.height() - 48)
        self.resize(dialog_width, dialog_height)
        self.setMinimumSize(min(860, dialog_width), min(620, dialog_height))
        self._maximum_dialog_height = available.height() - 32 if available is not None else dialog_height
        self._base_dialog_height = dialog_height

        layout = QVBoxLayout(self)
        title = QLabel(f"{self.chat_name} Chat")
        title.setObjectName("Title")
        layout.addWidget(title)

        note = QLabel(f"Write reusable {self.chat_name.lower()}-chat messages here. Edits save automatically. Use a message's copy button to copy it.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.content = QWidget()
        self.content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.message_layout = QVBoxLayout(self.content)
        self.message_layout.setContentsMargins(0, 0, 0, 0)
        self.message_layout.setSpacing(10)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        add = _button("Add chatbox", C_BLUE, "white")
        add.clicked.connect(self._add_message)
        close = _button("Close", C_BG3)
        close.clicked.connect(self.accept)
        footer.addWidget(add)
        footer.addStretch()
        footer.addWidget(close)
        layout.addLayout(footer)

        self._rebuild()

    def _rebuild(self):
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.editors = []
        for index, message in enumerate(self.messages):
            self._add_row(index, message)
        self.message_layout.addStretch()

    def _add_row(self, index: int, message: str):
        frame = QFrame()
        frame.setObjectName("Panel")
        row_layout = QVBoxLayout(frame)
        row_layout.setContentsMargins(10, 10, 10, 10)
        row_layout.setSpacing(6)

        header = QHBoxLayout()
        label = QLabel(f"Chatbox {index + 1}")
        label.setObjectName("Title")
        copy = _button("💬 Copy", C_GREEN, "white")
        delete = _button("Delete", C_RED, "white")
        copy.clicked.connect(lambda _checked=False, i=index: self._copy_message(i))
        delete.clicked.connect(lambda _checked=False, i=index: self._delete_message(i))
        header.addWidget(label)
        header.addStretch()
        header.addWidget(copy)
        header.addWidget(delete)
        row_layout.addLayout(header)

        editor = CopyOnClickTextEdit()
        editor.setPlainText(message)
        editor.setMinimumHeight(0)
        editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        editor.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor.textChanged.connect(lambda e=editor: self._resize_editor(e))
        editor.textChanged.connect(lambda i=index, e=editor: self._update_message(i, e.toPlainText()))
        self.editors.append(editor)
        row_layout.addWidget(editor)
        self.message_layout.addWidget(frame)
        self._resize_editor(editor)

    @staticmethod
    def _resize_editor(editor: CopyOnClickTextEdit):
        document = editor.document()
        document.setTextWidth(max(1, editor.viewport().width()))
        line_height = editor.fontMetrics().lineSpacing()
        line_count = max(3, int(document.size().height() / line_height + 0.5))
        margins = editor.contentsMargins()
        padding = 14
        editor.setFixedHeight(line_count * line_height + margins.top() + margins.bottom() + padding)

    def _update_message(self, index: int, text: str):
        if index < 0 or index >= len(self.messages):
            return
        self.messages[index] = text
        self.on_changed(list(self.messages))

    def _copy_message(self, index: int):
        if index < 0 or index >= len(self.messages):
            self.on_copy("", f"{self.chat_name} chat copied.", f"No {self.chat_name.lower()}-chat message to copy.")
            return
        self.on_copy(
            self.messages[index],
            f"{self.chat_name} chat {index + 1} copied to clipboard.",
            f"{self.chat_name} chat {index + 1} is empty.",
        )

    def _add_message(self):
        self.messages.append("")
        self.on_changed(list(self.messages))
        self._rebuild()
        self._grow_for_chatbox()

    def _grow_for_chatbox(self):
        if len(self.messages) <= 1:
            return
        row = self.message_layout.itemAt(self.message_layout.count() - 2)
        row_widget = row.widget() if row is not None else None
        if row_widget is None:
            return
        required_height = self.height() + row_widget.sizeHint().height() + self.message_layout.spacing()
        target_height = min(self._maximum_dialog_height, max(self._base_dialog_height, required_height))
        if target_height > self.height():
            self.resize(self.width(), target_height)

    def _delete_message(self, index: int):
        if index < 0 or index >= len(self.messages):
            return
        del self.messages[index]
        if not self.messages:
            self.messages.append("")
        self.on_changed(list(self.messages))
        self._rebuild()


class TokenDialog(QDialog):
    message_ready = Signal(str)

    def __init__(
        self,
        on_save_token: Callable[[str], str],
    ):
        super().__init__()
        self.on_save_token = on_save_token
        self.setWindowTitle("warframe.market Login")
        self.resize(620, 360)
        layout = QVBoxLayout(self)
        title = QLabel("warframe.market login token")
        title.setObjectName("Title")
        layout.addWidget(title)
        help_btn = _button("How to extract JWT?", C_BG3, C_GOLD)
        help_btn.clicked.connect(self._show_help)
        layout.addWidget(help_btn)
        self.status = QLabel(
            "Paste only the warframe.market JWT cookie value. Accepted formats: JWT=eyJ... or eyJ... "
            "Do not paste your whole cookie list."
        )
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        self.message_ready.connect(self._handle_message)
        layout.addWidget(self.status)
        self.token = QTextEdit()
        self.token.setPlaceholderText("Paste JWT cookie value here")
        layout.addWidget(self.token)
        row = QHBoxLayout()
        save = _button("Save pasted JWT", C_GREEN, "white")
        save.clicked.connect(self._save)
        row.addWidget(save)
        layout.addLayout(row)

    def _run(self, fn: Callable[[], str]):
        self.status.setText("Working...")

        def worker():
            try:
                msg = fn()
            except Exception as e:
                msg = f"ERROR: {e}"
            self.message_ready.emit(msg)

        threading.Thread(target=worker, daemon=True).start()

    def _save(self):
        self._run(lambda: self.on_save_token(self.token.toPlainText()))

    def _handle_message(self, message: str):
        self.status.setText(message)
        if message.startswith("Token saved and authenticated"):
            QTimer.singleShot(800, self.accept)

    def _show_help(self):
        QMessageBox.information(
            self,
            "How to extract JWT",
            "1. Open warframe.market in your browser and log in.\n"
            "2. Press F12 to open Developer Tools.\n"
            "3. Open the Network tab.\n"
            "4. Filter by Fetch/XHR.\n"
            "5. Update one of your market listings or refresh your profile/listings.\n"
            "6. Click the request to api.warframe.market.\n"
            "7. Open Headers.\n"
            "8. Find Request Headers -> Cookie.\n"
            "9. Copy only the JWT value. You may paste either JWT=eyJ... or just eyJ...\n\n"
            "Do not share this token. It acts like a login session.",
        )


class FirstLaunchDialog(QDialog):
    def __init__(self, open_settings: Callable[[], None], parent=None):
        super().__init__(parent)
        self.open_settings = open_settings
        self.setWindowTitle(f"{config.APP_NAME} first launch")
        self.resize(620, 430)
        layout = QVBoxLayout(self)
        title = QLabel(f"Welcome to {config.APP_NAME}")
        title.setObjectName("Title")
        layout.addWidget(title)
        body = QLabel(
            "Recommended first setup:\n\n"
            "1. Paste your warframe.market JWT token in Settings -> Account.\n"
            "2. Enter your exact Warframe in-game name for trade monitoring.\n"
            "3. Keep HDR off while scanning. HDR is unsupported for 1.0 because it hurts OCR contrast and can trigger Windows compositor flicker.\n"
            "4. OCR defaults: bundled PaddleOCR for inventory; RapidOCR PP-OCRv6 small for trade and rewards.\n"
            "5. Review hotkeys before running scans in-game."
        )
        body.setWordWrap(True)
        layout.addWidget(body)
        footer = QHBoxLayout()
        settings = _button("Open Settings", C_BLUE, "white")
        close = _button("Continue", C_GREEN, "white")
        settings.clicked.connect(self._open_settings)
        close.clicked.connect(self.accept)
        footer.addStretch()
        footer.addWidget(settings)
        footer.addWidget(close)
        layout.addLayout(footer)

    def _open_settings(self):
        self.accept()
        self.open_settings()


class SupportReminderDialog(QDialog):
    def __init__(self, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Support Sidro")
        self.setModal(False)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        text = QLabel(message)
        text.setWordWrap(True)
        layout.addWidget(text)
        link = QLabel(f'<a href="{SUPPORT_URL}" style="color: {C_BLUE};">{SUPPORT_URL}</a>')
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        dismiss = _button("Dismiss", C_BG3, C_TEXT)
        dismiss.clicked.connect(self.accept)
        layout.addWidget(dismiss, 0, Qt.AlignRight)


class SettingsDialog(QDialog):
    ocr_pack_install_finished = Signal(str, str, bool)

    COLOR_OPTIONS = [
        ("Window background", ("Background",), "Main app and dialog background."),
        ("Panels and tables", ("Panel", "Header"), "Tables, grouped panels, and table headers."),
        ("Inputs and controls", ("Control",), "Text boxes, dropdowns, small buttons, and inputs."),
        ("Accent / headings", ("Accent", "Accent hover"), "Gold titles, section labels, and emphasis."),
        ("Main text", ("Text",), "Normal readable text."),
        ("Secondary text", ("Muted text",), "Muted helper/status text."),
        ("Success buttons", ("Success",), "Positive actions such as update/list/save."),
        ("Danger buttons", ("Danger",), "Delete/cancel/destructive actions."),
        ("Info buttons", ("Info",), "Scan/refresh/action buttons."),
    ]

    HOTKEY_FIELDS = [
        ("Select one item (snip)", "HOTKEY_SCAN"),
        ("Inventory scan (6x3)", "HOTKEY_INVENTORY_SCAN"),
        ("Inventory scan (bottom 6)", "HOTKEY_INVENTORY_BOTTOM_SCAN"),
        ("Trade monitor", "HOTKEY_TRADE_MONITOR"),
        ("Account manager", "HOTKEY_ACCOUNT_UI"),
        ("Reward price check", "HOTKEY_REWARD_SCAN"),
        ("Load / unload OCRs", "HOTKEY_OCR_WARMUP"),
        ("Quit app", "HOTKEY_QUIT"),
    ]

    OCR_FIELDS = [
        ("Single item scan", "single_scan"),
        ("Inventory scan (6x3)", "inventory_6x3"),
        ("Inventory scan (bottom 6)", "inventory_bottom6"),
        ("Trade monitor", "trade_monitor"),
        ("Reward prices / Live Reward Overlay", "reward_prices"),
    ]

    OCR_ENGINE_LABELS = [
        ("RapidOCR", "rapidocr"),
        ("PaddleOCR", "paddleocr"),
    ]

    def __init__(
        self,
        parent=None,
        on_hotkeys_changed: Callable[[], None] | None = None,
        on_ocr_changed: Callable[[], None] | None = None,
        on_save_settings_token: Callable[[str], str] | None = None,
    ):
        super().__init__(parent)
        self.on_hotkeys_changed = on_hotkeys_changed
        self.on_ocr_changed = on_ocr_changed
        self.on_save_settings_token = on_save_settings_token or (lambda _token: "")
        self.setWindowTitle("Settings")
        available = self.screen().availableGeometry() if self.screen() is not None else None
        if available is None:
            dialog_width, dialog_height = 1060, 740
        else:
            dialog_width = max(720, min(1060, available.width() - 64))
            dialog_height = max(560, min(740, available.height() - 80))
        self.resize(dialog_width, dialog_height)
        self.setMinimumSize(min(860, dialog_width), min(620, dialog_height))
        self._initial_wfm_token = str(getattr(config, "WFM_TOKEN", "") or "").strip()
        self.color_buttons: dict[str, QPushButton] = {}
        self.hotkey_boxes: dict[str, QComboBox] = {}
        self.ocr_pack_labels: dict[str, QLabel] = {}
        self.ducat_spins: dict[int, NumberEditor] = {}
        self._pending = self._snapshot_settings()
        self._initial_hotkeys = dict(self._pending["hotkeys"])
        self._initial_ocr_engines = dict(self._pending["ocr_engines"])
        self._initial_ocr_fallback = (
            bool(self._pending["ocr_fallback_enabled"]),
            str(self._pending["ocr_fallback_engine"]).strip().lower(),
            int(self._pending["ocr_fallback_min_score"]),
        )
        self.ocr_pack_install_finished.connect(self._finish_ocr_pack_install)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        tabs = QTabWidget()
        tabs.setObjectName("SettingsTabs")
        tabs.setUsesScrollButtons(True)
        tabs.setStyleSheet(
            "QTabBar::tab { min-width: 72px; padding: 8px 10px; }"
        )
        outer.addWidget(tabs)

        def make_tab(label: str) -> QVBoxLayout:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(10)
            scroll.setWidget(content)
            short_labels = {
                "Notifications": "Alerts",
                "Sidro Visual": "Visuals",
                "Ducat saver": "Ducats",
            }
            index = tabs.addTab(scroll, short_labels.get(label, label))
            tabs.setTabToolTip(index, label)
            return layout

        ui_layout = make_tab("UI Appearance")
        title = QLabel("UI Appearance")
        title.setObjectName("Title")
        ui_layout.addWidget(title)

        font_grid = QGridLayout()
        font_grid.setColumnStretch(1, 1)
        font_grid.addWidget(QLabel("Font:"), 0, 0)
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self._pending["ui_font_family"]))
        self.font_combo.currentFontChanged.connect(self._set_font)
        font_grid.addWidget(self.font_combo, 0, 1)
        font_grid.addWidget(QLabel("Size:"), 0, 2)
        self.font_size = NumberEditor(9, 24, int(self._pending["ui_font_size"]))
        self.font_size.valueChanged.connect(self._set_font_size)
        font_grid.addWidget(self.font_size, 0, 3)
        font_grid.addWidget(QLabel("Text center:"), 1, 0)
        self.table_alignment = QComboBox()
        self.table_alignment.addItem("Left", "left")
        self.table_alignment.addItem("Center", "center")
        self.table_alignment.addItem("Right", "right")
        current_alignment = str(self._pending["ui_table_text_alignment"]).strip().lower()
        if current_alignment not in ("left", "center", "right"):
            current_alignment = "left"
        self.table_alignment.setCurrentText(current_alignment.title())
        self.table_alignment.currentIndexChanged.connect(
            lambda _index: self._set_table_text_alignment(self.table_alignment.currentData())
        )
        font_grid.addWidget(self.table_alignment, 1, 1)
        ui_layout.addLayout(font_grid)
        font_note = QLabel("You can use any font installed on your computer.")
        font_note.setObjectName("Muted")
        font_note.setWordWrap(True)
        ui_layout.addWidget(font_note)

        account_layout = make_tab("Account")
        account_title = QLabel("Account")
        account_title.setObjectName("Title")
        account_layout.addWidget(account_title)
        account_grid = QGridLayout()
        account_grid.setColumnStretch(1, 1)
        account_grid.addWidget(QLabel("Warframe name:"), 0, 0)
        self.ingame_name = QLineEdit(str(self._pending["wfm_ingame_name"]))
        self.ingame_name.setPlaceholderText("Your exact in-game name; used by trade and reward scanners")
        self.ingame_name.editingFinished.connect(self._set_ingame_name)
        account_grid.addWidget(self.ingame_name, 0, 1)
        account_grid.addWidget(QLabel("Backup JWT token:"), 1, 0)
        token_box = QHBoxLayout()
        self.wfm_token = QLineEdit(str(self._pending["wfm_token"]))
        self.wfm_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.wfm_token.setPlaceholderText("JWT=eyJ... or eyJ...")
        self.wfm_token.editingFinished.connect(self._set_wfm_token)
        token_box.addWidget(self.wfm_token, 1)
        self.show_token = _StyledCheckBox("Show token")
        self.show_token.toggled.connect(self._toggle_token_visibility)
        token_box.addWidget(self.show_token)
        account_grid.addLayout(token_box, 1, 1)
        token_note = QLabel(f"Stored in {config.ENV_PATH}. Hidden here to avoid accidental screenshots. Token backups are private credentials; never share them.")
        token_note.setObjectName("Muted")
        token_note.setWordWrap(True)
        account_grid.addWidget(token_note, 2, 1)
        backup_row = QHBoxLayout()
        export_backup = _button("Export settings/token backup", C_BLUE, "white")
        import_backup = _button("Import settings/token backup", C_BG3, C_TEXT)
        export_backup.clicked.connect(self._export_backup)
        import_backup.clicked.connect(self._import_backup)
        backup_row.addWidget(export_backup)
        backup_row.addWidget(import_backup)
        account_grid.addLayout(backup_row, 3, 1)
        account_layout.addLayout(account_grid)

        account_layout.addStretch()

        pricing_layout = make_tab("Pricing")
        pricing_title = QLabel("Pricing")
        pricing_title.setObjectName("Title")
        pricing_layout.addWidget(pricing_title)
        pricing_grid = QGridLayout()
        pricing_grid.setColumnStretch(1, 1)
        pricing_grid.addWidget(QLabel("Minimum platinum per listing:"), 0, 0)
        self.min_listing_price = NumberEditor(
            1,
            999,
            int(self._pending["min_listing_price"]),
        )
        self.min_listing_price.valueChanged.connect(self._set_min_listing_price)
        pricing_grid.addWidget(self.min_listing_price, 0, 1)
        pricing_layout.addLayout(pricing_grid)
        pricing_note = QLabel("Sidro will never price a listing below this platinum amount.")
        pricing_note.setObjectName("Muted")
        pricing_note.setWordWrap(True)
        pricing_layout.addWidget(pricing_note)
        pricing_layout.addStretch()

        ocr_layout = make_tab("OCR")
        ocr_title = QLabel("OCR")
        ocr_title.setObjectName("Title")
        ocr_layout.addWidget(ocr_title)
        ocr_note = QLabel(
            "Choose one OCR engine for all scans. RapidOCR is the default and is bundled. PaddleOCR is an optional install."
        )
        ocr_note.setObjectName("Muted")
        ocr_note.setWordWrap(True)
        ocr_layout.addWidget(ocr_note)
        ocr_help = _button("OCR Information Help", C_BG3, C_TEXT)
        ocr_help.clicked.connect(self._show_ocr_information_help)
        ocr_layout.addWidget(ocr_help, 0, Qt.AlignLeft)
        ocr_grid = QGridLayout()
        ocr_grid.setColumnStretch(1, 1)
        label_by_value = {value: label for label, value in self.OCR_ENGINE_LABELS}
        engine_by_context = dict(self._pending["ocr_engines"])
        self.ocr_backend = QComboBox()
        for engine_label, engine_value in self.OCR_ENGINE_LABELS:
            self.ocr_backend.addItem(engine_label, engine_value)
        self._set_combo_data(self.ocr_backend, self._ocr_selection_for_contexts(engine_by_context))
        self.ocr_backend.currentIndexChanged.connect(self._set_all_ocr_engines)
        ocr_grid.addWidget(QLabel("OCR engine:"), 0, 0)
        ocr_grid.addWidget(self.ocr_backend, 0, 1)
        recommended_label = QLabel("RapidOCR is ready to use. Install PaddleOCR below before selecting it.")
        recommended_label.setWordWrap(True)
        recommended_label.setObjectName("Muted")
        ocr_grid.addWidget(recommended_label, 0, 2)
        ocr_layout.addLayout(ocr_grid)

        aliases_title = QLabel("My name corrections")
        aliases_title.setObjectName("Title")
        ocr_layout.addWidget(aliases_title)
        self.name_alias_account = QLabel()
        self.name_alias_account.setWordWrap(True)
        ocr_layout.addWidget(self.name_alias_account)
        aliases_note = QLabel(
            "Add known OCR spellings of your name, one per line (up to 20). "
            "Trade and reward scans accept these exact spellings, ignoring letter case. "
            "Corrections are saved separately for each Warframe account."
        )
        aliases_note.setObjectName("Muted")
        aliases_note.setWordWrap(True)
        ocr_layout.addWidget(aliases_note)
        self.name_aliases = QTextEdit()
        self.name_aliases.setAcceptRichText(False)
        self.name_aliases.setFixedHeight(96)
        self.name_aliases.setAccessibleName("Accepted OCR spellings of my name")
        self.name_aliases.setPlaceholderText("Use this settings menu to correct OCR results if needed.\nEnter one accepted spelling of your name per line.")
        self.name_aliases.setToolTip("Enter complete spellings, up to 64 characters each. Leave empty to use only your real name.")
        ocr_layout.addWidget(self.name_aliases)
        self._load_name_alias_editor()
        self.ingame_name.textChanged.connect(self._on_alias_account_changed)

        fallback_title = QLabel("Fallback")
        fallback_title.setObjectName("Title")
        ocr_layout.addWidget(fallback_title)
        fallback_grid = QGridLayout()
        fallback_grid.setColumnStretch(1, 1)
        self.ocr_fallback_enabled = _StyledCheckBox("Retry missed inventory cells with another OCR (slower)")
        self.ocr_fallback_enabled.setChecked(bool(self._pending["ocr_fallback_enabled"]))
        self.ocr_fallback_enabled.toggled.connect(self._set_ocr_fallback_enabled)
        fallback_grid.addWidget(self.ocr_fallback_enabled, 0, 0, 1, 3)
        fallback_grid.addWidget(QLabel("Fallback OCR:"), 1, 0)
        self.ocr_fallback_engine = QComboBox()
        for engine_label, engine_value in self.OCR_ENGINE_LABELS:
            self.ocr_fallback_engine.addItem(engine_label, engine_value)
        current_fallback = str(self._pending["ocr_fallback_engine"]).strip().lower()
        self.ocr_fallback_engine.setCurrentText(label_by_value.get(current_fallback, "RapidOCR"))
        self.ocr_fallback_engine.currentIndexChanged.connect(
            lambda _index: self._set_ocr_fallback_engine(self.ocr_fallback_engine.currentData())
        )
        fallback_grid.addWidget(self.ocr_fallback_engine, 1, 1)
        fallback_grid.addWidget(QLabel("Only accept fallback score ≥"), 2, 0)
        self.ocr_fallback_score = NumberEditor(
            0,
            100,
            int(self._pending["ocr_fallback_min_score"]),
        )
        self.ocr_fallback_score.valueChanged.connect(self._set_ocr_fallback_min_score)
        fallback_grid.addWidget(self.ocr_fallback_score, 2, 1)
        ocr_layout.addLayout(fallback_grid)

        packs_title = QLabel("Optional OCR packs")
        packs_title.setObjectName("Title")
        ocr_layout.addWidget(packs_title)
        packs_note = QLabel(
            f"RapidOCR is bundled. PaddleOCR is optional. Install / Repair saves packs under {config.OCR_PACKS_DIR}\\<backend>. Test checks model loading and inference, not screenshot accuracy."
        )
        packs_note.setObjectName("Muted")
        packs_note.setWordWrap(True)
        ocr_layout.addWidget(packs_note)
        pack_grid = QGridLayout()
        pack_grid.setColumnStretch(1, 1)
        pack_info = {
            "rapidocr": ("RapidOCR", "RapidOCR 3.9.2 / PP-OCRv6 small, bundled for fast scanning."),
            "paddleocr": ("PaddleOCR", "PaddleOCR 3.7.0 / PaddlePaddle 3.3.1, optional; install separately."),
        }
        for row_idx, (backend, (label, description)) in enumerate(pack_info.items()):
            grid_row = row_idx * 2
            pack_grid.addWidget(QLabel(label), grid_row, 0)
            status = QLabel("")
            status.setObjectName("Muted")
            self.ocr_pack_labels[backend] = status
            pack_grid.addWidget(status, grid_row, 1)
            install = _button("Install / Repair", C_BLUE, "white")
            install.clicked.connect(lambda _checked=False, b=backend: self._install_ocr_pack(b))
            folder = _button("Open folder", C_BG3, C_TEXT)
            folder.clicked.connect(lambda _checked=False, b=backend: self._open_ocr_pack_folder(b))
            pack_grid.addWidget(install, grid_row, 2)
            pack_grid.addWidget(folder, grid_row, 3)
            test = _button("Test", C_BG3, C_TEXT)
            test.clicked.connect(lambda _checked=False, b=backend: self._test_ocr_pack(b))
            pack_grid.addWidget(test, grid_row, 4)
            desc = QLabel(description)
            desc.setObjectName("Muted")
            desc.setWordWrap(True)
            pack_grid.addWidget(desc, grid_row + 1, 1, 1, 3)
        ocr_layout.addLayout(pack_grid)
        self._refresh_ocr_pack_status()
        ocr_layout.addStretch()

        notify_layout = make_tab("Notifications")
        notify_title = QLabel("Notifications")
        notify_title.setObjectName("Title")
        notify_layout.addWidget(notify_title)

        notify_grid = QGridLayout()
        notify_grid.setColumnStretch(2, 1)
        self.sound_check = _StyledCheckBox("Sound alerts")
        self.sound_check.setChecked(bool(self._pending["alert_sound_enabled"]))
        self.sound_check.toggled.connect(self._set_sound_enabled)
        notify_grid.addWidget(self.sound_check, 0, 0)
        notify_grid.addWidget(QLabel("Sound:"), 0, 1)
        self.sound_style = QComboBox()
        self.sound_style.addItems(["Random", *generated_styles(), "Custom File"])
        self.sound_style.setCurrentText(str(self._pending["alert_sound_style"]))
        self.sound_style.currentTextChanged.connect(self._set_sound_style)
        notify_grid.addWidget(self.sound_style, 0, 2)
        custom_sound = _button("Sound file", C_BG3, C_TEXT)
        custom_sound.clicked.connect(self._pick_custom_sound)
        notify_grid.addWidget(custom_sound, 0, 3)
        preview_sound = _button("Preview", C_BG3, C_TEXT)
        preview_sound.clicked.connect(self._preview_sound)
        notify_grid.addWidget(preview_sound, 0, 4)
        notify_grid.addWidget(QLabel("Volume:"), 0, 5)
        self.sound_volume = QSlider(Qt.Horizontal)
        self.sound_volume.setRange(0, 100)
        self.sound_volume.setFixedWidth(100)
        self.sound_volume.setValue(int(float(self._pending["alert_sound_volume"]) * 100))
        self.sound_volume.valueChanged.connect(lambda value: self._set_sound_volume(value / 100))
        notify_grid.addWidget(self.sound_volume, 0, 6)

        self.visual_check = _StyledCheckBox("Visual notifications")
        self.visual_check.setChecked(bool(self._pending["visual_notifications_enabled"]))
        self.visual_check.toggled.connect(self._set_visual_notifications)
        notify_grid.addWidget(self.visual_check, 1, 0)
        notify_grid.addWidget(QLabel("Corner:"), 1, 1)
        self.corner_combo = QComboBox()
        self.corner_combo.addItems(["top_left", "top_right", "bottom_left", "bottom_right"])
        self.corner_combo.setCurrentText(str(self._pending["notification_corner"]))
        self.corner_combo.currentTextChanged.connect(self._set_notification_corner)
        notify_grid.addWidget(self.corner_combo, 1, 2)
        notify_layout.addLayout(notify_grid)
        voice_note = QLabel("Sidro Voice is generated locally with Windows speech synthesis and robotic post-processing.")
        voice_note.setObjectName("Muted")
        voice_note.setWordWrap(True)
        notify_layout.addWidget(voice_note)
        notify_layout.addStretch()

        assistant_layout = make_tab("Sidro Visual")
        assistant_title = QLabel("Sidro Visual")
        assistant_title.setObjectName("Title")
        assistant_layout.addWidget(assistant_title)
        assistant_note = QLabel(
            "Controls whether Sidro character pictures appear beside visual notifications."
        )
        assistant_note.setObjectName("Muted")
        assistant_note.setWordWrap(True)
        assistant_layout.addWidget(assistant_note)
        self.mascot_check = _StyledCheckBox("Show Sidro assistant pictures")
        self.mascot_check.setChecked(bool(self._pending.get("sidro_notification_mascot", False)))
        self.mascot_check.toggled.connect(self._set_notification_mascot)
        assistant_layout.addWidget(self.mascot_check)
        visual_grid = QGridLayout()
        visual_grid.setColumnStretch(1, 1)
        visual_grid.addWidget(QLabel("Character:"), 0, 0)
        self.sidro_visual_combo = QComboBox()
        for visual_key, (label, _filename) in SIDRO_VISUALS.items():
            self.sidro_visual_combo.addItem(label, visual_key)
        self._set_combo_data(
            self.sidro_visual_combo,
            str(self._pending.get("sidro_visual_character", "robot")),
        )
        self.sidro_visual_combo.currentIndexChanged.connect(
            lambda _index: self._set_sidro_visual_character(self.sidro_visual_combo.currentData())
        )
        visual_grid.addWidget(self.sidro_visual_combo, 0, 1)
        assistant_layout.addLayout(visual_grid)
        self.sidro_visual_preview = QLabel()
        self.sidro_visual_preview.setAlignment(Qt.AlignCenter)
        self.sidro_visual_preview.setMinimumHeight(220)
        self.sidro_visual_preview.setObjectName("Panel")
        assistant_layout.addWidget(self.sidro_visual_preview)
        self._update_sidro_visual_preview()
        visual_grid.addWidget(QLabel("Sidro Welcome Visual Frequency:"), 1, 0)
        self.sidro_welcome_frequency_combo = QComboBox()
        for label, value in (
            ("Only once a day", "once_a_day"),
            ("Show every time after launch", "every_launch"),
            ("Don't show after launch", "never"),
        ):
            self.sidro_welcome_frequency_combo.addItem(label, value)
        self._set_combo_data(
            self.sidro_welcome_frequency_combo,
            str(self._pending.get("sidro_welcome_visual_frequency", "once_a_day")),
        )
        self.sidro_welcome_frequency_combo.currentIndexChanged.connect(
            lambda _index: self._set_sidro_welcome_visual_frequency(
                self.sidro_welcome_frequency_combo.currentData()
            )
        )
        visual_grid.addWidget(self.sidro_welcome_frequency_combo, 1, 1)
        self.sidro_welcome_audio_check = _StyledCheckBox("Play welcome message audio after launch")
        self.sidro_welcome_audio_check.setChecked(
            bool(self._pending.get("sidro_welcome_audio_enabled", True))
        )
        self.sidro_welcome_audio_check.toggled.connect(self._set_sidro_welcome_audio_enabled)
        assistant_layout.addWidget(self.sidro_welcome_audio_check)
        assistant_layout.addStretch()

        ducat_layout = make_tab("Ducat saver")
        ducat_title = QLabel("Ducat saver")
        ducat_title.setObjectName("Title")
        ducat_layout.addWidget(ducat_title)
        ducat_note = QLabel(
            "Sidro will skip one-by-one listings when an item's market price is at or below your ducat-to-platinum value."
        )
        ducat_note.setObjectName("Muted")
        ducat_note.setWordWrap(True)
        ducat_layout.addWidget(ducat_note)
        self.ducat_check = _StyledCheckBox("Skip one-by-one listings at/below bulk ducat value")
        self.ducat_check.setChecked(bool(self._pending["ducat_filter_enabled"]))
        self.ducat_check.toggled.connect(self._set_ducat_filter_enabled)
        ducat_layout.addWidget(self.ducat_check)

        ducat_grid = QGridLayout()
        ducat_grid.setColumnStretch(1, 1)
        ducat_values = dict(self._pending["ducat_bulk_plat_values"])
        for row_idx, ducats in enumerate((15, 25, 45, 65, 100)):
            ducat_grid.addWidget(QLabel(f"{ducats} ducats ->"), row_idx, 0)
            spin = NumberEditor(0, 999, int(ducat_values.get(ducats, 0)))
            spin.valueChanged.connect(lambda value, d=ducats: self._set_ducat_value(d, value))
            self.ducat_spins[ducats] = spin
            ducat_grid.addWidget(spin, row_idx, 1)
        ducat_layout.addLayout(ducat_grid)
        ducat_layout.addStretch()

        color_layout = ui_layout
        color_title = QLabel("Colors")
        color_title.setObjectName("Title")
        color_layout.addWidget(color_title)
        color_note = QLabel("Choose a swatch to change that color. Save & Close applies changes across Sidro.")
        color_note.setObjectName("Muted")
        color_note.setWordWrap(True)
        color_layout.addWidget(color_note)
        colors = dict(self._pending["ui_colors"])
        color_grid = QGridLayout()
        color_grid.setContentsMargins(0, 4, 0, 0)
        color_grid.setHorizontalSpacing(10)
        color_grid.setVerticalSpacing(10)
        for row_idx, (label, internal_keys, description) in enumerate(self.COLOR_OPTIONS):
            card = QFrame()
            card.setObjectName("Panel")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 9, 10, 9)
            card_layout.setSpacing(6)
            title = QLabel(label)
            title.setObjectName("Title")
            card_layout.addWidget(title)
            role = QLabel(description)
            role.setObjectName("Muted")
            role.setWordWrap(True)
            card_layout.addWidget(role)
            primary_key = internal_keys[0]
            btn = _button(colors.get(primary_key, DEFAULT_UI_COLORS[primary_key]))
            btn.setMinimumHeight(38)
            btn.setToolTip(f"{description} Click to choose a color.")
            btn.clicked.connect(lambda _checked=False, k=label: self._pick_color(k))
            self.color_buttons[label] = btn
            self._update_color_button(label, colors.get(primary_key, DEFAULT_UI_COLORS[primary_key]))
            card_layout.addWidget(btn)
            grid_row, grid_col = divmod(row_idx, 3)
            color_grid.addWidget(card, grid_row, grid_col)
        for col in range(3):
            color_grid.setColumnStretch(col, 1)
        color_layout.addLayout(color_grid)
        reset = _button("Reset appearance", C_BG3, C_TEXT)
        reset.clicked.connect(self._reset)
        color_layout.addWidget(reset, 0, Qt.AlignLeft)
        color_layout.addStretch()

        hotkey_layout = make_tab("Hotkeys")
        hotkey_title = QLabel("Hotkeys")
        hotkey_title.setObjectName("Title")
        hotkey_layout.addWidget(hotkey_title)
        choices = _hotkey_choices()
        hotkey_grid = QGridLayout()
        for row_idx, (label, attr) in enumerate(self.HOTKEY_FIELDS):
            hotkey_grid.addWidget(QLabel(label), row_idx, 0)
            box = QComboBox()
            box.addItems(choices)
            current = str(self._pending["hotkeys"].get(attr, getattr(config, attr, "")))
            if current and box.findText(current) < 0:
                box.insertItem(0, current)
            box.setCurrentText(current)
            box.currentTextChanged.connect(lambda value, a=attr: self._set_hotkey(a, value))
            self.hotkey_boxes[attr] = box
            hotkey_grid.addWidget(box, row_idx, 1)
        hotkey_layout.addLayout(hotkey_grid)
        hotkey_layout.addStretch()

        updates_layout = make_tab("Updates")
        updates_title = QLabel("Sidro updates")
        updates_title.setObjectName("Title")
        updates_layout.addWidget(updates_title)
        updates_layout.addWidget(QLabel(f"Installed version: {config.APP_VERSION}"))
        updates_layout.addWidget(QLabel("Check for updates automatically"))
        self.update_frequency_combo = QComboBox()
        for label, value in (("Daily", "daily"), ("Weekly", "weekly"), ("Monthly", "monthly"), ("Never", "never")):
            self.update_frequency_combo.addItem(label, value)
        self._set_combo_data(self.update_frequency_combo, self._pending.get("update_check_frequency", "daily"))
        updates_layout.addWidget(self.update_frequency_combo)
        updates_note = QLabel("Checks run at startup when due: daily, every 7 days, or every 30 days. Never disables automatic checks. Installation restarts the app and preserves your settings.")
        updates_note.setWordWrap(True)
        updates_layout.addWidget(updates_note)
        check_updates = _button("Check for updates", C_BLUE, "white")
        def check_updates_now():
            from update_ui import check_for_updates
            check_for_updates()
        check_updates.clicked.connect(check_updates_now)
        updates_layout.addWidget(check_updates)
        updates_layout.addStretch()

        footer = QHBoxLayout()
        close = _button("Save && Close", C_GREEN, "white")
        close.clicked.connect(self.accept)
        footer.addStretch()
        footer.addWidget(close)
        outer.addLayout(footer)

    def _snapshot_settings(self) -> dict:
        settings = {
            "alert_sound_enabled": bool(getattr(config, "ALERT_SOUND_ENABLED", True)),
            "alert_sound_style": str(getattr(config, "ALERT_SOUND_STYLE", "Random")),
            "alert_sound_volume": float(getattr(config, "ALERT_SOUND_VOLUME", 0.45)),
            "alert_sound_custom_path": str(getattr(config, "ALERT_SOUND_CUSTOM_PATH", "")),
            "wfm_ingame_name": str(getattr(config, "WFM_INGAME_NAME", "")),
            "ocr_name_aliases": sanitize_name_aliases(getattr(config, "OCR_NAME_ALIASES", {})),
            "wfm_token": str(getattr(config, "WFM_TOKEN", "")),
            "min_listing_price": int(getattr(config, "MIN_LISTING_PRICE", 2)),
            "visual_notifications_enabled": bool(getattr(config, "VISUAL_NOTIFICATIONS_ENABLED", True)),
            "notification_corner": str(getattr(config, "NOTIFICATION_CORNER", "top_left")),
            "sidro_notification_mascot": bool(getattr(config, "SIDRO_NOTIFICATION_MASCOT", False)),
            "sidro_visual_character": str(getattr(config, "SIDRO_VISUAL_CHARACTER", "robot")),
            "sidro_welcome_visual_frequency": str(getattr(config, "SIDRO_WELCOME_VISUAL_FREQUENCY", "once_a_day")),
            "sidro_welcome_audio_enabled": bool(getattr(config, "SIDRO_WELCOME_AUDIO_ENABLED", True)),
            "update_check_frequency": getattr(config, "UPDATE_CHECK_FREQUENCY", "daily"),
            "ui_font_family": str(getattr(config, "UI_FONT_FAMILY", "Segoe UI")),
            "ui_font_size": int(getattr(config, "UI_FONT_SIZE", 12)),
            "ui_table_text_alignment": str(getattr(config, "UI_TABLE_TEXT_ALIGNMENT", "left")),
            "ui_colors": dict(getattr(config, "UI_COLORS", {}) or {}),
            "ocr_engines": dict(getattr(config, "OCR_ENGINE_BY_CONTEXT", {}) or {}),
            "ocr_fallback_enabled": bool(getattr(config, "OCR_FALLBACK_ENABLED", False)),
        "ocr_fallback_engine": str(getattr(config, "OCR_FALLBACK_ENGINE", "rapidocr")),
            "ocr_fallback_min_score": int(getattr(config, "OCR_FALLBACK_MIN_SCORE", 85)),
            "ducat_filter_enabled": bool(getattr(config, "DUCAT_FILTER_ENABLED", True)),
            "ducat_bulk_plat_values": {
                int(ducats): int(platinum)
                for ducats, platinum in dict(getattr(config, "DUCAT_BULK_PLAT_VALUES", {}) or {}).items()
            },
            "hotkeys": {
                attr: str(getattr(config, attr, ""))
                for _label, attr in self.HOTKEY_FIELDS
            },
        }
        if getattr(config, "OCR_SETTINGS_NEEDS_SAVE", False):
            settings["ocr_engines"] = dict(config.OCR_ENGINE_BY_CONTEXT)
            settings["ocr_fallback_enabled"] = config.OCR_FALLBACK_ENABLED
            settings["ocr_fallback_engine"] = config.OCR_FALLBACK_ENGINE
        return settings

    def _color_option_keys(self, label: str) -> tuple[str, ...]:
        for option_label, internal_keys, _description in self.COLOR_OPTIONS:
            if option_label == label:
                return internal_keys
        return (label,)

    def _collect_widget_values(self):
        self._store_name_alias_editor()
        self._pending["ui_font_family"] = self.font_combo.currentFont().family()
        self._pending["ui_font_size"] = self.font_size.value()
        self._pending["ui_table_text_alignment"] = str(self.table_alignment.currentData()).strip().lower()
        self._pending["wfm_ingame_name"] = self.ingame_name.text().strip()
        self._pending["wfm_token"] = self.wfm_token.text().strip()
        self._pending["min_listing_price"] = self.min_listing_price.value()
        self._pending["alert_sound_enabled"] = self.sound_check.isChecked()
        self._pending["alert_sound_style"] = self.sound_style.currentText()
        self._pending["alert_sound_volume"] = self.sound_volume.value() / 100
        self._pending["visual_notifications_enabled"] = self.visual_check.isChecked()
        self._pending["notification_corner"] = self.corner_combo.currentText()
        self._pending["sidro_notification_mascot"] = self.mascot_check.isChecked()
        self._pending["sidro_visual_character"] = str(self.sidro_visual_combo.currentData() or "robot")
        self._pending["sidro_welcome_visual_frequency"] = str(
            self.sidro_welcome_frequency_combo.currentData() or "once_a_day"
        )
        self._pending["sidro_welcome_audio_enabled"] = self.sidro_welcome_audio_check.isChecked()
        self._pending["update_check_frequency"] = self.update_frequency_combo.currentData()
        self._pending["ducat_filter_enabled"] = self.ducat_check.isChecked()
        self._pending["ducat_bulk_plat_values"] = {
            ducats: spin.value()
            for ducats, spin in self.ducat_spins.items()
        }
        self._set_all_ocr_engines()
        self._pending["ocr_fallback_enabled"] = self.ocr_fallback_enabled.isChecked()
        self._pending["ocr_fallback_engine"] = str(self.ocr_fallback_engine.currentData()).strip().lower()
        self._pending["ocr_fallback_min_score"] = self.ocr_fallback_score.value()
        self._pending["hotkeys"] = {
            attr: box.currentText()
            for attr, box in self.hotkey_boxes.items()
        }

    def _show_ocr_information_help(self):
        QMessageBox.information(
            self,
            "OCR Information Help",
            "RapidOCR\n"
            "The default engine for every scan. It is bundled with Sidro, including its models, and works without a separate Python installation.\n\n"
            "PaddleOCR\n"
            "An optional alternative. Use Install / Repair below, then Test before selecting it. Installation requires Python 3.12; its models download on first use.\n\n"
            "The selected engine applies to all scans. Different engines may read a particular image better; fallback OCR can retry missed inventory cells with the other engine, but takes longer."
        )

    def accept(self):
        if getattr(self, "_ocr_pack_busy", False):
            QMessageBox.information(self, "OCR operation in progress", "Leave Settings open until OCR installation or testing finishes.")
            return
        self._collect_widget_values()
        hotkeys_changed = self._pending["hotkeys"] != self._initial_hotkeys
        ocr_changed = (
            self._pending["ocr_engines"] != self._initial_ocr_engines
            or (
                bool(self._pending["ocr_fallback_enabled"]),
                str(self._pending["ocr_fallback_engine"]).strip().lower(),
                int(self._pending["ocr_fallback_min_score"]),
            ) != self._initial_ocr_fallback
        )

        config.ALERT_SOUND_ENABLED = bool(self._pending["alert_sound_enabled"])
        config.ALERT_SOUND_STYLE = str(self._pending["alert_sound_style"])
        config.ALERT_SOUND_VOLUME = float(self._pending["alert_sound_volume"])
        config.ALERT_SOUND_CUSTOM_PATH = str(self._pending["alert_sound_custom_path"])
        config.VISUAL_NOTIFICATIONS_ENABLED = bool(self._pending["visual_notifications_enabled"])
        config.NOTIFICATION_CORNER = str(self._pending["notification_corner"])
        config.SIDRO_NOTIFICATION_MASCOT = bool(self._pending["sidro_notification_mascot"])
        config.SIDRO_VISUAL_CHARACTER = str(self._pending["sidro_visual_character"]).strip().lower()
        if config.SIDRO_VISUAL_CHARACTER not in SIDRO_VISUALS:
            config.SIDRO_VISUAL_CHARACTER = "robot"
        config.SIDRO_WELCOME_VISUAL_FREQUENCY = str(
            self._pending.get("sidro_welcome_visual_frequency", "once_a_day")
        ).strip().lower()
        if config.SIDRO_WELCOME_VISUAL_FREQUENCY not in ("once_a_day", "every_launch", "never"):
            config.SIDRO_WELCOME_VISUAL_FREQUENCY = "once_a_day"
        config.UPDATE_CHECK_FREQUENCY = self._pending.get("update_check_frequency", "daily")
        config.SIDRO_WELCOME_AUDIO_ENABLED = bool(
            self._pending.get("sidro_welcome_audio_enabled", True)
        )
        config.WFM_INGAME_NAME = str(self._pending["wfm_ingame_name"]).strip()
        config.TRADE_MY_NAME = config.WFM_INGAME_NAME
        config.OCR_NAME_ALIASES = sanitize_name_aliases(self._pending["ocr_name_aliases"])
        new_token = str(self._pending["wfm_token"]).strip()
        token_error = ""
        if new_token and new_token != self._initial_wfm_token:
            try:
                token_message = self.on_save_settings_token(new_token)
            except Exception as e:
                token_error = str(e)
            else:
                if token_message:
                    QMessageBox.information(self, "Login token", token_message)
        config.MIN_LISTING_PRICE = max(1, int(self._pending["min_listing_price"]))
        config.UI_FONT_FAMILY = str(self._pending["ui_font_family"])
        config.UI_FONT_SIZE = int(self._pending["ui_font_size"])
        config.UI_TABLE_TEXT_ALIGNMENT = str(self._pending["ui_table_text_alignment"]).strip().lower()
        if config.UI_TABLE_TEXT_ALIGNMENT not in ("left", "center", "right"):
            config.UI_TABLE_TEXT_ALIGNMENT = "left"
        config.UI_COLORS = dict(self._pending["ui_colors"])
        config.OCR_ENGINE_BY_CONTEXT = dict(self._pending["ocr_engines"])
        config.OCR_FALLBACK_ENABLED = bool(self._pending["ocr_fallback_enabled"])
        config.OCR_FALLBACK_ENGINE = str(self._pending["ocr_fallback_engine"]).strip().lower()
        config.OCR_FALLBACK_MIN_SCORE = max(0, min(100, int(self._pending["ocr_fallback_min_score"])))
        config.DUCAT_FILTER_ENABLED = bool(self._pending["ducat_filter_enabled"])
        config.DUCAT_BULK_PLAT_VALUES = {
            int(ducats): max(0, int(platinum))
            for ducats, platinum in dict(self._pending["ducat_bulk_plat_values"]).items()
        }
        for attr, value in dict(self._pending["hotkeys"]).items():
            setattr(config, attr, value)

        _save_ui_settings()
        _load_color_settings()
        _apply_app_style(QApplication.instance())
        if hotkeys_changed and self.on_hotkeys_changed is not None:
            self.on_hotkeys_changed()
        if ocr_changed and self.on_ocr_changed is not None:
            self.on_ocr_changed()
        if token_error:
            QMessageBox.warning(self, "Login token not saved", token_error)
        else:
            message = "Settings saved."
            if ocr_changed:
                message += " Active scans are stopping and OCR engines are being unloaded."
            QMessageBox.information(self, "Settings", message)
        super().accept()

    def reject(self):
        if getattr(self, "_ocr_pack_busy", False):
            QMessageBox.information(self, "OCR operation in progress", "Leave Settings open until OCR installation or testing finishes.")
            return
        super().reject()

    def closeEvent(self, event):
        if getattr(self, "_ocr_pack_busy", False):
            QMessageBox.information(self, "OCR operation in progress", "Leave Settings open until OCR installation or testing finishes.")
            event.ignore()
            return
        super().closeEvent(event)

    def _set_font(self, font):
        self._pending["ui_font_family"] = font.family()

    def _set_font_size(self, value: int):
        self._pending["ui_font_size"] = int(value)

    def _set_table_text_alignment(self, value: str):
        selected = str(value or "left").strip().lower()
        if selected not in ("left", "center", "right"):
            selected = "left"
        self._pending["ui_table_text_alignment"] = selected

    def _set_ingame_name(self):
        self._pending["wfm_ingame_name"] = self.ingame_name.text().strip()

    def _store_name_alias_editor(self):
        mapping = sanitize_name_aliases(self._pending.get("ocr_name_aliases", {}))
        if self._alias_account_key:
            values = sanitize_name_aliases({
                self._alias_account_key: self.name_aliases.toPlainText().splitlines(),
            }).get(self._alias_account_key, [])
            if values:
                mapping[self._alias_account_key] = values
            else:
                mapping.pop(self._alias_account_key, None)
        self._pending["ocr_name_aliases"] = mapping

    def _load_name_alias_editor(self):
        name = self.ingame_name.text().strip()
        self._alias_account_key = normalize_alias_text(name)
        mapping = sanitize_name_aliases(self._pending.get("ocr_name_aliases", {}))
        self.name_aliases.setPlainText("\n".join(mapping.get(self._alias_account_key, [])))
        self.name_aliases.setEnabled(bool(self._alias_account_key))
        self.name_alias_account.setText(
            f"Corrections for: {name}" if self._alias_account_key
            else "Set your Warframe name in the Account tab first."
        )

    def _on_alias_account_changed(self, _name: str):
        self._store_name_alias_editor()
        self._load_name_alias_editor()

    def _set_wfm_token(self):
        self._pending["wfm_token"] = self.wfm_token.text().strip()

    def _toggle_token_visibility(self, value: bool):
        self.wfm_token.setEchoMode(
            QLineEdit.EchoMode.Normal if value else QLineEdit.EchoMode.Password
        )

    def _export_backup(self):
        self._collect_widget_values()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Sidro backup",
            "sidro-settings-backup.json",
            "JSON files (*.json)",
        )
        if not path:
            return
        backup = {
            "app": config.APP_NAME,
            "version": config.APP_VERSION,
            "settings": self._pending,
        }
        try:
            Path(path).write_text(json.dumps(backup, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Backup exported", f"Saved:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Backup export failed", str(e))

    def _import_backup(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Sidro backup",
            "",
            "JSON files (*.json)",
        )
        if not path:
            return
        try:
            if Path(path).stat().st_size > MAX_SETTINGS_BYTES:
                raise ValueError("Backup file is too large.")
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            imported = data.get("settings", data)
            if not isinstance(imported, dict):
                raise ValueError("Backup file does not contain a settings object.")
            self._pending.update(sanitize_settings(imported))
            self._normalize_pending_after_import()
            self._apply_pending_to_widgets()
            QMessageBox.information(self, "Backup imported", "Review the imported values, then click Save & Close.")
        except Exception as e:
            QMessageBox.critical(self, "Backup import failed", str(e))

    def _normalize_pending_after_import(self):
        self._pending["ocr_name_aliases"] = sanitize_name_aliases(self._pending.get("ocr_name_aliases", {}))
        self._pending["min_listing_price"] = max(1, int(self._pending.get("min_listing_price", 2)))
        self._pending["ui_font_size"] = int(self._pending.get("ui_font_size", 12))
        self._pending["ocr_fallback_min_score"] = max(0, min(100, int(self._pending.get("ocr_fallback_min_score", 85))))
        self._pending["ocr_engines"] = {
            key: (
                str(value).strip().lower()
                if str(value).strip().lower() in getattr(config, "OCR_ENGINE_CHOICES", ())
                else dict(getattr(config, "OCR_ENGINE_BY_CONTEXT", {})).get(key, "rapidocr")
            )
            for key, value in dict(self._pending.get("ocr_engines", {})).items()
        }
        for _label, context in self.OCR_FIELDS:
            self._pending["ocr_engines"].setdefault(context, config.OCR_ENGINE_BY_CONTEXT.get(context, "rapidocr"))
        visual = str(self._pending.get("sidro_visual_character", "robot")).strip().lower()
        self._pending["sidro_visual_character"] = visual if visual in SIDRO_VISUALS else "robot"
        welcome_frequency = str(
            self._pending.get("sidro_welcome_visual_frequency", "once_a_day")
        ).strip().lower()
        self._pending["sidro_welcome_visual_frequency"] = (
            welcome_frequency if welcome_frequency in ("once_a_day", "every_launch", "never") else "once_a_day"
        )
        self._pending["sidro_welcome_audio_enabled"] = bool(
            self._pending.get("sidro_welcome_audio_enabled", True)
        )
        ducats = self._pending.get("ducat_bulk_plat_values", {})
        self._pending["ducat_bulk_plat_values"] = {
            int(k): max(0, int(v))
            for k, v in dict(ducats).items()
        }
        hotkeys = dict(self._pending.get("hotkeys", {}))
        compact_to_attr = {
            "scan": "HOTKEY_SCAN",
            "inventory_scan": "HOTKEY_INVENTORY_SCAN",
            "inventory_bottom_scan": "HOTKEY_INVENTORY_BOTTOM_SCAN",
            "trade_monitor": "HOTKEY_TRADE_MONITOR",
            "account_ui": "HOTKEY_ACCOUNT_UI",
            "reward_scan": "HOTKEY_REWARD_SCAN",
            "ocr_warmup": "HOTKEY_OCR_WARMUP",
            "quit": "HOTKEY_QUIT",
        }
        for compact, attr in compact_to_attr.items():
            if compact in hotkeys and attr not in hotkeys:
                hotkeys[attr] = hotkeys[compact]
        self._pending["hotkeys"] = hotkeys
        for _label, attr in self.HOTKEY_FIELDS:
            self._pending["hotkeys"].setdefault(attr, str(getattr(config, attr, "")))

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str):
        for index in range(combo.count()):
            if str(combo.itemData(index)) == str(value):
                combo.setCurrentIndex(index)
                return
        if combo.findText(str(value)) >= 0:
            combo.setCurrentText(str(value))

    def _apply_pending_to_widgets(self):
        self._set_combo_data(self.update_frequency_combo, self._pending.get("update_check_frequency", "daily"))
        self.font_combo.setCurrentFont(QFont(str(self._pending.get("ui_font_family", "Segoe UI"))))
        self.font_size.setValue(int(self._pending.get("ui_font_size", 12)))
        self._set_combo_data(self.table_alignment, str(self._pending.get("ui_table_text_alignment", "left")))
        self.ingame_name.blockSignals(True)
        self.ingame_name.setText(str(self._pending.get("wfm_ingame_name", "")))
        self.ingame_name.blockSignals(False)
        self._load_name_alias_editor()
        self.wfm_token.setText(str(self._pending.get("wfm_token", "")))
        self.min_listing_price.setValue(int(self._pending.get("min_listing_price", 2)))
        self.sound_check.setChecked(bool(self._pending.get("alert_sound_enabled", True)))
        self.sound_style.setCurrentText(str(self._pending.get("alert_sound_style", "Random")))
        self.sound_volume.setValue(int(float(self._pending.get("alert_sound_volume", 0.45)) * 100))
        self.visual_check.setChecked(bool(self._pending.get("visual_notifications_enabled", True)))
        self.corner_combo.setCurrentText(str(self._pending.get("notification_corner", "top_left")))
        self.mascot_check.setChecked(bool(self._pending.get("sidro_notification_mascot", False)))
        self._set_combo_data(self.sidro_visual_combo, str(self._pending.get("sidro_visual_character", "robot")))
        self._set_combo_data(
            self.sidro_welcome_frequency_combo,
            str(self._pending.get("sidro_welcome_visual_frequency", "once_a_day")),
        )
        self.sidro_welcome_audio_check.setChecked(
            bool(self._pending.get("sidro_welcome_audio_enabled", True))
        )
        self._update_sidro_visual_preview()
        selected_backend = self._ocr_selection_for_contexts(self._pending["ocr_engines"])
        self.ocr_backend.blockSignals(True)
        self._set_combo_data(self.ocr_backend, selected_backend)
        self.ocr_backend.blockSignals(False)
        self._set_all_ocr_engines()
        self.ocr_fallback_enabled.setChecked(bool(self._pending.get("ocr_fallback_enabled", False)))
        fallback = str(self._pending.get("ocr_fallback_engine", "rapidocr"))
        if fallback not in dict(self.OCR_ENGINE_LABELS).values():
            fallback = "rapidocr"
        self._set_combo_data(self.ocr_fallback_engine, fallback)
        self.ocr_fallback_score.setValue(int(self._pending.get("ocr_fallback_min_score", 85)))
        self.ducat_check.setChecked(bool(self._pending.get("ducat_filter_enabled", True)))
        for ducats, spin in self.ducat_spins.items():
            spin.setValue(int(dict(self._pending.get("ducat_bulk_plat_values", {})).get(ducats, spin.value())))
        for attr, box in self.hotkey_boxes.items():
            box.setCurrentText(str(dict(self._pending.get("hotkeys", {})).get(attr, box.currentText())))
        colors = dict(self._pending.get("ui_colors", {}))
        for label, internal_keys, _description in self.COLOR_OPTIONS:
            self._update_color_button(
                label,
                colors.get(internal_keys[0], DEFAULT_UI_COLORS[internal_keys[0]]),
            )

    def _set_min_listing_price(self, value: int):
        self._pending["min_listing_price"] = max(1, int(value))

    def _ocr_selection_for_contexts(self, engines: dict) -> str:
        backends = set(engines.values())
        if len(backends) == 1:
            selected = next(iter(backends))
            if selected in dict(self.OCR_ENGINE_LABELS).values():
                return selected
        return "rapidocr"

    def _set_all_ocr_engines(self, _index: int = -1):
        if not hasattr(self, "ocr_backend"):
            return
        selected = str(self.ocr_backend.currentData() or "rapidocr").strip().lower()
        if selected not in dict(self.OCR_ENGINE_LABELS).values():
            selected = "rapidocr"
        self._pending["ocr_engines"] = {
            context: selected for _label, context in self.OCR_FIELDS
        }

    def _set_ocr_fallback_enabled(self, value: bool):
        self._pending["ocr_fallback_enabled"] = bool(value)

    def _set_ocr_fallback_engine(self, value: str):
        selected = str(value or "").strip().lower()
        if selected in getattr(config, "OCR_ENGINE_CHOICES", ()):
            self._pending["ocr_fallback_engine"] = selected

    def _set_ocr_fallback_min_score(self, value: int):
        self._pending["ocr_fallback_min_score"] = max(0, min(100, int(value)))

    def _ocr_pack_dir(self, backend: str) -> Path:
        return Path(getattr(config, "OCR_PACKS_DIR", config.USER_DATA_DIR / "ocr_packs")) / backend

    def _refresh_ocr_pack_status(self):
        for backend, label in self.ocr_pack_labels.items():
            available, dependency = is_ocr_backend_available(backend)
            pack_dir = self._ocr_pack_dir(backend)
            if available:
                suffix = "from pack" if pack_dir.exists() else "available in app/Python"
                result = getattr(self, "_ocr_pack_results", {}).get(backend)
                state = "Ready — inference tested" if result is True else "Test failed" if result is False else "Installed — not tested"
                label.setText(f"{state} ({dependency}, {suffix})")
            else:
                label.setText(f"Not installed ({dependency})")

    def _open_ocr_pack_folder(self, backend: str):
        pack_dir = self._ocr_pack_dir(backend)
        pack_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(pack_dir)))

    def _install_ocr_pack(self, backend: str):
        if getattr(self, "_ocr_pack_busy", False):
            return
        backend = str(backend).strip().lower()
        pack_dir = self._ocr_pack_dir(backend)
        pack_dir.mkdir(parents=True, exist_ok=True)
        python_base = self._pack_installer_python_base()
        if python_base is None:
            QMessageBox.information(
                self,
                "OCR pack install",
                "No Python interpreter was found for optional OCR pack installation.\n\n"
                "Install Python, or wait for prebuilt Sidro OCR pack zips in the release assets.",
            )
            self._open_ocr_pack_folder(backend)
            return
        commands = {
            "rapidocr": [*python_base, "-m", "pip", "install", "--no-deps", "-r", str(config.APP_DIR / "requirements-rapidocr-test.txt"), "--target", str(pack_dir), "--upgrade"],
            "paddleocr": [*python_base, "-m", "pip", "install", "--no-deps", "-r", str(config.APP_DIR / "requirements-paddleocr-test.txt"), "--target", str(pack_dir), "--upgrade"],
        }
        command = commands.get(backend)
        if not command:
            return
        answer = QMessageBox.question(
            self,
            "Install OCR pack",
            f"Install or repair {backend} into:\n{pack_dir}\n\n"
            "This downloads packages from PyPI. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._ocr_pack_busy = True
        self._ocr_pack_results = {}

        if backend in self.ocr_pack_labels:
            self.ocr_pack_labels[backend].setText("Installing...")

        def worker():
            try:
                completed = subprocess.run(command, cwd=str(config.APP_DIR), capture_output=True, text=True, timeout=1200)
                if completed.returncode:
                    raise RuntimeError((completed.stderr or completed.stdout)[-3000:])
                validate_ocr_backend(backend)
                message = f"{backend} installed; initialization and inference passed. Restart Sidro before using it."
                ok = True
            except Exception as e:
                message = f"{backend} installation or validation failed: {e}"
                ok = False
            self.ocr_pack_install_finished.emit(backend, message, ok)

        threading.Thread(target=worker, daemon=True).start()

    def _test_ocr_pack(self, backend: str):
        if getattr(self, "_ocr_pack_busy", False):
            return
        self._ocr_pack_busy = True
        self.ocr_pack_labels[backend].setText("Testing model loading and inference...")
        def worker():
            try:
                validate_ocr_backend(backend)
                message, ok = f"{backend}: initialization and inference passed.", True
            except Exception as exc:
                message, ok = f"{backend} test failed: {exc}", False
            self.ocr_pack_install_finished.emit(backend, message, ok)
        threading.Thread(target=worker, daemon=True).start()

    def _finish_ocr_pack_install(self, backend: str, message: str, ok: bool):
        self._ocr_pack_busy = False
        if not hasattr(self, "_ocr_pack_results"):
            self._ocr_pack_results = {}
        self._ocr_pack_results[backend] = ok
        self._refresh_ocr_pack_status()
        QMessageBox.information(self, "OCR pack install", message)

    def _pack_installer_python_base(self) -> list[str] | None:
        if not getattr(sys, "frozen", False):
            return [sys.executable]
        major = sys.version_info.major
        minor = sys.version_info.minor
        py_launcher = shutil.which("py")
        if py_launcher:
            try:
                check = subprocess.run(
                    [py_launcher, f"-{major}.{minor}", "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                    capture_output=True, text=True, timeout=10,
                )
                if check.returncode == 0 and check.stdout.strip() == f"{major}.{minor}":
                    return [py_launcher, f"-{major}.{minor}"]
            except (OSError, subprocess.TimeoutExpired):
                pass
        python = shutil.which("python")
        if python:
            try:
                check = subprocess.run(
                    [python, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                    capture_output=True, text=True, timeout=10,
                )
                if check.returncode == 0 and check.stdout.strip() == f"{major}.{minor}":
                    return [python]
            except (OSError, subprocess.TimeoutExpired):
                pass
        return None

    def _set_sound_enabled(self, value: bool):
        self._pending["alert_sound_enabled"] = bool(value)

    def _set_sound_style(self, value: str):
        self._pending["alert_sound_style"] = value

    def _set_sound_volume(self, value: float):
        self._pending["alert_sound_volume"] = float(value)

    def _preview_sound(self):
        old_enabled = bool(getattr(config, "ALERT_SOUND_ENABLED", True))
        old_style = str(getattr(config, "ALERT_SOUND_STYLE", "Random"))
        old_volume = float(getattr(config, "ALERT_SOUND_VOLUME", 0.45))
        old_custom = str(getattr(config, "ALERT_SOUND_CUSTOM_PATH", ""))
        try:
            config.ALERT_SOUND_ENABLED = True
            config.ALERT_SOUND_STYLE = str(self.sound_style.currentText())
            config.ALERT_SOUND_VOLUME = self.sound_volume.value() / 100
            config.ALERT_SOUND_CUSTOM_PATH = str(self._pending.get("alert_sound_custom_path", ""))
            play_alert("trade")
        finally:
            config.ALERT_SOUND_ENABLED = old_enabled
            config.ALERT_SOUND_STYLE = old_style
            config.ALERT_SOUND_VOLUME = old_volume
            config.ALERT_SOUND_CUSTOM_PATH = old_custom

    def _pick_custom_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose alert WAV",
            "",
            "WAV files (*.wav)",
        )
        if not path:
            return
        self._pending["alert_sound_custom_path"] = path
        self._pending["alert_sound_style"] = "Custom File"
        self.sound_style.setCurrentText("Custom File")

    def _set_visual_notifications(self, value: bool):
        self._pending["visual_notifications_enabled"] = bool(value)

    def _set_notification_corner(self, value: str):
        self._pending["notification_corner"] = value

    def _set_notification_mascot(self, value: bool):
        self._pending["sidro_notification_mascot"] = bool(value)

    def _set_sidro_visual_character(self, value: str):
        selected = str(value or "robot").strip().lower()
        if selected not in SIDRO_VISUALS:
            selected = "robot"
        self._pending["sidro_visual_character"] = selected
        self._update_sidro_visual_preview()

    def _set_sidro_welcome_visual_frequency(self, value: str):
        selected = str(value or "once_a_day").strip().lower()
        if selected not in ("once_a_day", "every_launch", "never"):
            selected = "once_a_day"
        self._pending["sidro_welcome_visual_frequency"] = selected

    def _set_sidro_welcome_audio_enabled(self, enabled: bool):
        self._pending["sidro_welcome_audio_enabled"] = bool(enabled)

    def _update_sidro_visual_preview(self):
        if not hasattr(self, "sidro_visual_preview"):
            return
        selected = str(self._pending.get("sidro_visual_character", "robot")).strip().lower()
        label, filename = SIDRO_VISUALS.get(selected, SIDRO_VISUALS["robot"])
        pixmap = QPixmap(str(config.resource_path(filename)))
        if pixmap.isNull():
            self.sidro_visual_preview.setPixmap(QPixmap())
            self.sidro_visual_preview.setText(f"{label}\n{filename} not found.")
            return
        self.sidro_visual_preview.setText("")
        self.sidro_visual_preview.setPixmap(
            pixmap.scaled(
                180,
                210,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def _set_ducat_filter_enabled(self, value: bool):
        self._pending["ducat_filter_enabled"] = bool(value)

    def _set_ducat_value(self, ducats: int, platinum: int):
        values = dict(self._pending["ducat_bulk_plat_values"])
        values[int(ducats)] = max(0, int(platinum))
        self._pending["ducat_bulk_plat_values"] = values

    def _pick_color(self, option_label: str):
        internal_keys = self._color_option_keys(option_label)
        primary_key = internal_keys[0]
        initial = QColor(self._pending["ui_colors"].get(primary_key, self.color_buttons[option_label].text()))
        color = QColorDialog.getColor(initial, self, f"Select {option_label}")
        if not color.isValid():
            return
        colors = dict(self._pending["ui_colors"])
        for internal_key in internal_keys:
            colors[internal_key] = color.name()
        self._pending["ui_colors"] = colors
        self._update_color_button(option_label, color.name())

    def _update_color_button(self, option_label: str, color_value: str):
        color = QColor(color_value)
        if not color.isValid():
            color = QColor(DEFAULT_UI_COLORS[self._color_option_keys(option_label)[0]])
        hex_color = color.name().upper()
        text_color = "#14141A" if color.lightness() >= 150 else "#FFFFFF"
        button = self.color_buttons.get(option_label)
        if button is None:
            return
        button.setProperty("sidro_theme_managed", False)
        button.setText(f"{hex_color}  ·  Change")
        button.setStyleSheet(
            f"QPushButton {{ background: {hex_color}; color: {text_color}; "
            "border: 1px solid #555563; padding: 7px 10px; font-weight: 700; }"
            "QPushButton:hover { border: 1px solid white; }"
            "QPushButton:disabled { color: #777; background: #30303c; }"
        )

    def _set_hotkey(self, attr: str, value: str):
        if not value:
            return
        hotkeys = dict(self._pending["hotkeys"])
        hotkeys[attr] = value
        self._pending["hotkeys"] = hotkeys

    def _reset(self):
        self._pending["ui_colors"] = {}
        self._pending["ui_font_family"] = "Segoe UI"
        self._pending["ui_font_size"] = 12
        self._pending["ui_table_text_alignment"] = "left"
        for label, internal_keys, _description in self.COLOR_OPTIONS:
            self._update_color_button(label, DEFAULT_UI_COLORS[internal_keys[0]])
        self.font_combo.setCurrentFont(QFont("Segoe UI"))
        self.font_size.setValue(12)
        self.table_alignment.setCurrentText("Left")


class BuyerListDialog(QDialog):
    def __init__(
        self,
        result: dict,
        on_copy: Callable[[str, str, str], bool],
        parent=None,
    ):
        super().__init__(parent)
        self.result = dict(result)
        self.on_copy = on_copy
        self.current_whisper = ""
        self.setWindowTitle(f"Available buyers - {self.result.get('display_name', '')}")
        self.resize(600, 420)

        layout = QVBoxLayout(self)
        title = QLabel(f"Available buyers for {self.result.get('display_name', '')}")
        title.setObjectName("Title")
        layout.addWidget(title)

        self.status = QLabel("Select a buyer to preview their WTS whisper, then copy it when ready.")
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)

        self.buyer_list = QListWidget()
        self.buyer_list.setMinimumHeight(190)
        self.buyer_list.itemClicked.connect(self._select_buyer)
        self.buyer_list.itemActivated.connect(self._select_buyer)
        layout.addWidget(self.buyer_list)

        self.whisper = QTextEdit()
        self.whisper.setReadOnly(True)
        self.whisper.setMaximumHeight(88)
        layout.addWidget(self.whisper)

        for order in self.result.get("top_orders", []) or []:
            item = QListWidgetItem(
                f"{order.get('price', '?')}p x{order.get('quantity', '?')}  "
                f"{order.get('buyer', '')} ({order.get('status', '')})"
            )
            item.setData(Qt.UserRole, dict(order))
            self.buyer_list.addItem(item)

        footer = QHBoxLayout()
        footer.addStretch()
        self.copy_button = _button("💬 Copy WTS whisper", C_GREEN, "white")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_current)
        close = _button("Close", C_BG3)
        close.clicked.connect(self.accept)
        footer.addWidget(self.copy_button)
        footer.addWidget(close)
        layout.addLayout(footer)

        if self.buyer_list.count():
            self.buyer_list.setCurrentRow(0)
            self._select_buyer(self.buyer_list.currentItem())
        else:
            self.status.setText("No active buyers found.")

    def _select_buyer(self, item: QListWidgetItem | None):
        if item is None:
            return
        order = item.data(Qt.UserRole) or {}
        self.current_whisper = str(order.get("whisper", "") or "")
        self.whisper.setPlainText(self.current_whisper)
        self.copy_button.setEnabled(bool(self.current_whisper))
        buyer = str(order.get("buyer", "") or "")
        price = order.get("price", "?")
        self.status.setText(f"Buyer selected: {buyer} at {price}p.")

    def _copy_current(self):
        self.on_copy(
            self.current_whisper,
            "WTS whisper copied to clipboard.",
            "No buyer whisper to copy.",
        )


class _WindowsDashboardFrame:
    """Keep Windows resizing and Snap with Qt-drawn caption buttons."""

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                    ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    class MinMaxInfo(ctypes.Structure):
        _fields_ = [("reserved", wintypes.POINT), ("max_size", wintypes.POINT),
                    ("max_position", wintypes.POINT), ("min_track", wintypes.POINT),
                    ("max_track", wintypes.POINT)]

    def __init__(self, window):
        self.window = window
        self.hwnd = None
        self.pressed = None
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        signatures = {
            "GetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t),
            "SetWindowLongPtrW": ([wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t),
            "SetWindowPos": ([wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            "ScreenToClient": ([wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "GetDpiForWindow": ([wintypes.HWND], wintypes.UINT),
            "GetSystemMetricsForDpi": ([ctypes.c_int, wintypes.UINT], ctypes.c_int),
            "MonitorFromWindow": ([wintypes.HWND, wintypes.DWORD], wintypes.HANDLE),
            "GetMonitorInfoW": ([wintypes.HANDLE, ctypes.POINTER(self.MonitorInfo)], wintypes.BOOL),
            "SetCapture": ([wintypes.HWND], wintypes.HWND),
            "ReleaseCapture": ([], wintypes.BOOL),
            "ShowWindow": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.user32, name)
            function.argtypes, function.restype = args, result

    def configure(self):
        self.hwnd = wintypes.HWND(int(self.window.winId()))
        style = self.user32.GetWindowLongPtrW(self.hwnd, -16)
        # Retain the native resize/system-menu/maximize capabilities, but no
        # Windows caption. Qt paints and operates the three buttons itself.
        desired = (style | 0x000F0000) & ~0x00C00000
        if style != desired:
            self.user32.SetWindowLongPtrW(self.hwnd, -16, desired)
            self.user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0, 0x0037)
        dwm = ctypes.WinDLL("dwmapi")
        margins = (ctypes.c_int * 4)(1, 1, 1, 1)
        dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
        dwm.DwmExtendFrameIntoClientArea(self.hwnd, ctypes.byref(margins))
        corners = ctypes.c_int(2)  # DWMWCP_ROUND, ignored on Windows 10.
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        dwm.DwmSetWindowAttribute(self.hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners))

    def point(self, packed, screen):
        point = wintypes.POINT(ctypes.c_short(packed & 0xFFFF).value,
                               ctypes.c_short((packed >> 16) & 0xFFFF).value)
        if screen:
            self.user32.ScreenToClient(self.hwnd, ctypes.byref(point))
        scale = self.window.devicePixelRatioF()
        return QPoint(round(point.x / scale), round(point.y / scale))

    def minimize(self):
        # Let Windows retain the native restore state. Qt can also classify a
        # borderless maximized window as fullscreen when it fills the monitor;
        # showMinimized() then reapplies fullscreen styles during this change.
        self.user32.ShowWindow(self.hwnd, 6)  # SW_MINIMIZE

    def caption_buttons(self):
        return ((8, self.window.window_minimize_button),
                (9, self.window.window_maximize_button))

    def caption_rect(self, button):
        return QRect(button.mapTo(self.window, QPoint()), button.size())

    def caption_at(self, point):
        if self.window.isEnabled():
            for hit, button in self.caption_buttons():
                if button.isEnabled() and self.caption_rect(button).contains(point):
                    return hit, button
        return 1, None  # HTCLIENT

    def hover(self, current=None):
        for _, button in self.caption_buttons():
            active = button is current
            if bool(button.property("sidro_caption_hover")) != active:
                button.setProperty("sidro_caption_hover", active)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()

    def cancel_press(self):
        button, self.pressed = self.pressed, None
        if button is not None:
            button.setDown(False)

    def handle(self, msg):
        window = self.window
        if msg.message == 0x0024:  # WM_GETMINMAXINFO: maximize to the work area.
            monitor = self.user32.MonitorFromWindow(self.hwnd, 2)
            info = self.MonitorInfo()
            info.size = ctypes.sizeof(info)
            if self.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                limits = self.MinMaxInfo.from_address(msg.lParam)
                limits.max_position.x = info.work.left - info.monitor.left
                limits.max_position.y = info.work.top - info.monitor.top
                limits.max_size.x = info.work.right - info.work.left
                limits.max_size.y = info.work.bottom - info.work.top
                scale = window.devicePixelRatioF()
                # A high-DPI monitor can have a smaller logical work area than
                # the preferred minimum. Maximizing must still fit that screen.
                limits.min_track.x = min(round(window.minimumWidth() * scale), limits.max_size.x)
                limits.min_track.y = min(round(window.minimumHeight() * scale), limits.max_size.y)
                return True, 0
        elif msg.message == 0x0084:  # WM_NCHITTEST
            point = self.point(msg.lParam, True)
            if not window.isMaximized() and not window.isFullScreen():
                dpi = self.user32.GetDpiForWindow(self.hwnd)
                border = (self.user32.GetSystemMetricsForDpi(32, dpi)
                          + self.user32.GetSystemMetricsForDpi(92, dpi)) / window.devicePixelRatioF()
                left, right = point.x() < border, point.x() >= window.width() - border
                top, bottom = point.y() < border, point.y() >= window.height() - border
                if left:
                    return True, 13 if top else (16 if bottom else 10)
                if right:
                    return True, 14 if top else (17 if bottom else 11)
                if top or bottom:
                    return True, 12 if top else 15
            hit, button = self.caption_at(point)
            self.hover(button)
            return True, hit  # HTMINBUTTON / HTMAXBUTTON / HTCLIENT
        elif msg.message == 0x0021 and (msg.lParam & 0xFFFF) in (8, 9):
            # WM_MOUSEACTIVATE: activate without discarding the caption click.
            return True, 1  # MA_ACTIVATE
        elif msg.message in (0x00A1, 0x00A3) and msg.wParam in (8, 9):
            # NC button events bypass QPushButton. Route both halves of the
            # click back to the widget, including minimize from a maximized
            # window, without mixing Qt client and native caption input paths.
            self.cancel_press()
            _, button = self.caption_at(self.point(msg.lParam, True))
            if button is not None:
                self.pressed = button
                button.setDown(True)
                self.user32.SetCapture(self.hwnd)
            return True, 0
        elif msg.message in (0x00A2, 0x0202) and self.pressed is not None:
            button = self.pressed
            inside = self.caption_rect(button).contains(self.point(msg.lParam, msg.message == 0x00A2))
            self.cancel_press()
            self.user32.ReleaseCapture()
            if inside and window.isEnabled() and button.isEnabled():
                button.click()
            return True, 0
        elif msg.message == 0x0200 and self.pressed is not None:  # Captured client mouse move.
            self.pressed.setDown(self.caption_rect(self.pressed).contains(self.point(msg.lParam, False)))
            return True, 0
        elif msg.message in (0x001F, 0x0215):  # WM_CANCELMODE / WM_CAPTURECHANGED
            self.cancel_press()
        elif msg.message == 0x00A0 and msg.wParam in (8, 9):  # WM_NCMOUSEMOVE
            _, button = self.caption_at(self.point(msg.lParam, True))
            self.hover(button)
            return True, 0
        elif msg.message == 0x02A2:  # WM_NCMOUSELEAVE
            self.hover()
        return False, 0


class _DashboardHeader(QWidget):
    """Use the existing branding area as the system window drag surface."""

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            window = self.window()
            window.showNormal() if window.isMaximized() else window.showMaximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class Dashboard(QMainWindow):
    prices_ready = Signal(dict, str)
    background_ready = Signal(str, bool)
    buyer_search_ready = Signal(dict)
    buyer_search_error = Signal(str)

    def __init__(
        self,
        store: AccountStore,
        on_refresh: Callable[[], str],
        on_update_prices: Callable[[dict[str, dict[str, object]], str], str],
        on_delete_all_listings: Callable[[], str],
        on_delete_listings: Callable[[list[str]], str],
        on_delete_listing: Callable[[str], str],
        on_price_preview: Callable[[list[str], str], dict[str, dict[str, int]]],
        on_buyer_search: Callable[[str, str], dict],
        on_save_token: Callable[[str], str],
        on_save_settings_token: Callable[[str], str],
        on_inventory_scan: Callable[[], None],
        on_inventory_bottom_scan: Callable[[], None],
        on_trade_monitor: Callable[[], None],
        on_reward_scan: Callable[[], None],
        on_ocr_warmup: Callable[[], None],
        on_reward_toggle: Callable[[bool], None],
        on_trade_scan_toggle: Callable[[bool], None],
        on_review_rewards: Callable[[], None] | None = None,
        on_hotkeys_changed: Callable[[], None] | None = None,
        on_ocr_changed: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.store = store
        self.on_refresh = on_refresh
        self.on_update_prices = on_update_prices
        self.on_delete_all_listings = on_delete_all_listings
        self.on_delete_listings = on_delete_listings
        self.on_delete_listing = on_delete_listing
        self.on_price_preview = on_price_preview
        self.on_buyer_search = on_buyer_search
        self.on_save_token = on_save_token
        self.on_save_settings_token = on_save_settings_token
        self.on_inventory_scan = on_inventory_scan
        self.on_inventory_bottom_scan = on_inventory_bottom_scan
        self.on_trade_monitor = on_trade_monitor
        self.on_reward_scan = on_reward_scan
        self.on_ocr_warmup = on_ocr_warmup
        self.on_reward_toggle = on_reward_toggle
        self.on_trade_scan_toggle = on_trade_scan_toggle
        self.on_review_rewards = on_review_rewards or (lambda: None)
        self.on_hotkeys_changed = on_hotkeys_changed
        self.on_ocr_changed = on_ocr_changed
        self.market_prices: dict[str, dict[str, int]] = {}
        self._background_busy = False
        self.log_stream = _install_log_capture()
        self.log_dialog: LogViewerDialog | None = None
        self.trade_chat_dialog: ChatMessagesDialog | None = None
        self.recruitment_chat_dialog: ChatMessagesDialog | None = None
        self.prices_ready.connect(self._finish_prices)
        self.background_ready.connect(self._finish_background)
        self.buyer_search_ready.connect(self._finish_buyer_search)
        self.buyer_search_error.connect(self._fail_buyer_search)
        self.local_toasts: list[Toast] = []
        self.startup_greeting: SidroGreeting | None = None
        self.setWindowTitle("Your best warframe companion!")
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self._windows_frame = None
        if sys.platform == "win32" and QGuiApplication.platformName() == "windows":
            self._windows_frame = _WindowsDashboardFrame(self)
        self.resize(1440, 900)
        self.setMinimumSize(1120, 740)
        self._build()
        self.refresh_rows()
        QTimer.singleShot(300, self.preview_prices)
        QTimer.singleShot(1500, self._startup_greeting)
        if getattr(config, "FIRST_LAUNCH", False):
            QTimer.singleShot(700, self._first_launch)

    def _build(self):
        central = QWidget()
        central.setAttribute(Qt.WA_ContentsMarginsRespectsSafeArea, False)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(12)
        self._responsive_bars = []

        def toolbar_group():
            group = QWidget()
            group.setObjectName("ToolbarGroup")
            group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            group_layout = QHBoxLayout(group)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(8)
            return group, group_layout

        self.window_header = _DashboardHeader()
        header = QHBoxLayout(self.window_header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(16)
        brand = QLabel()
        brand.setObjectName("BrandMark")
        brand.setProperty("sidro_icon", "brand")
        brand.setFixedSize(58, 66)
        brand.setPixmap(_ui_icon("brand", C_GOLD).pixmap(58, 66))
        header.addWidget(brand)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        title = QLabel("SIDRO")
        title.setObjectName("BrandTitle")
        subtitle = QLabel("Your best warframe companion!")
        subtitle.setObjectName("BrandSubtitle")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        header.addLayout(brand_text)
        header.addStretch()
        caption = QWidget()
        caption_layout = QHBoxLayout(caption)
        caption_layout.setContentsMargins(0, 0, 0, 0)
        caption_layout.setSpacing(0)
        for name, callback in (("minimize", self._minimize_window),
                               ("maximize", self._toggle_maximized), ("close", self.close)):
            button = QPushButton()
            button.setProperty("sidro_caption", True)
            button.setProperty("sidro_caption_icon", name)
            button.setObjectName("CaptionClose" if name == "close" else "CaptionButton")
            button.setFixedSize(44, 32)
            button.setIcon(_ui_icon(name, C_TEXT))
            button.setIconSize(QSize(16, 16))
            button.setAccessibleName(name.capitalize())
            button.setToolTip(name.capitalize())
            button.clicked.connect(callback)
            caption_layout.addWidget(button)
            setattr(self, f"window_{name}_button", button)
        header.addWidget(caption, 0, Qt.AlignTop)
        layout.addWidget(self.window_header)

        controls_frame = QFrame()
        controls_frame.setObjectName("Toolbar")
        controls_bar = QBoxLayout(QBoxLayout.LeftToRight, controls_frame)
        controls_bar.setContentsMargins(8, 8, 8, 8)
        controls_bar.setSpacing(12)
        primary_group, primary_controls = toolbar_group()
        utility_group, utility_controls = toolbar_group()
        controls_bar.addWidget(primary_group)
        controls_bar.addStretch()
        controls_bar.addWidget(utility_group)
        self._responsive_bars.append((controls_bar, primary_group, utility_group))
        self.login_button = None
        control_specs = [
            ("Scanner Help", self._scanner_help, C_BG3),
            ("Trade Chat", self._trade_chat, C_BG3),
            ("Recruitment Chat", self._recruitment_chat, C_BG3),
            ("Refresh WFM profile", self._refresh, C_BLUE),
            ("Settings", self._settings, C_BG3),
        ]
        if not getattr(config, "WFM_TOKEN", ""):
            control_specs.insert(3, ("Login / Token", self._token, C_BG3))
        for text, fn, color in control_specs:
            btn = _button(text, color, C_BG if color == C_GOLD else C_TEXT)
            btn.clicked.connect(fn)
            controls = primary_controls if text in {"Scanner Help", "Trade Chat", "Recruitment Chat", "Login / Token"} else utility_controls
            controls.addWidget(btn)
            if text == "Login / Token":
                self.login_button = btn
        self.reward_queue_button = _button("Review rewards (0)", C_GREEN, "white")
        self.reward_queue_button.setEnabled(False)
        self.reward_queue_button.clicked.connect(lambda _checked=False: self.on_review_rewards())
        utility_controls.addWidget(self.reward_queue_button)
        self.ocr_warmup_button = _button("Load OCRs", C_GREEN, "white")
        self.ocr_warmup_button.setToolTip(
            f"Load configured OCR engines, or unload them to release memory ({config.HOTKEY_OCR_WARMUP})"
        )
        self.ocr_warmup_button.clicked.connect(lambda _checked=False: self.on_ocr_warmup())
        self.ocr_warmup_status = QLabel("")
        self.ocr_warmup_status.setObjectName("Muted")
        self.set_ocr_load_state(False, "OCR engines are unloaded")
        self.reward_check = _StyledCheckBox("Void Fissure Scanning")
        config.REWARD_PRICE_CHECK_ENABLED = False
        self.reward_check.setChecked(False)
        self.reward_check.toggled.connect(self._set_reward_enabled)
        self.trade_scan_check = _StyledCheckBox("Live Trade Scanning")
        self.trade_scan_check.setChecked(False)
        self.trade_scan_check.toggled.connect(self._set_trade_scan_enabled)
        self.log_check = _StyledCheckBox("Enable log viewing")
        self.log_check.toggled.connect(self._set_log_viewing)
        layout.addWidget(controls_frame)

        options_frame = QFrame()
        options_frame.setObjectName("Toolbar")
        options = QHBoxLayout(options_frame)
        options.setContentsMargins(14, 9, 14, 9)
        options.setSpacing(20)
        options.addWidget(self._side_text("Background tools"))
        tools_divider = QFrame()
        tools_divider.setObjectName("Divider")
        tools_divider.setFixedSize(1, 24)
        options.addWidget(tools_divider)
        options.addWidget(self.reward_check)
        options.addWidget(self.trade_scan_check)
        options.addWidget(self.ocr_warmup_button)
        options.addWidget(self.ocr_warmup_status)
        options.addWidget(self.log_check)
        options.addStretch()
        layout.addWidget(options_frame)

        self.tabs = QTabWidget()
        self.tabs.setProperty("sidro_dashboard_tabs", True)
        self.tabs.setIconSize(QSize(18, 18))
        layout.addWidget(self.tabs, 1)

        inventory_tab = QWidget()
        inventory_tab.setObjectName("InventoryPage")
        inventory_layout = QVBoxLayout(inventory_tab)
        inventory_layout.setContentsMargins(0, 0, 0, 0)
        inventory_layout.setSpacing(0)

        self.summary = QLabel("")
        self.summary.setObjectName("InventorySummary")
        self.summary.setMinimumHeight(32)
        self.summary.setText("Ready")
        status_frame = QFrame()
        status_frame.setObjectName("InventoryStatus")
        inventory_status = QHBoxLayout(status_frame)
        inventory_status.setContentsMargins(12, 9, 12, 9)
        inventory_status.setSpacing(10)
        inventory_status.addWidget(self.summary, 1)
        self.inventory_search = QLineEdit()
        self.inventory_search.setPlaceholderText("Search by name...")
        self.inventory_search.setAccessibleName("Search inventory by name")
        self.inventory_search.setClearButtonEnabled(True)
        self.inventory_search.setMinimumWidth(260)
        self.inventory_search.setMaximumWidth(360)
        self.inventory_search.textChanged.connect(self._filter_inventory_rows)
        inventory_status.addWidget(self.inventory_search)
        inventory_layout.addWidget(status_frame)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "Use", "Names ↕", "Chat", "Quantity ↕", "Lowest", "Most Common", "Highest",
            "Price ↕", "Visible", "Delete", "Sort",
        ])
        self.table.setAlternatingRowColors(True)
        _prepare_table(self.table, [42, 330, 56, 112, 78, 104, 78, 112, 96, 102, 0], stretch_column=1)
        self.table.setColumnHidden(10, True)
        self._listing_sort_orders = {
            1: Qt.AscendingOrder,
            3: Qt.DescendingOrder,
            7: Qt.DescendingOrder,
        }
        self._active_listing_sort_column: int | None = None
        self.table.horizontalHeader().sectionClicked.connect(self._sort_inventory_column)
        self.table.horizontalHeaderItem(1).setToolTip("Click to sort names A-Z or Z-A")
        self.table.horizontalHeaderItem(3).setToolTip("Click to sort by your listed quantity")
        self.table.horizontalHeaderItem(7).setToolTip("Click to sort by your listed price")
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(max(44, int(getattr(config, "UI_FONT_SIZE", 12)) + 28))
        inventory_layout.addWidget(self.table, 1)

        footer_frame = QFrame()
        footer_frame.setObjectName("Toolbar")
        inventory_layout.addSpacing(12)
        footer_bar = QBoxLayout(QBoxLayout.LeftToRight, footer_frame)
        footer_bar.setContentsMargins(12, 10, 12, 10)
        footer_bar.setSpacing(12)
        selection_group, selection_controls = toolbar_group()
        action_group, action_controls = toolbar_group()
        footer_bar.addWidget(selection_group)
        footer_bar.addStretch()
        footer_bar.addWidget(action_group)
        self._responsive_bars.append((footer_bar, selection_group, action_group))
        footer = selection_controls
        select_label = QLabel("Select rows")
        select_label.setObjectName("Muted")
        footer.addWidget(select_label)
        self.selection_feedback = QLabel("0 selected")
        self.selection_feedback.setObjectName("SelectionFeedback")
        all_btn = _button("All")
        prime_btn = _button("Prime")
        non_prime_btn = _button("Non-Prime")
        none_btn = _button("None")
        update = _button("Update selected", C_GREEN, "white")
        delete_selected = _button("Delete selected", C_RED, "white")
        self.mode = QComboBox()
        self.mode.addItems(["Most Common", "Most Common -1", "Quicksell", "Lowest +1p", "Lowest +2p", "Lowest +3p"])
        self.mode.currentTextChanged.connect(self.apply_mode)
        preset_help = _button("Preset Help", C_BG3, C_TEXT)
        preset_help.clicked.connect(self._preset_help)
        all_btn.clicked.connect(lambda: self._set_all(True))
        prime_btn.clicked.connect(self._select_prime)
        non_prime_btn.clicked.connect(self._select_non_prime)
        none_btn.clicked.connect(lambda: self._set_all(False))
        update.clicked.connect(self._update_selected)
        delete_selected.clicked.connect(self._delete_selected)
        footer.addWidget(all_btn)
        footer.addWidget(prime_btn)
        footer.addWidget(non_prime_btn)
        footer.addWidget(none_btn)
        footer.addWidget(self.selection_feedback)
        footer = action_controls
        preset_label = QLabel("Price strategy")
        preset_label.setObjectName("Muted")
        footer.addWidget(preset_label)
        footer.addWidget(self.mode)
        footer.addWidget(preset_help)
        footer.addWidget(delete_selected)
        footer.addWidget(update)
        inventory_layout.addWidget(footer_frame)

        self.tabs.addTab(inventory_tab, _ui_icon("inventory", C_HEADER), "Inventory Management")
        all_visible = _button("Show all", C_BG3, C_TEXT)
        all_hidden = _button("Hide all", C_BG3, C_TEXT)
        all_visible.clicked.connect(lambda: self._set_all_visibility(True))
        all_hidden.clicked.connect(lambda: self._set_all_visibility(False))
        inventory_status.addWidget(all_visible)
        inventory_status.addWidget(all_hidden)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for bar, left, right in getattr(self, "_responsive_bars", []):
            margins = bar.contentsMargins()
            needed = left.minimumSizeHint().width() + right.minimumSizeHint().width() + bar.spacing()
            available = self.width() - 32 - margins.left() - margins.right()
            bar.setDirection(QBoxLayout.TopToBottom if needed > available else QBoxLayout.LeftToRight)

    def showEvent(self, event):
        if self._windows_frame is not None:
            self._windows_frame.configure()
        super().showEvent(event)

    def _toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def _minimize_window(self):
        frame = self._windows_frame
        if frame is not None and frame.hwnd is not None:
            frame.minimize()
        else:
            self.showMinimized()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange and hasattr(self, "window_maximize_button"):
            name = "restore" if self.isMaximized() else "maximize"
            button = self.window_maximize_button
            button.setProperty("sidro_caption_icon", name)
            button.setIcon(_ui_icon(name, C_TEXT))
            button.setAccessibleName(name.capitalize())
            button.setToolTip(name.capitalize())
            frame = getattr(self, "_windows_frame", None)
            if frame is not None:
                frame.hover()
        super().changeEvent(event)

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (0x0083, 0x0085):  # WM_NCCALCSIZE / WM_NCPAINT
                return True, 0
            if msg.message == 0x0086:  # WM_NCACTIVATE: never paint a system caption.
                return True, 1
            frame = getattr(self, "_windows_frame", None)
            if frame is not None and frame.hwnd is not None:
                handled, result = frame.handle(msg)
                if handled:
                    return True, result
        return super().nativeEvent(event_type, message)

    def _first_launch(self):
        config.FIRST_LAUNCH = False
        FirstLaunchDialog(self._settings, self).exec()

    def _startup_greeting(self):
        message = "Welcome, Master! Your maid is ready for service."
        if getattr(config, "SIDRO_WELCOME_AUDIO_ENABLED", True):
            play_voice_line(message, "startup_greeting")
        if not getattr(config, "VISUAL_NOTIFICATIONS_ENABLED", True):
            return
        frequency = str(getattr(config, "SIDRO_WELCOME_VISUAL_FREQUENCY", "once_a_day"))
        if frequency == "never":
            return
        if frequency == "once_a_day":
            from datetime import date

            today = date.today().isoformat()
            settings_path = config.APP_SETTINGS_PATH
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                settings = {}
            if not isinstance(settings, dict):
                settings = {}
            if settings.get("sidro_welcome_visual_last_shown") == today:
                return
            config.SIDRO_WELCOME_VISUAL_LAST_SHOWN = today
            _save_ui_settings()
        greeting = SidroGreeting(message)
        self.startup_greeting = greeting
        greeting.destroyed.connect(lambda _obj=None: setattr(self, "startup_greeting", None))
        greeting.place_near(self)
        greeting.show()

    def _side_text(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        return label

    def set_ocr_load_state(self, loaded: bool, status: str | None = None):
        button = getattr(self, "ocr_warmup_button", None)
        if button is None:
            return
        # Gold is the available load action; red still marks the unload action.
        role = "Danger" if loaded else "Accent"
        fallback = C_RED if loaded else C_GOLD
        button.setText("Unload OCRs" if loaded else "Load OCRs")
        button.setProperty("sidro_theme_background_role", role)
        button.setProperty("sidro_theme_background_fallback", fallback)
        button.setProperty("sidro_theme_text_role", "Text" if loaded else "Header")
        button.setProperty("sidro_theme_text_fallback", C_TEXT if loaded else C_HEADER)
        _refresh_themed_button(button)
        if status is not None and hasattr(self, "ocr_warmup_status"):
            self.ocr_warmup_status.setText(status)

    def _feature_info(self, title: str, body: str):
        QMessageBox.information(self, title, body)

    def _scanner_help(self):
        QMessageBox.information(
            self,
            "Scanner Help",
            "Single item scan\n"
            "Drag around one item on screen. Include its name and quantity badge (xN) in the selection; press Esc or right-click to cancel. "
            "Sidro reads the name, uses an included xN badge as the starting quantity, then looks up market prices and checks for an existing sell listing.\n"
            "Review the item, quantity, price, and action before confirming. You can create a new listing, update an existing listing, or skip it.\n"
            f"Hotkey: {config.HOTKEY_SCAN}\n\n"
            "Inventory scan (6x3)\n"
            f"Reads the first three visible Inventory/Sell rows and opens the listing review.\n"
            f"Hotkey: {config.HOTKEY_INVENTORY_SCAN}\n\n"
            "Inventory scan (bottom 6)\n"
            f"Reads only the final visible bottom row after you scroll to the end.\n"
            f"Hotkey: {config.HOTKEY_INVENTORY_BOTTOM_SCAN}\n\n"
            "Trade monitor\n"
            "Use this on the trade screen after your item is placed. It queues that item and waits for the final successful trade dialog before updating your WFM listing.\n"
            f"Hotkey: {config.HOTKEY_TRADE_MONITOR}\n\n"
            "Reward prices\n"
            "Reads Void Fissure reward cards and shows market values. Live monitoring queues each selected reward without opening a dialog; use Review rewards after your fissure run to review the batch.\n"
            "Use the checkbox for live overlay, or press the manual hotkey.\n"
            f"Hotkey: {config.HOTKEY_REWARD_SCAN}",
        )

    def _preset_help(self):
        QMessageBox.information(
            self,
            "Selling presets",
            "Most Common: Uses the most common price in the lowest price band supported by at least two sellers. "
            "Isolated cheaper offers are skipped; each seller counts once. If tied, picks the lower value. "
            "If no band has two sellers, uses the next-lowest offer when available.\n\n"
            "Most Common -1: Same as Most Common, then subtracts 1 platinum for faster selling.\n\n"
            "Quicksell: Matches the lowest online-in-game seller.\n\n"
            "Lowest +1p / +2p / +3p: Adds that amount to the current lowest online-in-game seller.\n\n"
            "All presets respect your minimum platinum setting.",
        )

    def _trade_chat(self):
        messages = list(getattr(config, "TRADE_CHAT_MESSAGES", [""]) or [""])
        self.trade_chat_dialog = ChatMessagesDialog(
            messages,
            self._save_trade_chat_messages,
            self._copy_text,
            "Trade",
            self,
        )
        self.trade_chat_dialog.exec()

    def _recruitment_chat(self):
        messages = list(getattr(config, "RECRUITMENT_CHAT_MESSAGES", [""]) or [""])
        self.recruitment_chat_dialog = ChatMessagesDialog(
            messages,
            self._save_recruitment_chat_messages,
            self._copy_text,
            "Recruitment",
            self,
        )
        self.recruitment_chat_dialog.exec()

    def _save_trade_chat_messages(self, messages: list[str]):
        config.TRADE_CHAT_MESSAGES = [str(message) for message in messages]
        if not config.TRADE_CHAT_MESSAGES:
            config.TRADE_CHAT_MESSAGES = [""]
        _save_ui_settings()

    def _save_recruitment_chat_messages(self, messages: list[str]):
        config.RECRUITMENT_CHAT_MESSAGES = [str(message) for message in messages]
        if not config.RECRUITMENT_CHAT_MESSAGES:
            config.RECRUITMENT_CHAT_MESSAGES = [""]
        _save_ui_settings()

    def _notify(self, message: str, status: str = "info", duration_ms: int = 3500, sound: str | None = None):
        if hasattr(self, "summary"):
            self.summary.setText(message)
        sound_event = sound or {
            "success": "success",
            "warning": "warning",
            "error": "error",
            "trade": "trade",
        }.get(status, "info")
        play_alert(sound_event)
        if not getattr(config, "VISUAL_NOTIFICATIONS_ENABLED", True):
            return
        toast = Toast(message, status, duration_ms)
        self.local_toasts = [item for item in self.local_toasts if item.isVisible()]
        self.local_toasts.append(toast)
        toast.destroyed.connect(lambda _obj=None, t=toast: self._remove_local_toast(t))
        self._place_local_toasts()
        toast.show()

    def _remove_local_toast(self, toast: Toast):
        self.local_toasts = [item for item in self.local_toasts if item is not toast and item.isVisible()]
        self._place_local_toasts()

    def _place_local_toasts(self):
        for index, toast in enumerate(self.local_toasts[:5]):
            toast.place(index)

    def _copy_text(self, text: str, success_message: str, failure_message: str) -> bool:
        text = str(text or "").strip()
        if not text:
            self._notify(failure_message, "warning")
            return False
        QApplication.clipboard().setText(text)
        self._notify(success_message, "success")
        return True

    def _show_buyers(self, url_name: str, display_name: str):
        url_name = str(url_name or "").strip()
        display_name = str(display_name or "").strip()
        if not url_name:
            self._notify("No item URL found for buyer lookup.", "warning")
            return
        self.summary.setText(f"Looking up buyers for {display_name}...")

        def worker():
            try:
                self.buyer_search_ready.emit(self.on_buyer_search(url_name, display_name))
            except Exception as e:
                self.buyer_search_error.emit(str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_buyer_search(self, result: dict):
        orders = result.get("top_orders", []) or []
        display_name = str(result.get("display_name", "") or "item")
        if not orders:
            self._notify(f"No active buyers found for {display_name}.", "warning")
            return
        dialog = BuyerListDialog(result, self._copy_text, self)
        dialog.exec()

    def _fail_buyer_search(self, message: str):
        self._notify(f"Buyer lookup failed: {message}", "error", 5000)

    def refresh_rows(self):
        self.store.load()
        rows = self.store.listed_rows()
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            for listing in rows:
                row = self.table.rowCount()
                self.table.insertRow(row)
                use = _StyledCheckBox()
                use.setChecked(False)
                use.toggled.connect(self._update_selection_feedback)
                self.table.setCellWidget(row, 0, use)
                self.table.setItem(row, 1, _table_item(listing.display_name))
                chat = _button("💬", C_BG3, C_TEXT)
                chat.setToolTip("Show active buyers and copy a WTS whisper")
                chat.clicked.connect(
                    lambda _checked=False, url=listing.url_name, name=listing.display_name: self._show_buyers(url, name)
                )
                self.table.setCellWidget(row, 2, chat)
                qty = NumberEditor(1, 999, max(1, listing.quantity))
                self.table.setCellWidget(row, 3, qty)
                market = self.market_prices.get(listing.url_name, {})
                self.table.setItem(row, 4, _table_item(f"{market.get('lowest', '...')}p" if market else "..."))
                self.table.setItem(row, 5, _table_item(f"{market.get('most_common', market.get('price', '...'))}p" if market else "..."))
                self.table.setItem(row, 6, _table_item(f"{market.get('highest', '...')}p" if market else "..."))
                price = NumberEditor(config.MIN_LISTING_PRICE, 99999, max(config.MIN_LISTING_PRICE, listing.platinum))
                self.table.setCellWidget(row, 7, price)
                visible = QComboBox()
                visible.addItem("visible", True)
                visible.addItem("hidden", False)
                visible.setCurrentText("visible" if listing.visible else "hidden")
                self.table.setCellWidget(row, 8, visible)
                delete = _button("Delete", C_RED, "white")
                delete.clicked.connect(lambda _checked=False, url=listing.url_name: self._delete_one(url))
                self.table.setCellWidget(row, 9, delete)
                self.table.setVerticalHeaderItem(row, QTableWidgetItem(listing.url_name))
                sort_key = QTableWidgetItem()
                sort_key.setData(Qt.EditRole, 0)
                sort_key.setData(Qt.UserRole, listing.url_name)
                self.table.setItem(row, 10, sort_key)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()
        self._update_selection_feedback()
        self.summary.setText(f"Ready  ·  {len(self.store.listings)} listings")
        self._filter_inventory_rows(self.inventory_search.text())

    def _filter_inventory_rows(self, query: str):
        query = str(query or "").strip().casefold()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            name = item.text() if item is not None else ""
            self.table.setRowHidden(row, bool(query) and query not in name.casefold())

    def _sort_inventory_column(self, column: int):
        if column not in (1, 3, 7):
            return
        sort_order = self._listing_sort_orders[column]
        self._listing_sort_orders[column] = (
            Qt.AscendingOrder if sort_order == Qt.DescendingOrder else Qt.DescendingOrder
        )
        for row in range(self.table.rowCount()):
            if column == 1:
                name = self.table.item(row, 1)
                value = name.text().casefold() if name is not None else ""
            else:
                editor = self.table.cellWidget(row, column)
                value = editor.value() if isinstance(editor, NumberEditor) else 0
            sort_key = self.table.item(row, 10)
            if sort_key is None:
                sort_key = QTableWidgetItem()
                self.table.setItem(row, 10, sort_key)
                header = self.table.verticalHeaderItem(row)
                sort_key.setData(Qt.UserRole, header.text() if header is not None else "")
            sort_key.setData(Qt.EditRole, value)
        self.table.sortItems(10, sort_order)
        for row in range(self.table.rowCount()):
            sort_key = self.table.item(row, 10)
            if sort_key is not None:
                self.table.setVerticalHeaderItem(row, QTableWidgetItem(str(sort_key.data(Qt.UserRole) or "")))
        self._active_listing_sort_column = column
        quantity_label = "Quantity ↕"
        price_label = "Price ↕"
        names_label = "Names ↕"
        if column == 3:
            quantity_label = "Quantity ↓" if sort_order == Qt.DescendingOrder else "Quantity ↑"
        elif column == 7:
            price_label = "Price ↓" if sort_order == Qt.DescendingOrder else "Price ↑"
        else:
            names_label = "Names A-Z" if sort_order == Qt.AscendingOrder else "Names Z-A"
        self.table.setHorizontalHeaderItem(1, QTableWidgetItem(names_label))
        self.table.setHorizontalHeaderItem(3, QTableWidgetItem(quantity_label))
        self.table.setHorizontalHeaderItem(7, QTableWidgetItem(price_label))
        self.table.horizontalHeaderItem(1).setToolTip("Click to sort names A-Z or Z-A")
        self.table.horizontalHeaderItem(3).setToolTip("Click to sort by your listed quantity")
        self.table.horizontalHeaderItem(7).setToolTip("Click to sort by your listed price")

    def _apply_table_alignment(self):
        alignment = _table_text_alignment()
        for row in range(self.table.rowCount()):
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item is not None:
                    item.setTextAlignment(alignment)
                widget = self.table.cellWidget(row, col)
                if isinstance(widget, NumberEditor):
                    widget.applyAlignment()

    def _set_all(self, value: bool):
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, 0).setChecked(value)
        self._update_selection_feedback()

    def _select_prime(self):
        for row in range(self.table.rowCount()):
            url_item = self.table.verticalHeaderItem(row)
            name_item = self.table.item(row, 1)
            url = url_item.text() if url_item is not None else ""
            display_name = name_item.text() if name_item is not None else ""
            self.table.cellWidget(row, 0).setChecked(_is_prime_item(display_name, url))
        self._update_selection_feedback()

    def _select_non_prime(self):
        for row in range(self.table.rowCount()):
            url_item = self.table.verticalHeaderItem(row)
            name_item = self.table.item(row, 1)
            url = url_item.text() if url_item is not None else ""
            display_name = name_item.text() if name_item is not None else ""
            self.table.cellWidget(row, 0).setChecked(not _is_prime_item(display_name, url))
        self._update_selection_feedback()

    def _update_selection_feedback(self, _checked: bool = False):
        if not hasattr(self, "selection_feedback"):
            return
        selected = sum(
            bool(self.table.cellWidget(row, 0).isChecked())
            for row in range(self.table.rowCount())
        )
        total = self.table.rowCount()
        self.selection_feedback.setText(f"{selected} of {total} selected")

    def _set_reward_enabled(self, value: bool):
        config.REWARD_PRICE_CHECK_ENABLED = bool(value)
        _save_ui_settings()
        self.on_reward_toggle(bool(value))

    def _set_trade_scan_enabled(self, value: bool):
        self.on_trade_scan_toggle(bool(value))

    def set_reward_queue_count(self, count: int):
        count = max(0, int(count))
        self.reward_queue_button.setText(f"Review rewards ({count})")
        self.reward_queue_button.setEnabled(count > 0)

    def _set_log_viewing(self, value: bool):
        if value:
            if self.log_dialog is None:
                self.log_dialog = LogViewerDialog(self.log_stream, self)
                self.log_dialog.finished.connect(lambda _result: self.log_check.setChecked(False))
            self.log_dialog.show()
            self.log_dialog.raise_()
            self.log_dialog.activateWindow()
            return
        if self.log_dialog is not None and self.log_dialog.isVisible():
            self.log_dialog.close()

    def preview_prices(self):
        urls = [self.table.verticalHeaderItem(row).text() for row in range(self.table.rowCount())]
        if not urls:
            return
        self.summary.setText("Fetching market prices...")

        def worker():
            try:
                prices = self.on_price_preview(urls, config.PRICE_STRATEGY_MOST_COMMON)
                msg = f"Loaded market prices for {len(prices)} item(s)."
            except Exception as e:
                prices = {}
                msg = f"ERROR: {e}"
            self.prices_ready.emit(prices, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_prices(self, prices: dict[str, dict[str, int]], msg: str):
        self.market_prices.update(prices)
        self.table.setUpdatesEnabled(False)
        try:
            for row in range(self.table.rowCount()):
                url = self.table.verticalHeaderItem(row).text()
                if url in self.market_prices:
                    info = self.market_prices[url]
                    for column, value in (
                        (4, info.get("lowest", "...")),
                        (5, info.get("most_common", info.get("price", "..."))),
                        (6, info.get("highest", "...")),
                    ):
                        item = self.table.item(row, column)
                        if item is not None:
                            item.setText(f"{value}p")
                            item.setTextAlignment(_table_text_alignment())
            self.apply_mode()
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()
        self.summary.setText(msg)

    def apply_mode(self):
        mode = self.mode.currentText()
        missing = False
        for row in range(self.table.rowCount()):
            url = self.table.verticalHeaderItem(row).text()
            info = self.market_prices.get(url)
            if not info:
                missing = True
                continue
            lowest = info["lowest"]
            if mode == "Quicksell":
                price = lowest
            elif mode == "Most Common -1":
                price = info["price"] - 1
            elif mode == "Lowest +1p":
                price = lowest + 1
            elif mode == "Lowest +2p":
                price = lowest + 2
            elif mode == "Lowest +3p":
                price = lowest + 3
            else:
                price = info["price"]
            self.table.cellWidget(row, 7).setValue(max(config.MIN_LISTING_PRICE, int(price)))
        if missing and self.table.rowCount():
            self.summary.setText("Some rows do not have loaded market prices yet.")

    def _refresh(self):
        self._run_background(self.on_refresh, reload_rows=True)

    def _token(self):
        TokenDialog(self.on_save_token).exec()
        if getattr(config, "WFM_TOKEN", ""):
            self._hide_login_button()
            self._notify("Token saved. Login button moved to Settings backup token.", "success")

    def _settings(self):
        old_token = str(getattr(config, "WFM_TOKEN", "") or "")
        result = SettingsDialog(
            self,
            on_hotkeys_changed=self.on_hotkeys_changed,
            on_ocr_changed=self.on_ocr_changed,
            on_save_settings_token=self.on_save_settings_token,
        ).exec()
        _load_color_settings()
        _apply_app_style(QApplication.instance())
        self._apply_table_alignment()
        new_token = str(getattr(config, "WFM_TOKEN", "") or "")
        if result == QDialog.Accepted and new_token and new_token != old_token:
            self._hide_login_button()
            self._run_background(self.on_refresh, reload_rows=True)

    def _hide_login_button(self):
        if self.login_button is None:
            return
        self.login_button.hide()
        self.login_button.setParent(None)
        self.login_button = None

    def _update_selected(self):
        selected = {}
        for row, url in self._selected_rows():
            selected[url] = {
                "price": self.table.cellWidget(row, 7).value(),
                "quantity": self.table.cellWidget(row, 3).value(),
                "visible": bool(self.table.cellWidget(row, 8).currentData()),
            }
        if not selected:
            self.summary.setText("No rows selected.")
            return
        self._run_background(lambda: self.on_update_prices(selected, self._strategy()), reload_rows=True)

    def _update_rows(self, rows: list[tuple[int, str]], summary_empty: str = "No rows selected."):
        selected = {}
        for row, url in rows:
            selected[url] = {
                "price": self.table.cellWidget(row, 7).value(),
                "quantity": self.table.cellWidget(row, 3).value(),
                "visible": bool(self.table.cellWidget(row, 8).currentData()),
            }
        if not selected:
            self.summary.setText(summary_empty)
            return
        self._run_background(lambda: self.on_update_prices(selected, self._strategy()), reload_rows=True)

    def _set_all_visibility(self, visible: bool):
        rows: list[tuple[int, str]] = []
        label = "visible" if visible else "hidden"
        for row in range(self.table.rowCount()):
            header = self.table.verticalHeaderItem(row)
            combo = self.table.cellWidget(row, 8)
            if header is None or combo is None:
                continue
            rows.append((row, header.text()))
        if not rows:
            self.summary.setText("No listings loaded.")
            return
        answer = QMessageBox.question(
            self,
            f"Make all listings {label}?",
            f"This will update {len(rows)} current warframe.market sell listing(s) to {label}. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        for row, _url in rows:
            combo = self.table.cellWidget(row, 8)
            if combo is not None:
                combo.setCurrentText(label)
        self._update_rows(rows, "No listings loaded.")

    def _strategy(self) -> str:
        return {
            "Most Common -1": config.PRICE_STRATEGY_MOST_COMMON_MINUS_1,
            "Quicksell": config.PRICE_STRATEGY_FAST_SELL,
            "Lowest +1p": config.PRICE_STRATEGY_LOWEST_PLUS_1,
            "Lowest +2p": config.PRICE_STRATEGY_LOWEST_PLUS_2,
            "Lowest +3p": config.PRICE_STRATEGY_LOWEST_PLUS_3,
        }.get(self.mode.currentText(), config.PRICE_STRATEGY_MOST_COMMON)

    def _run_background(self, action: Callable[[], str], reload_rows: bool = False, notify: bool = True):
        if self._background_busy:
            self.summary.setText("An action is already running. Please wait for it to finish.")
            return
        self._background_busy = True
        self._background_notify = notify
        self.summary.setText("Working...")

        def worker():
            try:
                msg = action()
            except Exception as e:
                msg = f"ERROR: {e}"
            self.background_ready.emit(msg, reload_rows)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_background(self, msg: str, reload_rows: bool):
        self._background_busy = False
        if reload_rows:
            self.market_prices.clear()
            self.refresh_rows()
            self.preview_prices()
        if getattr(self, "_background_notify", True) or msg.startswith("ERROR:"):
            self._notify(msg, self._background_status(msg), 6000)

    def _background_status(self, msg: str) -> str:
        text = str(msg or "").strip().lower()
        if not text:
            return "info"
        if text.startswith("error") or text.startswith("not authenticated"):
            return "error"
        if " failed" in text:
            if "0 failed" not in text and "0 skipped/failed" not in text:
                return "warning"
        if any(token in text for token in ("complete", "saved", "updated", "deleted")):
            return "success"
        return "info"

    def _update_all(self):
        self._set_all(True)
        self._update_selected()

    def _selected_rows(self) -> list[tuple[int, str]]:
        rows: list[tuple[int, str]] = []
        for row in range(self.table.rowCount()):
            use = self.table.cellWidget(row, 0)
            header = self.table.verticalHeaderItem(row)
            if use is None or header is None or not use.isChecked():
                continue
            rows.append((row, header.text()))
        return rows

    def _delete_selected(self):
        selected = [url for _row, url in self._selected_rows()]
        if not selected:
            self.summary.setText("No rows selected.")
            return
        answer = QMessageBox.question(
            self,
            "Delete selected listings?",
            f"This will permanently delete {len(selected)} selected warframe.market sell listing(s). Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_background(lambda: self.on_delete_listings(selected), reload_rows=True)

    def _delete_all(self):
        count = self.table.rowCount()
        if count <= 0:
            self.summary.setText("No listings to delete.")
            return
        answer = QMessageBox.question(
            self,
            "Delete all listings?",
            f"This will permanently delete all {count} current warframe.market sell listings. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_background(self.on_delete_all_listings, reload_rows=True)

    def _delete_one(self, url_name: str):
        display_name = self.store.display_name(url_name)
        answer = QMessageBox.question(
            self,
            "Delete listing?",
            f"Permanently delete this warframe.market sell listing?\n\n{display_name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_background(lambda: self.on_delete_listing(url_name), reload_rows=True)


class UIController:
    def __init__(self):
        self.app = QApplication.instance() or QApplication([])
        self.log_stream = _install_log_capture()
        icon = _app_icon()
        if not icon.isNull():
            self.app.setWindowIcon(icon)
        _apply_app_style(self.app)
        self.invoker = _Invoker()
        self.dashboard: Dashboard | None = None
        self.reward_queue_count = 0
        self.reward_tags: list[RewardPriceTag] = []
        self.toasts: list[Toast] = []
        self.saved_ducat_dialogs: list[SavedDucatDialog] = []
        self.support_reminders: SupportReminders | None = None
        self.support_reminder_timer: QTimer | None = None
        self.support_reminder_dialog: SupportReminderDialog | None = None

    def start_support_reminders(self, is_busy: Callable[[], bool]):
        def start():
            if self.support_reminder_timer is not None:
                return
            self.support_reminders = SupportReminders(config.USER_DATA_DIR / "support_reminder.json")
            self.support_reminder_timer = QTimer(self.invoker)
            self.support_reminder_timer.setInterval(30_000)
            self.support_reminder_timer.timeout.connect(lambda: self._tick_support_reminder(is_busy))
            self.support_reminder_timer.start()
        self.invoke(start)

    def _tick_support_reminder(self, is_busy: Callable[[], bool]):
        dashboard = self.dashboard
        can_show = bool(
            dashboard is not None and dashboard.isVisible() and dashboard.isActiveWindow()
            and not dashboard.isMinimized() and QApplication.activeModalWidget() is None
            and not getattr(dashboard, "_background_busy", False) and not is_busy()
            and (self.support_reminder_dialog is None or not self.support_reminder_dialog.isVisible())
        )
        message = self.support_reminders.tick(can_show=can_show)
        if message is not None:
            if self.support_reminder_dialog is not None:
                self.support_reminder_dialog.deleteLater()
            self.support_reminder_dialog = SupportReminderDialog(message, dashboard)
            self.support_reminder_dialog.show()

    def invoke(self, fn: Callable[[], object], wait: bool = False):
        if wait and QThread.currentThread() == self.app.thread():
            return fn()
        if not wait:
            self.invoker.call.emit(fn)
            return None
        done = threading.Event()
        result: queue.Queue = queue.Queue(maxsize=1)

        def wrapped():
            try:
                result.put((True, fn()))
            except Exception as e:
                result.put((False, e))
            finally:
                done.set()

        self.invoker.call.emit(wrapped)
        done.wait()
        ok, value = result.get()
        if ok:
            return value
        raise value

    def show_dashboard(self, **kwargs):
        def _show():
            if self.dashboard is None:
                self.dashboard = Dashboard(**kwargs)
                self.dashboard.set_reward_queue_count(self.reward_queue_count)
            self.dashboard.showMaximized()
            self.dashboard.raise_()
            self.dashboard.activateWindow()
        self.invoke(_show)

    def refresh_dashboard_profile(self):
        """Reload the visible listing profile after an external profile mutation."""
        def _refresh():
            dashboard = self.dashboard
            if dashboard is None or not dashboard.isVisible():
                return
            dashboard.refresh_rows()
            dashboard.market_prices.clear()
            dashboard.preview_prices()
        self.invoke(_refresh)

    def set_reward_queue_count(self, count: int):
        self.reward_queue_count = max(0, int(count))
        self.invoke(
            lambda: self.dashboard.set_reward_queue_count(self.reward_queue_count)
            if self.dashboard is not None else None
        )

    def show_inventory_review(
        self,
        items: list[InventoryItem],
        price_map: dict[str, PriceInfo],
        existing_listing_map: dict[str, ExistingListing] | None = None,
        scan_title: str = "Inventory scan",
        saved_ducat_items: list[dict] | None = None,
        failed_positions: list[tuple[int, int]] | None = None,
        preselect_single_item: bool = False,
        queued_reward_rows: list[dict] | None = None,
    ):
        def _show():
            dialog = InventoryReviewDialog(
                items,
                price_map,
                existing_listing_map,
                scan_title,
                saved_ducat_items,
                failed_positions,
                preselect_single_item,
                parent=None,
                queued_reward_rows=queued_reward_rows,
            )
            # Independent native window: dashboard focus/minimization must not
            # follow the review, and reviewing must not block other windows.
            dialog.setWindowModality(Qt.NonModal)

            def _bring_review_forward_once():
                dialog.raise_()
                dialog.activateWindow()
                if sys.platform != "win32":
                    return
                try:
                    import ctypes

                    hwnd = int(dialog.winId())
                    user32 = ctypes.windll.user32
                    flags = 0x0001 | 0x0002 | 0x0040  # NOSIZE | NOMOVE | SHOWWINDOW
                    user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, flags)  # HWND_TOPMOST
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass

            # Wait for the result without QDialog.exec(), which would make this
            # independent window application-modal. Keep the UI event loop live.
            review_loop = QEventLoop()
            dialog.finished.connect(review_loop.quit)
            QApplication.instance().aboutToQuit.connect(review_loop.quit)
            dialog.show()
            QTimer.singleShot(0, _bring_review_forward_once)
            review_loop.exec()
            if dialog.result() == QDialog.Accepted:
                selected_items = dialog.selected_items
            else:
                selected_items = []
            dialog.hide()
            dialog.deleteLater()
            if queued_reward_rows is not None:
                return selected_items, dialog.removed_queued_rewards
            return selected_items
        return self.invoke(_show, wait=True)

    def show_saved_ducat_items(self, rows: list[dict], title: str = "Saved for ducats"):
        if not rows:
            return

        def _show():
            dialog = SavedDucatDialog(rows, title)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            self.saved_ducat_dialogs = [item for item in self.saved_ducat_dialogs if item.isVisible()]
            self.saved_ducat_dialogs.append(dialog)
            dialog.destroyed.connect(
                lambda _obj=None, d=dialog: self.saved_ducat_dialogs.remove(d)
                if d in self.saved_ducat_dialogs else None
            )
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()

        self.invoke(_show)

    def ask_confirmation(
        self,
        matched_item: MatchedItem,
        price_info: PriceInfo,
        listing_price: int,
        quantity: int = 1,
        preview_image: QImage | None = None,
        existing_listing: ExistingListing | None = None,
    ):
        return self.review_single_item(
            matched_item,
            price_info,
            listing_price,
            quantity=quantity,
            preview_image=preview_image,
            existing_listing=existing_listing,
        )

    def snip_screen_area(self):
        def _show():
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            if screen is None:
                return None
            screenshot = screen.grabWindow(0)
            if screenshot.isNull():
                self.toast("Could not capture this screen for item selection.", "error", 5000)
                return None
            dialog = ScreenSelectionOverlay(screen, screenshot)
            if dialog.exec() != QDialog.Accepted or dialog.selected_image is None:
                return None
            image = dialog.selected_image.convertToFormat(QImage.Format.Format_RGB888)
            pixels = np.frombuffer(
                image.constBits(),
                dtype=np.uint8,
                count=image.sizeInBytes(),
            )
            rgb = pixels.reshape(image.height(), image.bytesPerLine())[:, : image.width() * 3]
            rgb = rgb.reshape(image.height(), image.width(), 3)
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy(), dialog.selected_image.copy()

        return self.invoke(_show, wait=True)

    def review_single_item(
        self,
        matched_item: MatchedItem,
        price_info: PriceInfo,
        listing_price: int,
        quantity: int = 1,
        preview_image: QImage | None = None,
        existing_listing: ExistingListing | None = None,
    ):
        def _show():
            dialog = ConfirmationDialog(
                matched_item,
                price_info,
                listing_price,
                quantity=quantity,
                preview_image=preview_image,
                existing_listing=existing_listing,
            )
            if dialog.exec() == QDialog.Accepted:
                return (
                    UserDecision.CONFIRMED,
                    dialog.quantity,
                    dialog.price_editor.value(),
                    dialog.selected_action,
                )
            return UserDecision.CANCELLED, quantity, listing_price, "SKIP"
        return self.invoke(_show, wait=True)

    def toast(self, message: str, status: str = "info", duration_ms: int = 3500):
        if not getattr(config, "VISUAL_NOTIFICATIONS_ENABLED", True):
            return

        def _show():
            toast = Toast(message, status, duration_ms)
            self.toasts = [item for item in self.toasts if item.isVisible()]
            self.toasts.append(toast)
            toast.destroyed.connect(lambda _obj=None, t=toast: self._remove_toast(t))
            self._place_toasts()
            toast.show()

        self.invoke(_show)

    def alert(self, message: str, status: str = "info", duration_ms: int = 5000, sound: str = "info"):
        self.toast(message, status, duration_ms)
        play_alert(sound)

    def _remove_toast(self, toast: Toast):
        self.toasts = [item for item in self.toasts if item is not toast and item.isVisible()]
        self._place_toasts()

    def _place_toasts(self):
        for index, toast in enumerate(self.toasts[:5]):
            toast.place(index)

    def show_reward_prices(self, rewards: list[dict], duration_ms: int | None = None):
        def _show():
            display_ms = int(
                duration_ms
                if duration_ms is not None
                else getattr(config, "REWARD_PRICE_OVERLAY_DURATION_MS", 8000)
            )
            for tag in self.reward_tags:
                tag.close()
            self.reward_tags = []
            for reward in rewards:
                x, y, w, _h = reward["card_rect"]
                base_card_w = float(getattr(config, "REWARD_CARD_RECTS", [(0, 0, 240, 240)])[0][2])
                scale = max(0.1, float(w) / base_card_w)
                tag_x = int(x + (w / 2) - (95 * scale))
                if getattr(config, "REWARD_PRICE_TAG_POSITION", "above") == "above":
                    tag_y = max(0, int(y - (84 * scale)))
                else:
                    fixed_tag_y = getattr(config, "REWARD_PRICE_TAG_Y", None)
                    tag_y = int((y + 238 * scale) if fixed_tag_y is None else fixed_tag_y * scale)
                tag = RewardPriceTag(
                    reward["display_name"],
                    reward.get("price"),
                    int(reward.get("match_score", 0) or 0),
                    tag_x,
                    tag_y,
                    display_ms,
                )
                tag.show()
                self.reward_tags.append(tag)
                alternates = list(reward.get("alternates") or [])
                if alternates and getattr(config, "REWARD_ALTERNATE_TAG_ENABLED", True):
                    alt_x = int(x + (w / 2) - (115 * scale))
                    alt_y = int(y + getattr(config, "REWARD_ALTERNATE_TAG_Y_OFFSET", 410) * scale)
                    alt_tag = RewardAlternateTag(alternates, alt_x, alt_y, display_ms)
                    alt_tag.show()
                    self.reward_tags.append(alt_tag)

        self.invoke(_show)

    def run(self):
        self.app.exec()

    def stop(self):
        def stop():
            if self.support_reminder_timer is not None:
                self.support_reminder_timer.stop()
                self.support_reminders.tick(can_show=False)
            if self.support_reminder_dialog is not None:
                self.support_reminder_dialog.close()
            self.app.quit()
        if QThread.currentThread() == self.app.thread():
            stop()
        else:
            self.invoke(stop)

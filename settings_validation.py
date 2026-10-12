"""Validate editable preferences from untrusted configuration files."""

import math
import re

from name_aliases import sanitize_name_aliases


MAX_SETTINGS_BYTES = 2 * 1024 * 1024


def sanitize_settings(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Settings must be a JSON object.")
    clean = {}
    booleans = (
        "alert_sound_enabled", "visual_notifications_enabled", "sidro_notification_mascot",
        "sidro_welcome_audio_enabled", "ocr_fallback_enabled", "ducat_filter_enabled",
        "reward_price_check_enabled",
    )
    for key in booleans:
        if type(data.get(key)) is bool:
            clean[key] = data[key]
    for key, low, high in (
        ("min_listing_price", 1, 99999), ("ui_font_size", 9, 24),
        ("ocr_fallback_min_score", 0, 100), ("ocr_settings_version", 1, 1000),
        ("alert_sound_volume", 0, 1),
    ):
        value = data.get(key)
        if type(value) is int or (type(value) is float and math.isfinite(value)):
            value = max(low, min(high, value))
            clean[key] = value if key == "alert_sound_volume" else int(value)
    for key, choices in (
        ("update_check_frequency", ("daily", "weekly", "monthly", "never")),
        ("notification_corner", ("top_left", "top_right", "bottom_left", "bottom_right")),
        ("sidro_visual_character", ("robot", "maid")),
        ("sidro_welcome_visual_frequency", ("once_a_day", "every_launch", "never")),
        ("ui_table_text_alignment", ("left", "center", "right")),
        ("ocr_fallback_engine", ("rapidocr", "paddleocr")),
    ):
        if isinstance(data.get(key), str) and data[key] in choices:
            clean[key] = data[key]
    for key, limit in (
        ("alert_sound_custom_path", 4096), ("alert_sound_style", 80),
        ("wfm_ingame_name", 64), ("wfm_token", 16384),
        ("sidro_welcome_visual_last_shown", 10),
    ):
        value = data.get(key)
        if isinstance(value, str) and len(value) <= limit and "\x00" not in value:
            clean[key] = value
    font = data.get("ui_font_family")
    if "ocr_name_aliases" in data:
        clean["ocr_name_aliases"] = sanitize_name_aliases(data["ocr_name_aliases"])
    if isinstance(font, str) and re.fullmatch(r"[\w .,-]{1,100}", font):
        clean["ui_font_family"] = font
    colors = data.get("ui_colors")
    if isinstance(colors, dict):
        clean["ui_colors"] = {
            k: v for k, v in colors.items()
            if isinstance(k, str) and len(k) <= 80
            and isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?", v)
        }
    engines = data.get("ocr_engines")
    if isinstance(engines, dict):
        clean["ocr_engines"] = {
            k: v for k, v in engines.items()
            if k in ("single_scan", "inventory_6x3", "inventory_bottom6", "trade_monitor", "reward_prices")
            and isinstance(v, str) and v in ("rapidocr", "paddleocr")
        }
    values = data.get("ducat_bulk_plat_values")
    if isinstance(values, dict):
        clean["ducat_bulk_plat_values"] = {
            str(k): min(99999, max(0, v)) for k, v in values.items()
            if str(k) in ("15", "25", "45", "65", "100") and type(v) is int
        }
    for key in ("trade_chat_messages", "recruitment_chat_messages"):
        messages = data.get(key)
        if isinstance(messages, list):
            clean[key] = [v for v in messages[:100] if isinstance(v, str) and len(v) <= 10000] or [""]
    hotkeys = data.get("hotkeys")
    if isinstance(hotkeys, dict):
        fields = ("scan", "inventory_scan", "inventory_bottom_scan", "trade_monitor", "account_ui", "reward_scan", "ocr_warmup", "quit")
        names = set(fields) | {"HOTKEY_" + k.upper() for k in fields}
        clean["hotkeys"] = {
            k: v for k, v in hotkeys.items() if k in names and isinstance(v, str)
            and re.fullmatch(r"[a-zA-Z0-9+ ,_-]{1,80}", v)
        }
    return clean

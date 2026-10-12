"""Sidro configuration, defaults, resource paths and local data migration.

Runtime files normally live under %LOCALAPPDATA%/Sidro. Source diagnostics can
override that directory with TTB_DATA_DIR. Tokens, personal settings, account
state and generated files must remain outside the published source snapshot.
"""

import os
import json
import shutil
import sys
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from settings_validation import MAX_SETTINGS_BYTES, sanitize_settings

APP_NAME = "Sidro"
APP_SLUG = "Sidro"
APP_VERSION = "1.0.0"
IS_FROZEN = bool(getattr(sys, "frozen", False))
APP_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)).resolve()


def resource_path(filename: str) -> Path:
    """Return a bundled resource path that works in source and PyInstaller builds."""
    for base in (RESOURCE_DIR, APP_DIR):
        candidate = base / filename
        if candidate.exists():
            return candidate
    return APP_DIR / filename


def _choose_user_data_dir() -> Path:
    requested = os.getenv("TTB_DATA_DIR")
    if requested:
        candidate = Path(requested).expanduser()
    else:
        local_appdata = os.getenv("LOCALAPPDATA")
        candidate = (Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local") / APP_SLUG
    candidate = candidate.resolve()
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate


def _migrate_legacy_user_data(destination: Path):
    """Copy legacy files once, retaining the originals and any existing data."""
    legacy = APP_DIR / "user_data"
    marker = destination / ".legacy_user_data_migrated"
    if os.getenv("TTB_DATA_DIR") or marker.exists() or not legacy.is_dir():
        return
    if legacy.resolve() == destination.resolve():
        return
    import tempfile

    for source in legacy.rglob("*"):
        # Do not migrate obsolete access credentials or quota ledgers.
        if source.name.casefold() in {"patreon_auth.bin", "du.bin", "daily_usage.bin"}:
            continue
        if source.is_symlink() or not source.is_file():
            continue
        target = destination / source.relative_to(legacy)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temporary = Path(handle.name)
            shutil.copy2(source, temporary)
            # On Windows, rename fails if another process created the target.
            try:
                temporary.rename(target)
            except FileExistsError:
                pass
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    # Prevent deleted credentials from being restored on subsequent launches.
    marker.write_text("Legacy user_data migration completed.\n", encoding="utf-8")


USER_DATA_DIR = _choose_user_data_dir()
_migrate_legacy_user_data(USER_DATA_DIR)
ENV_PATH = USER_DATA_DIR / ".env"

# Source checkouts may keep a local .env for development, but packaged builds
# only read/write the permanent user data directory's .env.
if not IS_FROZEN:
    load_dotenv(APP_DIR / ".env")
load_dotenv(ENV_PATH, override=True)


def _seed_user_data_file(filename: str) -> Path:
    """Copy bundled public cache files into user_data on first launch."""
    destination = USER_DATA_DIR / filename
    if destination.exists():
        return destination
    source = RESOURCE_DIR / filename
    if source.exists():
        try:
            if source.resolve() != destination.resolve():
                shutil.copyfile(source, destination)
        except OSError:
            pass
    return destination

# ─────────────────────────────────────────────
# SCREEN CAPTURE — Region of Interest (ROI)
# ─────────────────────────────────────────────
# Define the bounding box (pixels) where item names appear on screen.
# Common Warframe contexts:
#   • End-of-mission reward screen: item name is in center-top area
#   • Inventory / Relic opening: center band
#   • Trading post: right panel
#
# Format: (left, top, width, height)
# Calibrate these for YOUR resolution. Default: 1920×1080 mission reward band.

ROI_PRESETS = {
    # ── PRIMARY: Inventory/Sell screen (your screenshot) ──────────────────
    # Captures the full item grid: 6 cols × N rows of Prime Part cards.
    # Text labels sit below each card thumbnail.
    # Tuned for 1920×1080. For 2560×1440 use "inventory_sell_1440p".
    "inventory_sell": (76, 199, 1230, 860),

    # ── 1440p variant (scale up proportionally) ────────────────────────────
    "inventory_sell_1440p": (101, 265, 1640, 1147),

    # ── Single-card text band only (fastest, lowest CPU) ──────────────────
    # Scans only the bottom label strip of each visible card row.
    # Use if full-grid scan is too slow on your machine.
    "inventory_label_strip": (50, 410, 975, 75),   # Row 1 labels
    # (You'd loop this with y offsets: 410, 578, 746 for rows 1-3)

    # ── Other contexts (kept for reference) ───────────────────────────────
    "mission_reward":  (560, 280, 800, 200),
    "void_rewards":    (450, 220, 1030, 265),
    "trade_post":      (700, 400, 520, 300),
    "trade_screen":    (0, 0, 1920, 1080),
    "manual":          (0, 0, 1920, 1080),         # Full screen fallback
}

ACTIVE_ROI_PRESET = "inventory_sell"   # ← Active preset

# Resolution hint — used to auto-select correct preset at startup
BASE_SCREEN_WIDTH = 1920
BASE_SCREEN_HEIGHT = 1080
SCREEN_RESOLUTION = os.getenv("SIDRO_SCREEN_RESOLUTION", "auto").strip().lower()


def _detected_screen_size() -> tuple[int, int]:
    """Return the primary display size, falling back to Sidro's 1080p baseline."""
    try:
        if sys.platform.startswith("win"):
            import ctypes

            user32 = ctypes.windll.user32
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
            width = int(user32.GetSystemMetrics(0))
            height = int(user32.GetSystemMetrics(1))
            if width > 0 and height > 0:
                return width, height
    except Exception:
        pass
    return BASE_SCREEN_WIDTH, BASE_SCREEN_HEIGHT


def screen_size() -> tuple[int, int]:
    preset = str(SCREEN_RESOLUTION or "auto").strip().lower()
    if preset in ("1080", "1080p", "1920x1080"):
        return 1920, 1080
    if preset in ("1440", "1440p", "2560x1440"):
        return 2560, 1440
    if preset in ("4k", "2160", "2160p", "3840x2160"):
        return 3840, 2160
    return _detected_screen_size()


def screen_scale() -> tuple[float, float]:
    width, height = screen_size()
    return width / BASE_SCREEN_WIDTH, height / BASE_SCREEN_HEIGHT


def scaled_int(value: int | float, axis: str = "x", frame_shape=None) -> int:
    if frame_shape is not None:
        frame_h, frame_w = frame_shape[:2]
        scale = frame_h / BASE_SCREEN_HEIGHT if axis == "y" else frame_w / BASE_SCREEN_WIDTH
    else:
        scale_x, scale_y = screen_scale()
        scale = scale_y if axis == "y" else scale_x
    return int(round(float(value) * scale))


def scaled_rect(rect: tuple[int, int, int, int], frame_shape=None) -> tuple[int, int, int, int]:
    x, y, w, h = rect
    return (
        scaled_int(x, "x", frame_shape),
        scaled_int(y, "y", frame_shape),
        scaled_int(w, "x", frame_shape),
        scaled_int(h, "y", frame_shape),
    )


def scaled_roi_preset(preset_name: str) -> tuple[int, int, int, int]:
    rect = ROI_PRESETS[preset_name]
    return scaled_rect(rect)

# Inventory grid layout (used by the batch scanner)
INVENTORY_GRID_COLS = 6
INVENTORY_GRID_ROWS = 3        # Only the first 3 rows have reliable readable labels
INVENTORY_BOTTOM_GRID_ROWS = 1  # Separate final visible row scan
INVENTORY_BOTTOM_ROW_START = 3  # Fourth row in the same captured grid ROI
INVENTORY_BOTTOM_ROW_Y_OFFSET = 590 # Absolute frame Y for the final visible row labels
INVENTORY_MIN_REVIEW_ITEMS = 12 # Ignore partial live scans/caches below this

# Inventory/Sell screen geometry for a 1920x1080 screenshot.
# Warframe leaves visible gaps between cards, so scanning must use stride
# instead of treating cards as a packed grid.
INVENTORY_GRID_ORIGIN = (76, 199)   # (left, top) of first card at 1080p
INVENTORY_CARD_WIDTH = 190          # visible card/image+label width
INVENTORY_CARD_HEIGHT = 190         # visible card/image+label height
INVENTORY_CARD_STRIDE_X = 207       # distance between card left edges
INVENTORY_CARD_STRIDE_Y = 222       # distance between card top edges
INVENTORY_LABEL_Y_OFFSET = 108      # high enough for three-line Warframe part labels
INVENTORY_LABEL_HEIGHT = 82         # label crop ends at the card bottom
INVENTORY_LABEL_X_PAD = 0           # widen/narrow label crop inside the card
INVENTORY_BADGE_SCALE = 4.0         # Quantity digits are tiny; keep this modest

# ─────────────────────────────────────────────
# OCR SETTINGS
# ─────────────────────────────────────────────
# Increment when changing recommended OCR defaults so existing user settings
# are migrated once, then remain under the user's control.
OCR_SETTINGS_VERSION = 5
OCR_SETTINGS_NEEDS_SAVE = False
OCR_ENGINE_CHOICES = ("rapidocr", "paddleocr")
OCR_ENGINE = os.getenv("TTB_OCR_ENGINE", "rapidocr").strip().lower()
if OCR_ENGINE not in OCR_ENGINE_CHOICES:
    OCR_ENGINE = "rapidocr"
OCR_ENGINE_BY_CONTEXT = {
    "single_scan": "rapidocr",
    "inventory_6x3": "rapidocr",
    "inventory_bottom6": "rapidocr",
    "trade_monitor": "rapidocr",
    "reward_prices": "rapidocr",
}
OCR_RECOMMENDED_BY_CONTEXT = {
    "single_scan": "rapidocr",
    "inventory_6x3": "rapidocr",
    "inventory_bottom6": "rapidocr",
    "trade_monitor": "rapidocr",
    "reward_prices": "rapidocr",
}
OCR_RECOMMENDATION_NOTES = {
    "single_scan": "RapidOCR with PP-OCRv6 small (bundled with Sidro).",
    "inventory_6x3": "RapidOCR with PP-OCRv6 small (bundled with Sidro).",
    "inventory_bottom6": "RapidOCR with PP-OCRv6 small (bundled with Sidro).",
    "trade_monitor": "RapidOCR with PP-OCRv6 small (bundled with Sidro).",
    "reward_prices": "RapidOCR with PP-OCRv6 small (bundled with Sidro).",
}
OCR_FALLBACK_ENABLED = False
OCR_FALLBACK_ENGINE = "rapidocr"
OCR_FALLBACK_MIN_SCORE = 85
OCR_PACKS_DIR = USER_DATA_DIR / "ocr_packs"
OCR_PACK_REQUIREMENTS = {
    "rapidocr": "requirements-rapidocr-test.txt",
    "paddleocr": "requirements-paddleocr-test.txt",
}
OCR_PACKS_DIR.mkdir(parents=True, exist_ok=True)
OCR_LANGUAGES = ["en"]
OCR_CONFIDENCE_THRESHOLD = 0.45  # Reject detections below this confidence
OCR_BATCH_SIZE = 6               # Conservative default for OCR backends that batch crops
OCR_CANVAS_SIZE = 768            # OCR detector fallback canvas
OCR_DIGIT_CANVAS_SIZE = 320      # Quantity badge detector fallback canvas
OCR_DEVICE_MODE = os.getenv("WFM_OCR_GPU", "cpu").strip().lower()  # Sidro 1.0 defaults to CPU. Dev override: auto | 0 | 1
OCR_USE_GPU = OCR_DEVICE_MODE in ("1", "true", "gpu", "yes")
OCR_AUTO_GPU_MAX_UTIL_PERCENT = 75
OCR_AUTO_GPU_MAX_MEMORY_MB = 5000
OCR_USE_LABEL_RECOGNIZER_ONLY = False # Keep detection enabled until recognizer-only crops are tuned per engine
OCR_USE_BADGE_RECOGNIZER_ONLY = False # Batched detector is faster and steadier here
OCR_FAST_ACCEPT_THRESHOLD = 90    # Low fast-path matches fall back to full OCR
PRELOAD_OCR_ON_START = False      # Load OCR on first use to reduce idle memory
INVENTORY_OCR_DEBUG_MISSES = (
    os.getenv("TTB_INVENTORY_OCR_DEBUG", "0").strip().lower()
    in ("1", "true", "yes", "on")
) # Test mode: print raw OCR for failed inventory cells on any backend
INVENTORY_OCR_SAVE_DEBUG_SHEET = INVENTORY_OCR_DEBUG_MISSES # Save debug_inventory_ocr.png in OCR test mode
# Image preprocessing before OCR
PREPROCESS_SCALE_FACTOR = 2.0    # Upscale small text regions
PREPROCESS_SHARPEN = True
PREPROCESS_DENOISE = True
INVENTORY_LABEL_SCALE = 2.0      # 3.0 was accurate but very expensive on GPU
WINDOWS_OCR_INVENTORY_LABEL_SCALE = 3.0 # Windows OCR needs larger clean text crops
WINDOWS_OCR_DEBUG_MISSES = True    # Print raw OCR for failed Windows OCR inventory cells
WINDOWS_OCR_SAVE_DEBUG_SHEET = True # Save debug_windows_ocr_inventory.png after scans

# ─────────────────────────────────────────────
# FUZZY MATCHING
# ─────────────────────────────────────────────
FUZZY_MATCH_THRESHOLD = 72       # 0–100; raise if too many false positives
FUZZY_TOP_N_CANDIDATES = 5       # Show top N matches in UI for user selection
INVENTORY_MATCH_PRIME_ONLY = True # Inventory/Sell Prime Parts tab should not match non-prime parts
PRIME_FAMILY_FUZZY_THRESHOLD = 72 # Recover partial family reads like "Bta on" -> Braton
PRIME_BROAD_FALLBACK_MIN_SCORE = 98 # Only accept familyless all-prime fallback when nearly exact
PRIME_BROAD_FALLBACK_MIN_GAP = 3    # Top match must beat runner-up by this many points

# ─────────────────────────────────────────────
# WARFRAME.MARKET API
# ─────────────────────────────────────────────
WFM_API_BASE_V1 = "https://api.warframe.market/v1"
WFM_API_BASE_V2 = "https://api.warframe.market/v2"
WFM_API_BASE = WFM_API_BASE_V2
WFM_TOKEN = os.getenv("WFM_TOKEN", "")        # Never hardcode; stored in user_data/.env
WFM_PLATFORM = "pc"                           # "pc" | "xbox" | "ps4" | "switch"
WFM_LANGUAGE = "en"
WFM_INGAME_NAME = os.getenv("WFM_INGAME_NAME", "")  # Your Warframe in-game name

# Listing strategy
UNDERCUT_BY_PLATINUM = 1         # Used only if PRICE_STRATEGY = "undercut_lowest"
MIN_LISTING_PRICE = 2            # Never list anything for 1p
DEFAULT_QUANTITY = 1
PRICE_STRATEGY = "lowest_cluster_mode"
PRICE_STRATEGY_MOST_COMMON = "lowest_cluster_mode"
PRICE_STRATEGY_BALANCED = "lowest_cluster_mode"
PRICE_STRATEGY_MOST_COMMON_MINUS_1 = "lowest_cluster_mode_minus_1"
PRICE_STRATEGY_FAST_SELL = "fast_lowest"
PRICE_STRATEGY_LOWEST_PLUS_1 = "lowest_plus_1"
PRICE_STRATEGY_LOWEST_PLUS_2 = "lowest_plus_2"
PRICE_STRATEGY_LOWEST_PLUS_3 = "lowest_plus_3"
PRICE_STATUS_FILTER = ("ingame",)       # warframe.market "online in game"
PRICE_FALLBACK_STATUSES = ()             # Price references never fall back to online/offline sellers
PRICE_CLUSTER_MAX_SPREAD = 5            # Maximum spread within each candidate price band
PRICE_CLUSTER_MAX_GAP = 5               # Stop before a price jump larger than this
LISTING_REQUEST_DELAY_MIN_SECONDS = 1.0 # API pacing between live write requests
LISTING_REQUEST_DELAY_MAX_SECONDS = 2.0
PRICE_LOOKUP_DELAY_SECONDS = 0.25       # Read pacing during review prep
PRICE_LOOKUP_WORKERS = 2                # Keep conservative; WFM returns 429 when bursty
PRICE_CACHE_TTL_SECONDS = 300           # Reuse in-session price lookups for 5 minutes
API_MIN_REQUEST_INTERVAL_SECONDS = 0.35 # Global client-side request spacing
ITEM_ID_LOOKUP_WORKERS = 2              # Keep low; /item lookups trigger 429 easily

# Safety gate for live market writes.
# Leave disabled until warframe.market's current official create/update order
# endpoint is verified. Scanning and price lookup still work normally.
ENABLE_LIVE_LISTING = True

# ─────────────────────────────────────────────
# ITEM CACHE
# ─────────────────────────────────────────────
ITEMS_CACHE_PATH = _seed_user_data_file("items_cache.json")
ITEM_IDS_CACHE_PATH = _seed_user_data_file("item_ids_cache.json")
ACCOUNT_STATE_PATH = USER_DATA_DIR / "account_state.json"
APP_SETTINGS_PATH = USER_DATA_DIR / "app_settings.json"
FIRST_LAUNCH = not APP_SETTINGS_PATH.exists()
DUCAT_CACHE_PATH = _seed_user_data_file("ducats_cache.json")
GENERATED_SOUNDS_DIR = USER_DATA_DIR / "generated_sounds"
WARFRAMESTAT_ITEMS_URL = "https://api.warframestat.us/items"
ITEMS_CACHE_TTL_HOURS = 24       # Refresh item list every 24 hours
ITEM_IDS_CACHE_TTL_HOURS = 168   # Item ids rarely change; reuse for a week
DUCAT_CACHE_TTL_HOURS = 168      # Ducat values are static except new Prime releases

# ─────────────────────────────────────────────
# DUCAT / BULK-SALE FILTER
# ─────────────────────────────────────────────
# When enabled, items whose current market price is at/below your configured
# bulk ducat value are skipped from one-by-one listing reviews.
DUCAT_FILTER_ENABLED = True
DUCAT_BULK_PLAT_VALUES = {
    15: 1,
    25: 2,
    45: 3,
    65: 6,
    100: 10,
}

# ─────────────────────────────────────────────
# HOTKEYS
# ─────────────────────────────────────────────
HOTKEY_SCAN = "ctrl+shift+s"          # Select one item with Windows Snipping Tool
HOTKEY_INVENTORY_SCAN = "ctrl+shift+i" # Inventory/Sell batch scan  ← PRIMARY
HOTKEY_INVENTORY_BOTTOM_SCAN = "ctrl+shift+b" # Inventory/Sell final visible row scan
HOTKEY_TRADE_MONITOR = "ctrl+shift+t"  # Trading post sold-item monitor
HOTKEY_ACCOUNT_UI = "ctrl+shift+m"     # Local inventory/listing manager
HOTKEY_REWARD_SCAN = "ctrl+shift+r"    # Void Fissure reward price overlay
HOTKEY_OCR_WARMUP = "ctrl+shift+o"      # Load or unload the configured OCR engines
HOTKEY_CONFIRM = "y"                   # Confirm listing (inside single-item dialog)
HOTKEY_CANCEL = "n"                    # Cancel listing
HOTKEY_QUIT = "ctrl+shift+q"           # Exit app

# ─────────────────────────────────────────────
# VOID FISSURE REWARD PRICE CHECK
# ─────────────────────────────────────────────
REWARD_PRICE_CHECK_ENABLED = False
REWARD_MONITOR_POLL_SECONDS = 1.0
REWARD_ANALYSIS_WINDOW_SECONDS = 15.0
REWARD_ANALYSIS_MAX_ALTERNATES = 3
TRADE_LIVE_SCAN_POLL_SECONDS = 1.0
TRADE_KNOWN_ITEM_MATCH_THRESHOLD = 88
# Full-row calibration; the scanner centers shorter reward rows automatically.
REWARD_CARD_RECTS = [
    (476, 223, 240, 240),
    (719, 223, 240, 240),
    (963, 223, 240, 240),
    (1204, 223, 240, 240),
]
REWARD_TITLE_ROI = (280, 38, 610, 72)
REWARD_TITLE_GOLD_RATIO_MIN = 0.006
REWARD_LABEL_Y_OFFSET = 178
REWARD_LABEL_HEIGHT = 72
REWARD_LABEL_X_PAD = 8
REWARD_PRICE_TAG_Y = 462
REWARD_PRICE_TAG_POSITION = "above" # above | fixed
REWARD_ALTERNATE_TAG_ENABLED = True
REWARD_ALTERNATE_TAG_Y_OFFSET = 410
REWARD_NAME_BAND = (450, 480, 1030, 95)
REWARD_PRICE_OVERLAY_DURATION_MS = 8000
REWARD_OCR_CONFIDENCE_THRESHOLD = 0.20
REWARD_CONFIDENCE_OK_SCORE = 92
REWARD_CONFIDENCE_WARN_SCORE = 82
REWARD_FAST_ACCEPT_THRESHOLD = 88
REWARD_IGNORE_TEXT_CONTAINS = (
    "forma",
)
REWARD_REJECT_TEXT_CONTAINS = (
    "tradeable",
    "ducat",
    "ducats",
    "exchanged",
    "component",
    "platinum",
    "want to buy",
    "want to sell",
)
REWARD_SELECTED_SLOT_VISUAL_ENABLED = True
REWARD_SELECTED_SLOT_NAME_ENABLED = True
REWARD_SELECTED_SLOT_VISUAL_FALLBACK_WITH_NAME = False
REWARD_SELECTED_NAME_Y_OFFSET = 252
REWARD_SELECTED_NAME_HEIGHT = 96
REWARD_SELECTED_NAME_X_PAD = 36
REWARD_SELECTED_NAME_MATCH_SCORE = 85
REWARD_SELECTED_NAME_MATCH_GAP = 6
REWARD_SELECTED_MARKER_Y_OFFSET = 244
REWARD_SELECTED_MARKER_HEIGHT = 54
REWARD_SELECTED_MARKER_WIDTH = 96
REWARD_SELECTED_MARKER_MIN_SCORE = 35
REWARD_SELECTED_MARKER_RATIO = 1.22

# ─────────────────────────────────────────────
# TRADE SCREEN MONITOR
# ─────────────────────────────────────────────
TRADE_MY_NAME = WFM_INGAME_NAME
OCR_NAME_ALIASES = {}  # Account name -> user-approved OCR spellings.
TRADE_SUCCESS_TIMEOUT_SECONDS = 180
TRADE_SUCCESS_POLL_SECONDS = 0.05
TRADE_SUCCESS_REQUIRED_HITS = 2
TRADE_SUCCESS_REQUIRE_TEXT = True
TRADE_SUCCESS_USE_OCR_FALLBACK = True
TRADE_DISABLE_GLOBAL_ITEM_MATCH = False
TRADE_REQUIRE_SCREEN_GATE = True
TRADE_SCREEN_HEADER_ROI = (200, 42, 520, 70)
TRADE_SCREEN_OWN_PANEL_ROI = (214, 126, 490, 70)
TRADE_REJECT_INVENTORY_TITLE = True
TRADE_SCREEN_TITLE_ROI = (180, 36, 650, 86)
TRADE_TITLE_OCR_CACHE_SECONDS = 0.8
TRADE_SCREEN_MIN_GOLD_RATIO = 0.0025
TRADE_SCREEN_MIN_EDGE_RATIO = 0.006
TRADE_GLOBAL_ITEM_MATCH_THRESHOLD = 94
TRADE_GLOBAL_ITEM_MATCH_MIN_GAP = 4
TRADE_KNOWN_ITEM_MATCH_MIN_GAP = 4

# 1920x1080 Trading Post coordinates. Own/offered slots are the top row.
TRADE_MY_NAME_ROI = (286, 128, 420, 70)
TRADE_PLAYER_ROW_Y_OFFSET = 466  # Top-to-bottom player/name and offer-slot spacing
TRADE_SUCCESS_DIALOG_ROI = (660, 370, 600, 340)
TRADE_SLOT_LABEL_Y_OFFSET = 145
TRADE_SLOT_LABEL_HEIGHT = 78
TRADE_SLOT_RECTS = [
    (216, 209, 220, 218),
    (470, 209, 220, 218),
    (724, 209, 220, 218),
    (978, 209, 220, 218),
    (1232, 209, 220, 218),
    (1486, 209, 220, 218),
]

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
UI_THEME = "dark"                # "dark" | "light"
UI_COLOR_ACCENT = "#C8A96E"      # Warframe gold
UI_WINDOW_ALPHA = 0.95           # Slight transparency for overlay feel
UI_ALWAYS_ON_TOP = True
ALERT_SOUND_ENABLED = True       # Audible cues for trade monitor/listing events
ALERT_SOUND_STYLE = "Random"     # Random | generated styles | Custom File
ALERT_SOUND_VOLUME = 0.45        # 0.0 - 1.0 for generated alert WAVs
ALERT_SOUND_CUSTOM_PATH = ""
VISUAL_NOTIFICATIONS_ENABLED = True
NOTIFICATION_CORNER = "top_left" # top_left | top_right | bottom_left | bottom_right
SIDRO_NOTIFICATION_MASCOT = False
SIDRO_VISUAL_CHARACTER = "robot" # robot | maid
SIDRO_WELCOME_VISUAL_FREQUENCY = "once_a_day" # once_a_day | every_launch | never
SIDRO_WELCOME_AUDIO_ENABLED = True
UPDATE_CHECK_FREQUENCY = "daily"
UI_FONT_FAMILY = "Segoe UI"
UI_FONT_SIZE = 12
UI_TABLE_TEXT_ALIGNMENT = "left"  # left | center | right
UI_COLORS = {}
TRADE_CHAT_MESSAGES = [""]
RECRUITMENT_CHAT_MESSAGES = [""]

if APP_SETTINGS_PATH.exists():
    try:
        if APP_SETTINGS_PATH.stat().st_size > MAX_SETTINGS_BYTES:
            raise ValueError("Settings file is too large.")
        _settings = sanitize_settings(json.loads(APP_SETTINGS_PATH.read_text(encoding="utf-8")))
        ALERT_SOUND_ENABLED = bool(_settings.get("alert_sound_enabled", ALERT_SOUND_ENABLED))
        ALERT_SOUND_STYLE = str(_settings.get("alert_sound_style", ALERT_SOUND_STYLE))
        ALERT_SOUND_VOLUME = float(_settings.get("alert_sound_volume", ALERT_SOUND_VOLUME))
        ALERT_SOUND_CUSTOM_PATH = str(_settings.get("alert_sound_custom_path", ALERT_SOUND_CUSTOM_PATH))
        VISUAL_NOTIFICATIONS_ENABLED = bool(
            _settings.get("visual_notifications_enabled", VISUAL_NOTIFICATIONS_ENABLED)
        )
        NOTIFICATION_CORNER = str(_settings.get("notification_corner", NOTIFICATION_CORNER))
        SIDRO_NOTIFICATION_MASCOT = bool(
            _settings.get("sidro_notification_mascot", SIDRO_NOTIFICATION_MASCOT)
        )
        _sidro_visual_character = str(
            _settings.get("sidro_visual_character", SIDRO_VISUAL_CHARACTER)
        ).strip().lower()
        if _sidro_visual_character in ("robot", "maid"):
            SIDRO_VISUAL_CHARACTER = _sidro_visual_character
        _welcome_visual_frequency = str(
            _settings.get("sidro_welcome_visual_frequency", SIDRO_WELCOME_VISUAL_FREQUENCY)
        ).strip().lower()
        if _welcome_visual_frequency in ("once_a_day", "every_launch", "never"):
            SIDRO_WELCOME_VISUAL_FREQUENCY = _welcome_visual_frequency
        SIDRO_WELCOME_AUDIO_ENABLED = bool(
            _settings.get("sidro_welcome_audio_enabled", SIDRO_WELCOME_AUDIO_ENABLED)
        )
        UPDATE_CHECK_FREQUENCY = _settings.get("update_check_frequency", UPDATE_CHECK_FREQUENCY)
        WFM_INGAME_NAME = str(_settings.get("wfm_ingame_name", WFM_INGAME_NAME)).strip()
        TRADE_MY_NAME = WFM_INGAME_NAME
        OCR_NAME_ALIASES = dict(_settings.get("ocr_name_aliases", {}))
        MIN_LISTING_PRICE = max(1, int(_settings.get("min_listing_price", MIN_LISTING_PRICE)))
        UI_FONT_FAMILY = str(_settings.get("ui_font_family", "Segoe UI"))
        UI_FONT_SIZE = int(_settings.get("ui_font_size", 12))
        UI_TABLE_TEXT_ALIGNMENT = str(
            _settings.get("ui_table_text_alignment", UI_TABLE_TEXT_ALIGNMENT)
        ).strip().lower()
        if UI_TABLE_TEXT_ALIGNMENT not in ("left", "center", "right"):
            UI_TABLE_TEXT_ALIGNMENT = "left"
        UI_COLORS = dict(_settings.get("ui_colors", {}))
        _trade_chat_messages = _settings.get("trade_chat_messages", TRADE_CHAT_MESSAGES)
        if isinstance(_trade_chat_messages, list):
            TRADE_CHAT_MESSAGES = [str(message) for message in _trade_chat_messages]
        if not TRADE_CHAT_MESSAGES:
            TRADE_CHAT_MESSAGES = [""]
        _recruitment_chat_messages = _settings.get("recruitment_chat_messages", RECRUITMENT_CHAT_MESSAGES)
        if isinstance(_recruitment_chat_messages, list):
            RECRUITMENT_CHAT_MESSAGES = [str(message) for message in _recruitment_chat_messages]
        if not RECRUITMENT_CHAT_MESSAGES:
            RECRUITMENT_CHAT_MESSAGES = [""]
        _ocr_engines = dict(_settings.get("ocr_engines", {}))
        OCR_SETTINGS_NEEDS_SAVE = int(_settings.get("ocr_settings_version", 1) or 1) < OCR_SETTINGS_VERSION
        if OCR_SETTINGS_NEEDS_SAVE:
            _ocr_engines = {}
        OCR_ENGINE_BY_CONTEXT = {
            key: (
                str(_ocr_engines.get(key, value)).strip().lower()
                if str(_ocr_engines.get(key, value)).strip().lower() in OCR_ENGINE_CHOICES
                else value
            )
            for key, value in OCR_ENGINE_BY_CONTEXT.items()
        }
        # Settings now selects one engine for all scans. Legacy mixed choices
        # return to RapidOCR; an explicit uniform PaddleOCR choice is retained.
        if len(set(OCR_ENGINE_BY_CONTEXT.values())) > 1:
            OCR_ENGINE_BY_CONTEXT = dict(OCR_RECOMMENDED_BY_CONTEXT)
            OCR_SETTINGS_NEEDS_SAVE = True
        OCR_FALLBACK_ENABLED = bool(_settings.get("ocr_fallback_enabled", OCR_FALLBACK_ENABLED))
        _fallback_engine = str(_settings.get("ocr_fallback_engine", OCR_FALLBACK_ENGINE)).strip().lower()
        if _fallback_engine in OCR_ENGINE_CHOICES:
            OCR_FALLBACK_ENGINE = _fallback_engine
        OCR_FALLBACK_MIN_SCORE = max(0, min(100, int(_settings.get("ocr_fallback_min_score", OCR_FALLBACK_MIN_SCORE))))
        if OCR_SETTINGS_NEEDS_SAVE:
            OCR_ENGINE_BY_CONTEXT = dict(OCR_ENGINE_BY_CONTEXT)
            for _ocr_context, _ocr_default in OCR_RECOMMENDED_BY_CONTEXT.items():
                if _ocr_context in OCR_ENGINE_BY_CONTEXT:
                    OCR_ENGINE_BY_CONTEXT[_ocr_context] = _ocr_default
            OCR_FALLBACK_ENABLED = False
            OCR_FALLBACK_ENGINE = "rapidocr"
        DUCAT_FILTER_ENABLED = bool(_settings.get("ducat_filter_enabled", DUCAT_FILTER_ENABLED))
        _ducat_values = dict(_settings.get("ducat_bulk_plat_values", DUCAT_BULK_PLAT_VALUES))
        DUCAT_BULK_PLAT_VALUES.update({
            int(ducats): max(0, int(platinum))
            for ducats, platinum in _ducat_values.items()
        })
        REWARD_PRICE_CHECK_ENABLED = bool(
            _settings.get("reward_price_check_enabled", REWARD_PRICE_CHECK_ENABLED)
        )
        _hotkeys = dict(_settings.get("hotkeys", {}))
        HOTKEY_SCAN = str(_hotkeys.get("scan", HOTKEY_SCAN))
        HOTKEY_INVENTORY_SCAN = str(_hotkeys.get("inventory_scan", HOTKEY_INVENTORY_SCAN))
        HOTKEY_INVENTORY_BOTTOM_SCAN = str(
            _hotkeys.get("inventory_bottom_scan", HOTKEY_INVENTORY_BOTTOM_SCAN)
        )
        HOTKEY_TRADE_MONITOR = str(_hotkeys.get("trade_monitor", HOTKEY_TRADE_MONITOR))
        HOTKEY_ACCOUNT_UI = str(_hotkeys.get("account_ui", HOTKEY_ACCOUNT_UI))
        HOTKEY_REWARD_SCAN = str(_hotkeys.get("reward_scan", HOTKEY_REWARD_SCAN))
        HOTKEY_OCR_WARMUP = str(_hotkeys.get("ocr_warmup", HOTKEY_OCR_WARMUP))
        HOTKEY_QUIT = str(_hotkeys.get("quit", HOTKEY_QUIT))
    except Exception:
        OCR_SETTINGS_NEEDS_SAVE = False
else:
    OCR_SETTINGS_NEEDS_SAVE = False

# ─────────────────────────────────────────────
# CAPTURE ENGINE
# ─────────────────────────────────────────────
CAPTURE_ENGINE = "dxcam"         # "dxcam" | "mss"
CAPTURE_FPS_TARGET = 10
CAPTURE_CONTINUOUS_ENABLED = False

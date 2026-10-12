# Sidro source guide

Sidro's source repository contains the application, source launchers, development
checks and release materials. The portable Windows release is a separate package;
it includes RapidOCR, while the development environment also supports optional
PaddleOCR. Some older UI modules and launchers remain for compatibility and are
identified below.

## Application flow

`main.py` connects screen capture, OCR, item matching, the Qt UI and market actions.
Inventory, reward and trade scans have their own modules. `qt_ui.py` provides the
current Dashboard, dialogs and overlays. Warframe Market requests go through
`pillar3_api.py`; user-provided market credentials are handled by `auth_manager.py`.

## Application modules

| File | Purpose |
|---|---|
| [main.py](main.py) | Entry point; coordinates hotkeys, scan workers, review dialogs and market actions. |
| [qt_ui.py](qt_ui.py) | Current PySide6 Dashboard, settings, scan reviews, token dialog and overlays. |
| [pillar1_capture.py](pillar1_capture.py) | Captures screen regions with DXCam or the MSS fallback. |
| [pillar2_ocr.py](pillar2_ocr.py) | Loads OCR engines, normalizes text and matches recognized names to catalog items. |
| [pillar3_api.py](pillar3_api.py) | Fetches public item/price data and manages authenticated Warframe Market listings. |
| [inventory_scanner.py](inventory_scanner.py) | Reads inventory grid labels and quantities into structured scan results. |
| [reward_frame_buffer.py](reward_frame_buffer.py) | Keeps recent reward frames available while OCR runs on its worker. |
| [reward_layout.py](reward_layout.py) | Detects reward-card geometry and headings from pixels. |
| [reward_scanner.py](reward_scanner.py) | Recognizes reward item names for the price overlay. |
| [trade_scanner.py](trade_scanner.py) | Reads the trading screen and detects successful trades for listing synchronization. |
| [account_store.py](account_store.py) | Stores local inventory and listing state used by scan and trade workflows. |
| [auth_manager.py](auth_manager.py) | Validates and stores the user's Warframe Market token. |
| [config.py](config.py) | Central settings, paths, defaults and local data migration. |
| [settings_validation.py](settings_validation.py) | Sanitizes settings loaded from configuration files. |
| [name_aliases.py](name_aliases.py) | Validates account-specific corrections for OCR spellings of player names. |
| [alert_sound.py](alert_sound.py) | Generates and plays local notification sounds. |
| [support_reminders.py](support_reminders.py) | Tracks local app runtime and schedules dismissible, voluntary support reminders. |
| [update_core.py](update_core.py) | Checks public GitHub releases and verifies downloaded update assets. |
| [update_ui.py](update_ui.py) | Runs update checks and presents download/install controls without blocking the UI. |
| [updater_helper.py](updater_helper.py) | Separate updater executable that replaces the portable installation. |
| [account_ui.py](account_ui.py) | Older CustomTkinter account/listing and token dialogs; current dialogs are in `qt_ui.py`. |
| [inventory_ui.py](inventory_ui.py) | Older CustomTkinter inventory review window; current review is in `qt_ui.py`. |
| [pillar4_ui.py](pillar4_ui.py) | Older CustomTkinter confirmation dialogs and UI controller. |

## Source setup and launchers

The normal development entry point is `run_cpu.bat` after
`setup_environment.ps1`. Every source launcher checks the shared locked
environment before running the app. GPU-named launchers are compatibility aliases:
the supported baseline uses CPU OCR.

| File | Purpose |
|---|---|
| [setup_environment.ps1](setup_environment.ps1) | Creates the Python 3.12 environment and installs the exact shared dependency lock. |
| [run_cpu.bat](run_cpu.bat), [run_cpu.ps1](run_cpu.ps1) | Normal source launchers. |
| [run_gpu.bat](run_gpu.bat), [run_gpu.ps1](run_gpu.ps1), [run_force_gpu.bat](run_force_gpu.bat) | Older launcher names that use the same CPU baseline. |
| [run_rapidocr.bat](run_rapidocr.bat), [run_rapidocr.ps1](run_rapidocr.ps1) | Source launchers that request RapidOCR. |
| [run_paddleocr.bat](run_paddleocr.bat), [run_paddleocr.ps1](run_paddleocr.ps1) | Source launchers that request optional PaddleOCR. |
| [run_windows_ocr.bat](run_windows_ocr.bat), [run_windows_ocr.ps1](run_windows_ocr.ps1) | Historical Windows OCR entry points; Windows OCR is absent from the current selector and portable package. |
| [run_ui_software.bat](run_ui_software.bat) | Launches with Qt software-rendering preferences for graphics diagnostics. |
| [scan_inventory_cpu.bat](scan_inventory_cpu.bat), [scan_inventory_cpu.ps1](scan_inventory_cpu.ps1) | Runs the inventory scanner's standalone diagnostic and writes debug images locally. |
| [scan_inventory_gpu.bat](scan_inventory_gpu.bat), [scan_inventory_gpu.ps1](scan_inventory_gpu.ps1), [scan_inventory_force_gpu.bat](scan_inventory_force_gpu.bat) | Older names for the same standalone CPU scanner diagnostic. |
| [evaluate_inventory_scan.py](evaluate_inventory_scan.py) | Evaluates a saved inventory screenshot against optional expected results. |
| [expected_inventory_6x3.example.json](expected_inventory_6x3.example.json) | Synthetic expected-result format for an inventory grid. |
| [expected_inventory_bottom6.example.json](expected_inventory_bottom6.example.json) | Synthetic expected-result format for the inventory bottom row. |

## Dependency manifests

| File | Purpose |
|---|---|
| [requirements.txt](requirements.txt) | Direct package pins for the source environment. |
| [requirements-release.lock](requirements-release.lock) | Complete, exact source/build environment, including transitive dependencies. |
| [requirements-rapidocr-test.txt](requirements-rapidocr-test.txt) | Complete pinned RapidOCR repair/install closure. |
| [requirements-paddleocr-test.txt](requirements-paddleocr-test.txt) | Complete pinned optional PaddleOCR repair/install closure. |
| [requirements-windowsocr.txt](requirements-windowsocr.txt) | Retained Windows OCR dependency closure; it is outside the current portable OCR selection. |

Accepted compatibility exceptions and the frozen release baseline are explained
in [DEPENDENCY_BASELINE.md](release_materials/DEPENDENCY_BASELINE.md). Use
`tools/write_ocr_requirements.py` to regenerate repair manifests from the lock.

## Packaging

| File | Purpose |
|---|---|
| [build_exe.ps1](build_exe.ps1) | Builds the app/updater, bundles notices and required sources, and creates the release ZIP. |
| [build_exe.bat](build_exe.bat) | Batch entry point for the PowerShell release builder. |
| [Sidro.spec](Sidro.spec) | PyInstaller configuration for the RapidOCR-only portable application. |
| [tools/README.md](tools/README.md) | Lists packaging, dependency, OCR and publication utilities. |
| [release_materials/README.md](release_materials/README.md) | Explains maintained release documents, licenses and upstream source manifests. |
| [tests/README.md](tests/README.md) | Explains the public regression checks. |

## Settings, catalog data and artwork

| File | Purpose |
|---|---|
| [.env.example](.env.example) | Empty example of the user-supplied market token/name configuration. |
| [app_settings.example.json](app_settings.example.json) | Example preferences for source users; personal settings are stored outside the repository. |
| [items_cache.json](items_cache.json) | Public item-name/catalog cache for faster first launch and OCR matching. |
| [item_ids_cache.json](item_ids_cache.json) | Public Warframe Market item-ID lookup cache. |
| [ducats_cache.json](ducats_cache.json) | Public item ducat-value cache. |
| [Sidro.ico](Sidro.ico) | Windows executable/window icon. |
| [Sidro.png](Sidro.png) | Sidro artwork used by the UI and icon tooling. |
| [Sidrobot.png](Sidrobot.png) | Mascot artwork used by app greetings and notifications. |

The catalog caches contain public item data. User tokens, inventories, reminder
timing and preferences live under `%LOCALAPPDATA%\Sidro` and are not source assets.

## Repository rules and notices

| File | Purpose |
|---|---|
| [README.md](README.md) | Download, usage, setup and development instructions. |
| [SOURCE_GUIDE.md](SOURCE_GUIDE.md) | This file-purpose and architecture guide. |
| [SOURCE_PUBLICATION.md](SOURCE_PUBLICATION.md) | Safe export, Git history and secret-scan procedures. |
| [PUBLIC_SOURCE_FILES.json](PUBLIC_SOURCE_FILES.json) | Reviewed path/hash allowlist for public source. |
| [.gitignore](.gitignore) | Ignores everything outside the reviewed allowlist, including newly created files. |
| [.gitattributes](.gitattributes) | Preserves exact source bytes and line endings used by the reviewed hashes. |
| [.gitleaks.toml](.gitleaks.toml) | Secret-scan rules with narrow exceptions for reviewed hashes and public catalog IDs. |
| [.githooks/pre-commit](.githooks/pre-commit) | Verifies reviewed source bytes and staged files before committing. |
| [.githooks/pre-push](.githooks/pre-push) | Verifies source/history and scans all refs with Gitleaks before pushing. |
| [LICENSE](LICENSE) | MIT license for Sidro's original code. |

GitHub's file-list text is the latest commit message for each path. It records a
change; these linked guides provide the maintained file descriptions.

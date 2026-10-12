# Sidro 1.0

Portable Windows companion app for Warframe inventory scanning, reward price checks,
warframe.market listing management, and trade-completion sync.

The app is external-only: it captures the screen, runs OCR, and talks to
warframe.market through normal HTTPS requests using the user-provided JWT cookie.
It does not inject into Warframe or read game memory.

## Downloaded Release

1. Download the release zip from GitHub.
2. Extract it anywhere writable, for example `C:\Games\Sidro`.
3. Run `Sidro.exe`. Sidro opens its Dashboard and first-launch setup dialog.
4. Open **Login / Token** from the Dashboard and paste your warframe.market `JWT` cookie value.
5. Open **Settings** and set your exact Warframe in-game name.
6. Live listing changes are enabled. Review selected items and prices before applying them; listing actions require a valid token.
7. Keep Windows HDR disabled while scanning. HDR is unsupported for 1.0 because it can hurt OCR contrast and cause compositor flicker.

All personal files are stored permanently for your Windows account in:

```text
%LOCALAPPDATA%\Sidro\
```

That folder contains your token, settings, local account cache, generated sounds,
local support-reminder timing, and refreshed public item caches.
Do not upload this folder anywhere. Replacing or deleting the application folder
does not remove these files.

On first launch after upgrading, Sidro copies files from the old `user_data`
folder beside the executable. Existing AppData files take priority, and the old
folder is retained. Launch the upgraded app from the old installation location
before deleting it so migration can find the old data. `TTB_DATA_DIR` can override
the storage location for development; migration is skipped when it is set.

All features are free, with no daily action allowance, access codes, membership
checks, or Sidro authentication service. Warframe Market still requires your
own token for listing changes and applies its normal API limits.

An optional-support reminder appears after a random 2–4 hours of accumulated
app runtime, then waits another random 2–4 hours. Time is stored locally in
`support_reminder.json`; it is never uploaded. Closed-app time, long suspend
gaps, and long event-loop stalls are not counted. Reminders wait for an idle,
focused Dashboard, can be dismissed, and never interrupt game scans or restrict
features. The donation link opens only when clicked.

## Updates

Sidro checks for stable GitHub releases in the background. Settings → Updates
controls the check frequency and provides a manual check. Installation uses the
included SidroUpdater.exe, verifies the release checksum, and preserves AppData.

Build with `build_exe.ps1` or `build_exe.bat` and distribute
`dist/Sidro-windows-v1.0.zip`. Never include personal AppData or `.env` files.

## JWT Extraction

1. Open [warframe.market](https://warframe.market) while logged in.
2. Press `F12`.
3. Open the **Network** tab and filter by **Fetch/XHR**.
4. Update or create any listing on warframe.market.
5. Click the request, open **Headers**, and find the `cookie` header.
6. Copy only the `JWT=...` value, or just the token after `JWT=`.
7. Paste it into **Login / Token** in the app. Sidro validates it before replacing the saved token.

Sidro validates the token before saving it. The app never needs your warframe.market password.

## Live Listing Changes

Create, update, and delete actions use the authenticated Warframe Market v2 API.
These actions modify real market listings; review the selected items and prices
before confirming them.

**Most Common** uses the modal price in the lowest price band supported by at
least two distinct online-in-game sellers, counting each seller once. Isolated
cheaper offers are skipped: `7, 15, 15, 60` suggests **15p**. If no band has two
sellers, it uses the next-lowest offer when available (`7, 15` suggests **15p**).
**Lowest** still displays the actual minimum, and **Quicksell** still matches it.

## Default Hotkeys

| Action | Hotkey |
|---|---|
| Select and list one item (Sidro snip) | `Ctrl+Shift+S` |
| Inventory scan, first 3 rows | `Ctrl+Shift+I` |
| Inventory scan, bottom row | `Ctrl+Shift+B` |
| Trade monitor | `Ctrl+Shift+T` |
| Reward price overlay | `Ctrl+Shift+R` |
| Account manager | `Ctrl+Shift+M` |
| Quit | `Ctrl+Shift+Q` |

Hotkeys can be changed in **Settings**.

## Build From Source

Python 3.12 is the tested interpreter. Source launchers and release builders use
the same CPU-only `.venv-sidro`. Its tested 102-package baseline also contains
optional OCR backends, but the portable release bundles only RapidOCR and ONNX
Runtime. PaddleOCR remains an optional Settings install. Other OCR runtimes
are excluded from the base ZIP. No dependency versions change
when selecting this smaller release package.

`requirements-release.lock` pins the complete shared environment. Packages use
the newest upstream-compatible stable versions verified on 2026-10-06;
`release_materials/DEPENDENCY_BASELINE.md` explains each required exception.
`setup_environment.ps1` installs the lock with dependency resolution disabled.
Launchers and builders run `tools/check_dependencies.py` to verify exact versions,
active OCR extras and the single OpenCV provider. The checker explicitly validates
RapidOCR's use of OpenCV Contrib's core modules; ordinary `pip check` still reports
its differently named `opencv-python` requirement. Builders verify the tested
environment and do not install or upgrade packages during packaging.

Run `tools/check_dependency_updates.py` when preparing dependency upgrades, update
the lock and direct pins together, then regenerate the repair manifests with
`tools/write_ocr_requirements.py --write`. Source-test the new baseline before building.

```powershell
# Requires Windows x64 and Python 3.12; pass -PythonExecutable for a specific executable.
.\setup_environment.ps1
.\run_cpu.bat
```

Both builders also bundle Sidro's root MIT `LICENSE`, the maintained
`release_materials` documents,
Qt/PySide/Shiboken license texts and source/replacement information, Python's
license, available dependency notices, a dependency inventory, and all five
dependency/repair manifests. ZIP creation rejects missing or stale required
material. Update the licensing/source references together with library upgrades;
keep these documents in every redistributed package. See
`RELEASE_REQUIREMENTS.md` in the portable package (maintained as
`release_materials/RELEASE_REQUIREMENTS.md` in source) for publishing requirements.

```powershell
.\build_exe.bat
```

The portable output is:

```text
dist\Sidro\Sidro.exe
```

GPU OCR is not part of Sidro 1.0. Keeping OCR CPU-side makes the release smaller
and avoids driver/HDR/fullscreen compositor problems.

## OCR Backends

Settings offers two OCR engines: **RapidOCR** (the default, bundled) and
**PaddleOCR** (an optional install). The selected engine applies to every scan,
including inventory, trade monitoring and reward prices. Fallback OCR also
offers only these two engines.

RapidOCR 3.9.2 uses bundled PP-OCRv6 small detection and recognition models.
Its required models are included, so first-use RapidOCR does not need a model
download. Older mixed per-screen choices return to RapidOCR; a saved uniform
PaddleOCR choice from the current settings version remains selected.
Optional PaddleOCR 3.7.0 / PaddlePaddle 3.3.1 uses PP-OCRv6 models, downloads them
on first use, and retains them in AppData. A first-time Paddle scan needs internet
access. Install / Repair requires a separate Python 3.12 installation; running
the base portable app does not require Python.

Settings shows **Installed — not tested** until **Test** successfully loads the
selected engine and reads a synthetic test word. **Install / Repair** checks
the same path after installation. These checks verify runtime health, not
Warframe screenshot accuracy. Restart Sidro after installing or repairing packs.

Optional packs install under:

```text
%LOCALAPPDATA%\Sidro\ocr_packs\<backend>\
```

## Source Tree Hygiene

Source publication uses a reviewed path-and-hash allowlist in
`PUBLIC_SOURCE_FILES.json`. `.gitignore` ignores every other file, including
new files, backend code, credentials, personal state, screenshots and builds.
Run `python -B tools/verify_public_source.py` before preparing public source.
See `SOURCE_PUBLICATION.md` for export, Git index/history checks and hooks.

The reviewed public caches `items_cache.json`, `item_ids_cache.json`, and
`ducats_cache.json` contain item catalog data used to make first launch faster.
Official release builders still require matching upstream source archives;
public source publication does not remove those distribution obligations.

## License

Sidro's original code is licensed under the [MIT License](LICENSE), allowing
reuse, modification and commercial distribution while retaining its notice.
Third-party libraries, OCR models and game assets retain their own terms;
Sidro's license does not grant rights to those materials.
See [third-party notices](release_materials/THIRD_PARTY_NOTICES.md) for the
dependency notices and source/replacement requirements included in releases.

## Credits

Created and maintained by Hitakumori, with development assistance from OpenAI Codex.

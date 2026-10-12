# Development and release tools

These utilities support source development and packaging. Normal users run the
downloaded `Sidro.exe`; the normal source entry point is `run_cpu.bat`.

| File | Purpose |
|---|---|
| [run_source.bat](run_source.bat), [run_source.ps1](run_source.ps1) | Shared launcher: validates `.venv-sidro` against the lock, then runs the requested script. |
| [check_dependencies.py](check_dependencies.py) | Checks exact pins, activated OCR extras and the single OpenCV provider. |
| [check_dependency_updates.py](check_dependency_updates.py) | Queries PyPI for stable updates and compatibility limits when preparing a new baseline. |
| [write_ocr_requirements.py](write_ocr_requirements.py) | Checks or regenerates complete OCR repair closures from the shared lock. |
| [check_ocr.py](check_ocr.py) | Runs the app's OCR engine health test without opening Settings. |
| [check_startup.py](check_startup.py) | Starts and stops the real source UI using isolated local test data. |
| [trace_ocr_helpers.py](trace_ocr_helpers.py) | Checks Windows subprocess flags while exercising OCR helpers. |
| [prepare_rapidocr_models.py](prepare_rapidocr_models.py) | Downloads and verifies the exact RapidOCR models required for packaging. |
| [make_windows_icon.py](make_windows_icon.py) | Prepares the Windows icon from Sidro artwork. |
| [bundle_release_materials.py](bundle_release_materials.py) | Bundles license notices, dependency information and required upstream source archives. |
| [make_release_zip.py](make_release_zip.py) | Validates the portable package and creates its ZIP/checksums. |
| [sidro_version.txt](sidro_version.txt) | Windows version-resource metadata for `Sidro.exe`. |
| [updater_version.txt](updater_version.txt) | Windows version-resource metadata for `SidroUpdater.exe`. |
| [verify_public_source.py](verify_public_source.py) | Checks reviewed paths/hashes, Git content/history and exports a clean source snapshot. |

Use Python 3.12 from the shared environment. Some diagnostics capture the screen,
load OCR models or write local diagnostic files; read each tool's arguments before
running it. `check_startup.py` requires an isolated `TTB_DATA_DIR`.

The release entry point remains [build_exe.ps1](../build_exe.ps1), which runs the
packaging utilities in the required order. Follow the source-test, build, EXE-test
workflow in the [main README](../README.md).

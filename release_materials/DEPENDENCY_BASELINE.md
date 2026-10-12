# Sidro dependency baseline

Baseline prepared: **2026-10-06, Windows x64, Python 3.12**. The local interpreter
used to prepare this baseline is Python 3.12.14. This document describes package
versions for source testing and the subsequent build. Preparing this baseline
does not rebuild an existing EXE or certify Windows 10 execution.

The policy is the **newest stable upstream-compatible package versions**. Check
upstream releases when preparing a new baseline, discuss actual compatibility
conflicts, and retain exact pins after testing so packaging uses the tested stack.
No package upgrades occur during launch or build. Interpreter/platform migrations
require their own validation.

## v1.0 release-candidate freeze, 2026-10-07

The user accepted first-hand source testing of this exact baseline before the
candidate build. All 102 pins and repair closures remain unchanged. The fresh
PyPI check additionally found `hf-xet` 1.7.0 (baseline 1.6.0) and Shapely 2.2.0
(baseline 2.1.2). They are pending upgrade candidates, not new compatibility
exceptions. Upgrading them requires preparing and source-testing a new baseline;
the release builder must not change the accepted stack while packaging it.

## Baseline identity

### RapidOCR-only portable package, 2026-10-12

The user requested a smaller release containing only RapidOCR. All 102 tested
source/build pins and optional repair closures remain unchanged; this is a
packaging change, not a dependency upgrade. The release excludes PaddleOCR,
PaddlePaddle, PaddleX, EasyOCR, PyTorch and Windows OCR runtimes, together with
Paddle-only source archives. All scan contexts default to RapidOCR. Older OCR
settings migrate once to those defaults. Settings offers only RapidOCR and
PaddleOCR, with one engine applied to all scans. Legacy mixed choices return to
RapidOCR; a current-version uniform PaddleOCR choice is preserved. PaddleOCR
remains a separate optional install.

RapidOCR's PP-OCRv6 detection/recognition models are included once, alongside
its angle classifier. Matching Qt, GEOS and Certifi sources and required notices
remain bundled. `DEPENDENCY_INVENTORY.json` records `bundled_ocr_backends` and
still describes the complete build environment, including packages not shipped.

- `requirements.txt`: direct package pins.
- `requirements-release.lock`: all 102 source/build environment package pins.
- The three OCR repair manifests contain each backend's complete locked closure.
- `DEPENDENCY_INVENTORY.json` in each built package records installed versions,
  interpreter/Qt versions and the bundled lock's SHA-256. Keep it with that build.
- Source launchers and normal builds use the same `.venv-sidro`.

## Main versions

| Component | Baseline version |
|---|---|
| RapidOCR | 3.9.2 |
| PaddleOCR | 3.7.0 |
| PaddlePaddle CPU | 3.3.1 |
| PaddleX | 3.7.2 |
| ONNX Runtime CPU | 1.30.0 |
| PySide6 Essentials / Shiboken / Qt | 6.11.2 |
| NumPy | 2.3.5 |
| OpenCV Contrib | 4.10.0.84; loaded cv2 reports 4.10.0 |

Latest releases and published requirements were checked against the [PyPI JSON
API](https://docs.pypi.org/api/json/) and pip's Windows/Python 3.12 resolver.
The complete versions are in the lock; these main versions are only an overview.

## Accepted compatibility exceptions

These seven packages have newer stable releases but are constrained by the latest
parent package versions in this baseline. The user accepted preserving upstream
constraints on 2026-10-06. Recheck each constraint when its parent updates.

| Package | Baseline | Newest stable checked | Upstream requirement preventing the upgrade |
|---|---|---|---|
| NumPy | 2.3.5 | 2.5.3 | PaddleX 3.7.2 requires `numpy>=1.24,<2.4` |
| OpenCV Contrib | 4.10.0.84 | 5.0.0.93 | PaddleX 3.7.2's `ocr-core` extra requires `opencv-contrib-python==4.10.0.84` |
| PyYAML | 6.0.2 | 6.0.3 | PaddleX 3.7.2 requires `PyYAML==6.0.2` |
| antlr4-python3-runtime | 4.9.3 | 4.13.2 | OmegaConf 2.3.1 requires `antlr4-python3-runtime==4.9.*` |
| opt-einsum | 3.3.0 | 3.4.0 | PaddlePaddle 3.3.1 requires `opt-einsum==3.3.0` |
| pydantic-core | 2.46.5 | 2.49.0 | Pydantic 2.13.5 requires `pydantic-core==2.46.5` |
| multidict | 6.9.1 | 7.0.0 | aiohttp 3.14.4 requires `multidict>=4.5,<7.0` |

The published requirements can be inspected at the version-specific PyPI JSON
endpoints: [PaddleX](https://pypi.org/pypi/paddlex/3.7.2/json),
[OmegaConf](https://pypi.org/pypi/omegaconf/2.3.1/json),
[PaddlePaddle](https://pypi.org/pypi/paddlepaddle/3.3.1/json),
[Pydantic](https://pypi.org/pypi/pydantic/2.13.5/json), and
[aiohttp](https://pypi.org/pypi/aiohttp/3.14.4/json).

## Single OpenCV provider

The [OpenCV packaging guidance](https://pypi.org/project/opencv-python/) requires
selecting one OpenCV distribution: the variants share the `cv2` namespace, and
Contrib includes the main modules. Sidro uses PaddleX's required Contrib provider
for preprocessing and both OCR engines. Standard/headless providers are excluded.

RapidOCR 3.9.2 declares `opencv-python>=4.5.1.48`, naming the standard distribution.
The Contrib provider's core modules and version satisfy that runtime requirement,
but ordinary `pip check` reports the absent named distribution. Sidro leaves
upstream metadata intact and exposes that difference explicitly. The dedicated
checker accepts this one reviewed substitution only for RapidOCR 3.9.2, checks
the required version, checks the loaded cv2 binary, rejects overlapping providers,
and checks every other base/activated-extra requirement without substitutions.
OCR inference must also pass when preparing an upgraded baseline.

PaddlePaddle 3.3.1 additionally requires `enable_mkldnn=False` in Sidro's
PaddleOCR constructor on the tested Windows CPU stack. Its default oneDNN PIR
path failed real inference with `ConvertPirAttribute2RuntimeAttribute`; the
existing environment flags did not override the API default. The explicit
[PaddleOCR API option](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)
keeps the CPU backend usable. This disables oneDNN acceleration; measure actual
scan latency before accepting this baseline for a build.

Repair manifests install with `--no-deps` so pip cannot add another OpenCV provider
or choose different indirect versions. Each manifest already includes the complete
required closure. Regenerate them after dependency updates.

## Windows compatibility

PySide6 6.11.2 remains the newest stable package checked. Qt's current [supported
platforms](https://doc.qt.io/qt-6/supported-platforms.html) include Windows 10 1809+
and Windows 11, and identify Qt 6.12 as the final series supporting Windows 10.
Discuss a later Qt upgrade that changes OS support before altering Sidro's target.
Upstream support does not replace testing Sidro on the actual target OS.

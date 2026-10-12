# Third-party notices

Sidro includes software developed by third parties. Its libraries retain
their own copyright and license terms.

## Qt, PySide6 and Shiboken6

The current Windows package includes Qt **6.11.2**, PySide6-Essentials
**6.11.2**, and Shiboken6 **6.11.2**, developed by The Qt Company Ltd. and
contributors. The reviewed Qt modules are Core, Gui, Network, Svg and Widgets.

The Qt/PySide/Shiboken material supplied here documents the **LGPL version 3**
distribution route. The included GPL version 3 text accompanies LGPLv3; its
presence does not by itself choose the GPL as Sidro's application license.
Some upstream components carry additional third-party terms.

Read `licenses/qt/LGPL-3.0-only.txt`, `GPL-3.0-only.txt`, and
`licenses/qt/SOURCES_AND_REPLACEMENT.md`. Users retain the rights provided by
those licenses, including library modification/replacement and reverse
engineering necessary to debug modified libraries. Nothing in this notice
restricts those rights or overrides the license terms.

Official licensing information:

- https://www.qt.io/development/open-source-lgpl-obligations
- https://doc.qt.io/qt-6.11/licensing.html
- https://doc.qt.io/qtforpython-6/licenses.html

## Python and other dependencies

The interpreter's license is in `licenses/python/LICENSE.txt`.
`DEPENDENCY_INVENTORY.json` lists the pinned environment's package versions,
upstream license declarations and the license/notice files copied to
`licenses/dependencies`. Existing notices inside `_internal` are preserved.

The requirements files describe the build/install baseline; end users do not
need to install Python or execute them to run the portable EXE. Optional OCR
repair packs and first-use downloaded models have their own upstream terms.
The inventory includes build tools and does not assert that every package in
the build environment is shipped or that collecting notices alone completes
every source-distribution obligation.

Missing wheel notices for ANTLR, FlatBuffers and RapidOCR
are supplied under `licenses/supplemental`. Its manifest records reviewed
versions, upstream notice locations and checksums.

Matching Qt/PySide/Shiboken, GEOS and Certifi sources accompany the
package in `sources`. See `licenses/NATIVE_SOURCES_AND_REPLACEMENT.md` for
additional LGPL libraries and excluded optional components. The shipped
PP-OCRv6 models originate from the RapidOCR/PaddleOCR upstream projects;
their Apache license notices are retained.

This package bundles only RapidOCR and ONNX Runtime. PaddleOCR, PaddlePaddle,
PaddleX, EasyOCR, PyTorch and Windows OCR are not distributed in the base ZIP.
Their presence in the build environment or optional installer manifests does
not mean their runtimes are shipped. ONNX Runtime's third-party notices and
NumPy/OpenCV's native notices are retained; Python package version metadata
alone does not describe every compiled native component.

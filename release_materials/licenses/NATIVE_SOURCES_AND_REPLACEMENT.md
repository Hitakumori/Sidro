# Additional native library sources and replacement

Sidro distributes unmodified upstream libraries. Matching LGPL source archives
are included in `sources`; their origins, versions and SHA-256 hashes are in
`sources/manifest.json`. The bundler verifies these hashes.

## GEOS 3.13.1

Shapely 2.1.2's Windows wheel includes GEOS 3.13.1 under LGPL 2.1 or later.
`sources/geos-3.13.1.tar.bz2` includes source, build files, copyright notices
and license texts. Shapely's BSD license and GEOS notices are retained under
`licenses/dependencies/shapely`.

To replace GEOS, close Sidro, copy the portable application, build compatible
x64 GEOS DLLs from the source using its CMake instructions, and replace the
GEOS DLLs in `_internal/shapely.libs`. Preserve the C API, DLL names and
dependencies expected by the wheel, then test the copied application.

Users retain the applicable library modification, replacement, relinking and
reverse-engineering rights. Sidro does not enforce hashes/signatures of these
libraries at startup. Updates may overwrite modifications; test and retain
modified libraries in a separate copy. The license texts define the full terms.

## Certifi 2026.7.22

Certifi's unmodified MPL 2.0 source distribution accompanies the package in
`sources`, along with its license. The CA certificate data remains in
`_internal/certifi/cacert.pem` and can be replaced in a copy of the application.

## Native attribution beyond Python packages

ONNX Runtime's complete `ThirdPartyNotices.txt` is retained with its MIT license.
NumPy's OpenBLAS/LAPACK and GCC runtime exception notices and OpenCV's
third-party notices are preserved with their installed wheel license files.
The original wheel notices remain intact.

## Optional components excluded from this package

Only RapidOCR/ONNX Runtime is bundled. Settings offers PaddleOCR as an optional
install, including its PaddlePaddle/PaddleX dependencies. EasyOCR, PyTorch and
Windows OCR are not offered in Settings or included in the package. Paddle's native
runtime DLLs and their GCC source archive, plus crc32c/python-bidi binaries
and sources, are not included in this package.

Sidro uses thefuzz with RapidFuzz; the unused GPL Levenshtein extension is
explicitly excluded. OpenCV's optional FFmpeg video plugin is also excluded:
Sidro uses still-image capture and OCR and does not open video files. Their
presence in the installed build environment does not mean they are distributed.
The frozen-package audit checks these exclusions. PyInstaller's bootloader
license includes its exception allowing distribution of non-free applications.

See `qt/SOURCES_AND_REPLACEMENT.md` for Qt/PySide/Shiboken. These source
archives contain third-party libraries, not Sidro application source.

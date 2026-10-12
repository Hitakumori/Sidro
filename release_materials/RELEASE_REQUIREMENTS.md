# Sidro distribution requirements

Keep this document with every portable release. The repository's
`release_materials` directory is the maintained original; files under `dist`
are generated output and are removed by a normal rebuild.

## Testing and release workflow

1. The user tests changes first-hand from source using `run_cpu.bat` or
   `run_gpu.bat`. Either launcher is acceptable.
2. Fix and repeat source testing until the result is good enough to build.
   Then build the release candidate from the tested source and dependency
   baseline, explaining any necessary differences that could affect results.
3. The user tests the built `.exe` to determine whether it is ready to publish.
   Any problems return to source fixes, source testing, rebuilding and EXE
   testing before publication.

Automated checks supplement both first-hand testing stages.

## Required distribution contents

- `LICENSE` is the canonical root MIT license for Sidro's original code. The
  bundler copies it without changes; third-party materials retain their terms.
- `DEPENDENCY_BASELINE.md` records the dependency baseline and accepted upstream
  compatibility constraints. The lock and inventory record each package version;
  the inventory also records the exact lock file's SHA-256.
- `THIRD_PARTY_NOTICES.md` identifies the Qt/PySide/Shiboken licensing route.
- `licenses/qt/LGPL-3.0-only.txt` and `GPL-3.0-only.txt` provide the license texts.
- `licenses/qt/SOURCES_AND_REPLACEMENT.md` provides matching library source
  downloads and replacement/build instructions.
- `licenses/qt/manifest.json` identifies the reviewed Qt versions and modules.
- `licenses/python/LICENSE.txt` contains the shipped interpreter's license.
- `licenses/dependencies` retains license/notice files found in the pinned
  build environment, including available notices for bundled native libraries.
- `DEPENDENCY_INVENTORY.json` records the pinned build environment's package
  versions, declared licenses and collected notice files. It includes build
  tools; it is not a claim that every listed package is in the executable.
- The direct requirements, full release lock and three OCR repair manifests
  must accompany the package.
- `licenses/supplemental` supplies notices absent from installed wheels. Its
  manifest pins reviewed versions, upstream origins and SHA-256 hashes.
- `sources` supplies matching Qt/PySide/Shiboken, GEOS and Certifi sources for
  this RapidOCR-only release. Optional Paddle/GNU, crc32c and python-bidi
  runtimes and their corresponding source archives are excluded from the ZIP.
  The bundler must copy and verify them and the replacement instructions.

Both normal builders call `tools/bundle_release_materials.py` before ZIP
creation. `tools/make_release_zip.py` refuses to create a release when required
documents or manifests are absent, empty, stale or inconsistent with the
maintained originals. Copying a checklist alone does not satisfy a license.
The release inventory must declare RapidOCR as its only bundled OCR backend;
the document gate rejects optional OCR runtime folders and metadata in `_internal`.

## Review before publishing

1. Retain copyright notices and the applicable licenses of software and model
   assets actually distributed. Check inventory entries without collected
   license files rather than assuming package metadata is sufficient.
2. Review every dependency's terms, including GPL/LGPL libraries and native
   components. Sidro's original code uses MIT; that choice does not replace
   dependency licenses or their notice, source and replacement requirements.
3. Keep corresponding library source available for the exact binaries shipped,
   including library modifications and necessary build information. The Qt
   source document links the current unmodified upstream versions. Preserve
   access or arrange another compliant source distribution if those links
   become unavailable; retain any required offers for their required duration.
4. Preserve users' library modification/replacement rights and reverse
   engineering rights needed to debug library modifications. Do not add terms
   or runtime integrity restrictions that remove those rights.
5. When Qt/PySide/Shiboken versions or shipped Qt modules change, update the
   source references, license files and Qt manifest together. The bundler
   checks the installed version and included DLL names before proceeding.
6. Build and test the final package, verify exclusions of credentials/personal
   state, and retain its hash. Never ship backend private keys or local AppData.

The bundling step runs offline and preserves upstream license text. It does
not replace review of licensing conditions or automatically download missing
third-party source code. See the actual license texts for their full terms.

The ZIP gate rejects personal state, environment secrets, backend folders,
private keys, unreviewed top-level files and links before creating an archive.
ZIP creation verifies CRC integrity and atomically replaces an existing archive.
Audit actual executable contents and hashes for each build. EXE version
resources and icons are metadata, not an Authenticode signature.

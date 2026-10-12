# Qt/PySide/Shiboken source and library replacement

## Corresponding upstream sources

The current distribution uses Qt, PySide6 and Shiboken6 **6.11.2**.
Sidro's build scripts do not patch these libraries; they package the installed
upstream binaries. Application UI changes are separate from library changes.

Source archives for the reviewed library modules:

These exact archives accompany the portable package under `sources`.
`sources/manifest.json` records upstream locations and verified SHA-256 hashes.
They include library sources, build files and third-party notices.

- Qt Base 6.11.2 (Core, Gui, Network and Widgets):
  https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtbase-everywhere-src-6.11.2.tar.xz
- Qt SVG 6.11.2:
  https://download.qt.io/archive/qt/6.11/6.11.2/submodules/qtsvg-everywhere-src-6.11.2.tar.xz
- PySide6 and Shiboken6 6.11.2:
  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.tar.xz

These archives contain upstream source, build scripts, copyright notices and
third-party licensing information. No payment is required to access them.
A distributor must maintain compliant corresponding
source availability, including any future library modifications and build
information; a generic project homepage is not a substitute for matching
source. These upstream links are not a time-limited written source offer; the
matching archives accompany the binary package directly.

Official source-build guidance:

- Qt for Windows: https://doc.qt.io/qt-6.11/windows-building.html
- Qt for Python: https://doc.qt.io/qtforpython-6/building_from_source/windows.html

## Replacing or modifying the shared libraries

The Windows application uses dynamically loaded Qt/PySide/Shiboken libraries
inside the portable package. Their licenses permit compatible modified
libraries and the reverse engineering needed to debug those modifications.

1. Close Sidro and its updater. Make a separate copy of the extracted package.
2. Obtain the exact matching sources above. Follow upstream build guidance
   using an x64 Windows toolchain, Python 3.12-compatible bindings and a
   mutually compatible Qt/PySide/Shiboken ABI. Retain the relevant Qt modules
   and Windows platform plugin when building replacements.
3. Replace the corresponding files in the copy. Qt DLLs and plugins are in
   `_internal/PySide6`; bindings are in that same directory, and Shiboken files
   are in `_internal/shiboken6`. Preserve directory layout and dependent DLLs.
4. Launch the copied `Sidro.exe` and test the modified libraries. Sidro does
   not implement a startup signature/hash check of these library files.

The automatic updater installs the official package and may replace modified
libraries. Avoid installing updates into the modified copy while testing
modifications. Download checksum checks verify the official archive; they do
not prevent running compatible replacement libraries locally.

Keep library copyright notices and licensing material with redistributions.
This document supplies practical instructions; the license texts define the
rights and complete requirements.

# Public regression tests

These checks protect application behavior and the release/publication rules.
They supplement first-hand source and EXE testing.

| File | Coverage |
|---|---|
| [test_dependency_baseline.py](test_dependency_baseline.py) | Exact dependency checks and the single OpenCV provider. |
| [test_market_pricing.py](test_market_pricing.py) | Seller-supported suggested prices using offline market fixtures. |
| [test_name_aliases.py](test_name_aliases.py) | Player-name correction validation and matching. |
| [test_network_privacy.py](test_network_privacy.py) | Real request preparation with synthetic credentials and offline transport. |
| [test_ocr_pack_validation.py](test_ocr_pack_validation.py) | OCR installation, validation and health-check behavior. |
| [test_ocr_subprocesses.py](test_ocr_subprocesses.py) | Hidden OCR helpers and main-app subprocess boundaries. |
| [test_public_source.py](test_public_source.py) | Reviewed source bytes, Git index/history and default-deny ignores. |
| [test_release_contract.py](test_release_contract.py) | Dependency manifests and bundled resources stay aligned. |
| [test_release_materials.py](test_release_materials.py) | Required release documents using synthetic packages. |
| [test_release_regressions.py](test_release_regressions.py) | Isolated regressions for source/release behavior. |
| [test_reward_frame_buffer.py](test_reward_frame_buffer.py) | Capture/OCR worker concurrency and frame ownership. |
| [test_reward_selection.py](test_reward_selection.py) | Reward selections without capture, model loading or account writes. |
| [test_support_reminders.py](test_support_reminders.py) | Local runtime timing, restart, deferral and failure handling. |
| [test_ui_redesign.py](test_ui_redesign.py) | Dashboard interactions preserved by UI changes. |
| [test_ui_release.py](test_ui_release.py) | Qt dialogs with isolated data and mocked market services. |
| [test_updates.py](test_updates.py) | Release/download/update behavior using fixtures. |

Tests that need private game screenshots or diagnostic datasets are omitted from
the public source export. Follow the isolation requirements inside each test;
automated checks do not establish accuracy on every game UI layout or machine.

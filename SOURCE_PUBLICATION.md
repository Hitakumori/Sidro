# Publishing Sidro source safely

The public source set is a reviewed client snapshot. `PUBLIC_SOURCE_FILES.json`
lists exact paths and SHA256 hashes. `.gitignore` ignores everything else,
including new files. Source edits require a fresh privacy/security review and
updated reviewed hashes before publication. Hashes record a reviewed snapshot;
they do not prove that code has no vulnerabilities.

## Publication checks

Use Python 3.12 and Git. Verify and export into a **new** directory:

```powershell
python -B tools/verify_public_source.py
python -B tools/verify_public_source.py --export .source-publication-audit/public-source
```

The first command verifies source content only. Before any commit/push from a
real source checkout, verify the index and commit identities as well:

```powershell
python -B tools/verify_public_source.py --require-git --history
gitleaks git . --config .gitleaks.toml --log-opts="--all" --redact=100 --ignore-gitleaks-allow --no-banner
```

Install Gitleaks from its official project and verify its release checksum.
The reviewed scanner config keeps the default detection rules and narrowly
accepts only SHA256 values in the publication manifest and public catalog item
IDs. It does not exempt entire files from scanning.
Git hooks in `.githooks` enforce the source guard before commits and pushes;
the push hook also requires Gitleaks. Enable them in the publishing checkout:

```powershell
git config core.hooksPath .githooks
git add .githooks
git update-index --chmod=+x .githooks/pre-commit .githooks/pre-push
```

Hooks are local and can be bypassed. Repeat all checks immediately before the
actual upload, inspect the staged diff, and never use `git add -f` to publish
excluded files. `.gitignore` cannot remove files already tracked or erase leaked
Git history. Review GitHub release assets, attachments, issue reports and logs
separately. Keep Git's author email set to a GitHub noreply address.
Use a public author name. Exact-byte attributes preserve the reviewed Windows
line endings and hashes when Git stages files.

## Files kept private

- Credential/key files, retired service data and deployment caches.
- Runtime state, tokens, local reminder timing, screenshots, user datasets,
  investigation folders, agent/editor state and local reports.
- Environments, OCR model downloads, executables, archives and build outputs.

The client contains public Warframe Market and GitHub URLs, plus the voluntary
donation link. These are public addresses, not credentials. No Sidro access
service, membership verification key, or grant credentials are used.

The public source can run with `setup_environment.ps1` and `run_cpu.bat`.
Maintainer release builders additionally require the upstream source archives
listed in the release-material manifests.
Those archives remain in the maintainer workspace and distributed release
packages, together with the required notices and replacement instructions;
they are omitted from Git source publication. Tests that depend on private
screenshot fixtures and their diagnostic datasets are also omitted.

## Access and licensing

All app features are available without membership, access codes, or daily quotas.
Support reminders are voluntary and count app runtime only on the local PC.
They do not send analytics or unlock features. The client never contacts the
retired Sidro authentication service.

Sidro's original code uses the MIT License in the root `LICENSE` file. It permits
reuse, modification and commercial distribution with the copyright and license
notice retained. The source guard requires this file in the reviewed export;
release builders also copy and verify it in every package.
Third-party libraries, OCR models and game assets retain their own terms;
preserve their notices and required source/replacement rights.

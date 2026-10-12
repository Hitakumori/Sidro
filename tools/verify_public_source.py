"""Verify the reviewed public source snapshot without reading private directories."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "PUBLIC_SOURCE_FILES.json"
PRIVATE_DIRS = {
    "services", "user_data", "build", "dist", "localpycs", "generated_sounds",
    "ocr_release_models", "node_modules", "__pycache__", ".git", ".agents",
    ".codex", ".aws", ".azure", ".ssh", ".wrangler", ".vscode", ".idea",
}
PRIVATE_SUFFIXES = {
    ".exe", ".dll", ".pyd", ".pyc", ".pyo", ".pkg", ".pyz", ".toc", ".zip",
    ".7z", ".tar", ".gz", ".bz2", ".xz", ".b64", ".pem", ".key", ".pfx",
    ".p12", ".db", ".sqlite", ".sqlite3", ".log", ".bin",
}
SECRET_PATTERNS = {
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    "provider token": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{32,})\b",
    "JWT credential": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}\b",
    "access code": r"\bSIDRO-(?:[A-F0-9]{4}-){7}[A-F0-9]{4}\b",
    "personal Windows path": r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s\"']+",
    "URL password": r"https?://[^/\s@\"']+:[^/\s@\"']+@",
}
DEFENSE_RULES = [
    "/services/", "/user_data/", "/build/", "/dist/", "/localpycs/",
    "/generated_sounds/", "/ocr_release_models/", "/.source-publication-audit/",
    ".venv*/", ".release-audit*/", ".wrangler/", "node_modules/", "__pycache__/",
    ".agents/", ".codex/", ".aws/", ".azure/", ".ssh/", ".vscode/", ".idea/",
    ".env", ".env.*", "!/.env.example", ".dev.vars*", "*auth.bin", "*state.json",
    "app_settings.json", "support_reminder.json", "du.bin", "daily_usage.bin", "debug_*", "test_capture*",
    "*.exe", "*.dll", "*.pyd", "*.pyc", "*.pyo", "*.pkg", "*.pyz", "*.toc",
    "*.zip", "*.7z", "*.tar", "*.gz", "*.bz2", "*.xz", "*.b64", "*.pem",
    "*.key", "*.pfx", "*.p12", "*.db", "*.sqlite*", "*.log", "*.bin",
    "Thumbs.db", "Desktop.ini",
]


class PublicationError(ValueError):
    pass


def validate_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PublicationError("Invalid public path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(
        part in {".", ".."} or part.endswith((" ", "."))
        or any(c in part for c in "*?[]!#\r\n\x00")
        or part.casefold() in PRIVATE_DIRS
        or (part.startswith(".") and part not in {".gitignore", ".gitattributes", ".gitleaks.toml", ".env.example", ".githooks"})
        for part in path.parts
    ) or path.suffix.casefold() in PRIVATE_SUFFIXES:
        raise PublicationError("Forbidden public path")
    if path.name.casefold() in {"account_state.json", "app_settings.json", "support_reminder.json", ".env"}:
        raise PublicationError("Runtime state cannot be public")
    return value


def load_manifest(root: Path) -> dict[str, str]:
    manifest_path = root / MANIFEST
    reject_links(root, MANIFEST)
    content = manifest_path.read_bytes()
    check_content(MANIFEST, content)

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PublicationError("Duplicate publication manifest key")
            result[key] = value
        return result

    data = json.loads(content.decode("utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(data, dict) or set(data) != {"schema_version", "files"} or data["schema_version"] != 1:
        raise PublicationError("Unsupported publication manifest")
    if not isinstance(data["files"], dict):
        raise PublicationError("Manifest files must be a path-to-SHA256 mapping")
    files = data["files"]
    seen = set()
    for name, digest in files.items():
        validate_path(name)
        if name == MANIFEST or name.casefold() in seen or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise PublicationError("Invalid or duplicate publication entry")
        seen.add(name.casefold())
    if not {".gitignore", "tools/verify_public_source.py", "SOURCE_PUBLICATION.md", "LICENSE"} <= set(files):
        raise PublicationError("Publication safeguards missing from manifest")
    return files


def render_gitignore(files: dict[str, str]) -> str:
    names = sorted(set(files) | {MANIFEST})
    directories = sorted({str(parent) for name in names for parent in PurePosixPath(name).parents if str(parent) != "."})
    return "\n".join([
        "# Fail closed: only the reviewed paths in PUBLIC_SOURCE_FILES.json may be public.",
        "# New files require an explicit publication review; do not use git add -f.",
        "*", "", "# Traverse reviewed directories only.",
        *[f"!/{name}/" for name in directories], "", "# Reviewed public files.",
        *[f"!/{name}" for name in names], "", "# Private data and generated files remain excluded.",
        *DEFENSE_RULES, "",
    ])


def reject_links(root: Path, name: str) -> Path:
    current = root
    for part in PurePosixPath(name).parts:
        current /= part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PublicationError(f"Link or reparse point rejected: {name}")
    if not current.is_file() or not current.resolve().is_relative_to(root.resolve()):
        raise PublicationError(f"Not a regular file inside the source tree: {name}")
    return current


def check_content(name: str, content: bytes) -> None:
    text = content.decode("utf-8", errors="replace")
    for category, pattern in SECRET_PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            raise PublicationError(f"{name}:{line}: {category} detected (value withheld)")
    emails = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
    if not name.startswith("release_materials/licenses/") and any(
        email != "noreply@github.com" and not email.endswith("@users.noreply.github.com")
        for email in emails
    ):
        raise PublicationError(f"{name}: email address requires a privacy review")
    if name == ".env.example":
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                if line not in {"WFM_TOKEN=", "WFM_INGAME_NAME="}:
                    raise PublicationError("Environment example must contain blank reviewed values only")


def verify_worktree(root: Path, files: dict[str, str]) -> None:
    expected_ignore = render_gitignore(files)
    if (root / ".gitignore").read_text(encoding="utf-8") != expected_ignore:
        raise PublicationError(".gitignore does not match the reviewed allowlist")
    for name, digest in files.items():
        content = reject_links(root, name).read_bytes()
        check_content(name, content)
        if hashlib.sha256(content).hexdigest() != digest:
            raise PublicationError(f"Publication review required for changed file: {name}")


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=False)
    if result.returncode:
        raise PublicationError("Git verification failed; initialize a separate reviewed source checkout")
    return result.stdout


def verify_git_index(root: Path, files: dict[str, str], *, history: bool = False) -> None:
    top = Path(git(root, "rev-parse", "--show-toplevel").decode().strip())
    if top.resolve() != root.resolve():
        raise PublicationError("Git root must match the source root")
    approved = set(files) | {MANIFEST}
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, raw_name = record.split(b"\t", 1)
        mode, blob, stage = metadata.decode().split()
        name = raw_name.decode("utf-8")
        if name not in approved or mode not in {"100644", "100755"} or stage != "0":
            raise PublicationError(f"Unreviewed, linked, or conflicted tracked file: {name}")
        if name.startswith(".githooks/") and mode != "100755":
            raise PublicationError("Git hooks must be staged as executable files")
        content = git(root, "cat-file", "blob", blob)
        check_content(name, content)
        if name == MANIFEST:
            if content != (root / MANIFEST).read_bytes():
                raise PublicationError("Staged publication manifest differs from reviewed worktree")
        elif hashlib.sha256(content).hexdigest() != files[name]:
            raise PublicationError(f"Staged file differs from publication review: {name}")
    if history:
        for record in git(root, "for-each-ref", "--format=%(refname)%00%(objectname)").splitlines():
            ref, object_id = record.split(b"\0", 1)
            check_content("Git ref name", ref)
            if git(root, "cat-file", "-t", object_id.decode()).strip() == b"tag":
                check_content("Git annotated tag", git(root, "cat-file", "tag", object_id.decode()))
        check_content("Git commit messages", git(root, "log", "--all", "--format=%B"))
        for record in git(root, "rev-list", "--objects", "--all").decode().splitlines():
            blob, separator, name = record.partition(" ")
            if not separator or git(root, "cat-file", "-t", blob).strip() != b"blob":
                continue
            if name not in approved:
                raise PublicationError(f"Unreviewed file found in Git history: {name}")
            check_content(name, git(root, "cat-file", "blob", blob))
        for email in git(root, "log", "--all", "--format=%ae%n%ce").decode().splitlines():
            if email and email != "noreply@github.com" and not email.endswith("@users.noreply.github.com"):
                raise PublicationError("Personal commit email found in history (value withheld)")


def export_source(root: Path, files: dict[str, str], destination: Path) -> None:
    # Require a new directory so an earlier export cannot silently retain secrets.
    destination = destination.absolute()
    if destination.exists() or not destination.parent.is_dir():
        raise PublicationError("Export requires a new directory with an existing parent")
    for parent in [destination.parent, *destination.parents]:
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise PublicationError("Export parent cannot be a link or junction")
    destination.mkdir()
    for name in sorted(set(files) | {MANIFEST}):
        content = reject_links(root, name).read_bytes()
        if name != MANIFEST and hashlib.sha256(content).hexdigest() != files[name]:
            raise PublicationError(f"Source changed during export: {name}")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    verify_worktree(destination, load_manifest(destination))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-git", action="store_true", help="Also verify tracked/staged content")
    parser.add_argument("--history", action="store_true", help="Also reject personal commit emails; requires Git")
    parser.add_argument("--export", type=Path, help="Copy reviewed files into a new directory; never publishes")
    args = parser.parse_args()
    try:
        files = load_manifest(ROOT)
        verify_worktree(ROOT, files)
        if args.require_git or args.history:
            verify_git_index(ROOT, files, history=args.history)
        if args.export:
            export_source(ROOT, files, args.export)
    except (PublicationError, OSError, ValueError) as error:
        print(f"BLOCKED: {error}")
        return 1
    print(f"Reviewed source verified: {len(files) + 1} files; Git index/history checked: {bool(args.require_git or args.history)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

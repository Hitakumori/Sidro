"""Publication safety: reviewed bytes, Git's index/history, and fail-closed ignores."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tools import verify_public_source as guard


class PublicationTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".source-publication-audit"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tools").mkdir()
        (self.root / "tools/verify_public_source.py").write_text("# reviewed tool\n", encoding="utf-8")
        (self.root / "SOURCE_PUBLICATION.md").write_text("Reviewed publication.\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("Synthetic project license.\n", encoding="utf-8")
        self.files = {name: "" for name in (".gitignore", "tools/verify_public_source.py", "SOURCE_PUBLICATION.md", "LICENSE")}
        (self.root / ".gitignore").write_text(guard.render_gitignore(self.files), encoding="utf-8", newline="\n")
        self.files = {name: hashlib.sha256((self.root / name).read_bytes()).hexdigest() for name in self.files}
        (self.root / guard.MANIFEST).write_text(json.dumps({"schema_version": 1, "files": self.files}), encoding="utf-8")

    def git(self, *args, check=True):
        if not shutil.which("git"):
            self.skipTest("Git is required for index/history tests")
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, check=check)

    def init_git(self):
        self.git("init", "--quiet")
        # Preserve the bytes whose hashes were reviewed, on Windows as well.
        self.git("config", "core.autocrlf", "false")

    def test_reviewed_snapshot_and_export(self):
        guard.verify_worktree(self.root, guard.load_manifest(self.root))
        destination = self.root / "export"
        guard.export_source(self.root, self.files, destination)
        self.assertEqual(set(p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()),
                         set(self.files) | {guard.MANIFEST})
        with self.assertRaises(guard.PublicationError):
            guard.export_source(self.root, self.files, destination)

    def test_changed_allowed_file_requires_review(self):
        (self.root / "SOURCE_PUBLICATION.md").write_text("Changed bytes\n", encoding="utf-8")
        with self.assertRaisesRegex(guard.PublicationError, "changed file"):
            guard.verify_worktree(self.root, self.files)

    def test_project_license_cannot_be_omitted_from_public_manifest(self):
        del self.files["LICENSE"]
        (self.root / guard.MANIFEST).write_text(json.dumps({"schema_version": 1, "files": self.files}), encoding="utf-8")
        with self.assertRaisesRegex(guard.PublicationError, "Publication safeguards missing"):
            guard.load_manifest(self.root)

    def test_unknown_and_private_files_are_ignored_by_git(self):
        self.init_git()
        for name in ("new-source.py", "secret.txt", ".env.production", "services/src/index.js",
                     "services/token.b64", "user_data/patreon_auth.bin", "dist/Sidro.exe",
                     ".agents/private.md", "localpycs/main.pyc", "tests/fixtures/screenshot.png",
                     ".source-publication-audit/private.json", "release_materials/licenses/new-secret.txt"):
            with self.subTest(name=name):
                self.assertEqual(self.git("check-ignore", "--no-index", name, check=False).returncode, 0)
        for name in set(self.files) | {guard.MANIFEST}:
            self.assertEqual(self.git("check-ignore", "--no-index", name, check=False).returncode, 1)

    def test_ignore_rules_cannot_be_weakened(self):
        with (self.root / ".gitignore").open("a", encoding="utf-8") as stream:
            stream.write("!secret.txt\n")
        with self.assertRaisesRegex(guard.PublicationError, "allowlist"):
            guard.verify_worktree(self.root, self.files)

    def test_secret_values_are_rejected_without_echoing_them(self):
        samples = ["ghp_" + "A" * 36, "SIDRO-" + "-".join(["ABCD"] * 8),
                   "C:" + "\\Users\\" + "PrivatePerson\\secret", "private-person" + "@" + "example.com",
                   "-----BEGIN " + "PRIVATE KEY-----", "eyJ" + "A" * 12 + "." + "B" * 12 + "." + "C" * 24]
        for value in samples:
            with self.subTest(length=len(value)), self.assertRaises(guard.PublicationError) as caught:
                guard.check_content("main.py", value.encode())
            self.assertNotIn(value, str(caught.exception))

    def test_environment_example_has_blank_values_only(self):
        guard.check_content(".env.example", b"# example\nWFM_TOKEN=\nWFM_INGAME_NAME=\n")
        with self.assertRaises(guard.PublicationError):
            guard.check_content(".env.example", b"WFM_TOKEN=real-credential\n")

    def test_paths_cannot_escape_or_enable_backend_data(self):
        for name in ("../outside.py", "/outside.py", "services/src/index.js", ".env", "public.key",
                     "new\\file.py", "./file.py", "tools/../file.py", "tools/file.py.", "a*[0].py"):
            with self.subTest(name=name), self.assertRaises(guard.PublicationError):
                guard.validate_path(name)

    def test_duplicate_manifest_fields_are_rejected(self):
        (self.root / guard.MANIFEST).write_text('{"schema_version":1,"schema_version":1,"files":{}}', encoding="utf-8")
        with self.assertRaisesRegex(guard.PublicationError, "Duplicate"):
            guard.load_manifest(self.root)

    def test_windows_reparse_attribute_is_rejected(self):
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)):
            with self.assertRaisesRegex(guard.PublicationError, "reparse"):
                guard.reject_links(self.root, "SOURCE_PUBLICATION.md")

    def test_symlink_and_junction_guard(self):
        (self.root / "SOURCE_PUBLICATION.md").unlink()
        try:
            (self.root / "SOURCE_PUBLICATION.md").symlink_to(self.root / ".gitignore")
        except OSError:
            self.skipTest("Host does not permit creating a test symlink")
        with self.assertRaisesRegex(guard.PublicationError, "Link or reparse"):
            guard.verify_worktree(self.root, self.files)

    def test_forced_tracked_private_file_is_blocked_even_when_ignored(self):
        self.init_git()
        (self.root / "secret.txt").write_text("private", encoding="utf-8")
        self.git("add", "-f", "secret.txt")
        with self.assertRaisesRegex(guard.PublicationError, "Unreviewed"):
            guard.verify_git_index(self.root, self.files)

    def test_staged_secret_cannot_hide_behind_clean_worktree(self):
        self.init_git()
        original = (self.root / "SOURCE_PUBLICATION.md").read_bytes()
        (self.root / "SOURCE_PUBLICATION.md").write_text("ghp_" + "A" * 36, encoding="utf-8")
        self.git("add", "SOURCE_PUBLICATION.md")
        (self.root / "SOURCE_PUBLICATION.md").write_bytes(original)
        guard.verify_worktree(self.root, self.files)
        with self.assertRaisesRegex(guard.PublicationError, "provider token"):
            guard.verify_git_index(self.root, self.files)

    def test_deleted_private_file_in_history_is_still_blocked(self):
        self.init_git()
        self.git("config", "user.name", "PublicationFixture")
        self.git("config", "user.email", "noreply@github.com")
        (self.root / "secret.txt").write_text("private", encoding="utf-8")
        self.git("add", "-f", "secret.txt")
        self.git("commit", "--quiet", "-m", "Fixture")
        self.git("rm", "secret.txt")
        self.git("commit", "--quiet", "-m", "Remove fixture")
        with self.assertRaisesRegex(guard.PublicationError, "Git history"):
            guard.verify_git_index(self.root, self.files, history=True)

    def test_personal_commit_email_is_blocked_without_echoing_it(self):
        self.init_git()
        self.git("config", "user.name", "PublicationFixture")
        email = "private-owner" + "@" + "example.com"
        self.git("config", "user.email", email)
        self.git("add", "SOURCE_PUBLICATION.md")
        self.git("commit", "--quiet", "-m", "Fixture")
        with self.assertRaisesRegex(guard.PublicationError, "Personal commit email") as caught:
            guard.verify_git_index(self.root, self.files, history=True)
        self.assertNotIn(email, str(caught.exception))

    def test_annotated_tag_credentials_are_blocked(self):
        self.init_git()
        self.git("config", "user.name", "PublicationFixture")
        self.git("config", "user.email", "noreply@github.com")
        self.git("add", "SOURCE_PUBLICATION.md")
        self.git("commit", "--quiet", "-m", "Fixture")
        self.git("tag", "-a", "fixture-tag", "-m", "ghp_" + "A" * 36)
        with self.assertRaisesRegex(guard.PublicationError, "provider token"):
            guard.verify_git_index(self.root, self.files, history=True)


if __name__ == "__main__":
    unittest.main()

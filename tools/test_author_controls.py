"""Offline integration tests for author-stage's HEAD-based accept controls.

Git operations use a real throwaway repository. The compiler and model are
explicit stubs: these tests verify gate orchestration, not compiler behavior.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "author_controls_subject", Path(__file__).with_name("author_stage.py")
)
AUTHOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTHOR)

CONTROL = "tests/checker-corpus/accept.hy"
DIFF = """diff --git a/compiler/example.rs b/compiler/example.rs
--- a/compiler/example.rs
+++ b/compiler/example.rs
@@ -1 +1 @@
-// before
+// after
"""


class AuthorControlsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="author-controls-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.queue = self.root / "automation/land-queue"
        self.out = self.queue / "author-123"
        self.blocked = self.root / "target/author-blocked"
        self.repro_dir = self.root / "compiler/target/author-stage"
        self.git("init", "-q")
        self.git("config", "user.name", "Test Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write(".gitignore", "target/\ncompiler/target/\n")
        self.write("compiler/example.rs", "// before\n")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True,
            text=True, timeout=10,
        )

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def run_author(self, control_contents="fn main() {}\n", *, git_error=False,
                   rejects=False):
        if control_contents is not None:
            self.write(CONTROL, control_contents)
        self.commit()
        original_sh = AUTHOR.sh
        compiler_checks = []

        def shell(args, **kwargs):
            if args[:2] == ["cargo", "build"]:
                return subprocess.CompletedProcess(args, 0, "", "")
            if git_error and args == ["git", "show", "HEAD:" + CONTROL]:
                return subprocess.CompletedProcess(args, 128, "", "fixture Git read error")
            return original_sh(args, **kwargs)

        def checker(path):
            compiler_checks.append(path)
            if path.endswith("repro.hy"):
                return 1, "HYB-E6002"
            return (1, "rejected") if rejects else (0, "accepted")

        with contextlib.ExitStack() as stack:
            for name, value in {
                "ROOT": self.root, "COMPILER": self.root / "compiler",
                "QUEUE": self.queue, "BLOCKED_DIR": self.blocked,
                "REPRO_DIR": self.repro_dir, "ACCEPT_CONTROLS": [CONTROL],
            }.items():
                stack.enter_context(patch.object(AUTHOR, name, value))
            stack.enter_context(patch.dict(os.environ, {
                "AUTHOR_API_BASE": "https://fixture.invalid",
                "AUTHOR_MODEL": "offline-test-stub",
            }))
            stack.enter_context(patch.object(AUTHOR, "sh", side_effect=shell))
            stack.enter_context(patch.object(AUTHOR, "api_get", return_value={
                "number": 123, "title": "fixture", "body": "```hy\nfn main() {}\n``` HYB-E6002",
            }))
            brain = stack.enter_context(patch.object(
                AUTHOR, "chat", return_value="```diff\n" + DIFF + "```"
            ))
            stack.enter_context(patch.object(AUTHOR, "hyb_check", side_effect=checker))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = AUTHOR.run_author(123, self.out, 1)
        return result, brain, compiler_checks

    def assertBlocked(self, result, brain, checks):
        self.assertEqual(result, 3)
        self.assertFalse(self.out.exists(), "invalid controls must never yield a ready bundle")
        meta = json.loads((self.blocked / "issue-123/meta.json").read_text())
        self.assertEqual(meta["status"], "blocked")
        self.assertIn(CONTROL, meta["note"])
        brain.assert_not_called()
        self.assertEqual(checks, [])
        self.assertEqual((self.root / "compiler/example.rs").read_text(), "// before\n")

    def test_missing_head_control_blocks_before_model_or_apply(self):
        self.assertBlocked(*self.run_author(None))

    def test_empty_head_control_blocks_before_model_or_apply(self):
        self.assertBlocked(*self.run_author(""))

    def test_git_read_failure_blocks_before_model_or_apply(self):
        self.assertBlocked(*self.run_author(git_error=True))

    def test_valid_head_control_is_checked_and_bundle_is_ready(self):
        result, brain, checks = self.run_author()
        self.assertEqual(result, 0)
        brain.assert_called_once()
        self.assertEqual(len(checks), 2)
        meta = json.loads((self.out / "meta.json").read_text())
        self.assertEqual(meta["gates"]["acceptControls"], [CONTROL])
        self.assertIn("// after", (self.out / "fix.patch").read_text())
        self.assertEqual((self.root / "compiler/example.rs").read_text(), "// before\n")

    def test_rejecting_control_still_blocks_the_bundle(self):
        result, brain, checks = self.run_author(rejects=True)
        self.assertEqual(result, 3)
        self.assertFalse(self.out.exists())
        self.assertEqual(len(checks), 2)
        self.assertIn("OVER-REACH", (self.blocked / "issue-123/note.txt").read_text())


if __name__ == "__main__":
    unittest.main()

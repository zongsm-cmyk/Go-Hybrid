"""Offline CLI-boundary regression for the destructive queue sweeper.

No GitHub API calls or subprocesses. Run:
python3 -m unittest discover -s tools -p test_queue_sweeper_args.py -v
"""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "queue_sweeper_offline", Path(__file__).with_name("queue_sweeper.py")
)
sweeper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sweeper)


class QueueSweeperArgumentTests(unittest.TestCase):
    def run_args(self, *args, api):
        with patch.object(sweeper, "TOK", "offline-fixture-not-a-token"), \
             patch.object(sweeper, "api", side_effect=api), \
             patch.object(sweeper, "config_protected_numbers", return_value=set()), \
             patch.object(sys, "argv", ["queue_sweeper.py", *args]), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            try:
                sweeper.main()
            except SystemExit as exc:
                return exc.code, stdout.getvalue(), stderr.getvalue()
            return 0, stdout.getvalue(), stderr.getvalue()

    def test_negative_max_rejected_before_any_api(self):
        for value in ("-1", "-100"):
            with self.subTest(value=value):
                code, _, err = self.run_args(
                    "--max", value,
                    api=AssertionError("No requests on invalid bounds"),
                )
                self.assertEqual(code, 2)
                self.assertIn("non-negative", err)

    def test_negative_min_age_rejected_before_any_api(self):
        for value in ("-1", "-365"):
            with self.subTest(value=value):
                code, _, err = self.run_args(
                    "--min-age", value,
                    api=AssertionError("No requests on invalid bounds"),
                )
                self.assertEqual(code, 2)
                self.assertIn("non-negative", err)

    def test_zero_max_never_attempts_to_close(self):
        methods = []
        def fake_api(path, method="GET", body=None):
            methods.append(method)
            if "/pulls?" in path:
                return 200, [{
                    "number": 33, "created_at": "2020-01-01T00:00:00Z",
                    "labels": [], "user": {"login": "fixture"},
                    "title": "fixture",
                }]
            return 200, {"open_issues_count": 1}
        code, out, err = self.run_args("--max", "0", api=fake_api)
        self.assertEqual(code, 0, err)
        self.assertIn("closed=0 fails=0", out)
        self.assertEqual(methods, ["GET", "GET"])

    def test_zero_age_allowed_in_dry_mode(self):
        def fake_api(path, method="GET", body=None):
            if method != "GET":
                raise AssertionError("dry run attempted a write")
            if "/pulls?" in path:
                return 200, []
            return 200, {}
        code, out, err = self.run_args(
            "--min-age", "0", "--dry", api=fake_api,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("DRY RUN", out)


if __name__ == "__main__":
    unittest.main()

"""Regression checks for coverage, isolation and failure handling of verify-all."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUNNER = Path(__file__).with_name("run-verify.py").resolve()
spec = importlib.util.spec_from_file_location("run_verify", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class VerifyRunnerTests(unittest.TestCase):
    def test_shards_cover_each_target_once_in_stable_order(self):
        targets = [f"verify-{i:03}" for i in range(111)]
        for total in (1, 2, 4, 120):
            shards = [runner.select_targets(list(reversed(targets)), i, total) for i in range(total)]
            self.assertEqual(sorted(t for shard in shards for t in shard), targets)
            self.assertTrue(all(shard == sorted(shard) for shard in shards))
            self.assertLessEqual(max(map(len, shards)) - min(map(len, shards)), 1)
        self.assertEqual(runner.select_targets(targets, 0, 1), targets)

    def test_invalid_input_fails_instead_of_silently_skipping_coverage(self):
        for index, total in ((-1, 4), (4, 4), (0, 0), (0, -1)):
            with self.subTest(index=index, total=total), self.assertRaises(ValueError):
                runner.select_targets(["verify-one"], index, total)
        for targets in ([], ["verify-one", "verify-one"]):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                runner.select_targets(targets, 0, 1)

    def test_execution_is_serial_isolated_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            # Real recursive make: verify MAKEFLAGS command-line overrides survive.
            (root / "Makefile").write_text(
                'verify-one verify-two:\n'
                '\t@test "$(PROOF)" = inherited\n'
                '\t@test ! -e "$(TMPDIR)/$@"\n'
                '\t@touch "$(TMPDIR)/$@"\n'
                '\t@echo "$@ $(TMPDIR)" >> events\n'
                'all:\n'
                f'\t@{sys.executable} {RUNNER} --make "$(MAKE)" verify-two verify-one\n')
            for _ in range(2):
                subprocess.run(["make", "all", "PROOF=inherited"], cwd=root, check=True, capture_output=True)
            events = (root / "events").read_text().splitlines()
            self.assertEqual([line.split()[0] for line in events], ["verify-one", "verify-two"] * 2)
            paths = [line.split()[1] for line in events]
            self.assertEqual(paths[0], paths[1])
            self.assertNotEqual(paths[0], paths[2])
            self.assertTrue(all(not Path(p).exists() for p in paths))

    def test_failure_stops_the_shard_without_retry_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / "Makefile").write_text(
                'verify-bad:\n\t@echo "$(TMPDIR)" >> attempts\n\t@exit 7\n'
                'verify-later:\n\t@touch should-not-exist\n')
            result = subprocess.run([sys.executable, str(RUNNER), "verify-bad", "verify-later"],
                                    cwd=root, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL: verify-bad", result.stdout)
            paths = (root / "attempts").read_text().splitlines()
            self.assertEqual(len(paths), 1)
            self.assertFalse(Path(paths[0]).exists())
            self.assertFalse((root / "should-not-exist").exists())


if __name__ == "__main__":
    unittest.main()

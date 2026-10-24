import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def cli(*args):
    return subprocess.run([sys.executable, "-m", "devtools", *args], cwd=ROOT,
                          capture_output=True, text=True)


class SmokeTest(unittest.TestCase):
    def test_version(self):
        r = cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "devtools 0.1.0")

    def test_usage_error_exit_5(self):
        r = cli("--bogus")
        self.assertEqual(r.returncode, 5)


if __name__ == "__main__":
    unittest.main()

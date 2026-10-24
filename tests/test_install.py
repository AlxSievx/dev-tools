import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from devtools.install import install


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        (self.repo / "fusion" / "addin" / "DevToolsBridge").mkdir(parents=True)
        self.addins = root / "AddIns"

    def tearDown(self):
        link = self.addins / "DevToolsBridge"
        if link.is_junction():
            os.rmdir(link)       # removes the junction, not the target
        self.tmp.cleanup()

    def run_install(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = install(self.repo, self.addins)
        return code, out.getvalue()

    def test_creates_junction_then_idempotent(self):
        self.assertEqual(self.run_install()[0], 0)
        link = self.addins / "DevToolsBridge"
        self.assertTrue(link.is_junction())
        code, out = self.run_install()
        self.assertEqual(code, 0)
        self.assertIn("already installed", out)

    def test_refuses_foreign_folder(self):
        (self.addins / "DevToolsBridge").mkdir(parents=True)
        code, out = self.run_install()
        self.assertEqual(code, 1)
        self.assertIn("move it away", out)


if __name__ == "__main__":
    unittest.main()

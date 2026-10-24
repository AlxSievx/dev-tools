"""Live checks against a running Fusion; set DEVTOOLS_LIVE=1 to run them."""
import os
import unittest


@unittest.skipUnless(os.environ.get("DEVTOOLS_LIVE") == "1", "set DEVTOOLS_LIVE=1 with Fusion open")
class LiveTest(unittest.TestCase):
    def test_selftest(self):
        from devtools.client import Client
        from devtools.selftest import selftest
        self.assertEqual(selftest(Client()), 0)


if __name__ == "__main__":
    unittest.main()

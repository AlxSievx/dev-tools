import json
import unittest

from _paths import ADDIN


class AddinFilesTest(unittest.TestCase):
    """Fusion silently hides an add-in whose manifest does not parse."""

    def test_manifest_is_valid_json(self):
        manifest = json.loads((ADDIN / "DevToolsBridge.manifest").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], ADDIN.name)
        self.assertEqual(manifest["type"], "addin")
        self.assertTrue(manifest["runOnStartup"])

    def test_config_is_valid_json(self):
        self.assertEqual(json.loads((ADDIN / "config.json").read_text(encoding="utf-8")),
                         {"port": 7655})


if __name__ == "__main__":
    unittest.main()

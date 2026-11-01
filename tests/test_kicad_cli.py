import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

from _kicad import TempDir, copy_demo, needs_kicad
from kt import cli


def minimal_project(d: Path, name="t", extra_sch="") -> Path:
    (d / f"{name}.kicad_pro").write_text("{}", encoding="utf-8")
    (d / f"{name}.kicad_sch").write_text(
        f'(kicad_sch (version 20260306) (generator "kt") (generator_version "10.0") '
        f'(uuid "{uuid.uuid4()}") (paper "A4") (lib_symbols) {extra_sch}'
        f'(sheet_instances (path "/" (page "1"))) (embedded_fonts no))\n', encoding="utf-8")
    (d / f"{name}.kicad_pcb").write_text(
        '(kicad_pcb (version 20260206) (generator "kt") (generator_version "10.0"))\n', encoding="utf-8")
    return d


class FindTest(unittest.TestCase):
    def test_missing_cli(self):
        with mock.patch.dict(os.environ, {"DEVTOOLS_KICAD_CLI": r"C:\nowhere\kicad-cli.exe"}):
            with self.assertRaises(cli.KicadCliMissing) as cm:
                cli.find()
        self.assertIn("DEVTOOLS_KICAD_CLI", str(cm.exception))


@needs_kicad
class ChecksTest(unittest.TestCase):
    def test_erc_empty_schematic(self):
        with TempDir() as d:
            minimal_project(d)
            report = cli.erc(d / "t.kicad_sch")
        self.assertEqual(report.errors, [])

    def test_drc_empty_board_reports_outline(self):
        with TempDir() as d:
            minimal_project(d)
            report = cli.drc(d / "t.kicad_pcb")
        self.assertEqual([f.type for f in report.errors], ["invalid_outline"])
        self.assertIn("invalid_outline", report.summary())

    def test_load_error(self):
        with TempDir() as d:
            minimal_project(d, extra_sch="(frobnicate 1) ")
            with self.assertRaises(cli.LoadError) as cm:
                cli.erc(d / "t.kicad_sch")
        self.assertEqual(cm.exception.path.name, "t.kicad_sch")

    def test_netlist_and_ipc356_on_demo(self):
        with TempDir() as d:
            demo = copy_demo("pic_programmer", d)
            net = cli.netlist(demo / "pic_programmer.kicad_sch")
            names = [n.get("name")[0] for n in net.get("nets").all("net")]
            self.assertIn("/CLOCK-RB6", names)
            pads = cli.ipc356(demo / "pic_programmer.kicad_pcb")
        p3 = [p for p in pads if p.ref == "P3"]
        self.assertTrue(p3)
        self.assertTrue(all(p.side in ("top", "bottom", "both") for p in pads))


if __name__ == "__main__":
    unittest.main()

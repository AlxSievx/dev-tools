import re
import unittest

from _kicad import TempDir, copy_demo, needs_kicad
from kt import cli
from kt.info import in_step, info
from kt.project import Project, new_project
from test_kicad_schematic import build_demo_circuit


def upgraded_demo(d):
    demo = copy_demo("pic_programmer", d)
    for f in ("pic_programmer.kicad_sch", "pic_sockets.kicad_sch"):
        cli.run("sch", "upgrade", "--force", demo / f)
    cli.run("pcb", "upgrade", "--force", demo / "pic_programmer.kicad_pcb")
    return demo


@needs_kicad
class InfoTest(unittest.TestCase):
    def test_demo_in_step_then_missing(self):
        with TempDir() as d:
            demo = upgraded_demo(d)
            p = Project(demo)
            text = info(p)
            self.assertIn("in step: yes", text)
            self.assertRegex(text, r"board\s+[\d.]+ × [\d.]+ mm, \d+ footprints")
            self.assertIn("ERC:", text)
            self.assertIn("DRC:", text)
            p.sch().add_symbol("Device:R", "R99", (25.4, 25.4), footprint="Resistor_SMD:R_0603_1608Metric")
            p.save()
            text = info(Project(demo))
            self.assertIn("missing on board: R99", text)

    def test_symbols_and_nets_of_demo_circuit(self):
        with TempDir() as d:
            p = new_project(d, "t")
            build_demo_circuit(p.sch())
            p.save()
            text = info(Project(d))
        self.assertRegex(text, r"U1\s+AMS1117-3.3\s+Regulator_Linear:AMS1117-3.3\s+"
                               r"Package_TO_SOT_SMD:SOT-223-3_TabPin2")
        self.assertRegex(text, r"\+3V3: .*R1\.1")
        self.assertNotIn("#PWR", text.split("nets")[0])
        self.assertIn("missing on board: C1, C2, D1, J1, R1, U1", text)

    def test_in_step_differs(self):
        sch = {"R1": ("10k", "A:B"), "R2": ("1k", "A:B")}
        board = {"R1": ("4k7", "A:B"), "R3": ("1k", "A:B")}
        self.assertEqual(in_step(sch, board),
                         "missing on board: R2; not in schematic: R3; differs: R1 value 10k ≠ 4k7")
        self.assertEqual(in_step(sch, dict(sch)), "yes")


if __name__ == "__main__":
    unittest.main()

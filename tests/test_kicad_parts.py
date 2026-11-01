import unittest

from _kicad import KICAD_SHARE, TempDir, needs_kicad
from kt import cli
from kt.libs import register_library
from kt.parts import Footprint, Symbol
from kt.project import LockedError, Project, RejectedError, lock_path, new_project
from kt.sexpr import Node
from test_kicad_schematic import nets_by_name, raw_label


def sot23() -> Footprint:
    fp = Footprint("SOT-23_test", descr="SOT-23 built by kt", tags="SOT-23")
    for number, at in (("1", (-1.1375, -0.95)), ("2", (-1.1375, 0.95)), ("3", (1.1375, 0))):
        fp.pad(number, "smd", "roundrect", at, (1.325, 0.6))
    fp.rect("F.Fab", (-0.65, -1.45), (0.65, 1.45), 0.1)
    fp.line("F.SilkS", (0, -1.56), (0.65, -1.56), 0.12)
    fp.line("F.SilkS", (0, 1.56), (0.65, 1.56), 0.12)
    fp.courtyard(0.25)
    fp.model("${KICAD10_3DMODEL_DIR}/Package_TO_SOT_SMD.3dshapes/SOT-23.step")
    return fp


def test8() -> Symbol:
    s = Symbol("TEST8", reference="U", footprint="Mine:SOT-23_test", description="eight pins")
    s.pin("1", "IN", "input", "left")
    s.pin("2", "EN", "input", "left")
    s.pin("3", "OUT", "output", "right")
    s.pin("4", "FB", "input", "right")
    s.pin("5", "VCC", "power_in", "top")
    s.pin("6", "VBAT", "power_in", "top")
    s.pin("7", "GND", "power_in", "bottom")
    s.pin("8", "SPARE", "free", "bottom")
    return s


class BuilderTest(unittest.TestCase):
    def test_footprint_node(self):
        node = sot23().node()
        self.assertEqual(node.head, "footprint")
        self.assertEqual(node[0], "SOT-23_test")
        self.assertEqual(node.get("version")[0], 20260206)
        self.assertEqual(str(node.get("attr")[0]), "smd")
        self.assertEqual([p[0] for p in node.all("pad")], ["1", "2", "3"])
        crt = [g for g in node.all("fp_rect") if g.get("layer")[0] == "F.CrtYd"]
        self.assertEqual(len(crt), 1)
        self.assertEqual(crt[0].get("start").values(), [-2.05, -1.7])

    def test_symbol_node_pins_on_grid(self):
        node = test8().node()
        pins = [p for p in node.walk() if p.head == "pin"]
        self.assertEqual(len(pins), 8)
        for p in pins:
            x, y = p.get("at").values()[:2]
            self.assertAlmostEqual(x / 1.27, round(x / 1.27), places=6)
            self.assertAlmostEqual(y / 1.27, round(y / 1.27), places=6)


@needs_kicad
class LibraryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.d = self._tmp.__enter__()
        self.p = new_project(self.d / "proj", "t")

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def test_sot23_footprint_builds_and_places(self):
        lib = self.p.footprint_lib("Mine.pretty")
        lib.add(sot23())
        register_library("footprint", "Mine", lib.path, "project", self.p)
        self.p.save()
        self.assertTrue((self.p.dir / "Mine.pretty" / "SOT-23_test.kicad_mod").is_file())
        p = Project(self.p.dir)
        board = p.document(p.pcb_path)
        board.outline_rect(0, 0, 40, 40)
        board.add_footprint("Mine:SOT-23_test", "U1", (20, 20))
        drc = p.save().reports[p.pcb_path]
        bad = [f for f in drc.errors + drc.warnings if "courtyard" in f.type]
        self.assertEqual(bad, [], drc.summary())

    def test_symbol_builds_and_places(self):
        lib = self.p.symbol_lib("Mine.kicad_sym")
        lib.add(test8())
        register_library("symbol", "Mine", lib.path, "project", self.p)
        self.p.save()
        p = Project(self.p.dir)
        sch = p.sch()
        u = sch.add_symbol("Mine:TEST8", "U1", (101.6, 101.6))
        for number, at in u.pins().items():
            raw_label(sch, f"N_{number}", at)
        p.save()
        nets = nets_by_name(p.root_sch)
        for n in "12345678":
            self.assertEqual(nets.get(f"N_{n}"), {("U1", n)})

    def test_copy_and_adapt(self):
        stock = KICAD_SHARE / "footprints" / "Resistor_SMD.pretty" / "R_0603_1608Metric.kicad_mod"
        before = stock.stat().st_mtime_ns
        lib = self.p.footprint_lib("Mine.pretty")
        fp = lib.copy_from("Resistor_SMD:R_0603_1608Metric", "R_0603_mine")
        fp.get("pad").get("size").set(0, 0.9)
        self.assertEqual(lib.names(), ["R_0603_mine"])
        result = self.p.save()
        path = self.p.dir / "Mine.pretty" / "R_0603_mine.kicad_mod"
        self.assertIn(path, result.written)
        self.assertIn("(size 0.9 0.95)", path.read_text(encoding="utf-8"))
        self.assertEqual(stock.stat().st_mtime_ns, before)

    def test_symbol_copy(self):
        lib = self.p.symbol_lib("Mine.kicad_sym")
        sym = lib.copy_from("Regulator_Linear:AMS1117-3.3", "MY_LDO")
        self.assertEqual(sym[0], "MY_LDO")
        self.assertTrue(all(u[0].startswith("MY_LDO_") for u in sym.all("symbol")))
        self.p.save()
        self.assertEqual(lib.names(), ["MY_LDO"])

    def test_library_outside_project_locked(self):
        outside = self.d / "libs" / "Mine.kicad_sym"
        lib = self.p.symbol_lib(outside)
        lib.add(test8())
        self.p.save()
        self.assertTrue(outside.is_file())
        lib = Project(self.p.dir).symbol_lib(outside)
        lib.add(test8(), replace=True)
        lock_path(outside).write_text("x")
        with self.assertRaises(LockedError):
            lib.project.save()

    def test_broken_footprint_rejected(self):
        lib = self.p.footprint_lib("Mine.pretty")
        node = sot23().node()
        node.append(Node("frobnicate", 1))
        lib.add(node)
        with self.assertRaises(RejectedError):
            self.p.save()
        self.assertFalse((self.p.dir / "Mine.pretty" / "SOT-23_test.kicad_mod").exists())

    def test_add_twice_needs_replace(self):
        lib = self.p.footprint_lib("Mine.pretty")
        lib.add(sot23())
        with self.assertRaises(FileExistsError):
            lib.add(sot23())


if __name__ == "__main__":
    unittest.main()

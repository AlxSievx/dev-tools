import unittest
import uuid

from _kicad import TempDir, copy_demo, needs_kicad
from kt import cli
from kt.project import Project, new_project
from kt.sexpr import Node, Sym


def raw_label(sch, name, at):
    """A local net label written without kt's helpers (Task 6 tests geometry alone)."""
    sch.root.append(Node(
        "label", name, Node("at", at[0], at[1], 0),
        Node("effects", Node("font", Node("size", 1.27, 1.27)), Node("justify", Sym("left"), Sym("bottom"))),
        Node("uuid", str(uuid.uuid4()))))


def nets_by_name(sch_path):
    """{net name without sheet path: {(ref, pin), …}} from KiCad's own netlist."""
    out = {}
    for net in cli.netlist(sch_path).get("nets").all("net"):
        name = net.get("name")[0].rsplit("/", 1)[-1]
        out[name] = {(n.get("ref")[0], n.get("pin")[0]) for n in net.all("node")}
    return out


@needs_kicad
class SymbolTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.d = self._tmp.__enter__()
        self.p = new_project(self.d, "t")
        self.sch = self.p.sch()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def label_all_pins(self, sym):
        for number, at in sym.pins().items():
            raw_label(self.sch, f"N_{sym.ref}_{number}", at)

    def assert_labels_on_pins(self, syms):
        nets = nets_by_name(self.p.root_sch)
        for sym in syms:
            for number in sym.pins():
                with self.subTest(ref=sym.ref, pin=number, rot=sym.rot, mirror=sym.mirror):
                    self.assertEqual(nets.get(f"N_{sym.ref}_{number}"), {(sym.ref, number)})

    def test_pin_positions_match_kicad(self):
        placed, i = [], 0
        for rot in (0, 90, 180, 270):
            for mirror in (None, "x", "y"):
                i += 1
                at = (25.4 * (1 + (i - 1) % 4), 25.4 * (1 + (i - 1) // 4))
                placed.append(self.sch.add_symbol("Device:R", f"R{i}", at, rot=rot, mirror=mirror))
        placed.append(self.sch.add_symbol("Device:LED", "D1", (127, 25.4), rot=90))
        placed.append(self.sch.add_symbol("Regulator_Linear:AMS1117-3.3", "U1", (127, 76.2), rot=180, mirror="y"))
        for s in placed:
            self.label_all_pins(s)
        self.p.save()
        self.assert_labels_on_pins(placed)

    def test_multi_unit(self):
        a = self.sch.add_symbol("Amplifier_Operational:LM358", "U1", (50.8, 50.8), unit=1)
        b = self.sch.add_symbol("Amplifier_Operational:LM358", "U1", (101.6, 50.8), unit=2, rot=90)
        c = self.sch.add_symbol("Amplifier_Operational:LM358", "U1", (152.4, 50.8), unit=3)
        self.assertEqual(sorted(a.pins()), ["1", "2", "3"])
        self.assertEqual(sorted(b.pins()), ["5", "6", "7"])
        self.assertEqual(sorted(c.pins()), ["4", "8"])
        for s in (a, b, c):
            self.label_all_pins(s)
        embedded = [s[0] for s in self.sch.root.get("lib_symbols").all("symbol")]
        self.assertEqual(embedded, ["Amplifier_Operational:LM358"])
        self.p.save()
        self.assert_labels_on_pins([a, b, c])

    def test_add_symbol_fields(self):
        r = self.sch.add_symbol("Device:R", "R1", (50.8, 50.8), value="10k",
                                footprint="Resistor_SMD:R_0603_1608Metric", fields={"MPN": "RC0603"})
        self.assertEqual(r.fields()["Value"], "10k")
        self.assertEqual(r.fields()["Footprint"], "Resistor_SMD:R_0603_1608Metric")
        self.assertEqual(r.fields()["MPN"], "RC0603")
        self.assertEqual(self.sch.symbol("R1").value, "10k")
        result = self.p.save()
        self.assertEqual(result.reports[self.p.root_sch].kind, "ERC")
        nl = cli.netlist(self.p.root_sch)
        comp = nl.get("components").get("comp")
        self.assertEqual(comp.get("ref")[0], "R1")
        self.assertEqual(comp.get("value")[0], "10k")

    def test_off_grid_refused(self):
        with self.assertRaises(ValueError) as cm:
            self.sch.add_symbol("Device:R", "R1", (50.0, 50.8))
        self.assertIn("1.27", str(cm.exception))

    def test_next_ref(self):
        self.assertEqual(self.sch.next_ref("R"), "R1")
        self.sch.add_symbol("Device:R", "R1", (50.8, 50.8))
        self.sch.add_symbol("Device:R", "R2", (76.2, 50.8))
        self.assertEqual(self.sch.next_ref("R"), "R3")
        self.assertEqual(self.sch.next_ref("C"), "C1")

    def test_pin_by_name(self):
        d = self.sch.add_symbol("Device:LED", "D1", (50.8, 50.8))
        self.assertEqual(d.pin("A"), d.pins()["2"])
        self.assertEqual(d.pin("1"), d.pins()["1"])


@needs_kicad
class SubsheetTest(unittest.TestCase):
    def test_subsheet_instances(self):
        with TempDir() as d:
            demo = copy_demo("pic_programmer", d)
            cli.run("sch", "upgrade", "--force", demo / "pic_programmer.kicad_sch")
            cli.run("sch", "upgrade", "--force", demo / "pic_sockets.kicad_sch")
            p = Project(demo)
            self.assertEqual(sorted(s.path.name for s in p.sheets()),
                             ["pic_programmer.kicad_sch", "pic_sockets.kicad_sch"])
            sub = p.sch("pic_sockets.kicad_sch")
            self.assertEqual(len(sub.instance_paths), 1)
            self.assertEqual(sub.instance_paths[0].count("/"), 2)
            ref = sub.next_ref("R")
            sub.add_symbol("Device:R", ref, (25.4, 25.4))
            p.save()
            refs = [c.get("ref")[0] for c in cli.netlist(p.root_sch).get("components").all("comp")]
            self.assertIn(ref, refs)


if __name__ == "__main__":
    unittest.main()

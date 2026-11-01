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



def build_demo_circuit(sch):
    """+5V on J1 -> AMS1117-3.3 (C1 in, C2 out) -> +3V3 -> R1 -> D1 -> GND."""
    j1 = sch.add_symbol("Connector:Conn_01x02_Pin", "J1", (25.4, 50.8),
                        footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    u1 = sch.add_symbol("Regulator_Linear:AMS1117-3.3", "U1", (76.2, 50.8))
    c1 = sch.add_symbol("Device:C", "C1", (55.88, 58.42), value="10u", footprint="Capacitor_SMD:C_0805_2012Metric")
    c2 = sch.add_symbol("Device:C", "C2", (96.52, 58.42), value="10u", footprint="Capacitor_SMD:C_0805_2012Metric")
    r1 = sch.add_symbol("Device:R", "R1", (114.3, 58.42), value="330", footprint="Resistor_SMD:R_0603_1608Metric")
    d1 = sch.add_symbol("Device:LED", "D1", (114.3, 73.66), rot=90, footprint="LED_SMD:LED_0603_1608Metric")
    # +5V rail: J1 pin 1 -> C1 pin 1 -> U1 VI
    sch.wire("J1:1", (45.72, 50.8))
    sch.wire((45.72, 50.8), "U1:VI")
    sch.wire("C1:1", (55.88, 50.8))
    # +3V3 rail: U1 VO -> C2 pin 1 -> R1 pin 1
    sch.wire("U1:VO", (96.52, 50.8))
    sch.wire((96.52, 50.8), "C2:1")
    sch.wire((96.52, 50.8), (114.3, 50.8), "R1:1")
    sch.wire("R1:2", "D1:A")
    sch.power("+5V", (45.72, 45.72))
    sch.wire((45.72, 45.72), (45.72, 50.8))
    sch.power("PWR_FLAG", (40.64, 50.8))
    sch.wire((40.64, 50.8), (45.72, 50.8))
    sch.power("+3V3", (106.68, 45.72))
    sch.wire((106.68, 45.72), (106.68, 50.8))
    grounds = []
    for pin in ("J1:2", "U1:GND", "C1:2", "C2:2", "D1:K"):
        x, y = sch.pin_point(pin)
        grounds.append(sch.power("GND", (x, y + 7.62)))
        sch.wire((x, y), grounds[-1].pins()["1"])
    gx, gy = grounds[0].pins()["1"]
    flag = sch.power("PWR_FLAG", (gx - 5.08, gy))
    sch.wire(flag.pins()["1"], (gx, gy))
    return j1, u1, c1, c2, r1, d1


@needs_kicad
class WiringTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.d = self._tmp.__enter__()
        self.p = new_project(self.d, "t")
        self.sch = self.p.sch()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def test_demo_circuit_erc_clean(self):
        build_demo_circuit(self.sch)
        result = self.p.save()
        erc = result.reports[self.p.root_sch]
        self.assertEqual(erc.errors, [], erc.summary())
        nets = nets_by_name(self.p.root_sch)
        r1_d1 = [n for n, nodes in nets.items() if ("R1", "2") in nodes]
        self.assertEqual(len(r1_d1), 1)
        self.assertIn(("D1", "2"), nets[r1_d1[0]])
        self.assertEqual({r for r, _ in nets["+3V3"] if not r.startswith("#")}, {"U1", "C2", "R1"})
        self.assertIn(("U1", "3"), nets["+5V"])
        self.assertIn(("J1", "2"), nets["GND"])

    def test_wire_by_pin_names(self):
        self.sch.add_symbol("Device:R", "R1", (50.8, 50.8))
        self.sch.add_symbol("Device:LED", "D1", (76.2, 63.5))
        self.sch.wire("R1:1", "D1:A")
        self.sch.label("SIG", "R1:1")
        self.p.save()
        self.assertEqual(nets_by_name(self.p.root_sch)["SIG"], {("R1", "1"), ("D1", "2")})

    def test_junction_added(self):
        self.sch.wire((25.4, 25.4), (50.8, 25.4))
        self.sch.wire((38.1, 25.4), (38.1, 38.1))
        junctions = [tuple(j.get("at").values()) for j in self.sch.root.all("junction")]
        self.assertEqual(junctions, [(38.1, 25.4)])
        self.sch.wire((25.4, 25.4), (25.4, 12.7))   # a corner: two ends, no junction
        self.assertEqual(len(self.sch.root.all("junction")), 1)

    def test_junction_only_where_touched(self):
        for a, b in (((25.4, 25.4), (50.8, 25.4)), ((38.1, 25.4), (38.1, 38.1))):
            self.sch.root.insert(len(self.sch.root.items) - 2, Node(
                "wire", Node("pts", Node("xy", *a), Node("xy", *b)), Node("uuid", str(uuid.uuid4()))))
        self.sch.add_symbol("Device:R", "R1", (127, 127))
        self.assertEqual(self.sch.root.all("junction"), [])     # the user's T stays as drawn
        self.sch.wire((38.1, 25.4), (38.1, 12.7))               # a new wire end at the T
        self.assertEqual([tuple(j.get("at").values()) for j in self.sch.root.all("junction")], [(38.1, 25.4)])

    def test_labels_and_no_connect(self):
        r = self.sch.add_symbol("Device:R", "R1", (50.8, 50.8))
        self.sch.label("A", "R1:1", kind="global")
        self.sch.label("B", "R1:2", kind="hierarchical")
        self.sch.no_connect((76.2, 76.2))
        heads = [n.head for n in self.sch.root.all()]
        for h in ("global_label", "hierarchical_label", "no_connect"):
            self.assertIn(h, heads)
        self.p.save()  # loads in kicad-cli

    def test_move_rotate_delete_set_field(self):
        r = self.sch.add_symbol("Device:R", "R1", (50.8, 50.8))
        self.sch.add_symbol("Device:R", "R2", (76.2, 50.8))
        self.sch.move("R1", (63.5, 63.5))
        self.assertEqual(self.sch.symbol("R1").at, (63.5, 63.5))
        self.sch.rotate("R1", 90)
        self.assertEqual(self.sch.symbol("R1").rot, 90)
        self.sch.label("X", "R1:1")
        self.sch.symbol("R1").set_field("Value", "4k7")
        self.sch.symbol("R1").set_field("MPN", "ABC")
        self.p.save()
        nets = nets_by_name(self.p.root_sch)
        self.assertEqual(nets["X"], {("R1", "1")})
        sch = self.p.sch()
        self.assertEqual(sch.symbol("R1").fields()["MPN"], "ABC")
        sch.delete("R1")
        self.assertEqual([s[0] for s in sch.root.get("lib_symbols").all("symbol")], ["Device:R"])
        sch.delete("R2")
        self.assertEqual(sch.root.get("lib_symbols").all("symbol"), [])
        self.p.save()
        self.assertNotIn("R1", {r for nodes in nets_by_name(self.p.root_sch).values() for r, _ in nodes})

    def test_wire_off_grid(self):
        with self.assertRaises(ValueError):
            self.sch.wire((25.4, 25.4), (25.0, 30.48))

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

"""Sub-sheets: sheet symbols with pins, new sheet files, a file used for several instances."""
import unittest

from _kicad import TempDir, needs_kicad, needs_share
from kt import cli
from kt.project import Project, new_project

PINS = [("IN", "input", "left"), ("OUT", "output", "right")]


def nets_by_name(sch_path):
    out = {}
    for net in cli.netlist(sch_path).get("nets").all("net"):
        name = net.get("name")[0].rsplit("/", 1)[-1]
        out[name] = {(n.get("ref")[0], n.get("pin")[0]) for n in net.all("node")}
    return out


def netlist_refs(p):
    return {c.get("ref")[0] for c in cli.netlist(p.root_sch).get("components").all("comp")}


@needs_kicad
@needs_share
class SheetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.d = self._tmp.__enter__()
        self.p = new_project(self.d, "t")
        self.root = self.p.sch()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def build(self):
        """Root with Halfbridge_U → halfbridge.kicad_sch (page 2): IN —R— OUT inside."""
        hb = self.root.add_sheet("Halfbridge_U", "halfbridge.kicad_sch", (25.4, 25.4), (25.4, 20.32),
                                 pins=PINS, page="2")
        child = hb.child()
        r = child.add_symbol("Device:R", child.next_ref("R", start=201), (50.8, 50.8), rot=90)
        child.wire("R201:1", (40.64, 50.8))
        child.label("IN", (40.64, 50.8), rot=180, kind="hierarchical", shape="input")
        child.wire("R201:2", (60.96, 50.8))
        child.label("OUT", (60.96, 50.8), kind="hierarchical", shape="output")
        self.root.wire("Halfbridge_U:IN", (17.78, hb.pin("IN")[1]))
        self.root.label("A_U", (17.78, hb.pin("IN")[1]), rot=180)
        self.root.wire("Halfbridge_U:OUT", (60.96, hb.pin("OUT")[1]))
        self.root.label("B_U", (60.96, hb.pin("OUT")[1]))
        return hb, r

    def test_new_sheet_file_loads_and_connects(self):
        self.build()
        self.p.save()
        self.assertTrue((self.d / "halfbridge.kicad_sch").is_file())
        nets = nets_by_name(self.p.root_sch)
        self.assertIn(("R201", "1"), nets["A_U"])
        self.assertIn(("R201", "2"), nets["B_U"])
        self.assertEqual(cli.erc(self.p.root_sch).errors, [])

    def test_child_copies_paper_and_title_block(self):
        self.root.root.get("paper").set(0, "A3")
        hb = self.root.add_sheet("X", "x.kicad_sch", (25.4, 25.4), (25.4, 20.32))
        self.assertEqual(hb.child().root.get("paper")[0], "A3")

    def test_reuse_gives_new_instance_refs(self):
        self.build()
        self.root.add_sheet("Halfbridge_V", "halfbridge.kicad_sch", (101.6, 25.4), (25.4, 20.32),
                            pins=PINS, page="3", ref_start=301)
        self.p.save()
        self.assertTrue({"R201", "R301"} <= netlist_refs(self.p))
        self.assertEqual(sorted(self.p.sch("halfbridge.kicad_sch").instance_pages().values()), ["2", "3"])

    def test_reuse_in_one_script_before_save(self):
        hb = self.root.add_sheet("A", "same.kicad_sch", (25.4, 25.4), (25.4, 20.32), page="2")
        child = hb.child()
        child.add_symbol("Device:R", "R201", (50.8, 50.8))
        child.add_symbol("Device:R", "R202", (76.2, 50.8))
        child.add_symbol("Device:C", "C201", (101.6, 50.8))
        self.root.add_sheet("B", "same.kicad_sch", (76.2, 25.4), (25.4, 20.32), page="3", ref_start=301)
        self.p.save()
        self.assertTrue({"R201", "R202", "C201", "R301", "R302", "C301"} <= netlist_refs(self.p))

    def test_wire_to_sheet_pin_by_name(self):
        hb, _ = self.build()
        x, y = hb.pin("OUT")
        self.assertEqual(self.root.pin_point("Halfbridge_U:OUT"), (x, y))

    def test_pin_positions(self):
        hb = self.root.add_sheet("S", "s.kicad_sch", (25.4, 25.4), (25.4, 20.32),
                                 pins=[("A", "input", "left"), ("B", "input", "left"), ("C", "output", "right"),
                                       ("D", "bidirectional", "top"), ("E", "passive", "bottom")])
        self.assertEqual(hb.pin("A"), (25.4, 27.94))
        self.assertEqual(hb.pin("B"), (25.4, 30.48))
        self.assertEqual(hb.pin("C"), (50.8, 27.94))
        self.assertEqual(hb.pin("D"), (27.94, 25.4))
        self.assertEqual(hb.pin("E"), (27.94, 45.72))
        self.assertEqual(hb.add_pin("F", "input", "left", 10.16), (25.4, 35.56))

    def test_next_page(self):
        self.assertEqual(self.p.next_page(), "2")
        self.root.add_sheet("S", "s.kicad_sch", (25.4, 25.4), (25.4, 20.32))
        self.assertEqual(self.p.next_page(), "3")
        self.assertEqual(self.root.sheet("S").pages(), {"/" + self.root.uuid + "/" + self.root.sheet("S").uuid: "2"})

    def test_off_grid_and_overflow_refused(self):
        with self.assertRaises(ValueError):
            self.root.add_sheet("S", "s.kicad_sch", (25.0, 25.4), (25.4, 20.32))
        with self.assertRaises(ValueError):
            self.root.add_sheet("S", "s.kicad_sch", (25.4, 25.4), (25.4, 10.16),
                                pins=[(f"P{i}", "input", "left") for i in range(10)])
        with self.assertRaises(ValueError):
            self.root.add_sheet("S", "s.kicad_sch", (25.4, 25.4), (25.4, 20.32), pins=[("P", "sideways", "left")])
        self.assertEqual(self.root.sheet_symbols(), [])
        self.root.add_sheet("S", "s.kicad_sch", (25.4, 25.4), (25.4, 20.32))
        with self.assertRaises(ValueError):
            self.root.add_sheet("S", "t.kicad_sch", (76.2, 25.4), (25.4, 20.32))

    def test_nested_reuse_not_implemented(self):
        outer = self.root.add_sheet("Outer", "outer.kicad_sch", (25.4, 25.4), (25.4, 20.32))
        outer.child().add_sheet("Inner", "inner.kicad_sch", (25.4, 25.4), (25.4, 20.32))
        with self.assertRaises(NotImplementedError):
            self.root.add_sheet("Outer2", "outer.kicad_sch", (76.2, 25.4), (25.4, 20.32))

    # ---- review fixes
    def reused(self):
        self.build()
        self.root.add_sheet("Halfbridge_V", "halfbridge.kicad_sch", (101.6, 25.4), (25.4, 20.32),
                            pins=PINS, page="3", ref_start=301)
        return self.p.sch("halfbridge.kicad_sch")

    def test_set_reference_on_reused_sheet_refused(self):
        child = self.reused()
        with self.assertRaises(ValueError) as cm:
            child.symbol("R301").set_field("Reference", "R250")
        self.assertIn("set_ref", str(cm.exception))

    def test_set_ref_changes_one_instance(self):
        child = self.reused()
        child.symbol("R301").set_ref("R301", "R350")
        self.p.save()
        refs = netlist_refs(self.p)
        self.assertTrue({"R201", "R350"} <= refs, refs)
        self.assertNotIn("R301", refs)

    def test_set_ref_refuses_a_used_reference(self):
        child = self.reused()
        with self.assertRaises(ValueError):
            child.symbol("R301").set_ref("R301", "R201")

    def test_sheet_showing_its_own_file_refused(self):
        hb = self.root.add_sheet("A", "a.kicad_sch", (25.4, 25.4), (25.4, 20.32))
        with self.assertRaises(ValueError):
            hb.child().add_sheet("Self", "a.kicad_sch", (25.4, 25.4), (25.4, 20.32))

    def test_unannotated_parts_get_one_reference_each(self):
        hb = self.root.add_sheet("A", "same.kicad_sch", (25.4, 25.4), (25.4, 20.32), page="2")
        child = hb.child()
        child.add_symbol("Device:R", "R?", (50.8, 50.8))
        child.add_symbol("Device:R", "R?", (76.2, 50.8))
        self.root.add_sheet("B", "same.kicad_sch", (76.2, 25.4), (25.4, 20.32), page="3", ref_start=301)
        new = sorted(s.refs[next(p for p in s.refs if p.endswith(self.root.sheet("B").uuid))]
                     for s in child.symbols())
        self.assertEqual(new, ["R301", "R302"])

    def test_repeated_dict_refs_refused(self):
        child = self.reused()
        same = {path: "R401" for path in child.instance_paths}
        with self.assertRaises(ValueError):
            child.add_symbol("Device:R", same, (76.2, 76.2))

    def test_reused_file_with_subsheets_reached_twice(self):
        """root: X1 -> x (holds Y -> y), A -> a, and a also shows x. KiCad allows it;
        y then has two instances, so a single string reference must be refused."""
        x1 = self.root.add_sheet("X1", "x.kicad_sch", (25.4, 25.4), (25.4, 20.32), page="2")
        x1.child().add_sheet("Y", "y.kicad_sch", (25.4, 25.4), (25.4, 20.32), page="3")
        a = self.root.add_sheet("A", "a.kicad_sch", (76.2, 25.4), (25.4, 20.32), page="4")
        node = x1.node.copy()
        node.get("uuid").set(0, "11111111-2222-3333-4444-555555555555")
        inst = node.get("instances").get("project").get("path")
        inst.set(0, "/" + self.root.uuid + "/" + a.uuid)
        inst.get("page").set(0, "5")
        a.child()._insert_item(node)
        self.p.save()
        y = Project(self.d).sch("y.kicad_sch")
        self.assertEqual(len(y.instance_paths), 2)
        with self.assertRaises(ValueError):
            y.add_symbol("Device:R", "R3", (50.8, 50.8))


if __name__ == "__main__":
    unittest.main()

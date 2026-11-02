"""References per sheet instance, numbering from a start value, DNP, visible fields.

The fixture is KiCad's own `complex_hierarchy` demo: `ampli_ht.kicad_sch` is used
twice (pages 2 and 3), its parts are RV201 / RV301 and so on.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from _kicad import TempDir, copy_demo, needs_kicad, needs_share
from kt import cli
from kt.project import Project, new_project
from kt.sexpr import parse


def netlist_refs(p):
    return {c.get("ref")[0] for c in cli.netlist(p.root_sch).get("components").all("comp")}


def roundtrip(path: Path):
    """The file as KiCad saves it again: only what KiCad understood survives."""
    with tempfile.TemporaryDirectory(prefix="kt-rt-") as tmp:
        copy = Path(tmp) / path.name
        shutil.copy2(path, copy)
        cli.run("sch", "upgrade", "--force", copy)
        return parse(copy.read_text(encoding="utf-8"))


@needs_kicad
@needs_share
class InstanceRefsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        d = self._tmp.__enter__()
        demo = copy_demo("complex_hierarchy", d)
        for name in ("complex_hierarchy.kicad_sch", "ampli_ht.kicad_sch"):
            cli.run("sch", "upgrade", "--force", demo / name)
        self.p = Project(demo)
        self.amp = self.p.sch("ampli_ht.kicad_sch")

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def test_instance_pages(self):
        self.assertEqual(sorted(self.amp.instance_pages().values()), ["2", "3"])
        root = self.p.sch()
        self.assertEqual(root.instance_pages(), {"/" + root.uuid: "1"})

    def test_next_ref_from_start(self):
        self.assertEqual(self.amp.next_ref("RV", start=201), "RV202")
        self.assertEqual(self.amp.next_ref("RV", start=301), "RV302")
        self.assertEqual(self.amp.next_ref("R", start=900), "R900")

    def test_next_refs_distinct(self):
        starts = {path: 900 for path in self.amp.instance_paths}
        self.assertEqual(sorted(self.amp.next_refs("R", starts).values()), ["R900", "R901"])

    def test_dict_refs_reach_netlist(self):
        refs = self.amp.next_refs("R", {path: int(pg) * 100 + 90 for path, pg in self.amp.instance_pages().items()})
        self.amp.add_symbol("Device:R", refs, (25.4, 25.4))
        self.p.save()
        self.assertTrue({"R290", "R390"} <= netlist_refs(self.p))

    def test_dict_refs_must_cover_instances(self):
        one = {self.amp.instance_paths[0]: "R290"}
        with self.assertRaises(ValueError):
            self.amp.add_symbol("Device:R", one, (25.4, 25.4))

    def test_str_ref_on_multi_instance_refused(self):
        before = len(self.amp.symbols())
        with self.assertRaises(ValueError) as cm:
            self.amp.add_symbol("Device:R", "R999", (25.4, 25.4))
        self.assertIn("next_refs", str(cm.exception))
        self.assertEqual(len(self.amp.symbols()), before)

    def test_symbol_found_by_instance_ref(self):
        sym = self.amp.symbol("RV301")
        self.assertIn("RV301", sym.refs.values())
        self.assertIn("RV201", sym.refs.values())

    def test_power_distinct_per_instance(self):
        pwr = self.amp.power("GND", (25.4, 50.8))
        refs = list(pwr.refs.values())
        self.assertEqual(len(set(refs)), 2)
        self.p.save()
        dupes = [f for f in cli.erc(self.p.root_sch).errors if "duplicate" in f.type]
        self.assertEqual(dupes, [])


@needs_kicad
@needs_share
class SymbolFlagsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.d = self._tmp.__enter__()
        self.p = new_project(self.d, "t")
        self.sch = self.p.sch()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def _placed(self, root, ref):
        for s in root.all("symbol"):
            for p in s.all("property"):
                if p[0] == "Reference" and p[1] == ref:
                    return s
        raise KeyError(ref)

    def test_dnp_round_trip(self):
        self.sch.add_symbol("Device:R", "R1", (50.8, 50.8)).set_dnp()
        self.p.save()
        self.assertEqual(str(self._placed(roundtrip(self.p.root_sch), "R1").get("dnp")[0]), "yes")

    def test_show_field(self):
        r = self.sch.add_symbol("Device:R", "R1", (50.8, 50.8), fields={"Tolerance": "1%"})
        r.show_field("Tolerance")
        self.p.save()
        tol = next(p for p in self._placed(roundtrip(self.p.root_sch), "R1").all("property") if p[0] == "Tolerance")
        hide = tol.get("hide")
        self.assertTrue(hide is None or str(hide[0]) == "no", tol)

    def test_hide_field(self):
        r = self.sch.add_symbol("Device:R", "R1", (50.8, 50.8))
        r.show_field("Value", visible=False)
        self.p.save()
        val = next(p for p in self._placed(roundtrip(self.p.root_sch), "R1").all("property") if p[0] == "Value")
        self.assertEqual(str(val.get("hide")[0]), "yes")

    def test_show_unknown_field(self):
        r = self.sch.add_symbol("Device:R", "R1", (50.8, 50.8))
        with self.assertRaises(KeyError):
            r.show_field("NoSuchField")


if __name__ == "__main__":
    unittest.main()

import unittest

from _kicad import TempDir, needs_kicad
from kt import views
from kt.project import new_project
from test_kicad_cmd import run_cli
from test_kicad_info import upgraded_demo
from test_kicad_schematic import build_demo_circuit

PNG = b"\x89PNG"
PDF = b"%PDF"


def starts(path, magic):
    return path.is_file() and path.read_bytes()[:4] == magic


@needs_kicad
class ViewsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.d = cls._tmp.__enter__()
        from kt.project import Project
        cls.demo = Project(upgraded_demo(cls.d))
        cls.out = cls.d / "shots"

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_sch_pdf(self):
        self.assertTrue(starts(views.sch_pdf(self.demo, self.out), PDF))

    def test_sch_pdf_one_page(self):
        self.assertTrue(starts(views.sch_pdf(self.demo, self.out, pages="2"), PDF))

    def test_pcb_png_views(self):
        for view in ("top", "iso"):
            with self.subTest(view=view):
                path = views.pcb_png(self.demo, view, self.out, size=(800, 500))
                self.assertTrue(starts(path, PNG))
                self.assertIn(view, path.name)

    def test_pcb_layers_pdf(self):
        self.assertTrue(starts(views.pcb_layers_pdf(self.demo, "F.Cu,Edge.Cuts", self.out), PDF))

    def test_footprint_pdf(self):
        path = views.footprint_pdf("Resistor_SMD:R_0603_1608Metric", self.out, self.demo.libs)
        self.assertTrue(starts(path, PDF))

    def test_symbol_pdf(self):
        path = views.symbol_pdf("Device:R", self.out, self.demo.libs)
        self.assertTrue(starts(path, PDF))

    def test_bad_view(self):
        with self.assertRaises(ValueError):
            views.pcb_png(self.demo, "sideways", self.out)


@needs_kicad
class ShotCommandTest(unittest.TestCase):
    def test_shot_sch_prints_path(self):
        with TempDir() as d:
            p = new_project(d / "p", "t")
            build_demo_circuit(p.sch())
            p.save()
            code, out, err = run_cli("kicad", "shot", "sch", "--project", p.dir, "--out", d / "o")
            self.assertEqual(code, 0, err)
            self.assertTrue(starts(__import__("pathlib").Path(out.strip()), PDF))
            code, out, err = run_cli("kicad", "shot", "pcb", "sideways", "--project", p.dir)
            self.assertEqual(code, 5)
            code, out, err = run_cli("kicad", "shot", "sym", "Device:LED", "--project", p.dir, "--out", d / "o")
            self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()

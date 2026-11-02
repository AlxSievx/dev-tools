"""Text, text boxes, rectangles, title block and paper on a schematic sheet.

Each test saves, lets KiCad load and save the file again (`sch upgrade --force` on a
copy) and reads that copy: only what KiCad understood survives.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from _kicad import TempDir, needs_kicad
from kt import cli
from kt.project import new_project
from kt.sexpr import parse


def roundtrip(path: Path):
    with tempfile.TemporaryDirectory(prefix="kt-rt-") as tmp:
        copy = Path(tmp) / path.name
        shutil.copy2(path, copy)
        cli.run("sch", "upgrade", "--force", copy)
        return parse(copy.read_text(encoding="utf-8"))


@needs_kicad
class DrawingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.d = self._tmp.__enter__()
        self.p = new_project(self.d, "t")
        self.sch = self.p.sch()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def saved(self):
        self.p.save()
        return roundtrip(self.p.root_sch)

    def test_text_round_trip_unicode(self):
        note = 'Überschüssige Footprints "JLC"\nzweite Zeile'
        self.sch.add_text(note, (30, 30), size=2, bold=True)
        texts = self.saved().all("text")
        self.assertEqual([t[0] for t in texts], [note])
        font = texts[0].get("effects").get("font")
        self.assertEqual(list(font.get("size").values()), [2, 2])
        self.assertEqual(str(font.get("bold")[0]), "yes")

    def test_text_rotated_and_justified(self):
        self.sch.add_text("R", (40, 40), rot=90, justify=("right",))
        t = self.saved().get("text")
        self.assertEqual(t.get("at")[2], 90)
        self.assertIn("right", [str(v) for v in t.get("effects").get("justify").values()])

    def test_text_box_and_rect(self):
        self.sch.text_box("Hier nur einen der beiden Linearregler bestücken!", (20, 20), (40.64, 10.16))
        self.sch.rect((10, 10), (80, 60), style="dash")
        root = self.saved()
        box = root.get("text_box")
        self.assertEqual(box[0], "Hier nur einen der beiden Linearregler bestücken!")
        self.assertEqual(list(box.get("size").values()), [40.64, 10.16])
        rect = root.get("rectangle")
        self.assertEqual(list(rect.get("start").values()), [10, 10])
        self.assertEqual(list(rect.get("end").values()), [80, 60])
        self.assertEqual(str(rect.get("stroke").get("type")[0]), "dash")

    def test_text_box_without_border(self):
        self.sch.text_box("x", (20, 20), (20.32, 5.08), border=False)
        stroke = self.saved().get("text_box").get("stroke")
        self.assertLess(stroke.get("width")[0], 0)        # KiCad's "no border": a negative width

    def test_rect_style_refused(self):
        with self.assertRaises(ValueError):
            self.sch.rect((10, 10), (20, 20), style="wavy")

    def test_title_block(self):
        got = self.sch.title_block(title="Halfbridge", rev="1.0", company="SciMo", comments={1: "AS"})
        self.assertEqual(got["title"], "Halfbridge")
        self.sch.title_block(date="2026-11-02")             # adds, keeps the rest
        tb = self.saved().get("title_block")
        self.assertEqual(tb.get("title")[0], "Halfbridge")
        self.assertEqual(tb.get("rev")[0], "1.0")
        self.assertEqual(tb.get("date")[0], "2026-11-02")
        self.assertEqual(tb.get("company")[0], "SciMo")
        self.assertEqual([c[1] for c in tb.all("comment") if c[0] == 1], ["AS"])

    def test_title_block_comment_range(self):
        with self.assertRaises(ValueError):
            self.sch.title_block(comments={10: "x"})

    def test_paper(self):
        self.assertEqual(self.sch.paper, ("A4", False))
        self.sch.set_paper("A3")
        self.assertEqual(self.saved().get("paper")[0], "A3")
        self.sch.set_paper("A4", portrait=True)
        self.assertEqual(self.sch.paper, ("A4", True))
        with self.assertRaises(ValueError):
            self.sch.set_paper("A7")


if __name__ == "__main__":
    unittest.main()

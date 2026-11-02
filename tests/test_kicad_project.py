import base64
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from _kicad import TempDir, needs_kicad
from kt import cli
from kt.project import FormatError, LockedError, Project, RejectedError, lock_path, new_project
from kt.sexpr import Node


def mtimes(d: Path) -> dict:
    return {p.name: p.stat().st_mtime_ns for p in d.iterdir() if p.is_file()}


def set_paper(doc, size="A3"):
    doc.root.get("paper").set(0, size)


class ProjectTest(unittest.TestCase):
    def test_open_by_dir_or_pro(self):
        with TempDir() as d:
            p = new_project(d, "t")
            self.assertEqual(p.name, "t")
            self.assertEqual(Project(d / "t.kicad_pro").dir, d)
            self.assertEqual(p.root_sch, d / "t.kicad_sch")
            self.assertEqual(p.pcb_path, d / "t.kicad_pcb")

    def test_no_project_file(self):
        with TempDir() as d:
            with self.assertRaises(FileNotFoundError):
                Project(d)

    def test_old_version_refused(self):
        with TempDir() as d:
            p = new_project(d, "t")
            sch = p.root_sch
            sch.write_text(sch.read_text(encoding="utf-8").replace("20260306", "20250114"), encoding="utf-8")
            with self.assertRaises(FormatError) as cm:
                Project(d).document(sch)
        self.assertIn("devtools kicad upgrade", str(cm.exception))
        self.assertIn("20250114", str(cm.exception))

    def test_nothing_changed_writes_nothing(self):
        with TempDir() as d:
            new_project(d, "t")
            before = mtimes(d)
            p = Project(d)
            p.document(p.root_sch)
            result = p.save()
            self.assertEqual(result.written, [])
            self.assertEqual(mtimes(d), before)

    def test_locked_target(self):
        with TempDir() as d:
            p = new_project(d, "t")
            lock = lock_path(p.root_sch)
            self.assertEqual(lock.name, "~t.kicad_sch.lck")
            lock.write_text("someone")
            before = p.root_sch.read_bytes()
            set_paper(p.document(p.root_sch))
            with self.assertRaises(LockedError) as cm:
                p.save()
            self.assertEqual(cm.exception.paths, [p.root_sch])
            self.assertEqual(p.root_sch.read_bytes(), before)

    @needs_kicad
    def test_lock_on_other_file_ignored(self):
        with TempDir() as d:
            p = new_project(d, "t")
            lock_path(p.pcb_path).write_text("someone")
            set_paper(p.document(p.root_sch))
            result = p.save()
            self.assertEqual(result.written, [p.root_sch])
            self.assertIn('(paper "A3")', p.root_sch.read_text(encoding="utf-8"))

    @needs_kicad
    def test_rejected_writes_nothing(self):
        with TempDir() as d:
            p = new_project(d, "t")
            before = p.root_sch.read_bytes()
            p.document(p.root_sch).root.append(Node("frobnicate", 1))
            with self.assertRaises(RejectedError):
                p.save()
            self.assertEqual(p.root_sch.read_bytes(), before)
            self.assertEqual(list(d.glob("*.kt-tmp")), [])

    @needs_kicad
    def test_space_in_path(self):
        with TempDir() as d:
            p = new_project(d / "My Boards", "t")
            set_paper(p.document(p.root_sch))
            result = p.save()
            self.assertEqual(result.written, [p.root_sch])
            self.assertEqual(result.reports[p.root_sch].kind, "ERC")

    @needs_kicad
    def test_saved_file_loads_in_kicad(self):
        with TempDir() as d:
            p = new_project(d, "t")
            p.document(p.pcb_path).root.append(Node("paper", "A3"))
            result = p.save()
            self.assertEqual(result.written, [p.pcb_path])
            self.assertEqual(result.reports[p.pcb_path].kind, "DRC")
            cli.drc(p.pcb_path)  # loads


def _png() -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))

    def chunk(t, x):
        return struct.pack(">I", len(x)) + t + x + struct.pack(">I", zlib.crc32(t + x) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


MARK_WKS = ('(kicad_wks (version 20231118) (generator "kt")\n'
            ' (setup (textsize 1.5 1.5) (linewidth 0.15) (textlinewidth 0.15)\n'
            '  (left_margin 10) (right_margin 10) (top_margin 10) (bottom_margin 10))\n'
            ' (bitmap (name "") (pos 50 50) (scale 10) (data "{}"))\n)\n')
SHEET = "${KIPRJMOD}/templates/mark.kicad_wks"


def export_svg(p) -> str:
    with tempfile.TemporaryDirectory(prefix="kt-svg-") as tmp:
        cli.run("sch", "export", "svg", "-o", tmp, p.root_sch, input_file=p.root_sch)
        return "".join(f.read_text(encoding="utf-8") for f in Path(tmp).glob("*.svg"))


class DrawingSheetTest(unittest.TestCase):
    def make(self, d):
        p = new_project(d, "t")
        (d / "templates").mkdir()
        (d / "templates" / "mark.kicad_wks").write_text(MARK_WKS.format(base64.b64encode(_png()).decode()),
                                                         encoding="utf-8")
        return p

    @needs_kicad
    def test_drawing_sheet_used_by_kicad(self):
        with TempDir() as d:
            p = self.make(d)
            self.assertNotIn("<image", export_svg(p))        # control: KiCad's default sheet
            p.set_drawing_sheet(SHEET)
            result = p.save()
            self.assertEqual(result.written, [p.pro_path])
            self.assertIn("<image", export_svg(p))

    @needs_kicad
    def test_drawing_sheet_written_as_given(self):
        with TempDir() as d:
            p = self.make(d)
            p.set_drawing_sheet(SHEET)
            p.save()
            data = json.loads(p.pro_path.read_text(encoding="utf-8"))
            self.assertEqual(data["schematic"]["page_layout_descr_file"], SHEET)
            self.assertEqual(data["pcbnew"]["page_layout_descr_file"], SHEET)
            self.assertEqual(Project(d).settings().data["schematic"]["page_layout_descr_file"], SHEET)

    def test_unchanged_settings_write_nothing(self):
        with TempDir() as d:
            p = self.make(d)
            p.settings()
            self.assertEqual(p.save().written, [])

    def test_drawing_sheet_refused_while_open(self):
        with TempDir() as d:
            p = self.make(d)
            lock_path(p.pro_path).write_text("someone")
            before = p.pro_path.read_bytes()
            p.set_drawing_sheet(SHEET)
            with self.assertRaises(LockedError):
                p.save()
            self.assertEqual(p.pro_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

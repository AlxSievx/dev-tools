import unittest
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


if __name__ == "__main__":
    unittest.main()

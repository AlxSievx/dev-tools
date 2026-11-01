"""Regression tests from the final review: nothing KiCad cannot load, nothing open, nothing stale."""
import os
import unittest
import uuid
from unittest import mock

from _kicad import TempDir, copy_demo, needs_kicad
from kt import cli
from kt import project as project_mod
from kt.project import (ChangedOnDiskError, FormatError, LockedError, Project, RejectedError, WriteError,
                        lock_path, new_project)
from kt.sexpr import Node
from test_kicad_cmd import run_cli
from test_kicad_info import upgraded_demo


def insert_bad(root):
    """An unknown token in the middle of a file, where a lenient loader may skip past it."""
    i = next(i for i, it in enumerate(root.items) if isinstance(it, Node) and it.head in ("wire", "footprint"))
    root.insert(i, Node("frobnicate", 1))


@needs_kicad
class StandaloneLoadTest(unittest.TestCase):
    def test_broken_subsheet_rejected(self):
        with TempDir() as d:
            demo = upgraded_demo(d)
            sub = demo / "pic_sockets.kicad_sch"
            before = sub.read_bytes()
            p = Project(demo)
            insert_bad(p.sch("pic_sockets.kicad_sch").root)
            with self.assertRaises(RejectedError) as cm:
                p.save()
            self.assertEqual(cm.exception.path, sub.resolve())
            self.assertEqual(sub.read_bytes(), before)

    def test_other_board_in_project_checked(self):
        with TempDir() as d:
            p = new_project(d, "t")
            other = d / "panel.kicad_pcb"
            other.write_bytes(p.pcb_path.read_bytes())
            p.document(other).root.append(Node("frobnicate", 1))
            with self.assertRaises(RejectedError):
                p.save()

    def test_unknown_kind_refused(self):
        with TempDir() as d:
            p = new_project(d, "t")
            (d / "notes.kicad_wks").write_text("(kicad_wks (version 20231118))\n", encoding="utf-8")
            p.document("notes.kicad_wks").root.append(Node("x", 1))
            with self.assertRaises(RejectedError) as cm:
                p.save()
            self.assertIn("no check", str(cm.exception))


class LockTest(unittest.TestCase):
    def hier(self, d):
        p = new_project(d, "t")
        root_uuid = p.document(p.root_sch).root.get("uuid")[0]
        sub = d / "sub.kicad_sch"
        sub.write_text(p.root_sch.read_text(encoding="utf-8").replace(root_uuid, str(uuid.uuid4())),
                       encoding="utf-8")
        return Project(d), sub

    def test_subsheet_locked_by_root_lock(self):
        with TempDir() as d:
            p, sub = self.hier(d)
            lock_path(p.root_sch).write_text("x")
            p.document(sub).root.get("paper").set(0, "A3")
            with self.assertRaises(LockedError) as cm:
                p.save()
            self.assertEqual(cm.exception.paths, [sub.resolve()])

    def test_locks_command_names_every_sheet(self):
        with TempDir() as d:
            p, sub = self.hier(d)
            lock_path(p.root_sch).write_text("x")
            code, out, err = run_cli("kicad", "locks", d)
            self.assertIn("sub.kicad_sch", out)

    def test_lock_appearing_during_check(self):
        with TempDir() as d:
            p = new_project(d, "t")
            before = p.root_sch.read_bytes()
            p.document(p.root_sch).root.get("paper").set(0, "A3")

            def opened(*a, **k):
                lock_path(p.root_sch).write_text("x")
                return {}
            with mock.patch.object(project_mod, "_check_all", side_effect=opened):
                with self.assertRaises(LockedError):
                    p.save()
            self.assertEqual(p.root_sch.read_bytes(), before)

    def test_changed_on_disk_meanwhile(self):
        with TempDir() as d:
            p = new_project(d, "t")
            doc = p.document(p.root_sch)
            doc.root.get("paper").set(0, "A3")
            theirs = p.root_sch.read_text(encoding="utf-8").replace('"A4"', '"A5"')
            p.root_sch.write_text(theirs, encoding="utf-8")
            os.utime(p.root_sch, ns=(1, 1))
            with mock.patch.object(project_mod, "_check_all", return_value={}):
                with self.assertRaises(ChangedOnDiskError):
                    p.save()
            self.assertEqual(p.root_sch.read_text(encoding="utf-8"), theirs)


class WriteFailureTest(unittest.TestCase):
    def test_partial_write_reported(self):
        with TempDir() as d:
            p = new_project(d, "t")
            p.document(p.root_sch).root.get("paper").set(0, "A3")
            p.document(p.pcb_path).root.append(Node("paper", "A3"))
            real = project_mod.write_atomic
            calls = []

            def flaky(path, text):
                calls.append(path)
                if len(calls) == 2:
                    raise PermissionError(13, "denied", str(path))
                real(path, text)
            with mock.patch.object(project_mod, "write_atomic", side_effect=flaky), \
                    mock.patch.object(project_mod, "_check_all", return_value={}):
                with self.assertRaises(WriteError) as cm:
                    p.save()
            self.assertEqual(cm.exception.written, [calls[0]])
            self.assertEqual(cm.exception.failed, calls[1])
            self.assertIn("not written", str(cm.exception))


@needs_kicad
class LibraryFormatTest(unittest.TestCase):
    def test_upgrade_libraries(self):
        with TempDir() as d:
            demo = copy_demo("pic_programmer", d)
            sym = demo / "pic_programmer.kicad_sym"
            pretty = demo / "libs" / "pic_programmer_fp.pretty"
            with self.assertRaises(FormatError) as cm:
                Project(demo).symbol_lib(sym)
            self.assertIn("--lib", str(cm.exception))
            code, out, err = run_cli("kicad", "upgrade", demo, "--lib", sym, "--lib", pretty)
            self.assertEqual(code, 0, err)
            Project(demo).symbol_lib(sym)
            Project(demo).document(sorted(pretty.glob("*.kicad_mod"))[0])   # no FormatError

    def test_newer_format_message(self):
        with TempDir() as d:
            p = new_project(d, "t")
            p.root_sch.write_text(p.root_sch.read_text(encoding="utf-8").replace("20260306", "20991231"),
                                  encoding="utf-8")
            with self.assertRaises(FormatError) as cm:
                Project(d).document(p.root_sch)
            self.assertIn("newer", str(cm.exception))

    def test_replace_old_footprint_without_loading(self):
        with TempDir() as d:
            demo = copy_demo("pic_programmer", d)
            pretty = demo / "libs" / "pic_programmer_fp.pretty"
            first = sorted(pretty.glob("*.kicad_mod"))[0]
            p = Project(demo)
            node = p.libs.footprint("Resistor_SMD:R_0603_1608Metric")
            node.set(0, first.stem)
            p.footprint_lib(pretty).add(node, replace=True)
            p.save()
            self.assertIn("20260206", first.read_text(encoding="utf-8")[:200])


if __name__ == "__main__":
    unittest.main()

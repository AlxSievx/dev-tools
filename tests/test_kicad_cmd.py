import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from _kicad import TempDir, copy_demo, needs_kicad
from devtools.__main__ import main
from kt import cli
from kt.project import Project, lock_path, new_project


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main([str(a) for a in argv])
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


PAPER_A3 = 'project.document(project.root_sch).root.get("paper").set(0, "A3")\n'


class KicadCmdTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.d = self._tmp.__enter__()
        self.project = new_project(self.d / "proj", "t")
        self.logs = mock.patch.dict(os.environ, {"DEVTOOLS_KICAD_LOGS": str(self.d / "logs")})
        self.logs.start()

    def tearDown(self):
        self.logs.stop()
        self._tmp.__exit__(None, None, None)

    def script(self, text: str) -> Path:
        path = self.d / "script.py"
        path.write_text(text, encoding="utf-8")
        return path

    @needs_kicad
    def test_run_changes_and_saves(self):
        code, out, err = run_cli("kicad", "run", self.script(PAPER_A3), "--project", self.project.dir)
        self.assertEqual(code, 0, err)
        self.assertIn('(paper "A3")', self.project.root_sch.read_text(encoding="utf-8"))
        self.assertIn(str(self.project.root_sch), out)
        self.assertIn("ERC: 0 errors", out)

    def test_run_raises_writes_nothing(self):
        sch = self.project.root_sch
        before, mtime = sch.read_bytes(), sch.stat().st_mtime_ns
        code, out, err = run_cli("kicad", "run", self.script(PAPER_A3 + 'raise ValueError("boom")\n'),
                                 "--project", self.project.dir)
        self.assertEqual(code, 1)
        self.assertIn("boom", err)
        self.assertIn("Traceback", err)
        self.assertEqual((sch.read_bytes(), sch.stat().st_mtime_ns), (before, mtime))

    def test_run_no_change_no_check(self):
        before = {p: p.stat().st_mtime_ns for p in self.project.dir.iterdir()}
        with mock.patch.object(cli, "run", side_effect=AssertionError("kicad-cli called")):
            code, out, err = run_cli("kicad", "run", self.script('print("hi")\n'),
                                     "--project", self.project.dir)
        self.assertEqual(code, 0, err)
        self.assertIn("hi", out)
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.project.dir.iterdir()}, before)

    def test_locked_exit_6(self):
        lock_path(self.project.root_sch).write_text("x")
        code, out, err = run_cli("kicad", "run", self.script(PAPER_A3), "--project", self.project.dir)
        self.assertEqual(code, 6)
        self.assertIn("t.kicad_sch", err)
        self.assertIn("close", err)

    def test_locks_command(self):
        code, out, err = run_cli("kicad", "locks", self.project.dir)
        self.assertEqual(code, 0)
        self.assertIn("no files open in KiCad", out)
        lock_path(self.project.root_sch).write_text("x")
        code, out, err = run_cli("kicad", "locks", self.project.dir)
        self.assertIn("t.kicad_sch", out)

    def test_missing_cli_exit_2(self):
        with mock.patch.dict(os.environ, {"DEVTOOLS_KICAD_CLI": r"C:\nowhere\kicad-cli.exe"}):
            code, out, err = run_cli("kicad", "erc", self.project.dir)
        self.assertEqual(code, 2)
        self.assertIn("DEVTOOLS_KICAD_CLI", err)

    def test_no_project_exit_5(self):
        code, out, err = run_cli("kicad", "erc", self.d / "logs-not-here")
        self.assertEqual(code, 5)

    def test_exec_trailing_expression(self):
        code, out, err = run_cli("kicad", "exec", "1+1", "--project", self.project.dir)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "2")

    def test_output_capped(self):
        code, out, err = run_cli("kicad", "run", self.script("for i in range(250): print(i)\n"),
                                 "--project", self.project.dir)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[199], "199")
        self.assertIn("… 50 more lines in", lines[200])
        log = Path(lines[200].split(" in ", 1)[1])
        self.assertEqual(len(log.read_text(encoding="utf-8").splitlines()), 250)

    @needs_kicad
    def test_upgrade(self):
        demo = copy_demo("pic_programmer", self.d)
        code, out, err = run_cli("kicad", "upgrade", demo)
        self.assertEqual(code, 0, err)
        self.assertIn("pic_sockets.kicad_sch", out)
        p = Project(demo)
        for f in ("pic_programmer.kicad_sch", "pic_sockets.kicad_sch", "pic_programmer.kicad_pcb"):
            p.document(f)  # no FormatError

    @needs_kicad
    def test_strict(self):
        script = self.script(
            'from kt import Node\n'
            'root = project.document(project.pcb_path).root\n'
            'paper = root.get("paper")\n'
            'root.append(Node("paper", "A3")) if paper is None else paper.set(0, "A4" if paper[0] == "A3" else "A3")\n')
        code, out, err = run_cli("kicad", "run", script, "--project", self.project.dir)
        self.assertEqual(code, 0, err)
        self.assertIn("invalid_outline", out)
        code, out, err = run_cli("kicad", "run", script, "--project", self.project.dir, "--strict")
        self.assertEqual(code, 1)

    @needs_kicad
    def test_info_command(self):
        code, out, err = run_cli("kicad", "info", self.project.dir)
        self.assertEqual(code, 0, err)
        self.assertIn("in step: yes", out)
        self.assertIn("ERC: 0 errors", out)

    @needs_kicad
    def test_drc_command_reports_parity_apart(self):
        code, out, err = run_cli("kicad", "drc", self.project.dir)
        self.assertEqual(code, 0, err)
        self.assertIn("DRC: 1 errors", out)


if __name__ == "__main__":
    unittest.main()

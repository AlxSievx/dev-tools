import sys
import tempfile
import unittest
from pathlib import Path

import _paths  # noqa: F401
from devtools_bridge.executor import Session
from devtools_bridge.jobs import JobQueue


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        self.calls = 0

        def base():
            self.calls += 1
            return {"app": "APP", "design": f"design{self.calls}"}

        self.q = JobQueue()
        self.s = Session(base)

    def run_job(self, kind="script", **payload):
        job = self.q.submit(kind, payload)
        while self.s.step(self.q):
            pass
        return job

    def test_print_captured(self):
        job = self.run_job(script="print('hi', app)")
        self.assertEqual((job.state, job.read()[0]), ("done", "hi APP\n"))

    def test_namespace_persists(self):
        self.run_job(script="x = 41")
        job = self.run_job(script="print(x + 1)")
        self.assertEqual(job.read()[0], "42\n")

    def test_reset_clears(self):
        self.run_job(script="x = 1")
        self.assertEqual(self.run_job("reset").result, "reset")
        job = self.run_job(script="print(x)")
        self.assertEqual(job.state, "failed")
        self.assertIn("NameError", job.error)

    def test_run_called(self):
        job = self.run_job(script="def run(context):\n    print('run', context)\n")
        self.assertEqual(job.read()[0], "run None\n")

    def test_stale_run_not_called(self):
        self.run_job(script="def run(context):\n    print('stale')\n")
        job = self.run_job(script="print('fresh')")
        self.assertEqual(job.read()[0], "fresh\n")

    def test_path_job_sets_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "build.py"
            p.write_text("print(__file__)\nraise ValueError('x')\n", encoding="utf-8")
            job = self.run_job(path=str(p))
        self.assertEqual(job.read()[0].splitlines()[0], str(p))
        self.assertIn(f'File "{p}", line 2', job.error)

    def test_utf8_script_and_output(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "maß.py"
            p.write_text("# Größe\nprint('Maß')\n", encoding="utf-8")
            job = self.run_job(path=str(p))
        self.assertEqual((job.state, job.read()[0]), ("done", "Maß\n"))

    def test_exception_fails_job(self):
        job = self.run_job(script="print('before')\n1/0")
        self.assertEqual(job.state, "failed")
        self.assertIn("ZeroDivisionError", job.error)
        out = job.read()[0]
        self.assertTrue(out.startswith("before\n"))
        self.assertIn("ZeroDivisionError", out)

    def test_traceback_starts_at_the_script(self):
        job = self.run_job(script="def f():\n    1/0\nf()")
        self.assertNotIn("executor.py", job.error)
        self.assertIn('File "<exec>", line 3', job.error)
        self.assertIn('File "<exec>", line 2, in f', job.error)

    def test_exec_echoes_last_expression(self):
        self.assertEqual(self.run_job(script="x = 2\nx * 21").read()[0], "42\n")
        self.assertEqual(self.run_job(script="None").read()[0], "")
        self.assertEqual(self.run_job(script="'a'").read()[0], "'a'\n")

    def test_path_job_does_not_echo(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.py"
            p.write_text("1 + 1\n", encoding="utf-8")
            self.assertEqual(self.run_job(path=str(p)).read()[0], "")

    def test_base_exception_fails_job(self):
        for code in ("import sys\nsys.exit(3)", "raise KeyboardInterrupt"):
            job = self.run_job(script=code)
            self.assertEqual(job.state, "failed", code)
        self.assertEqual(self.run_job(script="print('alive')").read()[0], "alive\n")

    def test_generator_spans_steps(self):
        job = self.q.submit("script", {"script": "def run(c):\n    print(1)\n    yield\n    print(2)\n    yield\n    print(3)\n"})
        steps = [self.s.step(self.q), self.s.step(self.q), self.s.step(self.q)]
        self.assertEqual(steps, [True, True, False])
        self.assertEqual((job.state, job.read()[0]), ("done", "1\n2\n3\n"))

    def test_generator_error_fails_job(self):
        job = self.run_job(script="def run(c):\n    yield\n    raise RuntimeError('late')\n")
        self.assertEqual(job.state, "failed")
        self.assertIn("RuntimeError: late", job.error)

    def test_true_while_queued(self):
        self.q.submit("script", {"script": "pass"})
        self.q.submit("script", {"script": "pass"})
        self.assertEqual([self.s.step(self.q), self.s.step(self.q)], [True, False])

    def test_base_refreshed_each_job(self):
        self.run_job(script="design = 'overwritten'")
        job = self.run_job(script="print(design)")
        self.assertEqual(job.read()[0], "design2\n")

    def test_stdout_restored(self):
        out, err = sys.stdout, sys.stderr
        self.run_job(script="print('x')\n1/0")
        self.assertIs(sys.stdout, out)
        self.assertIs(sys.stderr, err)

    def test_shot_handler_result(self):
        def shot(ns, payload):
            print("shooting", payload["views"])
            yield
            return ["a.png"]

        self.s = Session(lambda: {}, handlers={"shot": shot})
        job = self.run_job("shot", views=["top"])
        self.assertEqual((job.state, job.result), ("done", ["a.png"]))
        self.assertEqual(job.read()[0], "shooting ['top']\n")

    def test_reload_dt(self):
        self.s = Session(lambda: {}, reload_dt=lambda: "DT")
        self.assertEqual(self.run_job(script="print(dt)").read()[0], "DT\n")


if __name__ == "__main__":
    unittest.main()

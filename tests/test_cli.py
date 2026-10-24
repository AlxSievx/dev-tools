import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fakes import TOKEN, FakeBridge
from devtools import client
from devtools.__main__ import main


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        secret = Path(self.tmp.name) / "secret"
        secret.write_text(TOKEN + "\n")
        self.env = mock.patch.dict(os.environ, {"DEVTOOLS_FUSION_SECRET_FILE": str(secret)})
        self.env.start()
        self.bridge = None

    def tearDown(self):
        if self.bridge:
            self.bridge.close()
        self.env.stop()
        self.tmp.cleanup()

    def serve(self, work=None):
        self.bridge = FakeBridge(work, log_dir=Path(self.tmp.name) / "logs")
        os.environ["DEVTOOLS_FUSION_PORT"] = str(self.bridge.port)
        return self.bridge

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_unreachable_exit_2(self):
        port = free_port()
        os.environ["DEVTOOLS_FUSION_PORT"] = str(port)
        code, out, err = self.cli("fusion", "health")
        self.assertEqual(code, 2)
        self.assertIn(str(port), err)
        self.assertIn("Add-Ins", err)
        self.assertIn("DevToolsBridge", err)

    def test_auth_exit_3(self):
        self.serve()
        Path(os.environ["DEVTOOLS_FUSION_SECRET_FILE"]).write_text("wrong")
        code, out, err = self.cli("fusion", "health")
        self.assertEqual(code, 3)
        self.assertIn("token", err)

    def test_health(self):
        self.serve()
        code, out, err = self.cli("fusion", "health")
        self.assertEqual(code, 0)
        self.assertIn("fake", out)

    def test_run_done_exit_0(self):
        def work(job):
            job.write(f"ran {job.payload['path']}\n")
            return None, None
        self.serve(work)
        script = Path(self.tmp.name) / "build.py"
        script.write_text("pass")
        code, out, err = self.cli("fusion", "run", str(script))
        self.assertEqual(code, 0)
        self.assertEqual(out, f"ran {script}\n")

    def test_run_path_absolute(self):
        seen = []
        self.serve(lambda job: (seen.append(job.payload["path"]), (None, None))[1])
        script = Path(self.tmp.name) / "rel.py"
        script.write_text("pass")
        cwd = os.getcwd()
        os.chdir(self.tmp.name)
        try:
            self.assertEqual(self.cli("fusion", "run", "rel.py")[0], 0)
        finally:
            os.chdir(cwd)
        self.assertEqual(seen, [str(script.resolve())])

    def test_run_missing_file_exit_5(self):
        self.serve()
        code, out, err = self.cli("fusion", "run", str(Path(self.tmp.name) / "nope.py"))
        self.assertEqual(code, 5)

    def test_run_failed_exit_1(self):
        def work(job):
            job.write("Traceback (most recent call last):\nZeroDivisionError\n")
            return None, "Traceback (most recent call last):\nZeroDivisionError\n"
        self.serve(work)
        code, out, err = self.cli("fusion", "exec", "1/0")
        self.assertEqual(code, 1)
        self.assertIn("ZeroDivisionError", out)

    def test_wait_cap_exit_4_and_reattach(self):
        gate = threading.Event()

        def work(job):
            job.write("start\n")
            gate.wait(5)
            job.write("end\n")
            return None, None
        self.serve(work)
        code, out, err = self.cli("fusion", "exec", "slow()", "--wait-cap", "0.3")
        self.assertEqual(code, 4)
        job_id = err.split("devtools fusion wait ")[1].split()[0]
        self.assertIn(f"still running: devtools fusion wait {job_id}", err)
        gate.set()
        code, out, err = self.cli("fusion", "wait", job_id)
        self.assertEqual((code, out), (0, "start\nend\n"))

    def test_detach_prints_id(self):
        b = self.serve()
        b.hold.clear()
        script = Path(self.tmp.name) / "x.py"
        script.write_text("pass")
        code, out, err = self.cli("fusion", "run", str(script), "--detach")
        self.assertEqual(code, 0)
        self.assertIsNotNone(b.queue.get(out.strip()))

    def test_cancel(self):
        b = self.serve()
        b.hold.clear()
        _, out, _ = self.cli("fusion", "exec", "x", "--detach")
        code, out2, err = self.cli("fusion", "cancel", out.strip())
        self.assertEqual(code, 0)
        self.assertEqual(b.queue.get(out.strip()).state, "cancelled")
        self.assertEqual(self.cli("fusion", "cancel", "ffffffff")[0], 2)

    def test_log(self):
        def work(job):
            job.write("one\ntwo\n")
            return None, None
        self.serve(work)
        _, out, _ = self.cli("fusion", "exec", "x", "--detach")
        time.sleep(0.3)
        code, log, err = self.cli("fusion", "log", out.strip())
        self.assertEqual((code, log), (0, "one\ntwo\n"))

    def test_follow_job_vanishes(self):
        def work(job):
            time.sleep(0.2)
            return None, None
        b = self.serve(work)
        _, out, _ = self.cli("fusion", "exec", "x", "--detach")
        job_id = out.strip()
        with b.queue._lock:   # the bridge restarted and forgot the job
            b.queue._jobs.clear()
        code, out, err = self.cli("fusion", "wait", job_id)
        self.assertEqual(code, 2)
        self.assertIn(f"job {job_id} is not known to the bridge (restarted?)", err)

    def test_output_capped(self):
        def work(job):
            for i in range(10000):
                job.write(f"line {i}\n")
            return None, None
        self.serve(work)
        code, out, err = self.cli("fusion", "exec", "spam()")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[:200], [f"line {i}" for i in range(200)])
        self.assertEqual(len(lines), 201)
        self.assertRegex(lines[200], r"^… 9800 more lines in .+\.log$")
        log = lines[200].split(" more lines in ")[1]
        self.assertEqual(len(Path(log).read_text(encoding="utf-8").splitlines()), 10000)

    def test_failure_traceback_shown_when_capped(self):
        def work(job):
            for i in range(300):
                job.write(f"line {i}\n")
            job.write("Traceback\nValueError: late\n")
            return None, "Traceback\nValueError: late\n"
        self.serve(work)
        code, out, err = self.cli("fusion", "exec", "x")
        self.assertEqual(code, 1)
        self.assertIn("ValueError: late", err)

    def test_shot_prints_paths(self):
        seen = []

        def work(job):
            seen.append(job.payload)
            return [str(Path(job.payload["dir"]) / f"{v}.png") for v in job.payload["views"]], None
        self.serve(work)
        out_dir = Path(self.tmp.name) / "shots"
        code, out, err = self.cli("fusion", "shot", "iso-top-right", "top", "--out", str(out_dir), "--size", "800x600")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [str(out_dir / "iso-top-right.png"), str(out_dir / "top.png")])
        self.assertEqual((seen[0]["width"], seen[0]["height"]), (800, 600))

    def test_info_and_reset(self):
        seen = []
        self.serve(lambda job: (seen.append((job.kind, job.payload)), (None, None))[1])
        self.assertEqual(self.cli("fusion", "info")[0], 0)
        self.assertEqual(self.cli("fusion", "reset")[0], 0)
        self.assertEqual(seen, [("script", {"script": "print(dt.info())"}), ("reset", {})])

    def test_port_precedence(self):
        cfg = Path(self.tmp.name) / "config.json"
        with mock.patch.object(client, "CONFIG", cfg):
            os.environ.pop("DEVTOOLS_FUSION_PORT", None)
            self.assertEqual(client.load_port(), 7655)
            cfg.write_text(json.dumps({"port": 7700}))
            self.assertEqual(client.load_port(), 7700)
            os.environ["DEVTOOLS_FUSION_PORT"] = "7800"
            self.assertEqual(client.load_port(), 7800)


if __name__ == "__main__":
    unittest.main()

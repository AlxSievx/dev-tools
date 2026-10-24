import tempfile
import threading
import unittest
from pathlib import Path

import _paths  # noqa: F401
from devtools_bridge.jobs import JobQueue


class JobQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.q = JobQueue(Path(self.tmp.name))

    def tearDown(self):
        for job in list(self.q._jobs.values()):
            job.close_log()
        self.tmp.cleanup()

    def test_fifo_order(self):
        a = self.q.submit("script", {"script": "a"})
        b = self.q.submit("script", {"script": "b"})
        self.assertIs(self.q.next(), a)
        self.assertEqual(a.state, "running")
        self.q.finish(a)
        self.assertIs(self.q.next(), b)
        self.assertIsNone(self.q.next())

    def test_read_since_offset(self):
        job = self.q.submit("script", {})
        job.write("a\n")
        job.write("b\n")
        self.assertEqual(job.read(0), ("a\nb\n", 4))
        self.assertEqual(job.read(2), ("b\n", 4))
        self.assertEqual(job.read(4), ("", 4))

    def test_cancel_only_queued(self):
        a = self.q.submit("script", {})
        b = self.q.submit("script", {})
        self.q.next()
        self.assertFalse(self.q.cancel(a.id))
        self.assertTrue(self.q.cancel(b.id))
        self.assertEqual(b.state, "cancelled")
        self.assertFalse(self.q.cancel("nope"))
        self.q.finish(a)
        self.assertIsNone(self.q.next())

    def test_finish_states(self):
        a = self.q.submit("script", {})
        b = self.q.submit("script", {})
        self.q.next()
        self.q.finish(a, result=42)
        self.q.next()
        self.q.finish(b, error="Traceback …")
        self.assertEqual((a.state, a.result, a.error), ("done", 42, None))
        self.assertEqual((b.state, b.error), ("failed", "Traceback …"))

    def test_to_dict(self):
        job = self.q.submit("shot", {"views": ["top"]}, label="look")
        job.write("hello\n")
        d = job.to_dict(2)
        self.assertEqual(set(d), {"id", "kind", "label", "state", "output", "offset",
                                  "result", "error", "log"})
        self.assertEqual((d["kind"], d["label"], d["output"], d["offset"]),
                         ("shot", "look", "llo\n", 6))
        self.assertRegex(job.id, r"^[0-9a-f]{8}$")

    def test_log_file_written(self):
        job = self.q.submit("script", {})
        job.write("Maß ä\n")
        self.q.next()
        self.q.finish(job)
        self.assertEqual(Path(job.log_path).read_text(encoding="utf-8"), "Maß ä\n")

    def test_concurrent_writes(self):
        job = self.q.submit("script", {})

        def spam():
            for _ in range(1000):
                job.write("x\n")

        threads = [threading.Thread(target=spam) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(job.read(0)[0].count("\n"), 4000)

    def test_keep_limit(self):
        q = JobQueue(None, keep=3)
        jobs = [q.submit("script", {}) for _ in range(5)]
        for j in jobs:
            q.next()
            q.finish(j)
        self.assertIsNone(q.get(jobs[0].id))
        self.assertIsNone(q.get(jobs[1].id))
        self.assertIs(q.get(jobs[4].id), jobs[4])

    def test_snapshot(self):
        a = self.q.submit("script", {})
        self.q.submit("script", {})
        self.assertEqual(self.q.snapshot(), {"queued": 2, "running": None})
        self.q.next()
        self.assertEqual(self.q.snapshot(), {"queued": 1, "running": a.id})


if __name__ == "__main__":
    unittest.main()

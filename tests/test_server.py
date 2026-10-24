import json
import time
import unittest
import urllib.error
import urllib.request

from fakes import TOKEN, FakeBridge


def call(port, method, path, body=None, token=TOKEN, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        with e:
            return e.code, json.loads(e.read())


def wait_state(port, job_id, states=("done", "failed")):
    for _ in range(100):
        code, d = call(port, "GET", f"/jobs/{job_id}")
        if d.get("state") in states:
            return d
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} stuck: {d}")


class ServerTest(unittest.TestCase):
    def setUp(self):
        def work(job):
            job.write("ran " + (job.payload.get("script") or job.payload.get("path") or job.kind) + "\n")
            if job.payload.get("script") == "boom":
                return None, "Traceback: boom"
            return "ok", None

        self.b = FakeBridge(work)
        self.port = self.b.port

    def tearDown(self):
        self.b.close()

    def test_auth_required_everywhere(self):
        for method, path, body in [("GET", "/health", None), ("POST", "/jobs", {"script": "x"}),
                                   ("GET", "/jobs/abc", None), ("DELETE", "/jobs/abc", None),
                                   ("POST", "/screenshot", {"views": ["top"]}),
                                   ("POST", "/reset", {})]:
            for token in (None, "wrong"):
                code, d = call(self.port, method, path, body, token=token)
                self.assertEqual(code, 401, (method, path, token))
                self.assertIn("error", d)

    def test_submit_and_poll(self):
        code, d = call(self.port, "POST", "/jobs", {"script": "print(1)", "label": "one"})
        self.assertEqual(code, 202)
        done = wait_state(self.port, d["id"])
        self.assertEqual((done["state"], done["result"], done["label"]), ("done", "ok", "one"))
        self.assertEqual(done["output"], "ran print(1)\n")
        code, tail = call(self.port, "GET", f"/jobs/{d['id']}?since=4")
        self.assertEqual(tail["output"], "print(1)\n")

    def test_failed_job(self):
        code, d = call(self.port, "POST", "/jobs", {"script": "boom"})
        done = wait_state(self.port, d["id"])
        self.assertEqual((done["state"], done["error"]), ("failed", "Traceback: boom"))

    def test_submit_validation(self):
        for body in ({}, {"script": "x", "path": "y"}, {"script": 3}):
            code, d = call(self.port, "POST", "/jobs", body)
            self.assertEqual(code, 400, body)
        code, d = call(self.port, "POST", "/jobs", raw=b"{nope")
        self.assertEqual(code, 400)

    def test_unknown(self):
        self.assertEqual(call(self.port, "GET", "/jobs/ffffffff")[0], 404)
        self.assertEqual(call(self.port, "DELETE", "/jobs/ffffffff")[0], 404)
        self.assertEqual(call(self.port, "GET", "/nope")[0], 404)

    def test_cancel_codes(self):
        self.b.hold.clear()
        _, a = call(self.port, "POST", "/jobs", {"script": "a"})
        _, b = call(self.port, "POST", "/jobs", {"script": "b"})
        code, d = call(self.port, "DELETE", f"/jobs/{b['id']}")
        self.assertEqual((code, d), (200, {"cancelled": True}))
        self.b.queue.next()  # a is now running
        code, d = call(self.port, "DELETE", f"/jobs/{a['id']}")
        self.assertEqual(code, 409)

    def test_health_fields(self):
        self.b.hold.clear()
        call(self.port, "POST", "/jobs", {"script": "a"})
        code, d = call(self.port, "GET", "/health")
        self.assertEqual(code, 200)
        self.assertEqual(d, {"ok": True, "fusion": "fake", "document": None,
                             "queued": 1, "running": None})

    def test_notify_called_on_submit(self):
        before = self.b.notified
        call(self.port, "POST", "/jobs", {"script": "a"})
        call(self.port, "POST", "/reset", {})
        self.assertEqual(self.b.notified, before + 2)

    def test_screenshot_and_reset_kinds(self):
        self.b.hold.clear()
        _, s = call(self.port, "POST", "/screenshot", {"views": ["top", "front"], "dir": "C:/x"})
        _, r = call(self.port, "POST", "/reset", {})
        shot = self.b.queue.get(s["id"])
        self.assertEqual(shot.kind, "shot")
        self.assertEqual(shot.payload, {"views": ["top", "front"], "width": 1600,
                                        "height": 1000, "dir": "C:/x"})
        self.assertEqual(self.b.queue.get(r["id"]).kind, "reset")
        code, d = call(self.port, "POST", "/screenshot", {"views": []})
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()


class PortTest(unittest.TestCase):
    def test_second_bridge_on_same_port_fails(self):
        """Windows lets SO_REUSEADDR sockets share a port; two bridges would split the jobs."""
        from devtools_bridge.jobs import JobQueue
        from devtools_bridge.server import make_server
        first = make_server(JobQueue(), TOKEN, lambda: None, dict, port=0)
        try:
            port = first.server_address[1]
            with self.assertRaises(OSError):
                second = make_server(JobQueue(), TOKEN, lambda: None, dict, port=port)
                second.server_close()
        finally:
            first.server_close()

"""A bridge without Fusion: the real queue and server, a thread as main-thread pump."""
import threading

import _paths  # noqa: F401
from devtools_bridge.jobs import JobQueue
from devtools_bridge.server import make_server

TOKEN = "test-token"


class FakeBridge:
    """Serves on a free port; `work(job)` stands in for Fusion running a job.

    `work` returns (result, error) and may write output into the job.
    """

    def __init__(self, work=None, log_dir=None, status=None):
        self.queue = JobQueue(log_dir)
        self.work = work or (lambda job: (None, None))
        self.wake = threading.Event()
        self.hold = threading.Event()   # cleared → jobs wait before running
        self.hold.set()
        self.notified = 0
        self.server = make_server(self.queue, TOKEN, self._notify,
                                  status or (lambda: {"fusion": "fake", "document": None}),
                                  port=0)
        self.port = self.server.server_address[1]
        self._stop = False
        self._threads = [threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True),
                         threading.Thread(target=self._pump, daemon=True)]
        for t in self._threads:
            t.start()

    def _notify(self):
        self.notified += 1
        self.wake.set()

    def _pump(self):
        while not self._stop:
            self.wake.wait(0.05)
            self.wake.clear()
            while not self._stop and self.hold.wait(0.05):
                job = self.queue.next()
                if not job:
                    break
                result, error = self.work(job)
                self.queue.finish(job, result=result, error=error)

    def close(self):
        self._stop = True
        self.server.shutdown()
        self.server.server_close()

"""Job queue shared by the HTTP thread and Fusion's main thread. No adsk here."""
import collections
import secrets
import threading
import time
from pathlib import Path


class Job:
    def __init__(self, kind: str, payload: dict, label: str, log_path: Path | None):
        self.id = secrets.token_hex(4)
        self.kind = kind
        self.payload = payload
        self.label = label
        self.state = "queued"
        self.result = None
        self.error = None
        self.log_path = str(log_path) if log_path else None
        self.created = time.time()
        self._chunks: list[str] = []
        self._size = 0
        self._log = None
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._chunks.append(text)
            self._size += len(text)
            if self.log_path:
                if self._log is None:
                    Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
                    self._log = open(self.log_path, "a", encoding="utf-8")
                self._log.write(text)

    def read(self, since: int = 0) -> tuple[str, int]:
        with self._lock:
            text = "".join(self._chunks)
            self._chunks = [text] if text else []
            return text[max(0, since):], self._size

    def close_log(self) -> None:
        with self._lock:
            if self._log:
                self._log.close()
                self._log = None

    def to_dict(self, since: int = 0) -> dict:
        output, offset = self.read(since)
        return {"id": self.id, "kind": self.kind, "label": self.label, "state": self.state,
                "output": output, "offset": offset, "result": self.result,
                "error": self.error, "log": self.log_path}


class JobQueue:
    def __init__(self, log_dir: Path | None = None, keep: int = 50):
        self.log_dir = Path(log_dir) if log_dir else None
        self.keep = keep
        self._jobs: dict[str, Job] = {}
        self._queue: collections.deque[Job] = collections.deque()
        self._finished: collections.deque[Job] = collections.deque()
        self._running: Job | None = None
        self._lock = threading.Lock()

    def submit(self, kind: str, payload: dict, label: str = "") -> Job:
        with self._lock:
            job = Job(kind, payload, label, None)
            if self.log_dir:
                job.log_path = str(self.log_dir / f"{job.id}.log")
            self._jobs[job.id] = job
            self._queue.append(job)
            return job

    def next(self) -> Job | None:
        with self._lock:
            while self._queue:
                job = self._queue.popleft()
                if job.state == "queued":
                    job.state = "running"
                    self._running = job
                    return job
            return None

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state != "queued":
                return False
            job.state = "cancelled"
            self._retire(job)
        job.close_log()
        return True

    def finish(self, job: Job, result=None, error: str | None = None) -> None:
        with self._lock:
            job.result = result
            job.error = error
            job.state = "failed" if error else "done"
            if self._running is job:
                self._running = None
            self._retire(job)
        job.close_log()

    def snapshot(self) -> dict:
        with self._lock:
            queued = sum(1 for j in self._queue if j.state == "queued")
            return {"queued": queued, "running": self._running.id if self._running else None}

    def _retire(self, job: Job) -> None:
        self._finished.append(job)
        while len(self._finished) > self.keep:
            old = self._finished.popleft()
            self._jobs.pop(old.id, None)

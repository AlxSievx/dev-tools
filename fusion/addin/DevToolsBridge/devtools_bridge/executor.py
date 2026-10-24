"""Runs jobs on Fusion's main thread, one unit of work per call. No adsk here.

A job whose `run()` (or handler) returns a generator is advanced one `next()`
per `step()`; between steps control goes back to Fusion, which repaints and
handles events. The shell re-fires its custom event while `step()` returns True.
"""
import ast
import inspect
import sys
import traceback
from pathlib import Path
from typing import Callable

from .jobs import Job, JobQueue


class _JobWriter:
    encoding = "utf-8"

    def __init__(self, job: Job):
        self.job = job

    def write(self, text: str) -> int:
        self.job.write(text)
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _user_traceback(e: BaseException) -> str:
    """The traceback without this module's own frames on top."""
    tb = e.__traceback__
    while tb and tb.tb_frame.f_code.co_filename == __file__:
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(e), e, tb))


def _echo_last(source: str):
    """Compile a one-off script; a trailing expression is printed like in a REPL."""
    tree = ast.parse(source, "<exec>")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        last = tree.body[-1]
        echo = ast.parse("if (__echo__ := None) is not None: print(repr(__echo__))").body[0]
        echo.test.left.value = last.value
        tree.body[-1] = ast.copy_location(echo, last)
        tree.body.append(ast.copy_location(ast.parse("del __echo__").body[0], last))
        ast.fix_missing_locations(tree)
    return compile(tree, "<exec>", "exec")


class Session:
    def __init__(self, base: Callable[[], dict],
                 handlers: dict[str, Callable[[dict, dict], object]] | None = None,
                 reload_dt: Callable[[], object | None] = lambda: None):
        self.base = base
        self.handlers = handlers or {}
        self.reload_dt = reload_dt
        self.ns: dict = {}
        self._pending: tuple[Job, object] | None = None

    def step(self, queue: JobQueue) -> bool:
        if self._pending:
            job, gen = self._pending
            self._guarded(queue, job, lambda: self._advance(queue, job, gen))
        else:
            job = queue.next()
            if job:
                self._guarded(queue, job, lambda: self._start(queue, job))
        return self._pending is not None or queue.snapshot()["queued"] > 0

    def _guarded(self, queue: JobQueue, job: Job, work: Callable[[], None]) -> None:
        out, err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _JobWriter(job)
        try:
            work()
        except BaseException as e:
            self._pending = None
            tb = _user_traceback(e)
            job.write(tb)
            queue.finish(job, error=tb)
        finally:
            sys.stdout, sys.stderr = out, err

    def _start(self, queue: JobQueue, job: Job) -> None:
        if job.kind == "reset":
            self.ns.clear()
            queue.finish(job, result="reset")
            return
        self.ns.update(self.base())
        dt = self.reload_dt()
        if dt is not None:
            self.ns["dt"] = dt
        if job.kind == "shot":
            return self._follow(queue, job, self.handlers["shot"](self.ns, job.payload))
        if job.kind != "script":
            raise ValueError(f"unknown job kind {job.kind!r}")

        if "path" in job.payload:
            path = job.payload["path"]
            code = compile(Path(path).read_text(encoding="utf-8"), path, "exec")
            self.ns["__file__"] = path
        else:
            code = _echo_last(job.payload["script"])
            self.ns.pop("__file__", None)
        self.ns.pop("run", None)
        exec(code, self.ns)
        run = self.ns.get("run")
        self._follow(queue, job, run(None) if callable(run) else None)

    def _follow(self, queue: JobQueue, job: Job, value) -> None:
        if inspect.isgenerator(value):
            self._advance(queue, job, value)
        else:
            queue.finish(job, result=value if job.kind == "shot" else None)

    def _advance(self, queue: JobQueue, job: Job, gen) -> None:
        try:
            next(gen)
            self._pending = (job, gen)
        except StopIteration as stop:
            self._pending = None
            queue.finish(job, result=stop.value)

"""DevToolsBridge — Fusion add-in that runs jobs sent by the `devtools` CLI.

HTTP thread (devtools_bridge.server) queues a job and fires a custom event;
the event handler runs on Fusion's main thread and calls Session.step(), which
does one unit of work. While more is pending it fires the event again, so
Fusion handles its own events in between.

Grew out of ndoo/fusion360-mcp-bridge (MIT).
"""
import importlib
import json
import logging
import secrets
import sys
import tempfile
import threading
import traceback
from pathlib import Path

import adsk.core
import adsk.fusion

HERE = Path(__file__).resolve().parent
FUSION_DIR = HERE.parents[1]           # repo/fusion, holds the dt package
for p in (HERE, FUSION_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from devtools_bridge.executor import Session  # noqa: E402
from devtools_bridge.jobs import JobQueue  # noqa: E402
from devtools_bridge.server import make_server  # noqa: E402

# per instance: a second copy of the add-in that stops must not unregister ours
EVENT_ID = f"DevToolsBridgeEvent-{secrets.token_hex(4)}"
DEFAULT_PORT = 7655
SECRET_FILE = Path.home() / ".fusion-mcp-secret"
WORK_DIR = Path(tempfile.gettempdir()) / "devtools" / "fusion"

log = logging.getLogger("DevToolsBridge")

_app: adsk.core.Application | None = None
_server = None
_queue: JobQueue | None = None
_session: Session | None = None
_handlers: list = []
_event = None
_document: str | None = None
_busy = False
_dt_stamp = None


def _port() -> int:
    try:
        return int(json.loads((HERE / "config.json").read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError):
        return DEFAULT_PORT


def _token() -> str:
    try:
        token = SECRET_FILE.read_text().strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_hex(32)
    SECRET_FILE.write_text(token)
    log.info("created %s", SECRET_FILE)
    return token


def _base() -> dict:
    design = None
    doc = _app.activeDocument
    if doc:
        design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    return {"adsk": adsk, "app": _app, "ui": _app.userInterface, "design": design,
            "root": design.rootComponent if design else None}


def _reload_dt():
    """Import dt, again whenever a file under fusion/dt/ changed since the last job."""
    global _dt_stamp
    files = sorted((FUSION_DIR / "dt").rglob("*.py"))
    stamp = tuple((str(f), f.stat().st_mtime_ns) for f in files)
    if stamp != _dt_stamp or "dt" not in sys.modules:
        for name in [m for m in sys.modules if m == "dt" or m.startswith("dt.")]:
            del sys.modules[name]
        importlib.invalidate_caches()
        importlib.import_module("dt")
        _dt_stamp = stamp
    return sys.modules["dt"]


def _shot(ns: dict, payload: dict):
    return ns["dt"].shot(payload["views"], payload["width"], payload["height"], payload["dir"])


def _status() -> dict:
    return {"fusion": _app.version if _app else None, "document": _document}


def _notify() -> None:
    _app.fireCustomEvent(EVENT_ID, "")


class _StepHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        global _busy
        if _busy:            # a job called adsk.doEvents(); the outer step goes on
            return
        _busy = True
        try:
            more = _session.step(_queue)
        except Exception:
            log.exception("step failed")
            more = False
        finally:
            _busy = False
        if more:
            _notify()


class _DocHandler(adsk.core.DocumentEventHandler):
    def __init__(self, active: bool):
        super().__init__()
        self.active = active

    def notify(self, args):
        global _document
        try:
            _document = args.document.name if self.active else None
        except Exception:
            _document = None


def run(context):
    global _app, _server, _queue, _session, _event, _document
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    if not log.handlers:
        h = logging.FileHandler(WORK_DIR / "bridge.log", encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    _app = adsk.core.Application.get()
    try:
        _queue = JobQueue(WORK_DIR)
        _session = Session(_base, handlers={"shot": _shot}, reload_dt=_reload_dt)
        port = _port()
        # bind first: if another copy holds the port, fail before touching Fusion
        _server = make_server(_queue, _token(), _notify, _status, port=port)
        _event = _app.registerCustomEvent(EVENT_ID)
        step = _StepHandler()
        _event.add(step)
        _handlers.append(step)
        for active, ev in ((True, _app.documentActivated), (False, _app.documentDeactivated)):
            h = _DocHandler(active)
            ev.add(h)
            _handlers.append((ev, h))
        _document = _app.activeDocument.name if _app.activeDocument else None
        threading.Thread(target=_server.serve_forever, daemon=True).start()
        log.info("DevToolsBridge running on 127.0.0.1:%d", port)
    except Exception:
        log.exception("start failed")
        _app.userInterface.messageBox(f"DevToolsBridge failed to start:\n{traceback.format_exc()}",
                                      "DevToolsBridge")


def stop(context):
    global _server, _event
    try:
        if _server:
            _server.shutdown()
            _server.server_close()
            _server = None
        for item in _handlers:
            if isinstance(item, tuple):
                item[0].remove(item[1])
            elif _event:
                _event.remove(item)
        _handlers.clear()
        if _app:
            _app.unregisterCustomEvent(EVENT_ID)
        _event = None
        log.info("DevToolsBridge stopped")
    except Exception:
        log.exception("stop failed")

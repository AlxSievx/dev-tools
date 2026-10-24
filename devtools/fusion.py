"""`devtools fusion …` — talk to the DevToolsBridge add-in inside Fusion.

Script output goes to stdout, messages to stderr. Exit codes: 0 done,
1 script failed, 2 bridge unreachable / job unknown, 3 auth, 4 still running,
5 usage.
"""
import sys
import tempfile
import time
from pathlib import Path

from devtools.client import BridgeError, Client

DONE, FAILED, UNKNOWN, STILL_RUNNING, USAGE = 0, 1, 2, 4, 5
WAIT_CAP = 540.0
MAX_LINES = 200


def say(msg: str) -> None:
    print(msg, file=sys.stderr)


def follow(client: Client, job_id: str, wait_cap: float = WAIT_CAP, max_lines: int = MAX_LINES,
           out=None, poll: float = 0.5) -> int:
    """Stream a job's output until it ends or `wait_cap` seconds pass."""
    out = out or sys.stdout
    since, shown, hidden = 0, 0, 0
    delay = 0.05                       # grows to `poll`: short jobs return fast
    deadline = time.monotonic() + wait_cap
    while True:
        code, job = client.request("GET", f"/jobs/{job_id}?since={since}")
        if code == 404:
            say(f"job {job_id} is not known to the bridge (restarted?)")
            return UNKNOWN
        since = job["offset"]
        for piece in job["output"].splitlines(keepends=True):
            if shown < max_lines:
                out.write(piece)
                shown += piece.endswith("\n")
            else:
                hidden += piece.endswith("\n")
        out.flush()
        if job["state"] in ("done", "failed", "cancelled"):
            break
        if time.monotonic() >= deadline:
            say(f"still running: devtools fusion wait {job_id}")
            return STILL_RUNNING
        time.sleep(delay)
        delay = min(poll, delay * 1.5)

    if hidden:
        print(f"… {hidden} more lines in {job['log']}", file=out)
    if job["state"] == "failed":
        if hidden:
            say(job["error"].rstrip())
        return FAILED
    if job["state"] == "cancelled":
        say(f"job {job_id} was cancelled")
        return FAILED
    if job["kind"] == "shot":
        for path in job["result"] or []:
            print(path, file=out)
    return DONE


def _submit(args, path: str, body: dict) -> int:
    client = Client()
    code, reply = client.request("POST", path, body)
    if code != 202:
        say(f"bridge refused the job: {reply.get('error', reply)}")
        return USAGE
    if getattr(args, "detach", False):
        print(reply["id"])
        return DONE
    return follow(client, reply["id"], wait_cap=getattr(args, "wait_cap", WAIT_CAP))


def cmd_health(args) -> int:
    _, h = Client().request("GET", "/health")
    print(f"fusion {h.get('fusion')} · document {h.get('document') or '-'} · "
          f"queued {h.get('queued')} · running {h.get('running') or '-'}")
    return DONE


def cmd_run(args) -> int:
    path = Path(args.file).resolve()
    if not path.is_file():
        say(f"no such script: {path}")
        return USAGE
    return _submit(args, "/jobs", {"path": str(path), "label": args.label or path.name})


def cmd_exec(args) -> int:
    return _submit(args, "/jobs", {"script": args.code})


def cmd_info(args) -> int:
    return _submit(args, "/jobs", {"script": "print(dt.info())"})


def cmd_reset(args) -> int:
    return _submit(args, "/reset", {})


def cmd_shot(args) -> int:
    try:
        width, height = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        say(f"--size wants WxH, e.g. 1600x1000, not {args.size!r}")
        return USAGE
    out_dir = Path(args.out).resolve() if args.out else (
        Path(tempfile.gettempdir()) / "devtools" / "fusion" / "shots" / time.strftime("%Y%m%d-%H%M%S"))
    return _submit(args, "/screenshot", {"views": args.views, "width": width,
                                         "height": height, "dir": str(out_dir)})


def cmd_wait(args) -> int:
    return follow(Client(), args.id, wait_cap=args.wait_cap)


def cmd_log(args) -> int:
    code, job = Client().request("GET", f"/jobs/{args.id}")
    if code == 404:
        say(f"job {args.id} is not known to the bridge (restarted?)")
        return UNKNOWN
    sys.stdout.write(job["output"])
    say(f"job {args.id}: {job['state']} · full log {job['log']}")
    return DONE


def cmd_cancel(args) -> int:
    code, reply = Client().request("DELETE", f"/jobs/{args.id}")
    if code == 200:
        say(f"job {args.id} cancelled")
        return DONE
    say(reply.get("error", str(reply)))
    return UNKNOWN if code == 404 else FAILED


def cmd_selftest(args) -> int:
    from devtools.selftest import selftest
    return selftest(Client(), quick=args.quick)


def cmd_install(args) -> int:
    from devtools.install import install
    return install()


def _guard(func):
    def run(args) -> int:
        try:
            return func(args)
        except BridgeError as e:
            say(str(e))
            return e.exit_code
    return run


def add_parser(tools) -> None:
    p = tools.add_parser("fusion", help="drive Autodesk Fusion through DevToolsBridge")
    cmds = p.add_subparsers(dest="cmd", metavar="COMMAND")

    def add(name, func, help):
        c = cmds.add_parser(name, help=help)
        c.set_defaults(func=_guard(func))
        return c

    def waiting(c):
        c.add_argument("--detach", action="store_true", help="print the job id and return at once")
        c.add_argument("--wait-cap", type=float, default=WAIT_CAP, metavar="S",
                       help=f"stop waiting after S seconds, exit 4 (default {WAIT_CAP:.0f})")

    add("health", cmd_health, "is the bridge up, which document is active")
    add("info", cmd_info, "the active design in numbers: bodies, parameters, timeline health")
    c = add("run", cmd_run, "run a script file inside Fusion")
    c.add_argument("file")
    c.add_argument("--label")
    waiting(c)
    c = add("exec", cmd_exec, "run a line of Python inside Fusion")
    c.add_argument("code")
    waiting(c)
    c = add("shot", cmd_shot, "write screenshots of named views as PNG files")
    c.add_argument("views", nargs="+", metavar="VIEW",
                   help="front back left right top bottom iso-top-right iso-top-left "
                        "iso-bottom-right iso-bottom-left current")
    c.add_argument("--out", help="directory for the PNGs (default: a new folder under %%TEMP%%)")
    c.add_argument("--size", default="1600x1000", help="WxH in pixels (default 1600x1000)")
    waiting(c)
    c = add("wait", cmd_wait, "follow a running job again")
    c.add_argument("id")
    c.add_argument("--wait-cap", type=float, default=WAIT_CAP, metavar="S")
    c = add("log", cmd_log, "print a job's output so far")
    c.add_argument("id")
    c = add("cancel", cmd_cancel, "drop a job that is still queued")
    c.add_argument("id")
    add("reset", cmd_reset, "clear the names kept between jobs")
    c = add("selftest", cmd_selftest, "live checks in throwaway documents (Fusion open)")
    c.add_argument("--quick", action="store_true", help="skip the 45 s job")
    add("install", cmd_install, "link the add-in from this repo into Fusion's AddIns folder")

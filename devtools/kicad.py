"""`devtools kicad …` — change KiCad projects through `kt` scripts, check them with kicad-cli.

Script output and results go to stdout, messages to stderr. Exit codes:
0 done · 1 script failed, rejected by kicad-cli, or findings with --strict ·
2 kicad-cli missing or failed · 5 usage · 6 a file to change is open in KiCad.
"""
import ast
import os
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

KICAD_DIR = Path(__file__).resolve().parents[1] / "kicad"
if str(KICAD_DIR) not in sys.path:
    sys.path.insert(0, str(KICAD_DIR))

import kt  # noqa: E402
from kt import cli  # noqa: E402
from kt.project import VERSIONS, FormatError, LockedError, RejectedError, WriteError, locked  # noqa: E402

DONE, FAILED, NO_CLI, USAGE, LOCKED = 0, 1, 2, 5, 6
MAX_LINES = 200


class Usage(Exception):
    pass


def say(msg: str) -> None:
    print(msg, file=sys.stderr)


def log_dir() -> Path:
    return Path(os.environ.get("DEVTOOLS_KICAD_LOGS") or Path(tempfile.gettempdir()) / "devtools" / "kicad")


class CappedOut:
    """Stdout for a script: everything to the log file, the first MAX_LINES to the console."""

    def __init__(self, console, label: str):
        safe = re.sub(r"[^\w.-]+", "_", label)[:40]
        self.path = log_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}.log"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(self.path, "w", encoding="utf-8")
        self.console = console
        self.shown = self.hidden = 0
        self._partial = ""

    def write(self, s: str) -> int:
        self.log.write(s)
        for piece in (self._partial + s).splitlines(keepends=True):
            if not piece.endswith("\n"):
                self._partial = piece
                break
            self._partial = ""
            if self.shown < MAX_LINES:
                self.console.write(piece)
                self.shown += 1
            else:
                self.hidden += 1
        return len(s)

    def flush(self):
        self.log.flush()
        self.console.flush()

    def close(self):
        if self._partial:
            self.write("\n")
        self.log.close()
        if self.hidden:
            self.console.write(f"… {self.hidden} more lines in {self.path}\n")


def open_project(where) -> "kt.Project":
    try:
        return kt.Project(where or ".")
    except FileNotFoundError as e:
        raise Usage(str(e)) from None


def execute(source: str, filename: str, project, echo_last: bool) -> int:
    """Run a script with `kt` and `project` preloaded. Returns FAILED if it raised."""
    ns = {"__name__": "__main__", "__file__": filename, "kt": kt, "project": project}
    out = CappedOut(sys.stdout, Path(filename).stem if filename != "<exec>" else "exec")
    real = sys.stdout
    sys.stdout = out
    try:
        tree = ast.parse(source, filename)
        last = None
        if echo_last and tree.body and isinstance(tree.body[-1], ast.Expr):
            last = compile(ast.Expression(tree.body.pop().value), filename, "eval")
        exec(compile(tree, filename, "exec"), ns)
        if last is not None:
            value = eval(last, ns)
            if value is not None:
                print(repr(value))
    except SystemExit as e:
        sys.stdout = real
        out.close()
        if e.code in (None, 0):
            return DONE
        say(f"script called sys.exit({e.code!r}): nothing written")
        return FAILED
    except Exception:
        sys.stdout = real
        out.close()
        say(traceback.format_exc().rstrip())
        say("script failed: nothing written")
        return FAILED
    finally:
        sys.stdout = real
    out.close()
    return DONE


def save(project, strict: bool) -> int:
    result = project.save()
    for path in result.written:
        print(f"written {path}")
    errors = 0
    for path, report in result.reports.items():
        print(report.summary())
        errors += len(report.errors)
    if strict and errors:
        say(f"--strict: {errors} errors")
        return FAILED
    return DONE


def cmd_run(args) -> int:
    path = Path(args.file).resolve()
    if not path.is_file():
        raise Usage(f"no such script: {path}")
    project = open_project(args.project)
    code = execute(path.read_text(encoding="utf-8"), str(path), project, echo_last=False)
    return code if code else save(project, args.strict)


def cmd_exec(args) -> int:
    project = open_project(args.project)
    code = execute(args.code, "<exec>", project, echo_last=True)
    return code if code else save(project, args.strict)


def cmd_erc(args) -> int:
    project = open_project(args.project)
    report = cli.erc(project.root_sch)
    print(report.summary())
    return FAILED if args.strict and report.errors else DONE


def cmd_drc(args) -> int:
    project = open_project(args.project)
    report = cli.drc(project.pcb_path, parity=project.root_sch.exists())
    print(report.summary())
    return FAILED if args.strict and report.errors else DONE


def cmd_info(args) -> int:
    from kt.info import info
    print(info(open_project(args.project)))
    return DONE


def cmd_shot(args) -> int:
    from kt import views
    try:
        width, height = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        raise Usage(f"--size wants WxH, e.g. 1600x1000, not {args.size!r}") from None
    out = Path(args.out).resolve() if args.out else log_dir() / "shots" / time.strftime("%Y%m%d-%H%M%S")
    project = open_project(args.project)
    what = args.what
    if args.kind == "sch":
        paths = [views.sch_pdf(project, out, pages=args.pages or "")]
    elif args.kind == "pcb":
        bad = [v for v in what if v not in views.VIEWS]
        if not what or bad:
            raise Usage(f"views: {', '.join(views.VIEWS)}" + (f" (not {', '.join(bad)})" if bad else ""))
        paths = [views.pcb_png(project, v, out, (width, height)) for v in what]
    elif args.kind == "pcb-layers":
        if len(what) != 1:
            raise Usage("shot pcb-layers F.Cu,F.Silkscreen,Edge.Cuts")
        paths = [views.pcb_layers_pdf(project, what[0], out)]
    else:
        if not what:
            raise Usage(f"shot {args.kind} LIB:NAME")
        make = views.footprint_pdf if args.kind == "fp" else views.symbol_pdf
        try:
            paths = [make(name, out, project.libs) for name in what]
        except KeyError as e:
            raise Usage(str(e).strip('"')) from None
    for path in paths:
        print(path)
    return DONE


def project_files(project) -> list[Path]:
    skip = (".git", ".history")
    return sorted(p for p in project.dir.rglob("*")
                  if p.is_file() and not any(part in skip or part.endswith("-backups") for part in p.parts))


def cmd_locks(args) -> int:
    project = open_project(args.project)
    open_files = [p for p in project_files(project) if not p.name.startswith("~") and project.is_locked(p)]
    if not open_files:
        print("no files open in KiCad")
    for p in open_files:
        print(f"open in KiCad: {p}")
    return DONE


def _version(path: Path):
    head = path.read_text(encoding="utf-8")[:300]
    m = re.search(r"\(version\s+(\d+)\)", head)
    return int(m.group(1)) if m else None


def cmd_upgrade(args) -> int:
    project = open_project(args.project)
    kinds = {".kicad_sch": ("sch", VERSIONS["kicad_sch"]), ".kicad_pcb": ("pcb", VERSIONS["kicad_pcb"]),
             ".kicad_sym": ("sym", VERSIONS["kicad_symbol_lib"])}
    files = [p for p in project_files(project) if p.suffix in (".kicad_sch", ".kicad_pcb")]
    libs = [Path(x).resolve() for x in args.lib or []]
    for lib in libs:
        if not (lib.suffix == ".kicad_sym" and lib.is_file() or lib.suffix == ".pretty" and lib.is_dir()):
            raise Usage(f"--lib wants a .kicad_sym file or a .pretty folder, not {lib}")
    lib_files = [lib for lib in libs if lib.is_file()] + [f for lib in libs if lib.is_dir()
                                                          for f in lib.glob("*.kicad_mod")]
    busy = [p for p in files + lib_files if project.is_locked(p)]
    if busy:
        raise LockedError(busy)
    for p in files + [lib for lib in libs if lib.is_file()]:
        tool, want = kinds[p.suffix]
        have = _version(p)
        if have == want:
            continue
        cli.run(tool, "upgrade", "--force", p, input_file=p)
        print(f"upgraded {p} ({have} → {_version(p)})")
    for lib in libs:
        if lib.is_dir():
            old = [f for f in lib.glob("*.kicad_mod") if _version(f) != VERSIONS["footprint"]]
            if old:
                cli.run("fp", "upgrade", "--force", lib, input_file=lib)
                print(f"upgraded {lib} ({len(old)} footprints)")
    return DONE


def _guard(func):
    def run(args) -> int:
        try:
            return func(args)
        except Usage as e:
            say(str(e))
            return USAGE
        except (cli.KicadCliMissing, cli.CliFailed) as e:
            say(str(e))
            return NO_CLI
        except LockedError as e:
            say(str(e))
            return LOCKED
        except (RejectedError, FormatError, cli.LoadError, WriteError) as e:
            say(str(e))
            return FAILED
    return run


def add_parser(tools) -> None:
    p = tools.add_parser("kicad", help="change KiCad projects with kt scripts, check them with kicad-cli")
    cmds = p.add_subparsers(dest="cmd", metavar="COMMAND")

    def add(name, func, help, project_option=False):
        c = cmds.add_parser(name, help=help)
        c.set_defaults(func=_guard(func))
        if project_option:
            c.add_argument("--project", help="project directory or .kicad_pro (default: current directory)")
        else:
            c.add_argument("project", nargs="?", help="project directory or .kicad_pro (default: current directory)")
        return c

    c = add("run", cmd_run, "run a script with kt and project loaded; save and check if it changed files", True)
    c.add_argument("file")
    c.add_argument("--strict", action="store_true", help="exit 1 if ERC/DRC report errors")
    c = add("exec", cmd_exec, "the same for a line of Python", True)
    c.add_argument("code")
    c.add_argument("--strict", action="store_true")
    c = add("erc", cmd_erc, "electrical rules check of the schematic")
    c.add_argument("--strict", action="store_true")
    c = add("drc", cmd_drc, "design rules check of the board (schematic parity listed apart)")
    c.add_argument("--strict", action="store_true")
    c = add("shot", cmd_shot, "pictures: sch (PDF), pcb VIEW… (PNG), pcb-layers LAYERS (PDF), fp/sym LIB:NAME (PDF)",
            True)
    c.add_argument("kind", choices=["sch", "pcb", "pcb-layers", "fp", "sym"])
    c.add_argument("what", nargs="*", help="pcb views: top bottom front back left right iso; "
                                            "layers: F.Cu,Edge.Cuts; fp/sym: LIB:NAME")
    c.add_argument("--pages", help="sch: page numbers, e.g. 1,3")
    c.add_argument("--out", help="directory for the pictures (default: a new folder under %%TEMP%%)")
    c.add_argument("--size", default="1600x1000", help="pcb renders: WxH in pixels")
    add("info", cmd_info, "the project in numbers: symbols, nets, footprints, in step with the board, checks")
    add("locks", cmd_locks, "which project files are open in KiCad right now")
    c = add("upgrade", cmd_upgrade, "bring schematic, board (and --lib libraries) to KiCad 10's current format")
    c.add_argument("--lib", action="append", metavar="PATH", help="a .kicad_sym file or .pretty folder (repeatable)")

"""A KiCad project on disk: its documents, and saving them in one checked step.

Saving never writes a file KiCad has open (its lock file exists), and never
replaces a file before `kicad-cli` has loaded the changed copy in a staging
directory.
"""
import csv
import io
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from kt import cli
from kt.sexpr import Node, dump, parse

# Format versions KiCad 10.0.1 writes (measured with `kicad-cli … upgrade`).
VERSIONS = {"kicad_sch": 20260306, "kicad_pcb": 20260206, "footprint": 20260206,
            "kicad_symbol_lib": 20251024}
KICAD_PROCESSES = {"kicad.exe", "eeschema.exe", "pcbnew.exe"}
STAGE_IGNORE = shutil.ignore_patterns(".git", ".history", "*-backups", "~*.lck", "*.kt-tmp")


class FormatError(Exception):
    pass


class LockedError(Exception):
    def __init__(self, paths: list[Path]):
        names = ", ".join(str(p) for p in paths)
        super().__init__(f"open in KiCad: {names} — save and close it there, then run again")
        self.paths = paths


class RejectedError(Exception):
    def __init__(self, path: Path, output: str):
        super().__init__(f"rejected by kicad-cli, nothing written: {path}\n{output.strip()}")
        self.path = path
        self.output = output


def lock_path(path: Path) -> Path:
    path = Path(path)
    return path.with_name(f"~{path.name}.lck")


def locked(path: Path) -> bool:
    return lock_path(path).exists()


def kicad_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             errors="replace").stdout
    except OSError:
        return False
    return any(row and row[0].lower() in KICAD_PROCESSES for row in csv.reader(io.StringIO(out)))


def read_text(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".kt-tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def check_version(root: Node, path: Path) -> None:
    want = VERSIONS.get(root.head)
    if want is None:
        return
    v = root.get("version")
    have = v[0] if v is not None else None
    if have != want:
        raise FormatError(
            f"{path} is in format {have}; kt edits only KiCad 10.0.1's format {want}. "
            f"Run `devtools kicad upgrade` (or open and save it in KiCad 10) first.")


class Document:
    """One S-expression file. Subclasses add helpers; `check` verifies a staged copy."""

    def __init__(self, project, path: Path, root: Node | None = None):
        self.project = project
        self.path = Path(path)
        self.new = root is not None
        if root is None:
            root = parse(read_text(self.path))
            check_version(root, self.path)
        self.root = root

    @property
    def changed(self) -> bool:
        return self.new or self.root.changed

    def text(self) -> str:
        return dump(self.root)

    def check(self, staged: Path):
        """Load the staged copy with kicad-cli; return a Report or None. Raise cli.LoadError."""
        return None

    def _saved(self) -> None:
        self.root = parse(read_text(self.path))
        self.new = False


# file suffix -> Document subclass; filled in by the modules that define them
DOCUMENT_TYPES: dict[str, type] = {}


@dataclass
class SaveResult:
    written: list[Path] = field(default_factory=list)
    reports: dict = field(default_factory=dict)


class Project:
    def __init__(self, path):
        path = Path(path).resolve()
        if path.suffix == ".kicad_pro":
            pro = path
        else:
            found = sorted(path.glob("*.kicad_pro"))
            if not found:
                raise FileNotFoundError(f"no .kicad_pro in {path}")
            if len(found) > 1:
                raise FileNotFoundError(f"several .kicad_pro in {path}: name the one to use")
            pro = found[0]
        self.pro_path = pro
        self.dir = pro.parent
        self.name = pro.stem
        self._docs: dict[Path, Document] = {}

    def __repr__(self):
        return f"<Project {self.name} in {self.dir}>"

    @property
    def root_sch(self) -> Path:
        return self.dir / f"{self.name}.kicad_sch"

    @property
    def pcb_path(self) -> Path:
        return self.dir / f"{self.name}.kicad_pcb"

    @property
    def libs(self):
        """The project's view of the library tables (project first, then global)."""
        if getattr(self, "_libs", None) is None:
            from kt.libs import LibTables
            self._libs = LibTables(self)
        return self._libs

    def sch(self, name=None):
        """The root schematic, or the sheet file `name` (relative to the project dir)."""
        import kt.schematic  # noqa: F401  registers the Schematic document type
        return self.document(name or self.root_sch)

    def sheets(self) -> list:
        """Every schematic sheet reachable from the root, root first."""
        from kt.schematic import sheet_tree
        return [self.document(p) for p in sheet_tree(self)]

    def document(self, path, root: Node | None = None) -> Document:
        """The document at `path` (relative to the project dir), loaded once.

        Pass `root` to create a new file.
        """
        path = Path(path)
        if not path.is_absolute():
            path = self.dir / path
        path = path.resolve()
        doc = self._docs.get(path)
        if doc is None:
            cls = DOCUMENT_TYPES.get(path.suffix, Document)
            doc = self._docs[path] = cls(self, path, root)
        elif root is not None:
            raise FileExistsError(f"{path} is already loaded")
        return doc

    def track(self, doc: Document) -> Document:
        self._docs[doc.path.resolve()] = doc
        return doc

    def changed(self) -> list[Document]:
        return [d for d in self._docs.values() if d.changed]

    def _inside(self, path: Path) -> bool:
        return path.is_relative_to(self.dir)

    def save(self) -> SaveResult:
        docs = self.changed()
        if not docs:
            return SaveResult()
        busy = [d.path for d in docs if locked(d.path)]
        if busy:
            raise LockedError(busy)
        with tempfile.TemporaryDirectory(prefix="kt-stage-") as tmp:
            reports = self._stage_and_check(docs, Path(tmp))
        written = []
        for d in docs:
            write_atomic(d.path, d.text())
            written.append(d.path)
            d._saved()
        return SaveResult(written, reports)

    def _stage_and_check(self, docs: list[Document], tmp: Path) -> dict:
        staged_dir = tmp / "project"
        shutil.copytree(self.dir, staged_dir, ignore=STAGE_IGNORE)
        staged = {}
        for i, d in enumerate(docs):
            if self._inside(d.path):
                target = staged_dir / d.path.relative_to(self.dir)
            else:
                target = tmp / "outside" / str(i) / d.path.parent.name / d.path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(d.text(), encoding="utf-8", newline="")
            staged[d.path] = target
        reports = {}
        try:
            if any(d.path.suffix == ".kicad_sch" and self._inside(d.path) for d in docs):
                reports[self.root_sch] = cli.erc(staged_dir / self.root_sch.name)
            if any(d.path == self.pcb_path for d in docs):
                reports[self.pcb_path] = cli.drc(staged_dir / self.pcb_path.name)
            for d in docs:
                if d.path.suffix in (".kicad_sch", ".kicad_pcb"):
                    continue
                report = d.check(staged[d.path])
                if report is not None:
                    reports[d.path] = report
        except cli.LoadError as e:
            original = next((p for p, s in staged.items() if s == e.path), e.path)
            raise RejectedError(original, e.output) from None
        return reports


def new_project(dir, name: str) -> Project:
    """Create an empty KiCad 10 project: `.kicad_pro`, root schematic, board."""
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    pro = dir / f"{name}.kicad_pro"
    if pro.exists():
        raise FileExistsError(pro)
    write_atomic(pro, "{}\n")
    write_atomic(dir / f"{name}.kicad_sch", dump(Node(
        "kicad_sch",
        Node("version", VERSIONS["kicad_sch"]), Node("generator", "kt"), Node("generator_version", "10.0"),
        Node("uuid", str(uuid.uuid4())), Node("paper", "A4"), Node("lib_symbols"),
        Node("sheet_instances", Node("path", "/", Node("page", "1"))),
        Node("embedded_fonts", False))) + "\n")
    write_atomic(dir / f"{name}.kicad_pcb", dump(Node(
        "kicad_pcb",
        Node("version", VERSIONS["kicad_pcb"]), Node("generator", "kt"),
        Node("generator_version", "10.0"))) + "\n")
    return Project(pro)

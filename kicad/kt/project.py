"""A KiCad project on disk: its documents, and saving them in one checked step.

Saving never writes a file KiCad has open (its lock file exists), and never
replaces a file before `kicad-cli` has loaded the changed copy in a staging
directory.
"""
import csv
import io
import json
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


class ChangedOnDiskError(LockedError):
    def __init__(self, paths: list[Path]):
        Exception.__init__(self, "changed on disk since the script read it: "
                           + ", ".join(str(p) for p in paths) + " — nothing written; run the script again")
        self.paths = paths


class WriteError(Exception):
    def __init__(self, written: list[Path], failed: Path, rest: list[Path], error: OSError):
        lines = [f"could not write {failed}: {error}"]
        lines += [f"  written: {p}" for p in written] or ["  written: nothing"]
        lines += [f"  not written: {p}" for p in [failed] + rest]
        super().__init__("\n".join(lines))
        self.written, self.failed, self.rest = written, failed, rest


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


def stamp(path: Path):
    """What a file on disk looks like now: (mtime ns, size), or None if it is missing."""
    try:
        st = Path(path).stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_size)


TABLE_FILES = ("sym-lib-table", "fp-lib-table")
RESAVE = {".kicad_sch": "sch", ".kicad_pcb": "pcb", ".kicad_sym": "sym", ".kicad_mod": "fp"}


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
    if have == want:
        return
    if isinstance(have, int) and have > want:
        raise FormatError(f"{path} is in format {have}, newer than KiCad 10.0.1's {want} that kt knows; "
                          f"kt cannot edit it safely.")
    if root.head in ("kicad_sch", "kicad_pcb"):
        how = "`devtools kicad upgrade`"
    else:
        lib = path.parent if root.head == "footprint" else path
        how = f"`devtools kicad upgrade --lib \"{lib}\"`"
    raise FormatError(f"{path} is in format {have}; kt edits only KiCad 10.0.1's format {want}. "
                      f"Run {how} (or open and save it in KiCad 10) first.")


class Document:
    """One S-expression file. Subclasses add helpers; `check` verifies a staged copy."""

    def __init__(self, project, path: Path, root: Node | None = None):
        self.project = project
        self.path = Path(path)
        self.new = root is not None
        self._stamp = stamp(self.path)
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
        self._stamp = stamp(self.path)


class ProjectFile(Document):
    """The `.kicad_pro` (JSON). Checked on save by parsing it and by an ERC run on the staged project."""

    def __init__(self, project, path: Path, root=None):
        self.project = project
        self.path = Path(path)
        self.new = False
        self._stamp = stamp(self.path)
        self._loaded = read_text(self.path)
        self.data = json.loads(self._loaded or "{}")
        self._original = json.loads(self._loaded or "{}")

    @property
    def changed(self) -> bool:
        return self.data != self._original

    def text(self) -> str:
        return json.dumps(self.data, indent=2) + "\n"

    def check(self, staged: Path):
        try:
            json.loads(staged.read_text(encoding="utf-8"))
        except ValueError as e:
            raise cli.LoadError(staged, f"not valid JSON: {e}") from None
        return None

    def _saved(self) -> None:
        self._loaded = read_text(self.path)
        self._original = json.loads(self._loaded)
        self._stamp = stamp(self.path)


# file suffix -> Document subclass; filled in by the modules that define them
DOCUMENT_TYPES: dict[str, type] = {".kicad_pro": ProjectFile}


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

    def settings(self) -> ProjectFile:
        """The project file's settings (`.data`, a dict), saved with the other documents."""
        return self.document(self.pro_path)

    def set_drawing_sheet(self, path: str) -> None:
        """The drawing sheet (`.kicad_wks`) for the schematic and the board, written as given,
        e.g. `${KIPRJMOD}/templates/Frame.kicad_wks`."""
        data = self.settings().data
        for section in ("schematic", "pcbnew"):
            data.setdefault(section, {})["page_layout_descr_file"] = path

    def next_page(self) -> str:
        """The highest page number used in the hierarchy + 1."""
        from kt.schematic import hierarchy
        numbers = [int(p) for p in hierarchy(self)[1].values() if str(p).isdigit()]
        return str(max(numbers, default=1) + 1)

    def footprint_lib(self, path):
        """A footprint library folder (`.pretty`), inside the project or anywhere else."""
        from kt.parts import FootprintLib
        return FootprintLib(self, path)

    def symbol_lib(self, path):
        """A symbol library file (`.kicad_sym`), created on save if missing."""
        import kt.parts  # noqa: F401  registers the SymbolLib document type
        return self.document(path)

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

    def _own(self, path: Path) -> bool:
        """Inside this project and not inside another project kept in a subfolder of it."""
        path, top = Path(path).resolve(), self.dir.resolve()
        if not path.is_relative_to(top):
            return False
        return not any(any(d.glob("*.kicad_pro")) for d in path.parents if d != top and d.is_relative_to(top))

    def schematic_open(self) -> bool:
        """KiCad's schematic editor locks only the sheet it opened; it holds every sheet.
        Locks of other projects in subfolders (e.g. reference copies) do not count."""
        return any(self._own(lock.with_name(lock.name[1:-4])) for lock in self.dir.rglob("~*.kicad_sch.lck"))

    def is_locked(self, path: Path) -> bool:
        path = Path(path)
        if locked(path):
            return True
        return path.suffix == ".kicad_sch" and self._own(path) and self.schematic_open()

    def _busy(self, docs) -> None:
        busy = [d.path for d in docs if self.is_locked(d.path)]
        if busy:
            raise LockedError(busy)
        moved = [d.path for d in docs if stamp(d.path) != d._stamp]
        if moved:
            raise ChangedOnDiskError(moved)

    def save(self) -> SaveResult:
        """Check locks, load every changed file in kicad-cli from a staged copy, check again, write."""
        docs = self.changed()
        if not docs:
            return SaveResult()
        self._busy(docs)
        with tempfile.TemporaryDirectory(prefix="kt-stage-") as tmp:
            reports = _check_all(self, docs, Path(tmp))
        self._busy(docs)          # opened or saved in KiCad while the checks ran?
        written = []
        for i, d in enumerate(docs):
            try:
                write_atomic(d.path, d.text())
            except OSError as e:
                raise WriteError(written, d.path, [x.path for x in docs[i + 1:]], e) from None
            written.append(d.path)
            d._saved()
        return SaveResult(written, reports)


def _resave_check(staged: Path) -> None:
    """Have kicad-cli load the file on its own and save it again; exit 3 = it cannot be loaded.

    Loading through the root sheet is lenient with sub-sheets, so every changed
    file is loaded by itself.
    """
    kind = RESAVE[staged.suffix]
    with tempfile.TemporaryDirectory(prefix="kt-probe-") as tmp:
        if kind == "fp":
            target = Path(tmp) / staged.parent.name
            target.mkdir()
            shutil.copy2(staged, target / staged.name)
        else:
            target = Path(tmp) / staged.name
            shutil.copy2(staged, target)
        try:
            cli.run(kind, "upgrade", "--force", target, input_file=staged)
        except cli.CliFailed as e:
            raise cli.LoadError(staged, str(e)) from None


def _check_all(project, docs: list[Document], tmp: Path) -> dict:
    staged_dir = tmp / "project"
    shutil.copytree(project.dir, staged_dir, ignore=STAGE_IGNORE)
    staged = {}
    for i, d in enumerate(docs):
        if project._inside(d.path):
            target = staged_dir / d.path.relative_to(project.dir)
        else:
            target = tmp / "outside" / str(i) / d.path.parent.name / d.path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(d.text(), encoding="utf-8", newline="")
        staged[d.path] = target
    reports = {}
    try:
        for d in docs:
            if d.path.suffix in RESAVE:
                _resave_check(staged[d.path])
            elif d.path.name not in TABLE_FILES and d.path.suffix != ".kicad_pro":
                raise RejectedError(d.path, "kt has no check for this kind of file, so it does not write it")
        if any(d.path.suffix in (".kicad_sch", ".kicad_pro") and project._inside(d.path) for d in docs):
            reports[project.root_sch] = cli.erc(staged_dir / project.root_sch.name)
        if any(d.path == project.pcb_path for d in docs):
            reports[project.pcb_path] = cli.drc(staged_dir / project.pcb_path.name)
        for d in docs:
            if d.path.suffix in (".kicad_sch", ".kicad_pcb"):
                continue
            report = d.check(staged[d.path])
            if report is not None:
                reports[d.path] = report
    except cli.LoadError as e:
        original = next((p for p, s in staged.items() if s == e.path or s.parent == e.path), e.path)
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

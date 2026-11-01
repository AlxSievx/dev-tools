"""Library tables: find symbols and footprints the way KiCad does, register new libraries.

Lookup order: the project's `sym-lib-table` / `fp-lib-table`, then the global
ones in %APPDATA%\\kicad\\10.0, following `(type "Table")` entries.
"""
import json
import os
import re
from pathlib import Path

from kt import cli
from kt.project import Document, kicad_running, read_text, write_atomic
from kt.sexpr import Node, Sym, dump, parse

TABLE_FILES = {"symbol": "sym-lib-table", "footprint": "fp-lib-table"}
TABLE_HEADS = {"symbol": "sym_lib_table", "footprint": "fp_lib_table"}


def config_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "kicad" / "10.0"


def global_table(kind: str) -> Path:
    return config_dir() / TABLE_FILES[kind]


def share_dir() -> Path:
    try:
        return cli.find().parent.parent / "share" / "kicad"
    except cli.KicadCliMissing:
        return cli.DEFAULT.parent.parent / "share" / "kicad"


def _user_vars() -> dict:
    try:
        data = json.loads((config_dir() / "kicad_common.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (data.get("environment") or {}).get("vars") or {}


def expand(uri: str, project_dir: Path | None) -> str:
    share = share_dir()
    values = {"KICAD10_SYMBOL_DIR": str(share / "symbols"),
              "KICAD10_FOOTPRINT_DIR": str(share / "footprints"),
              "KICAD10_3DMODEL_DIR": str(share / "3dmodels"),
              "KICAD10_TEMPLATE_DIR": str(share / "template")}
    values.update(_user_vars())
    values.update(os.environ)
    if project_dir is not None:
        values["KIPRJMOD"] = str(project_dir)

    def sub(m):
        name = m.group(1)
        if name not in values:
            raise KeyError(f"unknown path variable ${{{name}}} in {uri}")
        return values[name].replace("\\", "/") if "/" in uri else values[name]
    return re.sub(r"\$\{(\w+)\}", sub, uri)


def _entries(table: Path, project_dir: Path | None, seen: set) -> list[tuple[str, str]]:
    """(name, expanded uri) of every library in `table`, nested tables flattened."""
    table = Path(table)
    key = table.resolve()
    if key in seen or not table.is_file():
        return []
    seen.add(key)
    out = []
    for lib in parse(read_text(table)).all("lib"):
        name, kind, uri = (lib.get(k)[0] if lib.get(k) else "" for k in ("name", "type", "uri"))
        path = expand(uri, project_dir)
        if kind == "Table":
            out += _entries(Path(path), project_dir, seen)
        else:
            out.append((name, path))
    return out


_PARSED: dict[Path, tuple[float, Node]] = {}


def _parsed(path: Path) -> Node:
    path = Path(path)
    mtime = path.stat().st_mtime
    hit = _PARSED.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    root = parse(read_text(path))
    _PARSED[path] = (mtime, root)
    return root


def split_id(lib_id: str) -> tuple[str, str]:
    lib, sep, name = lib_id.partition(":")
    if not sep or not lib or not name:
        raise KeyError(f"{lib_id!r} is not a library id like 'Device:R'")
    return lib, name


def flatten(lib_root: Node, name: str) -> Node:
    """Symbol `name` from a parsed `.kicad_sym`, with `extends` resolved; a fresh copy."""
    syms = {s[0]: s for s in lib_root.all("symbol")}
    if name not in syms:
        raise KeyError(name)
    sym = syms[name]
    ext = sym.get("extends")
    if ext is None:
        return sym.copy()
    parent = flatten(lib_root, ext[0])
    out = Node("symbol", name)
    child_heads = {i.head for i in sym.all() if i.head not in ("extends", "property")}
    child_props = {p[0]: p for p in sym.all("property")}
    for item in parent.items[1:]:
        if not isinstance(item, Node):
            continue
        if item.head == "property" and item[0] in child_props:
            continue
        if item.head in child_heads:
            continue
        if item.head == "symbol":       # unit: "Parent_1_1" -> "Child_1_1"
            unit = item.copy()
            unit.set(0, name + unit[0][len(ext[0]):])
            out.append(unit)
        else:
            out.append(item.copy())
    for item in sym.items[1:]:
        if isinstance(item, Node) and item.head != "extends":
            out.append(item.copy())
    # KiCad's order: flags and properties first, then units
    units = out.all("symbol")
    for u in units:
        out.remove(u)
    for u in units:
        out.append(u)
    return out


class LibTables:
    def __init__(self, project=None):
        self.project = project
        pdir = project.dir if project is not None else None
        self._tables = {}
        for kind in TABLE_FILES:
            seen: set = set()
            entries = []
            if project is not None:
                entries += _entries(project.dir / TABLE_FILES[kind], pdir, seen)
            entries += _entries(global_table(kind), pdir, seen)
            table = {}
            for name, path in entries:
                table.setdefault(name, path)   # first wins: project before global
            self._tables[kind] = table

    def _lib(self, kind: str, lib: str) -> Path:
        try:
            return Path(self._tables[kind][lib])
        except KeyError:
            raise KeyError(f"no {kind} library {lib!r} in the project or global library table") from None

    def symbol_lib_path(self, lib: str) -> Path:
        return self._lib("symbol", lib)

    def footprint_lib_path(self, lib: str) -> Path:
        return self._lib("footprint", lib)

    def symbol(self, lib_id: str) -> Node:
        lib, name = split_id(lib_id)
        try:
            return flatten(_parsed(self.symbol_lib_path(lib)), name)
        except KeyError as e:
            if str(e).strip("'\"") == name:
                raise KeyError(f"no symbol {name!r} in library {lib!r}") from None
            raise

    def footprint(self, fp_id: str) -> Node:
        lib, name = split_id(fp_id)
        path = self.footprint_lib_path(lib) / f"{name}.kicad_mod"
        if not path.is_file():
            raise KeyError(f"no footprint {name!r} in library {lib!r} ({path})")
        return _parsed(path).copy()

    def has_symbol(self, lib_id: str) -> bool:
        try:
            self.symbol(lib_id)
            return True
        except KeyError:
            return False


def _lib_node(name: str, uri: str) -> Node:
    return Node("lib", Node("name", name), Node("type", "KiCad"), Node("uri", uri),
                Node("options", ""), Node("descr", ""))


def _uri(path: Path, project) -> str:
    path = Path(path).resolve()
    if project is not None and path.is_relative_to(project.dir):
        return "${KIPRJMOD}/" + path.relative_to(project.dir).as_posix()
    return path.as_posix()


def _add_entry(root: Node, name: str, uri: str) -> str:
    for lib in root.all("lib"):
        if lib.get("name")[0] == name:
            have = lib.get("uri")[0]
            if have == uri:
                return f"library {name} already registered"
            raise ValueError(f"library {name} is already registered with another path: {have}")
    root.append(_lib_node(name, uri))
    return ""


def register_library(kind: str, name: str, path, scope: str, project=None) -> str:
    """Add a library to the project's or the global table. Returns what was done, in one line."""
    if kind not in TABLE_FILES:
        raise ValueError(f"kind must be 'symbol' or 'footprint', not {kind!r}")
    if scope == "project":
        if project is None:
            raise ValueError("project scope needs the project")
        table = project.dir / TABLE_FILES[kind]
        if table.exists():
            doc = project.document(table)
        else:
            doc = project.document(table, root=Node(TABLE_HEADS[kind], Node("version", 7)))
        uri = _uri(path, project)
        done = _add_entry(doc.root, name, uri)
        return done or f"registered {kind} library {name} → {uri} in {table} (written on save)"
    if scope != "global":
        raise ValueError(f"scope must be 'project' or 'global', not {scope!r}")
    table = global_table(kind)
    uri = _uri(path, None)
    line = "(lib " + " ".join(dump(n) for n in _lib_node(name, uri).all()) + ")"
    if kicad_running():
        return (f"KiCad is running, so the global table was not changed. Add this line to {table} "
                f"(or Preferences → Manage Libraries) with KiCad closed:\n{line}")
    root = parse(read_text(table)) if table.exists() else Node(TABLE_HEADS[kind], Node("version", 7))
    done = _add_entry(root, name, uri)
    if done:
        return done
    write_atomic(table, dump(root) + ("" if table.exists() else "\n"))
    return f"registered {kind} library {name} → {uri} in {table}"

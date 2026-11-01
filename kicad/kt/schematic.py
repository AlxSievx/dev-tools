"""Schematic sheets: place symbols from the libraries, find their pins.

A placed symbol carries a copy of its library symbol in the sheet's
`lib_symbols` (named `Lib:Name`) and an `instances` entry for every place the
sheet is used in the hierarchy, as KiCad writes them.
"""
import re
import uuid as _uuid
from pathlib import Path

from kt.geometry import require_grid, sch_transform
from kt.project import DOCUMENT_TYPES, Document
from kt.sexpr import Node, Sym

SKIP_FIELDS = ("ki_",)          # library-only properties not copied onto placed symbols
FLAGS = (("exclude_from_sim", False), ("in_bom", True), ("on_board", True), ("in_pos_files", True))


def new_uuid() -> str:
    return str(_uuid.uuid4())


def _prop(node: Node, name: str):
    for p in node.all("property"):
        if p[0] == name:
            return p
    return None


def unit_of(unit_name: str) -> tuple[int, int]:
    """'LM2904_2_1' -> (2, 1): unit number and body style."""
    parts = unit_name.rsplit("_", 2)
    return int(parts[1]), int(parts[2])


def sheet_tree(project) -> dict[Path, list[str]]:
    """Every sheet file reachable from the root, with the instance paths it is used under."""
    root_doc = project.document(project.root_sch)
    tree = {root_doc.path: ["/" + root_doc.root.get("uuid")[0]]}
    todo = [root_doc]
    while todo:
        doc = todo.pop(0)
        for sheet in doc.root.all("sheet"):
            file = _prop(sheet, "Sheetfile")[1]
            child_path = (doc.path.parent / file).resolve()
            sid = sheet.get("uuid")[0]
            paths = [p + "/" + sid for p in tree[doc.path]]
            if child_path in tree:
                tree[child_path] += paths
            else:
                tree[child_path] = paths
                todo.append(project.document(child_path))
    return tree


class PlacedSymbol:
    def __init__(self, sch: "Schematic", node: Node):
        self.sch = sch
        self.node = node

    def __repr__(self):
        return f"<{self.ref} {self.lib_id} unit {self.unit} at {self.at} rot {self.rot}>"

    @property
    def ref(self) -> str:
        return _prop(self.node, "Reference")[1]

    @property
    def value(self) -> str:
        return _prop(self.node, "Value")[1]

    @property
    def lib_id(self) -> str:
        return self.node.get("lib_id")[0]

    @property
    def unit(self) -> int:
        u = self.node.get("unit")
        return u[0] if u is not None else 1

    @property
    def at(self) -> tuple[float, float]:
        a = self.node.get("at")
        return (a[0], a[1])

    @property
    def rot(self) -> int:
        a = self.node.get("at")
        return int(a[2]) if len(a) > 2 else 0

    @property
    def mirror(self) -> str | None:
        m = self.node.get("mirror")
        return str(m[0]) if m is not None else None

    def fields(self) -> dict[str, str]:
        return {p[0]: p[1] for p in self.node.all("property")}

    def lib_symbol(self) -> Node:
        return self.sch.embedded(self.lib_id)

    def _lib_pins(self):
        body = self.node.get("body_style")
        body = body[0] if body is not None else 1
        for unit in self.lib_symbol().all("symbol"):
            u, b = unit_of(unit[0])
            if u in (0, self.unit) and b in (0, body):
                yield from unit.all("pin")

    def pins(self) -> dict[str, tuple[float, float]]:
        """Pin number -> connection point on the sheet."""
        f = sch_transform(self.at, self.rot, self.mirror)
        out = {}
        for pin in self._lib_pins():
            at = pin.get("at")
            out[pin.get("number")[0]] = f(at[0], at[1])
        return out

    def pin(self, key: str) -> tuple[float, float]:
        """A pin's sheet point, by number or (if unique) by name."""
        pins = self.pins()
        if key in pins:
            return pins[key]
        named = [p.get("number")[0] for p in self._lib_pins() if p.get("name")[0] == key]
        if len(named) == 1:
            return pins[named[0]]
        if not named:
            raise KeyError(f"{self.ref} has no pin {key!r} (pins: {', '.join(sorted(pins))})")
        raise KeyError(f"{self.ref}: pin name {key!r} is not unique ({', '.join(named)}); use the number")


class Schematic(Document):
    @property
    def uuid(self) -> str:
        return self.root.get("uuid")[0]

    @property
    def instance_paths(self) -> list[str]:
        return sheet_tree(self.project).get(self.path.resolve(), [])

    @property
    def lib_symbols(self) -> Node:
        node = self.root.get("lib_symbols")
        if node is None:
            node = Node("lib_symbols")
            self.root.insert(self._index_of("paper", after=True), node)
        return node

    def _index_of(self, head: str, after: bool = False) -> int:
        for i, item in enumerate(self.root.items):
            if isinstance(item, Node) and item.head == head:
                return i + 1 if after else i
        return len(self.root.items)

    def _insert_item(self, node: Node) -> Node:
        """Add a drawing item before `sheet_instances`, as KiCad orders them."""
        return self.root.insert(self._index_of("sheet_instances"), node)

    def embedded(self, lib_id: str) -> Node:
        for s in self.lib_symbols.all("symbol"):
            if s[0] == lib_id:
                return s
        raise KeyError(f"{lib_id} is not embedded in {self.path.name}")

    def _embed(self, lib_id: str) -> Node:
        try:
            return self.embedded(lib_id)
        except KeyError:
            pass
        sym = self.project.libs.symbol(lib_id)
        sym.set(0, lib_id)
        return self.lib_symbols.append(sym)

    def symbols(self) -> list[PlacedSymbol]:
        return [PlacedSymbol(self, n) for n in self.root.all("symbol")]

    def symbol(self, ref: str, unit: int | None = None) -> PlacedSymbol:
        for s in self.symbols():
            if s.ref == ref and (unit is None or s.unit == unit):
                return s
        raise KeyError(f"no symbol {ref}" + (f" unit {unit}" if unit else "") + f" in {self.path.name}")

    def next_ref(self, prefix: str) -> str:
        pat = re.compile(re.escape(prefix) + r"(\d+)\Z")
        top = 0
        for sheet in self.project.sheets():
            for node in sheet.root.all("symbol"):
                refs = [_prop(node, "Reference")[1]]
                refs += [r[0] for r in node.walk() if r.head == "reference"]
                for ref in refs:
                    m = pat.match(ref)
                    if m:
                        top = max(top, int(m.group(1)))
        return f"{prefix}{top + 1}"

    def add_symbol(self, lib_id: str, ref: str, at, *, value: str | None = None,
                   footprint: str | None = None, rot: int = 0, mirror: str | None = None,
                   unit: int = 1, fields: dict | None = None) -> PlacedSymbol:
        require_grid(tuple(at), what=f"{ref} position")
        if rot not in (0, 90, 180, 270):
            raise ValueError(f"rot must be 0, 90, 180 or 270, not {rot}")
        if mirror not in (None, "x", "y"):
            raise ValueError(f"mirror must be None, 'x' or 'y', not {mirror!r}")
        lib = self._embed(lib_id)
        units = {unit_of(u[0])[0] for u in lib.all("symbol")} - {0}
        if units and unit not in units:
            raise ValueError(f"{lib_id} has units {sorted(units)}, not {unit}")
        x, y = at
        f = sch_transform(at, rot, mirror)
        node = Node("symbol", Node("lib_id", lib_id), Node("at", x, y, rot))
        if mirror:
            node.append(Node("mirror", Sym(mirror)))
        node.append(Node("unit", unit))
        node.append(Node("body_style", 1))
        for head, default in FLAGS:
            flag = lib.get(head)
            node.append(Node(head, flag[0] if flag is not None else Sym("yes" if default else "no")))
        node.append(Node("dnp", Sym("no")))
        node.append(Node("uuid", new_uuid()))
        values = {"Reference": ref}
        if value is not None:
            values["Value"] = value
        if footprint is not None:
            values["Footprint"] = footprint
        for p in lib.all("property"):
            if p[0].startswith(SKIP_FIELDS):
                continue
            q = p.copy()
            if p[0] in values:
                q.set(1, values[p[0]])
            pat = q.get("at")
            px, py = f(pat[0], pat[1])
            angle = ((pat[2] if len(pat) > 2 else 0) + rot) % 180
            q.replace(pat, Node("at", px, py, angle))
            node.append(q)
        for name, val in (fields or {}).items():
            if _prop(node, name) is not None:
                _prop(node, name).set(1, val)
                continue
            node.append(Node("property", name, val, Node("at", x, y, 0), Node("hide", Sym("yes")),
                             Node("show_name", Sym("no")), Node("do_not_autoplace", Sym("no")),
                             Node("effects", Node("font", Node("size", 1.27, 1.27)))))
        placed = PlacedSymbol(self, node)
        numbers = [p.get("number")[0] for p in placed._lib_pins()]
        for n in dict.fromkeys(numbers):
            node.append(Node("pin", n, Node("uuid", new_uuid())))
        proj = Node("project", self.project.name)
        for path in self.instance_paths or ["/" + self.uuid]:
            proj.append(Node("path", path, Node("reference", ref), Node("unit", unit)))
        node.append(Node("instances", proj))
        self._insert_item(node)
        return placed


DOCUMENT_TYPES[".kicad_sch"] = Schematic

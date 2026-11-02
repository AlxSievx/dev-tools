"""Sheet symbols: hierarchical sub-sheets on a schematic, their pins, sheet files used several times.

Node layout copied from sheets KiCad 10 wrote (stock demos `pic_programmer`,
`complex_hierarchy` after `kicad-cli sch upgrade`): `(sheet (at) (size) flags (stroke)
(fill) (uuid) (property "Sheetname") (property "Sheetfile") (pin …)… (instances …))`.
"""
import re
import uuid as _uuid
from pathlib import Path

from kt.geometry import require_grid
from kt.project import VERSIONS
from kt.sexpr import Node, Sym

SHAPES = ("input", "output", "bidirectional", "tri_state", "passive")
# side -> pin angle, text justification inside the box
SIDES = {"left": (180, "left"), "right": (0, "right"), "top": (90, "right"), "bottom": (270, "left")}
PITCH = 2.54
FONT = 1.27


def _new_uuid() -> str:
    return str(_uuid.uuid4())


def _font(*justify) -> Node:
    eff = Node("effects", Node("font", Node("size", FONT, FONT)))
    if justify:
        eff.append(Node("justify", *(Sym(j) for j in justify)))
    return eff


def _prop_value(node: Node, name: str):
    for p in node.all("property"):
        if p[0] == name:
            return p[1]
    return None


def pin_point(at, size, side: str, offset: float) -> tuple[float, float]:
    """Where a sheet pin `offset` mm along `side` sits (from the top or left corner)."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {', '.join(SIDES)}, not {side!r}")
    (x, y), (w, h) = at, size
    length = h if side in ("left", "right") else w
    if not 0 < offset < length:
        raise ValueError(f"a pin {offset} mm along the {side} side does not fit a side of {length} mm")
    point = {"left": (x, y + offset), "right": (x + w, y + offset),
             "top": (x + offset, y), "bottom": (x + offset, y + h)}[side]
    return (round(point[0], 4), round(point[1], 4))


def pin_node(name: str, shape: str, side: str, point) -> Node:
    if shape not in SHAPES:
        raise ValueError(f"shape must be one of {', '.join(SHAPES)}, not {shape!r}")
    angle, justify = SIDES[side]
    return Node("pin", name, Sym(shape), Node("at", point[0], point[1], angle),
                Node("uuid", _new_uuid()), _font(justify))


def sheet_node(name: str, file: str, at, size, project_name: str, parent_path: str, page: str) -> Node:
    (x, y), (w, h) = at, size
    return Node(
        "sheet", Node("at", x, y), Node("size", w, h),
        Node("exclude_from_sim", Sym("no")), Node("in_bom", Sym("yes")), Node("on_board", Sym("yes")),
        Node("dnp", Sym("no")),
        Node("stroke", Node("width", 0), Node("type", Sym("solid"))),
        Node("fill", Node("color", 0, 0, 0, 0)),
        Node("uuid", _new_uuid()),
        Node("property", "Sheetname", name, Node("at", x, round(y - 0.7625, 4), 0),
             Node("show_name", Sym("no")), Node("do_not_autoplace", Sym("no")), _font("left", "bottom")),
        Node("property", "Sheetfile", file, Node("at", x, round(y + h + 0.6, 4), 0),
             Node("show_name", Sym("no")), Node("do_not_autoplace", Sym("no")), _font("left", "top")),
        Node("instances", Node("project", project_name, Node("path", parent_path, Node("page", page)))))


def child_root(parent: Node) -> Node:
    """An empty sub-sheet file as KiCad 10 writes one: no sheet_instances, no embedded_fonts."""
    root = Node("kicad_sch", Node("version", VERSIONS["kicad_sch"]), Node("generator", "kt"),
                Node("generator_version", "10.0"), Node("uuid", _new_uuid()),
                parent.get("paper").copy() if parent.get("paper") is not None else Node("paper", "A4"))
    if parent.get("title_block") is not None:
        root.append(parent.get("title_block").copy())
    root.append(Node("lib_symbols"))
    return root


class Sheet:
    """A sheet symbol on a schematic (`sch`)."""

    def __init__(self, sch, node: Node):
        self.sch = sch
        self.node = node

    def __repr__(self):
        return f"<Sheet {self.name} → {self.file} at {self.at}>"

    def __eq__(self, other):
        return isinstance(other, Sheet) and other.node is self.node

    @property
    def name(self) -> str:
        return _prop_value(self.node, "Sheetname")

    @property
    def file(self) -> str:
        return _prop_value(self.node, "Sheetfile")

    @property
    def uuid(self) -> str:
        return self.node.get("uuid")[0]

    @property
    def at(self) -> tuple[float, float]:
        a = self.node.get("at")
        return (a[0], a[1])

    @property
    def size(self) -> tuple[float, float]:
        s = self.node.get("size")
        return (s[0], s[1])

    def pins(self) -> dict[str, tuple[float, float]]:
        return {p[0]: (p.get("at")[0], p.get("at")[1]) for p in self.node.all("pin")}

    def pin(self, name: str) -> tuple[float, float]:
        pins = self.pins()
        if name not in pins:
            raise KeyError(f"sheet {self.name} has no pin {name!r} (pins: {', '.join(pins)})")
        return pins[name]

    def add_pin(self, name: str, shape: str, side: str, offset: float) -> tuple[float, float]:
        """A sheet pin `offset` mm along `side` from its top (left/right) or left (top/bottom) corner."""
        if name in self.pins():
            raise ValueError(f"sheet {self.name} already has a pin {name!r}")
        point = pin_point(self.at, self.size, side, offset)
        require_grid(point, what=f"sheet pin {name}")
        node = pin_node(name, shape, side, point)
        self.node.insert(self.node.items.index(self.node.get("instances")), node)
        return point

    def child(self):
        """The schematic this sheet symbol shows."""
        return self.sch.project.sch(self.sch.path.parent / self.file)

    def pages(self) -> dict[str, str]:
        """Child instance path -> page, for each instance of the sheet the symbol sits on."""
        from kt.schematic import sheet_page
        return {parent + "/" + self.uuid: sheet_page(self.node, parent) for parent in self.sch.instance_paths}


def _split_ref(ref: str) -> tuple[str, int | None]:
    m = re.match(r"(.*?)(\d+)\Z", ref)
    return (m.group(1), int(m.group(2))) if m else (ref.rstrip("?"), None)


def add_instance(child, new_path: str, ref_start: int | None) -> None:
    """Give every symbol on `child` an instance entry under `new_path` with a reference of its own.

    Per prefix, in the order of the current reference numbers, the first free numbers from
    `ref_start` on (None: after the highest in use). Units of one part keep one reference.
    """
    renamed: dict[str, str] = {}
    symbols = sorted(child.symbols(), key=lambda s: (_split_ref(s.ref)[0], _split_ref(s.ref)[1] or 0, s.unit))
    for s in symbols:
        prefix, number = _split_ref(s.ref)
        key = s.ref if number is not None else s.node.get("uuid")[0]   # unannotated ("R?"): one each
        if key not in renamed:
            renamed[key] = child.next_ref(prefix, ref_start)
        inst = s.node.get("instances")
        if inst is None:
            inst = s.node.append(Node("instances", Node("project", child.project.name)))
        proj = inst.get("project") or inst.append(Node("project", child.project.name))
        proj.append(Node("path", new_path, Node("reference", renamed[key]), Node("unit", s.unit)))


def is_known(project, path: Path) -> bool:
    return path.exists() or path.resolve() in project._docs

"""Schematic sheets: place symbols from the libraries, find their pins.

A placed symbol carries a copy of its library symbol in the sheet's
`lib_symbols` (named `Lib:Name`) and an `instances` entry for every place the
sheet is used in the hierarchy, as KiCad writes them.
"""
import re
import uuid as _uuid
from pathlib import Path

from kt.geometry import require_grid, rotate_screen, sch_transform
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


def _new_field(name: str, value: str, x: float, y: float) -> Node:
    return Node("property", name, value, Node("at", x, y, 0), Node("hide", Sym("yes")),
                Node("show_name", Sym("no")), Node("do_not_autoplace", Sym("no")),
                Node("effects", Node("font", Node("size", 1.27, 1.27))))


def _font(justify: tuple = ()) -> Node:
    eff = Node("effects", Node("font", Node("size", 1.27, 1.27)))
    if justify:
        eff.append(Node("justify", *(Sym(j) for j in justify)))
    return eff


def _pt(p) -> tuple[float, float]:
    return (round(float(p[0]), 4), round(float(p[1]), 4))


def _wire(a, b) -> Node:
    return Node("wire", Node("pts", Node("xy", a[0], a[1]), Node("xy", b[0], b[1])),
                Node("stroke", Node("width", 0), Node("type", Sym("default"))),
                Node("uuid", new_uuid()))


def _interior(p, a, b) -> bool:
    """`p` lies strictly inside the axis-parallel segment a–b."""
    if a[0] == b[0] == p[0]:
        return min(a[1], b[1]) < p[1] < max(a[1], b[1])
    if a[1] == b[1] == p[1]:
        return min(a[0], b[0]) < p[0] < max(a[0], b[0])
    return False


def _root_page(root: Node) -> str:
    inst = root.get("sheet_instances")
    for path in inst.all("path") if inst is not None else []:
        if path[0] == "/" and path.get("page") is not None:
            return str(path.get("page")[0])
    return "1"


def sheet_page(sheet: Node, parent_path: str) -> str:
    """The page number a `(sheet …)` node gives its instance under `parent_path` ('' if none)."""
    inst = sheet.get("instances")
    for proj in inst.all("project") if inst is not None else []:
        for path in proj.all("path"):
            if path[0] == parent_path and path.get("page") is not None:
                return str(path.get("page")[0])
    return ""


def hierarchy(project) -> tuple[dict[Path, list[str]], dict[str, str]]:
    """Every sheet file reachable from the root with its instance paths, and each path's page."""
    root_doc = project.document(project.root_sch)
    root_path = "/" + root_doc.root.get("uuid")[0]
    tree: dict[Path, list[str]] = {}
    pages = {root_path: _root_page(root_doc.root)}

    def visit(doc, path: str, stack: tuple) -> None:
        # every instance path is followed down on its own, so a file reached again later
        # still passes the new path on to its sub-sheets
        tree.setdefault(doc.path, []).append(path)
        for sheet in doc.root.all("sheet"):
            child_path = (doc.path.parent / _prop(sheet, "Sheetfile")[1]).resolve()
            if child_path in stack:          # a sheet showing one of its ancestors: KiCad refuses it too
                continue
            child = path + "/" + sheet.get("uuid")[0]
            pages[child] = sheet_page(sheet, path)
            visit(project.document(child_path), child, stack + (child_path,))

    visit(root_doc, root_path, (root_doc.path,))
    return tree, pages


def sheet_tree(project) -> dict[Path, list[str]]:
    """Every sheet file reachable from the root, with the instance paths it is used under."""
    return hierarchy(project)[0]


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

    @property
    def refs(self) -> dict[str, str]:
        """Instance path -> reference (a sheet used several times gives one per instance)."""
        out = {}
        inst = self.node.get("instances")
        for proj in inst.all("project") if inst is not None else []:
            for path in proj.all("path"):
                if path.get("reference") is not None:
                    out[path[0]] = path.get("reference")[0]
        return out

    def answers_to(self, ref: str) -> bool:
        return self.ref == ref or ref in self.refs.values()

    def fields(self) -> dict[str, str]:
        return {p[0]: p[1] for p in self.node.all("property")}

    def set_dnp(self, dnp: bool = True) -> None:
        """Mark the symbol Do Not Populate (KiCad's DNP flag), or clear it."""
        flag = Sym("yes" if dnp else "no")
        node = self.node.get("dnp")
        if node is None:
            self.node.insert(self.node.items.index(self.node.get("uuid")), Node("dnp", flag))
        else:
            node.set(0, flag)

    def show_field(self, name: str, visible: bool = True, at: tuple | None = None) -> None:
        """Show or hide a field on the sheet; `at` moves it."""
        p = _prop(self.node, name)
        if p is None:
            raise KeyError(f"{self.ref} has no field {name!r} (fields: {', '.join(self.fields())})")
        hide = p.get("hide")
        if visible:
            if hide is not None:
                p.remove(hide)
        elif hide is not None:
            hide.set(0, Sym("yes"))
        else:
            p.insert(p.items.index(p.get("at")) + 1, Node("hide", Sym("yes")))
        if at is not None:
            pa = p.get("at")
            pa.set(0, at[0])
            pa.set(1, at[1])

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

    def set_ref(self, old: str, new: str) -> None:
        """Rename one instance's reference (`old` -> `new`); the other instances keep theirs."""
        hits = [path for path, r in self.refs.items() if r == old]
        if not hits and self.ref != old:
            raise KeyError(f"{self.ref} has no instance with reference {old!r} ({', '.join(self.refs.values())})")
        if new != old and any(s.answers_to(new) for sheet in self.sch.project.sheets() for s in sheet.symbols()):
            raise ValueError(f"reference {new!r} is already in use")
        inst = self.node.get("instances")
        for proj in inst.all("project") if inst is not None else []:
            for path in proj.all("path"):
                if path[0] in hits:
                    path.get("reference").set(0, new)
        if self.ref == old:
            _prop(self.node, "Reference").set(1, new)

    def set_field(self, name: str, value: str) -> None:
        if name == "Reference" and len(set(self.refs.values())) > 1:
            raise ValueError(f"{self.ref} sits on a sheet used several times ({', '.join(self.refs.values())}); "
                             "rename one instance with set_ref(old, new)")
        p = _prop(self.node, name)
        if p is None:
            x, y = self.at
            self.node.insert(self._last_property_index(), _new_field(name, value, x, y))
        else:
            p.set(1, value)
        if name == "Reference":
            for r in [n for n in self.node.walk() if n.head == "reference"]:
                r.set(0, value)

    def _last_property_index(self) -> int:
        idx = 0
        for i, item in enumerate(self.node.items):
            if isinstance(item, Node) and item.head == "property":
                idx = i + 1
        return idx

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

    def instance_pages(self) -> dict[str, str]:
        """Instance path -> page number of this sheet file."""
        tree, pages = hierarchy(self.project)
        return {p: pages.get(p, "") for p in tree.get(self.path.resolve(), [])}

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
        """Add a drawing item before `sheet_instances` / `embedded_fonts`, as KiCad orders them."""
        return self.root.insert(min(self._index_of("sheet_instances"), self._index_of("embedded_fonts")), node)

    # ---- drawing items and sheet settings
    def add_text(self, text: str, at, *, size: float = 1.27, rot: int = 0,
                 justify: tuple = ("left", "bottom"), bold: bool = False, italic: bool = False) -> Node:
        """A note on the sheet (not a label: it connects nothing)."""
        if rot not in (0, 90, 180, 270):
            raise ValueError(f"rot must be 0, 90, 180 or 270, not {rot}")
        font = Node("font", Node("size", size, size))
        if bold:
            font.append(Node("thickness", round(size * 0.2, 4)))
            font.append(Node("bold", Sym("yes")))
        if italic:
            font.append(Node("italic", Sym("yes")))
        effects = Node("effects", font)
        if justify:
            effects.append(Node("justify", *(Sym(j) for j in justify)))
        return self._insert_item(Node("text", text, Node("exclude_from_sim", Sym("no")),
                                      Node("at", at[0], at[1], rot), effects, Node("uuid", new_uuid())))

    def text_box(self, text: str, at, size, *, font: float = 1.27, border: bool = True) -> Node:
        """A text box: `at` its top-left corner, `size` (w, h)."""
        # KiCad marks a text box without border by a negative stroke width
        stroke = Node("stroke", Node("width", 0 if border else -0.0001), Node("type", Sym("default" if border else "solid")))
        return self._insert_item(Node(
            "text_box", text, Node("exclude_from_sim", Sym("no")), Node("at", at[0], at[1], 0),
            Node("size", size[0], size[1]), Node("margins", 0.9525, 0.9525, 0.9525, 0.9525), stroke,
            Node("fill", Node("type", Sym("none"))),
            Node("effects", Node("font", Node("size", font, font)), Node("justify", Sym("left"), Sym("top"))),
            Node("uuid", new_uuid())))

    RECT_STYLES = ("default", "solid", "dash", "dot", "dash_dot", "dash_dot_dot")

    def rect(self, start, end, *, width: float = 0, style: str = "default") -> Node:
        """A rectangle (no fill), e.g. around a group of parts."""
        if style not in self.RECT_STYLES:
            raise ValueError(f"style must be one of {', '.join(self.RECT_STYLES)}, not {style!r}")
        return self._insert_item(Node(
            "rectangle", Node("start", start[0], start[1]), Node("end", end[0], end[1]),
            Node("stroke", Node("width", width), Node("type", Sym(style))),
            Node("fill", Node("type", Sym("none"))), Node("uuid", new_uuid())))

    def title_block(self, *, title: str | None = None, date: str | None = None, rev: str | None = None,
                    company: str | None = None, comments: dict[int, str] | None = None) -> dict:
        """Set the given title-block fields (comments 1–9); returns all of them."""
        for n in comments or {}:
            if not 1 <= n <= 9:
                raise ValueError(f"title block comments are numbered 1–9, not {n}")
        tb = self.root.get("title_block")
        if tb is None:
            tb = self.root.insert(self._index_of("paper", after=True), Node("title_block"))
        for head, value in (("title", title), ("date", date), ("rev", rev), ("company", company)):
            if value is None:
                continue
            node = tb.get(head)
            if node is None:
                order = ["title", "date", "rev", "company"]
                later = [i for i, item in enumerate(tb.items) if isinstance(item, Node)
                         and (item.head == "comment" or (item.head in order and order.index(item.head) > order.index(head)))]
                tb.insert(later[0] if later else len(tb.items), Node(head, value))
            else:
                node.set(0, value)
        for n, value in sorted((comments or {}).items()):
            node = next((c for c in tb.all("comment") if c[0] == n), None)
            if node is None:
                tb.append(Node("comment", n, value))
            else:
                node.set(1, value)
        out = {h: (tb.get(h)[0] if tb.get(h) is not None else "") for h in ("title", "date", "rev", "company")}
        out["comments"] = {c[0]: c[1] for c in tb.all("comment")}
        return out

    PAPERS = ("A5", "A4", "A3", "A2", "A1", "A0", "A", "B", "C", "D", "E", "USLetter", "USLegal", "USLedger")

    @property
    def paper(self) -> tuple[str, bool]:
        """(size, portrait)."""
        p = self.root.get("paper")
        return (p[0], any(str(v) == "portrait" for v in p.values()[1:])) if p is not None else ("A4", False)

    def set_paper(self, size: str, portrait: bool = False) -> None:
        if size not in self.PAPERS:
            raise ValueError(f"paper must be one of {', '.join(self.PAPERS)}, not {size!r}")
        node = Node("paper", size, Sym("portrait")) if portrait else Node("paper", size)
        old = self.root.get("paper")
        if old is None:
            self.root.insert(self._index_of("uuid", after=True), node)
        else:
            self.root.replace(old, node)

    # ---- sub-sheets
    def sheet_symbols(self) -> list:
        from kt.sheets import Sheet
        return [Sheet(self, n) for n in self.root.all("sheet")]

    def sheet(self, name: str):
        for s in self.sheet_symbols():
            if s.name == name:
                return s
        raise KeyError(f"no sheet {name!r} on {self.path.name}")

    def add_sheet(self, name: str, file: str, at, size, *, pins=(), page: str | None = None,
                  ref_start: int | None = None):
        """A sheet symbol showing `file` (relative to this sheet). A new file is created; an existing
        one gets a further instance, its parts new references from `ref_start` on."""
        from kt import sheets
        at, size = tuple(at), tuple(size)
        require_grid(at, (at[0] + size[0], at[1] + size[1]), what=f"sheet {name}")
        paths = self.instance_paths or ["/" + self.uuid]
        if len(paths) > 1:
            raise NotImplementedError(f"{self.path.name} is used {len(paths)} times; "
                                      "sheets on a reused sheet are not supported yet")
        if any(s.name == name for s in self.sheet_symbols()):
            raise ValueError(f"{self.path.name} already has a sheet named {name!r}")
        placed, count = [], {}
        for pin_name, shape, side in pins:
            count[side] = count.get(side, 0) + 1
            point = sheets.pin_point(at, size, side, count[side] * sheets.PITCH)
            placed.append(sheets.pin_node(pin_name, shape, side, point))
        if len({p[0] for p in placed}) != len(placed):
            raise ValueError(f"sheet {name}: pin names repeat")
        child_path = (self.path.parent / file).resolve()
        if child_path == self.path.resolve():
            raise ValueError(f"sheet {name}: {file} cannot show itself")
        reuse = sheets.is_known(self.project, child_path)
        if reuse and self.project.document(child_path).root.all("sheet"):
            raise NotImplementedError(f"{file} contains sheets; using it again is not supported yet")
        page = page or self.project.next_page()
        node = sheets.sheet_node(name, file, at, size, self.project.name, paths[0], str(page))
        for p in placed:
            node.insert(node.items.index(node.get("instances")), p)
        if not reuse:
            self.project.document(child_path, root=sheets.child_root(self.root))
        self._insert_item(node)
        sheet = sheets.Sheet(self, node)
        if reuse:
            sheets.add_instance(self.project.sch(child_path), paths[0] + "/" + sheet.uuid, ref_start)
        return sheet

    def embedded(self, lib_id: str) -> Node:
        for s in self.lib_symbols.all("symbol"):
            if s[0] == lib_id:
                return s
        raise KeyError(f"{lib_id} is not embedded in {self.path.name}")

    def _embed(self, lib_id: str, libs=None) -> Node:
        try:
            return self.embedded(lib_id)
        except KeyError:
            pass
        sym = (libs or self.project.libs).symbol(lib_id)
        sym.set(0, lib_id)
        return self.lib_symbols.append(sym)

    def symbols(self) -> list[PlacedSymbol]:
        return [PlacedSymbol(self, n) for n in self.root.all("symbol")]

    def symbol(self, ref: str, unit: int | None = None) -> PlacedSymbol:
        """A symbol by its reference, or by the reference of any of its instances."""
        for s in self.symbols():
            if s.answers_to(ref) and (unit is None or s.unit == unit):
                return s
        raise KeyError(f"no symbol {ref}" + (f" unit {unit}" if unit else "") + f" in {self.path.name}")

    def _used_numbers(self, prefix: str) -> set[int]:
        pat = re.compile(re.escape(prefix) + r"(\d+)\Z")
        used = set()
        for sheet in self.project.sheets():
            for node in sheet.root.all("symbol"):
                refs = [_prop(node, "Reference")[1]]
                refs += [r[0] for r in node.walk() if r.head == "reference"]
                for ref in refs:
                    m = pat.match(ref)
                    if m:
                        used.add(int(m.group(1)))
        return used

    def next_ref(self, prefix: str, start: int | None = None) -> str:
        """Highest number in use + 1, or with `start` the first free number from `start` on.

        Every sheet of the project counts, every instance of it too.
        """
        used = self._used_numbers(prefix)
        if start is None:
            return f"{prefix}{max(used, default=0) + 1}"
        n = start
        while n in used:
            n += 1
        return f"{prefix}{n}"

    def next_refs(self, prefix: str, starts: dict[str, int]) -> dict[str, str]:
        """One free reference per instance path, each the first free number from its start."""
        used = self._used_numbers(prefix)
        out = {}
        for path, n in starts.items():
            while n in used:
                n += 1
            used.add(n)
            out[path] = f"{prefix}{n}"
        return out

    def _instance_refs(self, ref) -> dict[str, str]:
        paths = self.instance_paths or ["/" + self.uuid]
        if isinstance(ref, dict):
            if set(ref) != set(paths):
                raise ValueError(f"references must be given for exactly the instances of {self.path.name}: "
                                 f"{paths}, not {list(ref)}")
            if len(set(ref.values())) != len(ref):
                raise ValueError(f"one reference per instance, all different: {ref}")
            return {p: ref[p] for p in paths}
        if len(paths) > 1:
            raise ValueError(f"{self.path.name} is used {len(paths)} times, so {ref!r} would repeat: pass one "
                             f"reference per instance, e.g. sch.next_refs('R', {{path: start for path in "
                             f"sch.instance_paths}})")
        return {paths[0]: ref}

    def add_symbol(self, lib_id: str, ref: str | dict[str, str], at, *, value: str | None = None,
                   footprint: str | None = None, rot: int = 0, mirror: str | None = None,
                   unit: int = 1, fields: dict | None = None, libs=None) -> PlacedSymbol:
        """Place a library symbol. `ref`: a reference, or {instance path: reference} on a sheet
        used several times (see `next_refs`)."""
        refs = self._instance_refs(ref)
        ref = next(iter(refs.values()))
        require_grid(tuple(at), what=f"{ref} position")
        if rot not in (0, 90, 180, 270):
            raise ValueError(f"rot must be 0, 90, 180 or 270, not {rot}")
        if mirror not in (None, "x", "y"):
            raise ValueError(f"mirror must be None, 'x' or 'y', not {mirror!r}")
        lib = self._embed(lib_id, libs)
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
            else:
                node.append(_new_field(name, val, x, y))
        placed = PlacedSymbol(self, node)
        numbers = [p.get("number")[0] for p in placed._lib_pins()]
        for n in dict.fromkeys(numbers):
            node.append(Node("pin", n, Node("uuid", new_uuid())))
        proj = Node("project", self.project.name)
        for path, r in refs.items():
            proj.append(Node("path", path, Node("reference", r), Node("unit", unit)))
        node.append(Node("instances", proj))
        self._insert_item(node)
        self.junctions_fix(placed.pins().values())
        return placed

    # ---- connections
    def pin_point(self, spec) -> tuple[float, float]:
        """`(x, y)` as given, or `"REF:PIN"` (pin number or unique name, any unit)."""
        if not isinstance(spec, str):
            return _pt(spec)
        ref, sep, pin = spec.partition(":")
        if not sep:
            raise ValueError(f"{spec!r}: a point is (x, y) or 'REF:PIN'")
        units = [s for s in self.symbols() if s.answers_to(ref)]
        if not units:
            for sheet in self.sheet_symbols():
                if sheet.name == ref:
                    return sheet.pin(pin)
            raise KeyError(f"no symbol or sheet {ref} on {self.path.name}")
        errors = []
        for s in units:
            try:
                return s.pin(pin)
            except KeyError as e:
                errors.append(str(e))
        raise KeyError(errors[0])

    def wire(self, *points, via: str = "hv") -> list[Node]:
        """Wires through `points`, with orthogonal bends (`hv`: horizontal first)."""
        if via not in ("hv", "vh"):
            raise ValueError("via must be 'hv' or 'vh'")
        pts = [self.pin_point(p) for p in points]
        if len(pts) < 2:
            raise ValueError("a wire needs two points")
        require_grid(*pts, what="wire point")
        path = [pts[0]]
        for b in pts[1:]:
            a = path[-1]
            if a[0] != b[0] and a[1] != b[1]:
                path.append((b[0], a[1]) if via == "hv" else (a[0], b[1]))
            path.append(b)
        nodes = [self._insert_item(_wire(a, b)) for a, b in zip(path, path[1:]) if a != b]
        self.junctions_fix(path)
        return nodes

    def _segments(self) -> list[tuple]:
        out = []
        for w in self.root.all("wire"):
            xy = w.get("pts").all("xy")
            out.append((_pt(xy[0].values()), _pt(xy[1].values())))
        return out

    def junctions_fix(self, points=None) -> list[Node]:
        """Add a junction wherever three or more wire ends, wire middles or pins meet.

        With `points`, only there — the helpers pass what they just drew, so a
        T the user left open elsewhere stays open.
        """
        segs = self._segments()
        pins = [p for s in self.symbols() for p in s.pins().values()]
        pins += [p for s in self.sheet_symbols() for p in s.pins().values()]
        have = {_pt(j.get("at").values()) for j in self.root.all("junction")}
        candidates = {p for s in segs for p in s} | set(pins)
        if points is not None:
            candidates &= {_pt(p) for p in points}
        added = []
        for p in sorted(candidates):
            if p in have:
                continue
            ends = sum(1 for a, b in segs for q in (a, b) if q == p)
            if ends == 0:
                continue
            count = ends + pins.count(p) + 2 * sum(1 for a, b in segs if _interior(p, a, b))
            if count >= 3:
                added.append(self._insert_item(Node(
                    "junction", Node("at", p[0], p[1]), Node("diameter", 0),
                    Node("color", 0, 0, 0, 0), Node("uuid", new_uuid()))))
        return added

    def label(self, name: str, at, rot: int = 0, kind: str = "local", shape: str = "bidirectional") -> Node:
        """A net label at a point or pin. `kind`: local, global or hierarchical."""
        x, y = self.pin_point(at)
        require_grid((x, y), what=f"label {name}")
        right = rot in (180, 270)
        if kind == "local":
            node = Node("label", name, Node("at", x, y, rot),
                        _font(("right" if right else "left", "bottom")), Node("uuid", new_uuid()))
        elif kind == "global":
            node = Node("global_label", name, Node("shape", Sym(shape)), Node("at", x, y, rot),
                        Node("fields_autoplaced", Sym("yes")), _font(("right" if right else "left",)),
                        Node("uuid", new_uuid()),
                        Node("property", "Intersheetrefs", "${INTERSHEET_REFS}", Node("at", x, y, 0),
                             Node("effects", Node("font", Node("size", 1.27, 1.27)),
                                  Node("justify", Sym("left")), Node("hide", Sym("yes")))))
        elif kind == "hierarchical":
            node = Node("hierarchical_label", name, Node("shape", Sym(shape)), Node("at", x, y, rot),
                        _font(("right" if right else "left",)), Node("uuid", new_uuid()))
        else:
            raise ValueError("kind must be 'local', 'global' or 'hierarchical'")
        return self._insert_item(node)

    def power(self, name: str, at, rot: int = 0) -> PlacedSymbol:
        """A power symbol (`power:<name>`, e.g. GND, +3V3, PWR_FLAG) whose pin sits at `at`."""
        prefix = "#FLG" if name == "PWR_FLAG" else "#PWR"
        paths = self.instance_paths
        if len(paths) > 1:
            first = int(self.next_ref(prefix)[len(prefix):])
            ref = self.next_refs(prefix, {p: first for p in paths})
        else:
            ref = self.next_ref(prefix)
        return self.add_symbol(f"power:{name}", ref, self.pin_point(at), rot=rot)

    def no_connect(self, at) -> Node:
        x, y = self.pin_point(at)
        require_grid((x, y), what="no-connect flag")
        return self._insert_item(Node("no_connect", Node("at", x, y), Node("uuid", new_uuid())))

    # ---- edits
    def move(self, ref: str, at, unit: int | None = None) -> None:
        """Move a symbol (with its fields). Wires stay where they are."""
        require_grid(tuple(at), what=f"{ref} position")
        s = self.symbol(ref, unit)
        dx, dy = at[0] - s.at[0], at[1] - s.at[1]
        for n in [s.node.get("at")] + [p.get("at") for p in s.node.all("property")]:
            n.set(0, round(n[0] + dx, 4))
            n.set(1, round(n[1] + dy, 4))

    def rotate(self, ref: str, rot: int, unit: int | None = None) -> None:
        if rot not in (0, 90, 180, 270):
            raise ValueError(f"rot must be 0, 90, 180 or 270, not {rot}")
        s = self.symbol(ref, unit)
        delta = (rot - s.rot) % 360
        cx, cy = s.at
        a = s.node.get("at")
        if len(a) > 2:
            a.set(2, rot)
        else:
            a.append(rot)
        for p in s.node.all("property"):
            pa = p.get("at")
            dx, dy = rotate_screen(pa[0] - cx, pa[1] - cy, delta)
            angle = ((pa[2] if len(pa) > 2 else 0) + delta) % 180
            p.replace(pa, Node("at", round(cx + dx, 4), round(cy + dy, 4), angle))

    def delete(self, ref: str) -> int:
        """Remove every unit of `ref` (wires and labels stay). Returns how many were removed."""
        doomed = [s for s in self.symbols() if s.ref == ref]
        if not doomed:
            raise KeyError(f"no symbol {ref} on {self.path.name}")
        for s in doomed:
            self.root.remove(s.node)
        used = {s.lib_id for s in self.symbols()}
        for lib in self.lib_symbols.all("symbol"):
            if lib[0] not in used:
                self.lib_symbols.remove(lib)
        return len(doomed)


DOCUMENT_TYPES[".kicad_sch"] = Schematic

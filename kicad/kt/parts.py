"""Library parts: build footprints and symbols, keep them in a private library.

General building blocks only; how a part should look (silk style, pin naming
habits) belongs to the project's own skills.
"""
import math
import tempfile
from pathlib import Path

from kt import cli
from kt.project import DOCUMENT_TYPES, VERSIONS, Document, read_text
from kt.sexpr import Node, Sym, parse

DEFAULT_LAYERS = {"smd": ("F.Cu", "F.Mask", "F.Paste"), "thru_hole": ("*.Cu", "*.Mask"),
                  "np_thru_hole": ("*.Cu", "*.Mask"), "connect": ("F.Cu", "F.Mask")}
PIN_TYPES = ("input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
             "power_in", "power_out", "open_collector", "open_emitter", "no_connect")
PIN_LEN = 2.54
PITCH = 2.54


def _effects(size: float = 1.0, thickness: float | None = 0.15, hide: bool = False) -> Node:
    font = Node("font", Node("size", size, size))
    if thickness is not None:
        font.append(Node("thickness", thickness))
    return Node("effects", font)


def _stroke(width: float) -> Node:
    return Node("stroke", Node("width", width), Node("type", Sym("solid")))


def _floor(v: float, step: float = 0.01) -> float:
    return round(math.floor(round(v / step, 6)) * step, 4)


def _ceil(v: float, step: float = 0.01) -> float:
    return round(math.ceil(round(v / step, 6)) * step, 4)


class Footprint:
    """A footprint built from pads and outlines; `.node()` is the `.kicad_mod` content."""

    def __init__(self, name: str, *, descr: str = "", tags: str = "", attr: str | None = None):
        self.name = name
        self.descr = descr
        self.tags = tags
        self.attr = attr
        self._pads: list[Node] = []
        self._graphics: list[Node] = []
        self._models: list[Node] = []
        self._extent: list[tuple[float, float]] = []

    def pad(self, number: str, kind: str, shape: str, at, size, *, drill=None, layers=None,
            rratio: float = 0.25, rot: float = 0) -> Node:
        if kind not in DEFAULT_LAYERS:
            raise ValueError(f"pad kind must be one of {', '.join(DEFAULT_LAYERS)}")
        if shape not in ("rect", "roundrect", "circle", "oval"):
            raise ValueError("pad shape must be rect, roundrect, circle or oval")
        if kind in ("thru_hole", "np_thru_hole") and drill is None:
            raise ValueError(f"a {kind} pad needs drill=")
        node = Node("pad", str(number), Sym(kind), Sym(shape),
                    Node("at", at[0], at[1], rot) if rot else Node("at", at[0], at[1]),
                    Node("size", size[0], size[1]))
        if drill is not None:
            node.append(Node("drill", drill))
        node.append(Node("layers", *(layers or DEFAULT_LAYERS[kind])))
        if kind == "thru_hole":
            node.append(Node("remove_unused_layers", Sym("no")))
        if shape == "roundrect":
            node.append(Node("roundrect_rratio", rratio))
        self._pads.append(node)
        w, h = (size[1], size[0]) if rot % 180 == 90 else (size[0], size[1])
        self._extent += [(at[0] - w / 2, at[1] - h / 2), (at[0] + w / 2, at[1] + h / 2)]
        return node

    def _graphic(self, node: Node, layer: str, points) -> Node:
        node.append(Node("layer", layer))
        self._graphics.append(node)
        if layer.endswith(".Fab"):
            self._extent += list(points)
        return node

    def line(self, layer: str, a, b, width: float = 0.12) -> Node:
        return self._graphic(Node("fp_line", Node("start", *a), Node("end", *b), _stroke(width)), layer, (a, b))

    def rect(self, layer: str, a, b, width: float = 0.1) -> Node:
        return self._graphic(Node("fp_rect", Node("start", *a), Node("end", *b), _stroke(width),
                                  Node("fill", Sym("no"))), layer, (a, b))

    def circle(self, layer: str, center, r: float, width: float = 0.1) -> Node:
        end = (center[0] + r, center[1])
        return self._graphic(Node("fp_circle", Node("center", *center), Node("end", *end), _stroke(width),
                                  Node("fill", Sym("no"))), layer,
                             ((center[0] - r, center[1] - r), (center[0] + r, center[1] + r)))

    def bbox(self):
        if not self._extent:
            return (-0.5, -0.5, 0.5, 0.5)
        xs = [p[0] for p in self._extent]
        ys = [p[1] for p in self._extent]
        return min(xs), min(ys), max(xs), max(ys)

    def courtyard(self, margin: float = 0.25) -> Node:
        """A rectangle on F.CrtYd around the pads and the F.Fab body, on a 0.01 mm grid."""
        x0, y0, x1, y1 = self.bbox()
        return self.rect("F.CrtYd", (_floor(x0 - margin), _floor(y0 - margin)),
                         (_ceil(x1 + margin), _ceil(y1 + margin)), 0.05)

    def model(self, path: str, offset=(0, 0, 0), scale=(1, 1, 1), rotate=(0, 0, 0)) -> Node:
        node = Node("model", path, Node("offset", Node("xyz", *offset)), Node("scale", Node("xyz", *scale)),
                    Node("rotate", Node("xyz", *rotate)))
        self._models.append(node)
        return node

    def node(self) -> Node:
        x0, y0, x1, y1 = self.bbox()
        attr = self.attr or ("through_hole" if any(str(p[1]) == "thru_hole" for p in self._pads) else "smd")
        fp = Node("footprint", self.name,
                  Node("version", VERSIONS["footprint"]), Node("generator", "kt"),
                  Node("generator_version", "10.0"), Node("layer", "F.Cu"))
        if self.descr:
            fp.append(Node("descr", self.descr))
        if self.tags:
            fp.append(Node("tags", self.tags))
        fp.append(Node("property", "Reference", "REF**", Node("at", 0, round(y0 - 1.2, 4), 0),
                       Node("layer", "F.SilkS"), _effects()))
        fp.append(Node("property", "Value", self.name, Node("at", 0, round(y1 + 1.2, 4), 0),
                       Node("layer", "F.Fab"), _effects()))
        for name, value in (("Datasheet", ""), ("Description", self.descr)):
            fp.append(Node("property", name, value, Node("at", 0, 0, 0), Node("layer", "F.Fab"),
                           Node("hide", Sym("yes")), _effects(1.27, None)))
        fp.append(Node("attr", Sym(attr)))
        fp.append(Node("duplicate_pad_numbers_are_jumpers", Sym("no")))
        for g in self._graphics:
            fp.append(g.copy())
        fab = [g for g in self._graphics if g.get("layer")[0].endswith(".Fab")]
        body_w = (x1 - x0) if not fab else max(
            abs(n[0]) * 2 for g in fab for n in g.walk() if n.head in ("start", "end", "center"))
        size = round(min(1.0, max(0.4, body_w / 4)), 2)
        fp.append(Node("fp_text", Sym("user"), "${REFERENCE}", Node("at", 0, 0, 0), Node("layer", "F.Fab"),
                       _effects(size, round(size * 0.15, 3))))
        for p in self._pads:
            fp.append(p.copy())
        fp.append(Node("embedded_fonts", Sym("no")))
        for m in self._models:
            fp.append(m.copy())
        return fp


class Symbol:
    """A box symbol with pins on its sides; `.node()` is the library `(symbol …)`."""

    def __init__(self, name: str, *, reference: str = "U", value: str | None = None, footprint: str = "",
                 datasheet: str = "", description: str = ""):
        self.name = name
        self.reference = reference
        self.value = value or name
        self.footprint = footprint
        self.datasheet = datasheet
        self.description = description
        self.pins: dict[str, list] = {"left": [], "right": [], "top": [], "bottom": []}

    def pin(self, number: str, name: str, type: str = "passive", side: str = "left") -> None:
        if type not in PIN_TYPES:
            raise ValueError(f"pin type must be one of {', '.join(PIN_TYPES)}")
        if side not in self.pins:
            raise ValueError("side must be left, right, top or bottom")
        self.pins[side].append((str(number), name, type))

    def _size(self) -> tuple[float, float]:
        def text(pins):
            return max((len(n) for _, n, _ in pins), default=0) * 1.27
        rows = max(len(self.pins["left"]), len(self.pins["right"]), 1)
        cols = max(len(self.pins["top"]), len(self.pins["bottom"]), 0)
        # pin names sit inside the box: side names and top/bottom names must not meet
        w = max(text(self.pins["left"]) + text(self.pins["right"]) + (cols + 1) * PITCH, 5.08)
        h = max(text(self.pins["top"]) + text(self.pins["bottom"]) + (rows + 1) * PITCH, 5.08)
        half = lambda v: math.ceil(round(v / 2 / PITCH, 6)) * PITCH
        return half(w), half(h)

    @staticmethod
    def _offsets(n: int) -> list[float]:
        return [round((i - (n - 1) / 2) * PITCH, 4) for i in range(n)]

    def node(self) -> Node:
        hw, hh = self._size()
        name = self.name
        sym = Node("symbol", name, Node("pin_names", Node("offset", 1.016)),
                   Node("exclude_from_sim", Sym("no")), Node("in_bom", Sym("yes")),
                   Node("on_board", Sym("yes")), Node("in_pos_files", Sym("yes")),
                   Node("duplicate_pin_numbers_are_jumpers", Sym("no")))
        top = hh + (PIN_LEN if self.pins["top"] else 0) + 1.27
        bottom = hh + (PIN_LEN if self.pins["bottom"] else 0) + 1.27
        for prop, value, at, hide in (("Reference", self.reference, (-hw, top), False),
                                      ("Value", self.value, (-hw, -bottom), False),
                                      ("Footprint", self.footprint, (0, 0), True),
                                      ("Datasheet", self.datasheet, (0, 0), True),
                                      ("Description", self.description, (0, 0), True)):
            p = Node("property", prop, value, Node("at", at[0], at[1], 0))
            if hide:
                p.append(Node("hide", Sym("yes")))
            eff = _effects(1.27, None)
            if not hide:
                eff.append(Node("justify", Sym("left")))
            p.append(eff)
            sym.append(p)
        body = Node("symbol", f"{name}_0_1",
                    Node("rectangle", Node("start", -hw, hh), Node("end", hw, -hh),
                         Node("stroke", Node("width", 0.254), Node("type", Sym("default"))),
                         Node("fill", Node("type", Sym("background")))))
        sym.append(body)
        pins = Node("symbol", f"{name}_1_1")
        for side, pins_on_side in self.pins.items():
            offs = self._offsets(len(pins_on_side))
            for (number, pname, ptype), off in zip(pins_on_side, offs):
                if side == "left":
                    at = (-hw - PIN_LEN, -off, 0)
                elif side == "right":
                    at = (hw + PIN_LEN, -off, 180)
                elif side == "top":
                    at = (off, hh + PIN_LEN, 270)
                else:
                    at = (off, -hh - PIN_LEN, 90)
                pins.append(Node("pin", Sym(ptype), Sym("line"), Node("at", *at), Node("length", PIN_LEN),
                                 Node("name", pname, _effects(1.27, None)),
                                 Node("number", number, _effects(1.27, None))))
        sym.append(pins)
        sym.append(Node("embedded_fonts", Sym("no")))
        return sym


def _export_check(*args, staged: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="kt-check-") as tmp:
        try:
            cli.run(*args, "-o", tmp, staged, input_file=staged)
        except cli.CliFailed as e:
            raise cli.LoadError(staged, str(e)) from None


class FootprintDoc(Document):
    def check(self, staged: Path):
        _export_check("fp", "export", "svg", "--fp", self.path.stem, staged=staged.parent)
        return None


class SymbolLib(Document):
    """A `.kicad_sym` library file."""

    def __init__(self, project, path, root=None):
        if root is None and not Path(path).exists():
            root = Node("kicad_symbol_lib", Node("version", VERSIONS["kicad_symbol_lib"]),
                        Node("generator", "kt"), Node("generator_version", "10.0"))
        super().__init__(project, path, root)
        self._touched: list[str] = []

    def names(self) -> list[str]:
        return [s[0] for s in self.root.all("symbol")]

    def _find(self, name: str):
        return next((s for s in self.root.all("symbol") if s[0] == name), None)

    def add(self, part, replace: bool = False) -> Node:
        node = part.node() if isinstance(part, Symbol) else part
        old = self._find(node[0])
        if old is not None and not replace:
            raise FileExistsError(f"{node[0]} is already in {self.path.name}; pass replace=True")
        if old is not None:
            self.root.replace(old, node)
        else:
            idx = next((i for i, it in enumerate(self.root.items)
                        if isinstance(it, Node) and it.head == "embedded_fonts"), len(self.root.items))
            self.root.insert(idx, node)
        self._touched.append(node[0])
        return node

    def copy_from(self, lib_id: str, new_name: str, libs=None) -> Node:
        from kt.libs import split_id
        sym = (libs or self.project.libs).symbol(lib_id)
        old = split_id(lib_id)[1]
        sym.set(0, new_name)
        for unit in sym.all("symbol"):
            unit.set(0, new_name + unit[0][len(old):])
        for p in sym.all("property"):
            if p[0] == "Value":
                p.set(1, new_name)
        return self.add(sym)

    def check(self, staged: Path):
        for name in dict.fromkeys(self._touched) or [None]:
            args = ["sym", "export", "svg"] + (["-s", name] if name else [])
            _export_check(*args, staged=staged)
        return None


class FootprintLib:
    """A `.pretty` folder; each footprint is its own file, saved with the project."""

    def __init__(self, project, path):
        self.project = project
        self.path = Path(path) if Path(path).is_absolute() else project.dir / path
        self.path = self.path.resolve()

    def file(self, name: str) -> Path:
        return self.path / f"{name}.kicad_mod"

    def names(self) -> list[str]:
        on_disk = {p.stem for p in self.path.glob("*.kicad_mod")} if self.path.is_dir() else set()
        pending = {d.path.stem for d in self.project.changed() if d.path.parent == self.path}
        return sorted(on_disk | pending)

    def add(self, part, replace: bool = False) -> Node:
        node = part.node() if isinstance(part, Footprint) else part
        path = self.file(node[0])
        loaded = path.resolve() in {p.resolve() for p in self.project._docs}
        if (path.exists() or loaded) and not replace:
            raise FileExistsError(f"{path.name} is already in {self.path.name}; pass replace=True")
        if path.exists() or loaded:
            doc = self.project.document(path)
            doc.root = node
            doc.new = True
        else:
            self.project.document(path, root=node)
        return node

    def copy_from(self, fp_id: str, new_name: str, libs=None) -> Node:
        node = (libs or self.project.libs).footprint(fp_id)
        node.set(0, new_name)
        for p in node.all("property"):
            if p[0] == "Value":
                p.set(1, new_name)
        return self.add(node)


DOCUMENT_TYPES[".kicad_mod"] = FootprintDoc
DOCUMENT_TYPES[".kicad_sym"] = SymbolLib

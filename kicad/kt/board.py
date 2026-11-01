"""Boards: footprints (read, move, rotate, flip, lock), the outline, extra library footprints.

Footprints of schematic parts reach the board through F8 in KiCad; kt places them.
"""
import uuid as _uuid

from kt.geometry import pcb_point
from kt.project import DOCUMENT_TYPES, Document
from kt.sexpr import Node, Sym

LIBRARY_ONLY = ("version", "generator", "generator_version")
ANGLED = ("pad", "property", "fp_text")   # items whose (at …) angle is stored absolute on a board


def _prop(node: Node, name: str):
    for p in node.all("property"):
        if p[0] == name:
            return p
    return None


class PlacedFootprint:
    def __init__(self, board: "Board", node: Node):
        self.board = board
        self.node = node

    def __repr__(self):
        return f"<{self.ref} {self.fp_id} at {self.at} rot {self.rot} {self.side}>"

    @property
    def ref(self) -> str:
        p = _prop(self.node, "Reference")
        return p[1] if p is not None else ""

    @property
    def value(self) -> str:
        p = _prop(self.node, "Value")
        return p[1] if p is not None else ""

    @property
    def fp_id(self) -> str:
        return self.node[0]

    @property
    def at(self) -> tuple[float, float]:
        a = self.node.get("at")
        return (a[0], a[1])

    @property
    def rot(self) -> float:
        a = self.node.get("at")
        return a[2] if len(a) > 2 else 0

    @property
    def side(self) -> str:
        return "bottom" if self.node.get("layer")[0].startswith("B.") else "top"

    @property
    def locked(self) -> bool:
        lk = self.node.get("locked")
        if lk is not None:
            return not lk.values() or str(lk[0]) == "yes"
        return "locked" in [str(v) for v in self.node.values()]

    @property
    def board_only(self) -> bool:
        attr = self.node.get("attr")
        return attr is not None and "board_only" in [str(v) for v in attr.values()]

    def pad_list(self) -> list[tuple[str, float, float, str]]:
        """Every pad: (number, x, y, net) on the board."""
        out = []
        for pad in self.node.all("pad"):
            at = pad.get("at")
            x, y = pcb_point(self.at, self.rot, (at[0], at[1]))
            net = pad.get("net")
            out.append((pad[0], round(x, 4), round(y, 4), net[-1] if net is not None else ""))
        return out

    # ---- placing
    def move(self, x: float, y: float) -> None:
        at = self.node.get("at")
        at.set(0, x)
        at.set(1, y)

    def rotate_to(self, deg: float) -> None:
        """Set the footprint's rotation (degrees, counter-clockwise on screen)."""
        deg = _angle(deg)
        delta = deg - self.rot
        at = self.node.get("at")
        self.node.replace(at, Node("at", at[0], at[1], deg) if deg else Node("at", at[0], at[1]))
        _turn_angles(self.node, delta)

    def flip(self) -> None:
        """Move to the other side, mirrored top-to-bottom about the footprint's origin, as KiCad's flip."""
        at = self.node.get("at")
        new_rot = _angle(-self.rot)
        for item in self.node.walk():
            if item is self.node or item is at:
                continue
            if item.head in ("at", "start", "end", "mid", "center", "xy") and len(item) >= 2                     and isinstance(item[1], (int, float)) and not _in_model(item):
                item.set(1, -item[1] if item[1] else 0)
            elif item.head == "layer" and isinstance(item[0], str):
                item.set(0, _other_side(item[0]))
            elif item.head == "layers":
                for i, v in enumerate(item.values()):
                    if isinstance(v, str) and _other_side(v) != v:
                        item.set(i, _other_side(v))
        for item in self.node.all():
            if item.head in ANGLED:
                a = item.get("at")
                if a is not None and len(a) > 2:
                    a.set(2, _angle(-a[2]))
            if item.head in ("property", "fp_text"):
                _toggle_mirror(item)
        self.node.replace(at, Node("at", at[0], at[1], new_rot) if new_rot else Node("at", at[0], at[1]))

    def lock(self, on: bool = True) -> None:
        lk = self.node.get("locked")
        if on and lk is None:
            self.node.insert(1, Node("locked", Sym("yes")))
        elif on:
            lk.set(0, Sym("yes")) if len(lk) else lk.append(Sym("yes"))
        elif lk is not None:
            self.node.remove(lk)

    def pads(self) -> dict[str, tuple[float, float, str]]:
        """Pad number -> (x, y, net) on the board. Pads sharing a number: the first one."""
        out = {}
        for pad in self.node.all("pad"):
            at = pad.get("at")
            x, y = pcb_point(self.at, self.rot, (at[0], at[1]))
            net = pad.get("net")
            out.setdefault(pad[0], (round(x, 4), round(y, 4), net[-1] if net is not None else ""))
        return out


class Board(Document):
    def footprints(self) -> list[PlacedFootprint]:
        return [PlacedFootprint(self, n) for n in self.root.all("footprint")]

    def footprint(self, ref: str) -> PlacedFootprint:
        for f in self.footprints():
            if f.ref == ref:
                return f
        raise KeyError(f"no footprint {ref} on the board")

    def edge_items(self) -> list[Node]:
        return [g for g in self.root.all() if g.head.startswith("gr_")
                and g.get("layer") is not None and g.get("layer")[0] == "Edge.Cuts"]

    def bbox(self):
        """(x0, y0, x1, y1) of the board outline on Edge.Cuts, or None."""
        pts = []
        for g in self.edge_items():
            for n in g.walk():
                if n.head in ("start", "end", "mid", "center", "xy"):
                    pts.append((n[0], n[1]))
            if g.head == "gr_circle":
                c, e = g.get("center"), g.get("end")
                r = ((c[0] - e[0]) ** 2 + (c[1] - e[1]) ** 2) ** 0.5
                pts += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
        if not pts:
            return None
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


    # ---- outline
    def _clear_outline(self) -> None:
        for g in self.edge_items():
            self.root.remove(g)

    def _insert_graphic(self, node: Node) -> Node:
        idx = next((i for i, it in enumerate(self.root.items)
                    if isinstance(it, Node) and it.head == "embedded_fonts"), len(self.root.items))
        return self.root.insert(idx, node)

    def outline_rect(self, x: float, y: float, w: float, h: float, r: float = 0) -> None:
        """Replace the board outline with a rectangle (corner radius `r`)."""
        if w <= 0 or h <= 0 or r < 0 or 2 * r > min(w, h):
            raise ValueError(f"bad outline {w}×{h} r={r}")
        self._clear_outline()
        x1, y1 = x + w, y + h
        if not r:
            self._insert_graphic(_gr("gr_rect", Node("start", x, y), Node("end", x1, y1), fill=False))
            return
        k = r * (1 - 0.5 ** 0.5)       # arc midpoint inset from the corner
        rd = lambda v: round(v, 6)
        for a, b in (((x + r, y), (x1 - r, y)), ((x1, y + r), (x1, y1 - r)),
                     ((x1 - r, y1), (x + r, y1)), ((x, y1 - r), (x, y + r))):
            self._insert_graphic(_gr("gr_line", Node("start", *a), Node("end", *b)))
        for s, m, e in (((x, y + r), (x + k, y + k), (x + r, y)),
                        ((x1 - r, y), (x1 - k, y + k), (x1, y + r)),
                        ((x1, y1 - r), (x1 - k, y1 - k), (x1 - r, y1)),
                        ((x + r, y1), (x + k, y1 - k), (x, y1 - r))):
            self._insert_graphic(_gr("gr_arc", Node("start", *s), Node("mid", rd(m[0]), rd(m[1])),
                                     Node("end", *e)))

    def outline_polygon(self, points) -> None:
        """Replace the board outline with a closed polygon through `points`."""
        if len(points) < 3:
            raise ValueError("an outline polygon needs three points or more")
        self._clear_outline()
        pts = Node("pts", *(Node("xy", p[0], p[1]) for p in points))
        self._insert_graphic(_gr("gr_poly", pts, fill=False))

    def _insert_footprint(self, node: Node) -> Node:
        items = self.root.items
        last = max((i for i, it in enumerate(items) if isinstance(it, Node) and it.head == "footprint"),
                   default=None)
        if last is None:
            last = next((i - 1 for i, it in enumerate(items) if isinstance(it, Node)
                         and it.head in ("gr_line", "gr_rect", "gr_arc", "gr_circle", "gr_poly", "gr_text",
                                         "segment", "arc", "via", "zone", "embedded_fonts")), len(items) - 1)
        return self.root.insert(last + 1, node)

    def add_footprint(self, fp_id: str, ref: str, at, rot: float = 0, side: str = "top", *,
                      value: str | None = None, board_only: bool = True, libs=None) -> "PlacedFootprint":
        """Place a library footprint with no nets — for mounting holes, fiducials, logos.

        Parts from the schematic arrive through F8 in KiCad, not through here.
        `board_only` marks it "not in schematic" so schematic parity does not flag it.
        """
        if side != "top":
            raise ValueError("add_footprint places on top; flip() it afterwards")
        lib = (libs or self.project.libs).footprint(fp_id)
        node = Node("footprint", fp_id)
        for item in lib.items[1:]:
            if not isinstance(item, Node) or item.head in LIBRARY_ONLY:
                continue
            node.append(item.copy())
        layer_at = next(i for i, it in enumerate(node.items) if isinstance(it, Node) and it.head == "layer")
        node.insert(layer_at + 1, Node("uuid", str(_uuid.uuid4())))
        node.insert(layer_at + 2, Node("at", at[0], at[1], rot) if rot else Node("at", at[0], at[1]))
        for p in node.all("property"):
            if p[0] == "Reference":
                p.set(1, ref)
            elif p[0] == "Value" and value is not None:
                p.set(1, value)
        if rot:
            _turn_angles(node, rot)
        if board_only:
            attr = node.get("attr")
            if attr is None:
                node.append(Node("attr", Sym("board_only")))
            elif "board_only" not in [str(v) for v in attr.values()]:
                attr.append(Sym("board_only"))
        self._insert_footprint(node)
        return PlacedFootprint(self, node)


def _angle(a: float):
    a = a % 360
    return int(a) if float(a).is_integer() else round(a, 6)


def _other_side(layer: str) -> str:
    if layer.startswith("F."):
        return "B." + layer[2:]
    if layer.startswith("B."):
        return "F." + layer[2:]
    return layer


def _in_model(node: Node) -> bool:
    n = node.parent
    while n is not None:
        if n.head == "model":
            return True
        n = n.parent
    return False


def _toggle_mirror(item: Node) -> None:
    eff = item.get("effects")
    if eff is None:
        return
    j = eff.get("justify")
    if j is None:
        eff.append(Node("justify", Sym("mirror")))
        return
    vals = [str(v) for v in j.values()]
    if "mirror" in vals:
        if len(vals) == 1:
            eff.remove(j)
        else:
            eff.replace(j, Node("justify", *(Sym(v) for v in vals if v != "mirror")))
    else:
        j.append(Sym("mirror"))


def _gr(head: str, *geometry, fill: bool | None = None) -> Node:
    node = Node(head, *geometry, Node("stroke", Node("width", 0.05), Node("type", Sym("default"))))
    if fill is not None:
        node.append(Node("fill", Sym("yes" if fill else "no")))
    node.append(Node("layer", "Edge.Cuts"))
    node.append(Node("uuid", str(_uuid.uuid4())))
    return node


def _turn_angles(fp: Node, delta: float) -> None:
    """Add `delta` to the stored angle of pads and texts (KiCad keeps them absolute)."""
    for item in fp.all():
        if item.head not in ANGLED:
            continue
        at = item.get("at")
        if at is None:
            continue
        angle = ((at[2] if len(at) > 2 else 0) + delta) % 360
        angle = int(angle) if float(angle).is_integer() else round(angle, 6)
        item.replace(at, Node("at", at[0], at[1], angle) if angle else Node("at", at[0], at[1]))


DOCUMENT_TYPES[".kicad_pcb"] = Board

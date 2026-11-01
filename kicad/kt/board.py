"""Boards: read footprints and the outline.

Placement helpers (move, rotate, flip, outline, adding library footprints) follow in this module.
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

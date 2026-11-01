"""Boards: read footprints and the outline.

Placement helpers (move, rotate, flip, outline, adding library footprints) follow in this module.
"""
from kt.geometry import pcb_point
from kt.project import DOCUMENT_TYPES, Document
from kt.sexpr import Node


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


DOCUMENT_TYPES[".kicad_pcb"] = Board

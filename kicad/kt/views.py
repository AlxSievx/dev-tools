"""Pictures Claude can open: schematics and layer plots as PDF, 3D renders as PNG.

Every picture is made by kicad-cli; if it cannot make one, the call fails.
"""
import re
import tempfile
from pathlib import Path

from kt import cli
from kt.geometry import snap
from kt.sexpr import Node

VIEWS = {
    "top": ["--side", "top"], "bottom": ["--side", "bottom"],
    "front": ["--side", "front"], "back": ["--side", "back"],
    "left": ["--side", "left"], "right": ["--side", "right"],
    "iso": ["--side", "top", "--rotate", "-45,0,45", "--perspective"],
}
FOOTPRINT_LAYERS = "F.Cu,F.Paste,F.Mask,F.Silkscreen,F.Fab,F.Courtyard,B.Cu,Edge.Cuts"


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name)


def _out(out: Path, name: str) -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    return out / name


def _made(path: Path) -> Path:
    if not path.is_file() or path.stat().st_size == 0:
        raise cli.CliFailed(f"kicad-cli reported success but wrote no {path.name}")
    return path


def sch_pdf(project, out: Path, pages: str = "") -> Path:
    path = _out(out, f"{project.name}-schematic{'-p' + _safe(pages) if pages else ''}.pdf")
    args = ["sch", "export", "pdf", "-o", path]
    if pages:
        args += ["--pages", pages]
    cli.run(*args, project.root_sch, input_file=project.root_sch)
    return _made(path)


def pcb_png(project, view: str, out: Path, size=(1600, 1000)) -> Path:
    if view not in VIEWS:
        raise ValueError(f"view must be one of {', '.join(VIEWS)}, not {view!r}")
    path = _out(out, f"{project.name}-pcb-{view}.png")
    cli.run("pcb", "render", *VIEWS[view], "--quality", "high", "--width", size[0], "--height", size[1],
            "--background", "opaque", "-o", path, project.pcb_path, input_file=project.pcb_path)
    return _made(path)


def pcb_layers_pdf(project, layers: str, out: Path) -> Path:
    path = _out(out, f"{project.name}-layers-{_safe(layers)}.pdf")
    cli.run("pcb", "export", "pdf", "--mode-single", "-l", layers, "-o", path, project.pcb_path,
            input_file=project.pcb_path)
    return _made(path)


def footprint_pdf(fp_id: str, out: Path, libs) -> Path:
    """One library footprint on its own, scaled to the page, pads sketched with numbers."""
    from kt.project import new_project
    import kt.board  # noqa: F401
    path = _out(out, f"footprint-{_safe(fp_id)}.pdf")
    with tempfile.TemporaryDirectory(prefix="kt-view-") as tmp:
        p = new_project(Path(tmp), "view")
        board = p.document(p.pcb_path)
        board.add_footprint(fp_id, "REF**", (100, 100), libs=libs)
        p.pcb_path.write_text(board.text(), encoding="utf-8", newline="")
        cli.run("pcb", "export", "pdf", "--mode-single", "-l", FOOTPRINT_LAYERS, "--sp", "--scale", "0",
                "-o", path, p.pcb_path, input_file=p.pcb_path)
    return _made(path)


def _extent(sym: Node) -> tuple[float, float, float, float]:
    xs, ys = [0.0], [0.0]
    for n in sym.walk():
        if n.head in ("at", "start", "end", "xy", "center", "mid") and len(n) >= 2 \
                and isinstance(n[0], (int, float)) and isinstance(n[1], (int, float)):
            xs.append(n[0])
            ys.append(n[1])
    return min(xs), min(ys), max(xs), max(ys)


def symbol_pdf(lib_id: str, out: Path, libs) -> Path:
    """One library symbol (every unit side by side) on a page sized to fit it."""
    from kt.project import new_project
    import kt.schematic  # noqa: F401
    path = _out(out, f"symbol-{_safe(lib_id)}.pdf")
    sym = libs.symbol(lib_id)
    x0, y0, x1, y1 = _extent(sym)
    units = sorted({int(u[0].rsplit("_", 2)[1]) for u in sym.all("symbol")} - {0}) or [1]
    w, h = (x1 - x0) + 20, (y1 - y0) + 20
    with tempfile.TemporaryDirectory(prefix="kt-view-") as tmp:
        p = new_project(Path(tmp), "view")
        sch = p.sch()
        paper = sch.root.get("paper")
        paper.set(0, "User")
        paper.append(round(max(60, w * len(units)), 2))
        paper.append(round(max(50, h), 2))
        for i, unit in enumerate(units):
            cx = snap(10 - x0 + i * w)
            cy = snap(10 + y1)       # library Y up -> sheet Y down
            sch.add_symbol(lib_id, "U1" if not sym.get("power") else "#PWR1", (cx, cy), unit=unit, libs=libs)
        p.root_sch.write_text(sch.text(), encoding="utf-8", newline="")
        cli.run("sch", "export", "pdf", "-e", "-o", path, p.root_sch, input_file=p.root_sch)
    return _made(path)

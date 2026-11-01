"""Build the hand-check project: open it in KiCad and confirm nothing is lost or flagged.

    python kicad/docs/handcheck.py [DIR]        (default %TEMP%\\devtools\\kicad\\handcheck)

Then, after F8 in KiCad's board editor (save, close):

    devtools kicad run kicad/docs/handcheck.py --project DIR      (places the footprints)
"""
import sys
import tempfile
from pathlib import Path

try:
    import kt
except ImportError:  # run directly with python, not through devtools
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import kt

from kt.parts import Footprint, Symbol


def sot23() -> Footprint:
    fp = Footprint("SOT-23_handcheck", descr="SOT-23, built by kt for the hand check", tags="SOT-23")
    for number, at in (("1", (-1.1375, -0.95)), ("2", (-1.1375, 0.95)), ("3", (1.1375, 0))):
        fp.pad(number, "smd", "roundrect", at, (1.325, 0.6))
    fp.rect("F.Fab", (-0.65, -1.45), (0.65, 1.45), 0.1)
    fp.line("F.SilkS", (-0.65, -1.56), (0.65, -1.56), 0.12)
    fp.line("F.SilkS", (-0.65, 1.56), (0.65, 1.56), 0.12)
    fp.courtyard(0.25)
    fp.model("${KICAD10_3DMODEL_DIR}/Package_TO_SOT_SMD.3dshapes/SOT-23.step")
    return fp


def ldo3() -> Symbol:
    s = Symbol("LDO3_handcheck", reference="U", footprint="Mine:SOT-23_handcheck",
               description="3-pin regulator built by kt for the hand check")
    s.pin("1", "GND", "power_in", "bottom")
    s.pin("2", "VO", "power_out", "right")
    s.pin("3", "VI", "power_in", "left")
    return s


def build(dir: Path) -> "kt.Project":
    p = kt.new_project(dir, "handcheck")
    fp_lib = p.footprint_lib("Mine.pretty")
    fp_lib.add(sot23())
    sym_lib = p.symbol_lib("Mine.kicad_sym")
    sym_lib.add(ldo3())
    kt.register_library("footprint", "Mine", fp_lib.path, "project", p)
    kt.register_library("symbol", "Mine", sym_lib.path, "project", p)
    p.save()

    p = kt.Project(dir)
    sch = p.sch()
    sch.add_symbol("Connector:Conn_01x02_Pin", "J1", (25.4, 50.8),
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    sch.add_symbol("Mine:LDO3_handcheck", "U1", (76.2, 50.8))
    sch.add_symbol("Device:C", "C1", (55.88, 58.42), value="10u", footprint="Capacitor_SMD:C_0805_2012Metric")
    sch.add_symbol("Device:C", "C2", (101.6, 58.42), value="10u", footprint="Capacitor_SMD:C_0805_2012Metric")
    sch.add_symbol("Device:R", "R1", (119.38, 58.42), value="330", footprint="Resistor_SMD:R_0603_1608Metric")
    sch.add_symbol("Device:LED", "D1", (119.38, 73.66), rot=90, footprint="LED_SMD:LED_0603_1608Metric")
    vi, vo = sch.pin_point("U1:VI"), sch.pin_point("U1:VO")
    sch.wire("J1:1", (55.88, vi[1]), vi)
    sch.wire("C1:1", (55.88, vi[1]))
    sch.wire(vo, (101.6, vo[1]), (119.38, vo[1]), "R1:1")
    sch.wire("C2:1", (101.6, vo[1]))
    sch.wire("R1:2", "D1:A")
    sch.power("+5V", (45.72, vi[1] - 5.08))
    sch.wire((45.72, vi[1] - 5.08), (45.72, vi[1]))
    sch.power("PWR_FLAG", (40.64, vi[1] - 5.08))
    sch.wire((40.64, vi[1] - 5.08), (40.64, vi[1]))
    sch.power("+3V3", (111.76, vo[1] - 5.08))
    sch.wire((111.76, vo[1] - 5.08), (111.76, vo[1]))
    grounds = []
    for pin in ("J1:2", "U1:GND", "C1:2", "C2:2", "D1:K"):
        x, y = sch.pin_point(pin)
        grounds.append(sch.power("GND", (x, y + 7.62)))
        sch.wire((x, y), grounds[-1].pins()["1"])
    gx, gy = grounds[0].pins()["1"]
    sch.power("PWR_FLAG", (gx - 5.08, gy))
    sch.wire((gx - 5.08, gy), (gx, gy))

    board = p.document(p.pcb_path)
    board.outline_rect(0, 0, 60, 40, r=2)
    for i, (x, y) in enumerate([(4, 4), (56, 4), (4, 36), (56, 36)], 1):
        board.add_footprint("MountingHole:MountingHole_3.2mm_M3", f"H{i}", (x, y))
    p.save()
    return p


def place(project) -> None:
    """After F8: put every footprint from the schematic in a row inside the outline."""
    board = project.document(project.pcb_path)
    parts = [f for f in board.footprints() if not f.board_only]
    if not parts:
        raise SystemExit("no schematic footprints on the board yet — press F8 in KiCad's board editor first")
    x = 10.0
    for f in sorted(parts, key=lambda f: f.ref):
        f.move(x, 20.0)
        x += 7.0
    print(f"placed {len(parts)} footprints")


if __name__ == "__main__":
    if "project" in globals():          # devtools kicad run … --project DIR
        place(project)  # noqa: F821
    else:
        target = Path(sys.argv[1]) if len(sys.argv) > 1 else \
            Path(tempfile.gettempdir()) / "devtools" / "kicad" / "handcheck"
        if target.exists() and any(target.iterdir()):
            raise SystemExit(f"{target} is not empty; remove it or name another folder")
        p = build(target)
        print(kt.info(p))

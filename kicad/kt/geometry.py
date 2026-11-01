"""Coordinates. Millimetres; schematic and board Y point down, symbol-library Y points up.

Angles in KiCad files are counter-clockwise as seen on screen.
"""
import math

GRID = 1.27   # schematic connection grid, mm (50 mil)


def on_grid(x: float, y: float, grid: float = GRID) -> bool:
    return all(abs(v / grid - round(v / grid)) < 1e-4 for v in (x, y))


def require_grid(*points, what: str = "point") -> None:
    for p in points:
        if not on_grid(*p):
            raise ValueError(f"{what} {p} is off KiCad's {GRID} mm schematic grid "
                             f"(use multiples of {GRID} mm so pins and wires connect)")


def snap(v: float, grid: float = GRID) -> float:
    return round(round(v / grid) * grid, 4)


def rotate_screen(x: float, y: float, deg: float) -> tuple[float, float]:
    """Rotate a vector counter-clockwise on screen (Y down) by `deg`."""
    r = math.radians(deg)
    c, s = round(math.cos(r), 12), round(math.sin(r), 12)
    return x * c + y * s, -x * s + y * c


def sch_transform(at, rot: int = 0, mirror: str | None = None):
    """A function mapping a symbol-library point to a sheet point for a placed symbol.

    Library Y is flipped to screen Y, then the symbol is rotated, then mirrored
    on the sheet (`x`: about the horizontal axis, `y`: about the vertical axis).
    The order is KiCad's: checked against its netlist for every rotation and mirror.
    """
    ax, ay = at

    def f(px: float, py: float) -> tuple[float, float]:
        x, y = rotate_screen(px, -py, rot)
        if mirror == "x":
            y = -y
        elif mirror == "y":
            x = -x
        return round(ax + x, 4), round(ay + y, 4)
    return f


def pcb_point(at, rot: float, local) -> tuple[float, float]:
    """Footprint-local point -> board point for a footprint at `at` turned by `rot`."""
    x, y = rotate_screen(local[0], local[1], rot)
    return round(at[0] + x, 6), round(at[1] + y, 6)

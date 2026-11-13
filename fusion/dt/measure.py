"""Measure what bounding boxes miss: holes and bosses, gaps, clashes, mass.

Bodies are found by name. Bodies inside occurrences come back as proxies, so
every position is in assembly (root) coordinates. Everything reports mm.
"""
import math

import adsk.core
import adsk.fusion

from .core import _design, mm

CLASH_LIMIT = 12          # info() checks clashes only up to this many visible bodies
AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}


def label(body: adsk.fusion.BRepBody) -> str:
    """"Body1" for a root body, "Comp1:1/Body1" for a body in an occurrence."""
    occ = body.assemblyContext
    return f"{occ.fullPathName}/{body.name}" if occ else body.name


def _all(design) -> list[tuple[set[str], adsk.fusion.BRepBody]]:
    root = design.rootComponent
    found = [({b.name, f"{root.name}/{b.name}"}, b) for b in root.bRepBodies]
    for occ in root.allOccurrences:
        for b in occ.bRepBodies:
            found.append(({b.name, f"{occ.component.name}/{b.name}", label(b)}, b))
    return found


def body(name, design=None) -> adsk.fusion.BRepBody:
    """A body by name, "component/body" or "occurrence/body"; a body passes through."""
    if not isinstance(name, str):
        return name
    found = _all(_design(design))
    hits = [b for names, b in found if name in names]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise ValueError(f"{name!r} names {len(hits)} bodies: {', '.join(label(b) for b in hits)}")
    known = [label(b) for _, b in found]
    raise KeyError(f"no body {name!r}; bodies: {', '.join(known[:20])}"
                   + (f", … {len(known) - 20} more" if len(known) > 20 else ""))


def mass(name, design=None) -> dict:
    """Mass (g, from the body's material), volume, area, centre of mass (mm)."""
    p = body(name, design).physicalProperties
    c = p.centerOfMass
    return {"mass_g": round(p.mass * 1000.0, 3), "volume_mm3": round(p.volume * 1000.0, 3),
            "area_mm2": round(p.area * 100.0, 3), "com_mm": tuple(round(mm(v), 3) for v in (c.x, c.y, c.z))}


def gap(a, b, design=None) -> float:
    """Smallest distance between two bodies (or faces, edges, points) in mm; 0 when they touch or overlap."""
    result = adsk.core.Application.get().measureManager.measureMinimumDistance(
        body(a, design), body(b, design))
    return round(mm(result.value), 4)


def clashes(names=None, design=None) -> list[tuple[str, str, float]]:
    """Pairs of bodies that overlap, with the shared volume in mm³. Default: every visible body."""
    design = _design(design)
    bodies = [body(n, design) for n in names] if names else [b for _, b in _all(design) if b.isVisible]
    if len(bodies) < 2:
        return []
    group = adsk.core.ObjectCollection.create()
    for b in bodies:
        group.add(b)
    results = design.analyzeInterference(design.createInterferenceInput(group))
    return [(label(r.entityOne), label(r.entityTwo), round(r.interferenceBody.volume * 1000.0, 3))
            for r in results]


def _axis_name(v) -> str | tuple:
    for name, (x, y, z) in AXES.items():
        if abs(v.x * x + v.y * y + v.z * z) > 0.99999:
            return name
    return tuple(round(c, 4) for c in (v.x, v.y, v.z))


def holes(name, design=None) -> list[dict]:
    """Cylindrical faces grouped by axis and diameter.

    kind: "hole" / "boss" for a full turn, "inner arc" / "outer arc" for part of
    one (fillets, slots). axis: "x"/"y"/"z" or a direction. at_mm: where the axis
    meets the plane through the origin across it ((y, z) for an x axis, (x, z)
    for y, (x, y) for z; the full point otherwise). span_mm: from–to along the axis.
    """
    groups: dict[tuple, dict] = {}
    for face in body(name, design).faces:
        g = face.geometry
        if g.surfaceType != adsk.core.SurfaceTypes.CylinderSurfaceType:
            continue
        axis, origin = g.axis, g.origin
        axis.normalize()
        big = max((axis.x, axis.y, axis.z), key=abs)
        if big < 0:
            axis.scaleBy(-1)
        p = face.pointOnFace
        op = origin.vectorTo(p)
        along = op.dotProduct(axis)
        radial = adsk.core.Vector3D.create(op.x - axis.x * along, op.y - axis.y * along, op.z - axis.z * along)
        concave = face.evaluator.getNormalAtPoint(p)[1].dotProduct(radial) < 0
        o_along = origin.x * axis.x + origin.y * axis.y + origin.z * axis.z
        foot = (origin.x - axis.x * o_along, origin.y - axis.y * o_along, origin.z - axis.z * o_along)
        ts = [v.geometry.x * axis.x + v.geometry.y * axis.y + v.geometry.z * axis.z for v in face.vertices]
        if not ts:
            continue
        key = (concave, round(mm(g.radius), 3), _axis_name(axis), tuple(round(mm(c), 3) for c in foot))
        grp = groups.setdefault(key, {"lo": min(ts), "hi": max(ts), "area": 0.0, "faces": 0})
        grp["lo"], grp["hi"] = min(grp["lo"], *ts), max(grp["hi"], *ts)
        grp["area"] += face.area
        grp["faces"] += 1

    rows = []
    for (concave, r_mm, axis, foot), grp in groups.items():
        length = grp["hi"] - grp["lo"]
        sweep = math.degrees(grp["area"] / (r_mm / 10.0 * length)) if length > 1e-9 else 0.0
        full = sweep > 359.0
        kind = ("hole" if concave else "boss") if full else ("inner arc" if concave else "outer arc")
        drop = {"x": 0, "y": 1, "z": 2}.get(axis) if isinstance(axis, str) else None
        at = tuple(c for i, c in enumerate(foot) if i != drop)
        rows.append({"kind": kind, "d_mm": round(2 * r_mm, 3), "axis": axis, "at_mm": at,
                     "span_mm": (round(mm(grp["lo"]), 3), round(mm(grp["hi"]), 3)),
                     "sweep_deg": round(min(sweep, 360.0), 1)})
    order = {"hole": 0, "boss": 1, "inner arc": 2, "outer arc": 3}
    rows.sort(key=lambda r: (order[r["kind"]], r["d_mm"], str(r["axis"]), r["at_mm"]))
    return rows


def _fmt(t) -> str:
    return ", ".join(f"{v:g}" for v in t)


def inspect(name, design=None, max_rows: int = 30) -> str:
    """One body in a few lines: size and place, mass, faces by type, holes and bosses."""
    b = body(name, design)
    box = b.boundingBox
    lo = (box.minPoint.x, box.minPoint.y, box.minPoint.z)
    hi = (box.maxPoint.x, box.maxPoint.y, box.maxPoint.z)
    m = mass(b)
    kinds: dict[str, int] = {}
    for f in b.faces:
        t = f.geometry.surfaceType
        kinds[_SURFACES.get(t, "other")] = kinds.get(_SURFACES.get(t, "other"), 0) + 1
    lines = [
        f"body      {label(b)}" + ("" if b.isSolid else " · not solid") + ("" if b.isVisible else " · hidden"),
        f"bbox      {' × '.join(f'{mm(h - l):g}' for l, h in zip(lo, hi))} mm, "
        f"from ({_fmt(round(mm(v), 3) for v in lo)}) to ({_fmt(round(mm(v), 3) for v in hi)})",
        f"volume    {m['volume_mm3']:g} mm³ · area {m['area_mm2']:g} mm² · mass {m['mass_g']:g} g"
        f" ({b.material.name if b.material else 'no material'}) · centre of mass ({_fmt(m['com_mm'])})",
        f"faces     {b.faces.count}: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        + f" · {b.edges.count} edges · {b.shells.count} shell(s)",
    ]
    rows = holes(b)
    lines.append(f"cylinders {len(rows)}" + (" (kind ⌀ axis at span)" if rows else ""))
    for r in rows[:max_rows]:
        a = r["axis"] if isinstance(r["axis"], str) else f"({_fmt(r['axis'])})"
        sweep = "" if r["kind"] in ("hole", "boss") else f" · {r['sweep_deg']:g}°"
        lines.append(f"  {r['kind']} ⌀{r['d_mm']:g}  {a}  at ({_fmt(r['at_mm'])})  "
                     f"{a if isinstance(r['axis'], str) else 't'} {r['span_mm'][0]:g}..{r['span_mm'][1]:g}{sweep}")
    if len(rows) > max_rows:
        lines.append(f"  … {len(rows) - max_rows} more, see dt.holes({label(b)!r})")
    return "\n".join(lines)


_S = adsk.core.SurfaceTypes
_SURFACES = {_S.PlaneSurfaceType: "planar", _S.CylinderSurfaceType: "cylindrical",
             _S.ConeSurfaceType: "conical", _S.SphereSurfaceType: "spherical",
             _S.TorusSurfaceType: "toroidal", _S.EllipticalCylinderSurfaceType: "elliptical cylinder",
             _S.EllipticalConeSurfaceType: "elliptical cone", _S.NurbsSurfaceType: "freeform"}

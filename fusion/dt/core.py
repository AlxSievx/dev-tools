"""Read and change the active design in numbers. Fusion stores lengths in cm;
everything here reports mm."""
import adsk.core
import adsk.fusion

MAX_ROWS = 40


def _design(design=None) -> adsk.fusion.Design:
    if design:
        return design
    doc = adsk.core.Application.get().activeDocument
    if not doc:
        raise RuntimeError("no document is open")
    design = adsk.fusion.Design.cast(doc.products.itemByProductType("DesignProductType"))
    if not design:
        raise RuntimeError(f"document {doc.name!r} has no design")
    return design


def mm(v_cm: float) -> float:
    return v_cm * 10.0


def cm(v_mm: float) -> float:
    return v_mm / 10.0


def bodies(design=None) -> list[dict]:
    """Every body of every component: volume in mm³, bounding box size (dx, dy, dz) and
    its low corner in mm, both in the component's own coordinates."""
    rows = []
    for comp in _design(design).allComponents:
        for body in comp.bRepBodies:
            box = body.boundingBox
            rows.append({
                "component": comp.name,
                "body": body.name,
                "volume_mm3": round(body.volume * 1000.0, 4),
                "bbox_mm": tuple(round(mm(hi - lo), 4) for lo, hi in (
                    (box.minPoint.x, box.maxPoint.x), (box.minPoint.y, box.maxPoint.y),
                    (box.minPoint.z, box.maxPoint.z))),
                "min_mm": tuple(round(mm(v), 4) for v in (box.minPoint.x, box.minPoint.y, box.minPoint.z)),
            })
    return rows


def params(design=None) -> dict[str, str]:
    """User parameters: name → expression."""
    return {p.name: p.expression for p in _design(design).userParameters}


def set_params(values: dict[str, str], design=None) -> None:
    """Set user parameters by expression, e.g. {"w": "30 mm"}. KeyError on an unknown name."""
    user = _design(design).userParameters
    for name, expression in values.items():
        p = user.itemByName(name)
        if not p:
            raise KeyError(f"no user parameter {name!r}")
        p.expression = expression


def health(design=None) -> list[str]:
    """Timeline items with an error or warning, as "<name>: <message>"."""
    design = _design(design)
    if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        return []
    healthy = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
    problems = []
    timeline = design.timeline
    for i in range(timeline.count):
        item = timeline.item(i)
        if item.healthState != healthy:
            problems.append(f"{item.name}: {item.errorOrWarningMessage}")
    return problems


def info(design=None) -> str:
    """The design in a few dozen lines: document, bodies, parameters, timeline health."""
    design = _design(design)
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    rows = bodies(design)
    lines = [
        f"document  {design.parentDocument.name} · {'parametric' if parametric else 'direct'} · "
        f"units {design.unitsManager.defaultLengthUnits}",
        f"counts    {design.allComponents.count} components · {len(rows)} bodies"
        + (f" · {design.timeline.count} timeline items" if parametric else ""),
        "bodies    (volume mm³, bbox dx × dy × dz mm, from its low corner)",
    ]
    for r in rows[:MAX_ROWS]:
        dx, dy, dz = r["bbox_mm"]
        x, y, z = r["min_mm"]
        lines.append(f"  {r['component']}/{r['body']}  {r['volume_mm3']:.1f}  {dx:g} × {dy:g} × {dz:g}"
                     f"  from ({x:g}, {y:g}, {z:g})")
    if len(rows) > MAX_ROWS:
        lines.append(f"  … {len(rows) - MAX_ROWS} more, see dt.bodies()")
    user = list(design.userParameters)
    lines.append(f"params    {len(user)}")
    units = design.unitsManager
    for p in user[:MAX_ROWS]:
        value = units.formatInternalValue(p.value, p.unit or "", True)
        lines.append(f"  {p.name} = {p.expression}" + ("" if value == p.expression else f"  → {value}"))
    if len(user) > MAX_ROWS:
        lines.append(f"  … {len(user) - MAX_ROWS} more, see dt.params()")
    problems = health(design)
    lines.append("health    ok" if not problems else f"health    {len(problems)} problems")
    lines.extend(f"  {p}" for p in problems[:MAX_ROWS])
    lines.extend(_clash_lines(design))
    return "\n".join(lines)


def _clash_lines(design) -> list[str]:
    from .measure import CLASH_LIMIT, _all, clashes
    visible = sum(1 for _, b in _all(design) if b.isVisible)
    if visible < 2:
        return []
    if visible > CLASH_LIMIT:
        return [f"clashes   not checked ({visible} visible bodies), see dt.clashes()"]
    found = clashes(design=design)
    return [f"clashes   {len(found) or 'none'}"] + [f"  {a} ∩ {b}  {v:g} mm³" for a, b, v in found]


def trim(count: int, design=None) -> int:
    """Delete the timeline items from position `count` on (what a failed job left
    behind, as its note says). Returns how many were deleted."""
    timeline = _design(design).timeline
    if not 0 <= count <= timeline.count:
        raise ValueError(f"timeline has {timeline.count} items; cannot trim to {count}")
    extra = timeline.count - count
    if extra:
        timeline.markerPosition = count
        timeline.deleteAllAfterMarker()
        timeline.moveToEnd()
    return extra

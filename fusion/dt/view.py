"""Screenshots that show the view they are named for.

`shot()` is a generator: the bridge advances it one step per event, so Fusion
repaints between views. With `isSmoothTransition = False`, fit and refresh, the
camera lands before `saveAsImageFile` (checked on 2705.1.30); the read-back
and the duplicate check still guard against a wrong image.
"""
import hashlib
from pathlib import Path

import adsk.core

# name → (direction from target to eye, up vector)
VIEWS = {
    "front":            ((0, -1, 0), (0, 0, 1)),
    "back":             ((0, 1, 0), (0, 0, 1)),
    "left":             ((-1, 0, 0), (0, 0, 1)),
    "right":            ((1, 0, 0), (0, 0, 1)),
    "top":              ((0, 0, 1), (0, 1, 0)),
    "bottom":           ((0, 0, -1), (0, 1, 0)),
    "iso-top-right":    ((1, -1, 1), (0, 0, 1)),
    "iso-top-left":     ((-1, -1, 1), (0, 0, 1)),
    "iso-bottom-right": ((1, 1, -1), (0, 0, 1)),
    "iso-bottom-left":  ((-1, 1, -1), (0, 0, 1)),
}
CURRENT = "current"
MIN_DOT = 0.999


def _unit(x, y, z) -> adsk.core.Vector3D:
    v = adsk.core.Vector3D.create(x, y, z)
    v.normalize()
    return v


def _orient(vp, name: str) -> None:
    direction, up = VIEWS[name]
    d = _unit(*direction)
    cam = vp.camera
    dist = cam.eye.distanceTo(cam.target)
    t = cam.target
    cam.eye = adsk.core.Point3D.create(t.x + d.x * dist, t.y + d.y * dist, t.z + d.z * dist)
    cam.upVector = adsk.core.Vector3D.create(*up)
    cam.isSmoothTransition = False
    vp.camera = cam
    vp.fit()
    vp.refresh()


def _looks_along(vp, name: str) -> float:
    cam = vp.camera
    v = cam.target.vectorTo(cam.eye)
    v.normalize()
    return v.dotProduct(_unit(*VIEWS[name][0]))


def shot(views: list[str], width: int, height: int, out_dir: str):
    """Write <out_dir>/<view>.png per view; returns the paths. "current" = as on screen."""
    unknown = [v for v in views if v != CURRENT and v not in VIEWS]
    if unknown:
        raise ValueError(f"unknown view(s) {unknown}; known: {CURRENT}, {', '.join(VIEWS)}")
    vp = adsk.core.Application.get().activeViewport
    if not vp:
        raise RuntimeError("no active viewport")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved_cam, saved_style = vp.camera, vp.visualStyle
    paths, digests = [], {}
    try:
        vp.visualStyle = adsk.core.VisualStyles.ShadedWithVisibleEdgesOnlyVisualStyle
        for name in views:
            if name != CURRENT:
                _orient(vp, name)
                yield
                dot = _looks_along(vp, name)
                if dot < MIN_DOT:
                    raise RuntimeError(f"view {name}: camera did not land (direction · wanted = {dot:.4f})")
            vp.refresh()
            path = out / f"{name}.png"
            if not vp.saveAsImageFile(str(path), width, height):
                raise RuntimeError(f"view {name}: Fusion did not save {path}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            twin = digests.get(digest)
            if twin and CURRENT not in (twin, name):
                path.unlink()
                (out / f"{twin}.png").unlink()
                raise RuntimeError(f"views {twin} and {name} produced the same image; both deleted")
            digests[digest] = name
            paths.append(str(path))
            print(f"saved {name}")
    finally:
        saved_cam.isSmoothTransition = False
        vp.camera = saved_cam
        vp.visualStyle = saved_style
        vp.refresh()
    return paths

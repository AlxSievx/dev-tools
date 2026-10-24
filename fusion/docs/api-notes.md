# Fusion API notes

Hard-won knowledge about the Fusion Python API, so that scripts work first
time. Read the section you need; the short card is [`../CLAUDE.md`](../CLAUDE.md).

Taken from `ndoo/fusion360-mcp-bridge` (MIT License, Copyright (c) 2026 Andrew),
adapted to DevToolsBridge. Check entries against the running Fusion after an
update: `devtools fusion exec "app.version"`.

---

## Units

Fusion stores all geometry in **centimetres (cm)**. When calling API methods that accept a `ValueInput`, pass strings like `"3cm"`, `"10mm"`, `"45 deg"` — Fusion converts them. Raw floats are always in cm.

---

## Script structure

A job runs in the bridge's session namespace, which already holds `adsk`,
`app`, `ui`, `design` (the active design, or `None`), `root`
(`design.rootComponent`) and `dt`. Module-level code runs; if the script
defines `run(context)`, it is called afterwards with `None` — so scripts written
for Fusion's own Scripts dialog work unchanged.

```python
import adsk.core, adsk.fusion, math

def run(_context):
    comp = design.activeComponent
    # ... work here ...
    print("done")
```

`print()` is how results come back. Names stay for the next job until
`devtools fusion reset`; `design` and `root` are refreshed before every job,
so do not keep your own reference to the design across documents. If `run`
is a generator, every `yield` hands control back to Fusion (it repaints and
handles events) and the job continues on the next event.

---

## Design mode

Check before scripting anything parametric:

```python
import adsk.fusion
def run(_context):
    mode = design.designType
    if mode == adsk.fusion.DesignTypes.ParametricDesignType:
        print("parametric")
    else:
        print("direct")
```

- **Parametric mode**: features are recorded in the timeline. `TemporaryBRepManager` bodies need a `BaseFeature` wrapper (see below).
- **Direct mode**: operations happen immediately, no timeline. Some parametric APIs are unavailable.

---

## Revolve / profile rules

**The single most common error: profile crossing the revolve axis.**

Fusion's modelling kernel rejects any revolve where the sketch profile has geometry on both sides of the axis — even if the crossing point is just a line endpoint at the origin.

### Rules
1. The profile must lie **entirely on one side** of the revolve axis (x ≥ 0 or x ≤ 0).
2. Profile edges **touching** the axis are fine (the poles of a sphere land on the axis).
3. A closing line that runs **along** the axis is fine.
4. A line that **passes through** the axis (e.g. from `(r,0,0)` to `(-r,0,0)`) is not fine.

### Correct sphere pattern (use this every time)

Revolve on the **XY plane around the Y axis**. Use `addByThreePoints` — it's unambiguous about arc direction.

```python
import adsk.core, adsk.fusion, math

def run(_context):
    comp = design.activeComponent
    r = 3.0  # 3 cm radius → 6 cm diameter

    sk = comp.sketches.add(comp.xYConstructionPlane)

    # Arc: top pole → equator → bottom pole (all x >= 0)
    sk.sketchCurves.sketchArcs.addByThreePoints(
        adsk.core.Point3D.create(0,  r, 0),
        adsk.core.Point3D.create(r,  0, 0),
        adsk.core.Point3D.create(0, -r, 0))

    # Closing line along Y axis (ON the axis — allowed)
    sk.sketchCurves.sketchLines.addByTwoPoints(
        adsk.core.Point3D.create(0, -r, 0),
        adsk.core.Point3D.create(0,  r, 0))

    # Sanity check
    if sk.profiles.count == 0:
        print("Error: no profile formed")
        return

    rev = comp.features.revolveFeatures.createInput(
        sk.profiles.item(0),
        comp.yConstructionAxis,
        adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    rev.setAngleExtent(False, adsk.core.ValueInput.createByString('360 deg'))
    feat = comp.features.revolveFeatures.add(rev)
    print(f"Sphere created: {feat.name}")
```

### Why not `addByCenterStartSweep`?

`addByCenterStartSweep(center, start, sweep_angle)` sweeps in the sketch plane's "positive" rotation direction. The direction is easy to get wrong, and if the start point is on the axis the profile may fail silently. `addByThreePoints` is always unambiguous.

---

## Profile formation checklist

If `sk.profiles.count == 0` after adding sketch curves:

1. **Open contour** — every curve endpoint must connect to exactly one other curve endpoint. Check for gaps.
2. **Duplicate edge** — adding the same line twice creates a zero-area region. Remove duplicates.
3. **Endpoints on revolve axis** — can sometimes prevent profile closure. Shift the arc to avoid placing both endpoints on the axis, or switch to `addByThreePoints`.
4. **Wrong sketch plane** — verify `comp.xYConstructionPlane` vs `xZConstructionPlane` vs `yZConstructionPlane`.

Always add an explicit check before using `profiles.item(0)`:

```python
if sk.profiles.count == 0:
    print(f"No profile — curves added: {sk.sketchCurves.count}")
    return
```

---

## TemporaryBRepManager pattern (complex shapes)

Use this for polyhedra, convex hulls, and anything that can't be built from a single sketch profile. It constructs BRep topology directly without needing Fusion's feature history.

```python
import adsk.core, adsk.fusion

def run(_context):
    tbm = adsk.fusion.TemporaryBRepManager.get()
    # ... build bodies with tbm ...

    if design.designType == adsk.fusion.DesignTypes.ParametricDesignType:
        # Parametric mode: must wrap in a BaseFeature
        base_feat = design.activeComponent.features.baseFeatures.add()
        base_feat.startEdit()
        try:
            design.activeComponent.bRepBodies.add(body, base_feat)
        finally:
            base_feat.finishEdit()
    else:
        # Direct mode: add directly
        design.activeComponent.bRepBodies.add(body)
```

### Half-space intersection (convex polyhedra)

Efficient way to build any convex solid — intersect half-spaces defined by face planes:

```python
def make_convex_solid(tbm, planes_with_normals):
    """
    planes_with_normals: list of (point_on_plane, outward_normal_vector)
    Returns a BRepBody or None.
    """
    # Start with a large box that definitely contains the solid
    box_input = adsk.core.OrientedBoundingBox3D.create(
        adsk.core.Point3D.create(0, 0, 0),
        adsk.core.Vector3D.create(1, 0, 0),
        adsk.core.Vector3D.create(0, 1, 0),
        200, 200, 200)
    solid = tbm.createBox(box_input)

    for pt, normal in planes_with_normals:
        # Build a very large cutting box on the outside of each face
        # (intersect away the outside half-space)
        plane = adsk.core.Plane.create(pt, normal)
        cutter = tbm.createHalfSpace(plane, pt)  # if available
        # Alternative: use boolean difference with a half-space solid
        tbm.booleanOperation(solid, cutter,
                             adsk.fusion.BooleanTypes.IntersectionBooleanType)
    return solid
```

---

## Boolean operations

`booleanOperation` mutates the target body in-place and takes a **single** `BRepBody` (not a collection):

```python
# Correct
tbm.booleanOperation(
    target_body,
    tool_body,
    adsk.fusion.BooleanTypes.UnionBooleanType)

# Wrong — do not pass ObjectCollection
```

Boolean types: `UnionBooleanType`, `IntersectionBooleanType`, `DifferenceBooleanType`.

---

## Checking document state

Before any operation, it's defensive to confirm a design is open:

```python
def run(_context):
    if design is None:
        print("No active design — open or create a Fusion document first")
        return
    comp = design.activeComponent
    print(f"Design: {design.rootComponent.name}, mode: {design.designType}")
    print(f"Bodies: {comp.bRepBodies.count}, Sketches: {comp.sketches.count}")
```

---

## Common construction planes

```python
comp.xYConstructionPlane   # horizontal (Z up)
comp.xZConstructionPlane   # front face (Y up)
comp.yZConstructionPlane   # side face (X up)
comp.xConstructionAxis     # X axis
comp.yConstructionAxis     # Y axis
comp.zConstructionAxis     # Z axis
```

---

---

## Viewport and camera

Use `devtools fusion shot` or `dt.shot()` rather than scripting captures. If you
must move the camera yourself: `vp.camera` returns a **copy**; change it, set
`cam.isSmoothTransition = False` (otherwise the camera animates and a capture
right after shows the old view), assign it back, then `vp.fit()` and
`vp.refresh()`. `vp.saveAsImageFile(path, w, h)` writes a PNG
(`saveAsImageFileWithOptions` takes a `SaveImageFileOptions` since 2705.x).
Checked on 2705.1.30.

---

## Gotchas found on real projects

Collected on the Crawler project, Fusion 2705.x, Personal licence.

- **A UI command that opens a dialog freezes the bridge.** `CommandDefinition.execute()`
  runs asynchronously; if it opens a modal, Fusion's main thread waits for a
  human and every job stays queued. If the bridge goes silent while Fusion runs,
  assume a modal is open and ask the user to close it.
- **`save()` can silently do nothing**: it returns True, `isModified` stays True,
  the version never moves. Causes seen: the Personal licence's cap of 10 editable
  documents (check `DataFile.isReadOnly` first), and editing a part that is open
  as the reference of a parent assembly.
- **Editing a referenced part:** close every document first (loop
  `app.documents.item(0).close(False)` — iterating `app.documents` breaks as
  closing one cascades), then `app.documents.open(dataFile, True)`, edit, save;
  then reopen the parents and update their references.
- **References do not auto-update**, and one pass of `getLatestVersion()` is not
  enough (iterating `documentReferences` while updating skips entries): loop
  until none reports `isOutOfDate`, parents last.
- **Version numbers read stale.** `doc.dataFile.versionNumber` can keep the old
  number after a successful save; re-query `project.rootFolder.dataFiles` and use
  `latestVersionNumber` of a freshly fetched `DataFile`.
- **Offline Fusion** (`app.isOffLine`): `rootFolder.dataFiles` raises
  `InternalValidationError`; setting `isOffLine = False` from a script does
  nothing. Ask the user to go online.
- **The sketch Y of `xZConstructionPlane` runs along model −Z**, not +Z.
- `Occurrence.isSuppressed` and `Sketch.referencePlane` can raise; use
  `isLightBulbOn` / skip the plane lookup.
- **Threads:** `ThreadDataQuery.allSizes(type)` returns the tuple directly;
  `createThreadInfo` takes 4 arguments; sizes are `"3.0"` / `"4.0"`, not `"M3"`.
  `ThreadFeatureInput.isModeled = False` gives correct thread data without a helix.
- `occurrences.addNewComponent` also creates an occurrence at the origin; delete
  that one after placing real instances. Deleting a component's **last**
  occurrence removes the component.
- **Material and property names are localized** (a German Fusion has no PLA or
  PETG): copy `ABS-Kunststoff` with `design.materials.addByCopy(src, name)` and set
  `Dichte` (kg/m³). Match properties by name, not by index.
- **Interference volumes:** read `result.interferenceBody.volume`; booleans of the
  result's entities measure nothing, because they come back in their components'
  frames.

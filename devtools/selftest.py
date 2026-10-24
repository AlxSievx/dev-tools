"""`devtools fusion selftest` — the live checks, with Fusion open.

Works only in documents it creates and closes them unsaved. Prints one
`PASS|FAIL <name>` line per check; exit 0 only if all pass.
"""
import hashlib
import io
import tempfile
import time
from pathlib import Path

from devtools.client import Client, Unreachable
from devtools.fusion import follow

PART = '''
import adsk.core, adsk.fusion
st_doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
d = adsk.fusion.Design.cast(app.activeProduct)
r = d.rootComponent
for n, v in (("st_w", "20 mm"), ("st_d", "10 mm"), ("st_h", "5 mm"), ("st_r", "1 mm")):
    d.userParameters.add(n, adsk.core.ValueInput.createByString(v), "mm", "")
sk = r.sketches.add(r.xYConstructionPlane)
lines = sk.sketchCurves.sketchLines.addTwoPointRectangle(
    adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(2, 1, 0))
o = adsk.core.Point3D.create(1, -0.5, 0)
H = adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation
V = adsk.fusion.DimensionOrientations.VerticalDimensionOrientation
sk.sketchDimensions.addDistanceDimension(lines.item(0).startSketchPoint, lines.item(0).endSketchPoint, H, o).parameter.expression = "st_w"
sk.sketchDimensions.addDistanceDimension(lines.item(1).startSketchPoint, lines.item(1).endSketchPoint, V, o).parameter.expression = "st_d"
ext = r.features.extrudeFeatures.addSimple(sk.profiles.item(0), adsk.core.ValueInput.createByString("st_h"),
                                           adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
body = ext.bodies.item(0)
x_edges = adsk.core.ObjectCollection.create()
for e in body.edges:
    a, b = e.startVertex.geometry, e.endVertex.geometry
    if abs(a.y - b.y) < 1e-9 and abs(a.z - b.z) < 1e-9:
        x_edges.add(e)
fi = r.features.filletFeatures.createInput()
fi.edgeSetInputs.addConstantRadiusEdgeSet(x_edges, adsk.core.ValueInput.createByString("st_r"), True)
r.features.filletFeatures.add(fi)
print(x_edges.count, round(dt.bodies(d)[0]["volume_mm3"], 3))
'''

LONG = '''
import time
def run(context):
    t0 = time.time()
    for i in range(9):
        print("tick", i)
        time.sleep(5)
        yield
    print("seconds", round(time.time() - t0))
'''


class Live:
    def __init__(self, client: Client):
        self.client = client
        self.results: list[tuple[str, bool, str]] = []

    def job(self, script: str, wait_cap: float = 120) -> tuple[int, str]:
        code, reply = self.client.request("POST", "/jobs", {"script": script, "label": "selftest"})
        if code != 202:
            return 5, str(reply)
        out = io.StringIO()
        return follow(self.client, reply["id"], wait_cap=wait_cap, out=out), out.getvalue()

    def check(self, name: str, func) -> None:
        try:
            ok, detail = func()
        except Exception as e:  # a check that crashes is a failed check
            ok, detail = False, f"{type(e).__name__}: {e}"
        self.results.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" — {detail}" if detail else ""), flush=True)


def selftest(client: Client, quick: bool = False) -> int:
    t = Live(client)

    def health():
        code, h = client.request("GET", "/health")
        return code == 200 and h.get("ok") is True, f"fusion {h.get('fusion')}"

    def short_run():
        a = t.job("print('hello')")
        b = t.job("6 * 7")
        return a == (0, "hello\n") and b == (0, "42\n"), f"{a} {b}"

    def long_job():
        start = time.monotonic()
        code, out = t.job(LONG)
        took = time.monotonic() - start
        return code == 0 and out.count("tick") == 9 and took > 30, f"{took:.0f} s"

    def error_then_alive():
        bad = t.job("raise ValueError('selftest')")
        good = t.job("'alive'")
        return bad[0] == 1 and "ValueError: selftest" in bad[1] and good == (0, "'alive'\n"), ""

    def namespace():
        t.job("st_keep = 41")
        kept = t.job("st_keep + 1")
        client.request("POST", "/reset", {})
        time.sleep(0.5)
        gone = t.job("'st_keep' in globals()")
        return kept == (0, "42\n") and gone == (0, "False\n"), f"{kept} {gone}"

    def dt_reload():
        before = t.job("id(dt)")[1]
        init = Path(__file__).resolve().parents[1] / "fusion" / "dt" / "__init__.py"
        init.touch()
        after = t.job("id(dt)")[1]
        return before != after, ""

    def doc_switch():
        code, out = t.job(
            "import adsk.core\n"
            "st_a = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)\n"
            "st_b = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)\n"
            "st_a.activate()")
        on_a = t.job("design.parentDocument == st_a")
        t.job("st_b.activate()")
        on_b = t.job("design.parentDocument == st_b")
        t.job("st_a.close(False); st_b.close(False)")
        return on_a == (0, "True\n") and on_b == (0, "True\n"), f"{on_a} {on_b}"

    def part():
        code, out = t.job(PART)
        if code != 0:
            return False, out[-300:]
        edges, v1 = out.split()
        code, out = t.job("dt.set_params({'st_w': '30 mm'}); round(dt.bodies()[0]['volume_mm3'], 3)")
        v2 = float(out.strip().strip("'"))
        delta = v2 - float(v1)
        want = 10 * (50 - 4 * (1 - 3.141592653589793 / 4))   # 10 mm more of the filleted section
        return edges == "4" and abs(delta - want) < 0.01, f"Δ {delta:.3f} mm³, want {want:.3f}"

    def shots():
        out_dir = Path(tempfile.mkdtemp(prefix="devtools-selftest-"))
        code, reply = client.request("POST", "/screenshot", {
            "views": ["iso-top-right", "front", "top", "right"], "width": 640, "height": 400,
            "dir": str(out_dir)})
        out = io.StringIO()
        rc = follow(client, reply["id"], wait_cap=60, out=out)
        files = [Path(p) for p in out.getvalue().splitlines() if p.endswith(".png")]
        digests = {hashlib.sha256(f.read_bytes()).hexdigest() for f in files if f.exists()}
        return rc == 0 and len(files) == 4 and len(digests) == 4, f"{out_dir}"

    def wait_cap_and_cancel():
        code, reply = client.request("POST", "/jobs", {"script": "import time\ndef run(c):\n"
                                                       "    for _ in range(6):\n"
                                                       "        time.sleep(0.5)\n        yield\n"})
        queued_code, queued = client.request("POST", "/jobs", {"script": "print('never')"})
        cancel_code, _ = client.request("DELETE", f"/jobs/{queued['id']}")
        sink = io.StringIO()
        capped = follow(client, reply["id"], wait_cap=1, out=sink)
        done = follow(client, reply["id"], wait_cap=30, out=sink)
        return (capped, done, cancel_code) == (4, 0, 200), f"{capped} {done} {cancel_code}"

    def unreachable():
        try:
            Client(port=1, token=client.token).request("GET", "/health")
        except Unreachable:
            return True, ""
        return False, "no error from port 1"

    def cleanup():
        code, out = t.job("st_doc.close(False) if 'st_doc' in globals() else None\n'closed'")
        return code == 0, ""

    t.check("health", health)
    t.check("short run and exec", short_run)
    if not quick:
        t.check("job past 30 s", long_job)
    t.check("error, then still alive", error_then_alive)
    t.check("namespace persists, reset clears", namespace)
    t.check("dt hot reload", dt_reload)
    t.check("design follows the active document", doc_switch)
    t.check("parametric part in numbers", part)
    t.check("four confirmed screenshots", shots)
    t.check("wait cap, wait again, cancel", wait_cap_and_cancel)
    t.check("unreachable bridge reported", unreachable)
    t.check("cleanup", cleanup)
    failed = [name for name, ok, _ in t.results if not ok]
    print(f"{len(t.results) - len(failed)}/{len(t.results)} passed")
    return 0 if not failed else 1

# kt: sub-sheets, per-instance references, drawing items, project drawing sheet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `kt` what a hierarchical, house-styled KiCad 10 schematic needs: sub-sheets with sheet pins (also reused), one reference per sheet instance, numbering from a start value, text / text boxes / rectangles, title block and paper, the project's drawing sheet, DNP and visible fields, and library lookup that skips disabled libraries.

**Architecture:** New module `kicad/kt/sheets.py` (sheet symbols, child files, instance propagation); additions to `schematic.py` (references, drawing items, title block), `libs.py` (disabled entries, config override) and `project.py` (`.kicad_pro` as a checked JSON document). Every behaviour is proven through `kicad-cli` (netlist, ERC JSON, `upgrade` round trip, SVG export), never through `kt`'s own reading.

**Tech Stack:** Python 3.15 standard library, KiCad 10.0.1 `kicad-cli`, `unittest`.

**Spec:** `C:\SciMo\hardware\uCCxServo\docs\superpowers\specs\2026-11-02-step1-schematic-style-design.md` (section D3; D4 for the disabled-library rule). Repo rules: `C:\dev-tools\CLAUDE.md`.

## Global Constraints

- Standard library only; nothing gets pip-installed.
- No fake results: if `kicad-cli` is missing or fails, fail loudly.
- General tooling only: no SciMo style in `kt` (no "page × 100", no prefixes, no field choices). Style lives in the project.
- KiCad 10.0.1 formats: `.kicad_sch` version `20260306` (`kt.project.VERSIONS`).
- Schematic points on the 1.27 mm grid (`kt.geometry.require_grid`) for symbols, wires, labels, sheet positions, sheet sizes and sheet pins; text, text boxes and rectangles are free.
- Untouched parts of a file stay byte-identical.
- Format details are copied from files KiCad itself wrote: the stock demo `complex_hierarchy` (two instances of `ampli_ht.kicad_sch`, pages 2 and 3, references `RV201` / `RV301`) and `pic_programmer`, after `kicad-cli sch upgrade --force`. New format facts go into `kicad/docs/format-notes.md`.
- Work on branch `kicad-sheets` in `C:\dev-tools`. Commits: short, area prefix (`kicad:`, `doc:`, `treewide:`), **no `Co-Authored-By` trailer**. Ask the user once before the first commit whether per-task commits on the branch are fine; merge to `main` locally only after their OK; the user pushes.
- Tests: `cd C:\dev-tools; python -m unittest discover -s tests -v` (KiCad tests skip without KiCad 10).

## Review Focus

- A sheet file reused for a second instance whose symbols already carry references → every symbol gets a distinct reference for the new instance; the netlist lists both sets (Task 3, `test_reuse_gives_new_instance_refs`).
- `add_symbol` with one string reference on a sheet used twice → `ValueError`, nothing added, instead of silent duplicates (Task 2, `test_str_ref_on_multi_instance_refused`).
- A library nickname that is disabled inside the nested `KiCad` table but registered again on its own (the user's `power` case) → the enabled entry is found (Task 1, `test_enabled_entry_wins_over_disabled_nested`).
- `.kicad_pro` changed while KiCad has the project open (`~<name>.kicad_pro.lck`) → `LockedError`, nothing written (Task 5, `test_drawing_sheet_refused_while_open`).
- German note text with umlauts, quotes and a line break survives KiCad's round trip (Task 4, `test_text_round_trip_unicode`).

---

### Task 1: Library lookup honours `(disabled)`; tests get their own KiCad config

**Files:**
- Modify: `kicad/kt/libs.py` (`config_dir`, `_entries`)
- Modify: `tests/_kicad.py` (test config at import)
- Test: `tests/test_kicad_libs.py`
- Docs: `kicad/CLAUDE.md` (one line under Rules), `kicad/docs/format-notes.md`

**Interfaces:**
- Produces: `kt.libs.config_dir() -> Path` honours env `DEVTOOLS_KICAD_CONFIG` (a folder holding `sym-lib-table`, `fp-lib-table`, optionally `kicad_common.json`); unset → `%APPDATA%\kicad\10.0` as today.
- Produces: `_entries` skips every `(lib …)` that has a `(disabled)` child — a disabled `Table` entry skips its whole nested table. `(hidden)` entries are still found (KiCad only hides them in the chooser).
- Produces (tests): importing `tests/_kicad.py` sets `DEVTOOLS_KICAD_CONFIG` (unless already set) to a temp folder whose `sym-lib-table` / `fp-lib-table` hold one enabled entry `(lib (name "KiCad") (type "Table") (uri "${KICAD10_TEMPLATE_DIR}/sym-lib-table") (options "") (descr ""))` (resp. `fp-lib-table`); removed at exit. Existing tests that use `Device:R` keep working on a machine whose real config disables the stock libraries.

- [ ] **Step 0: Branch** — `git -C C:\dev-tools switch -c kicad-sheets`; run the full suite once and note the baseline (all pass or skip).
- [ ] **Step 1: Write the failing tests** in `tests/test_kicad_libs.py`:

```python
def test_disabled_library_skipped(self):        # project table: Mine (disabled) -> not found
    ...; with self.assertRaises(KeyError): LibTables(p).symbol("Mine:R")

def test_disabled_nested_table_skipped(self):   # global table = one disabled Table entry -> Device:R not found
    ...; with mock.patch.object(libs, "global_table", return_value=table): ...assertRaises(KeyError)

def test_enabled_entry_wins_over_disabled_nested(self):
    # global: (lib KiCad Table … (disabled)) then (lib power KiCad "${KICAD10_SYMBOL_DIR}/power.kicad_sym")
    self.assertTrue(LibTables(p).has_symbol("power:GND"))
    self.assertFalse(LibTables(p).has_symbol("Device:R"))

def test_hidden_library_still_found(self):      # (hidden) -> found

def test_config_dir_override(self):
    with mock.patch.dict(os.environ, {"DEVTOOLS_KICAD_CONFIG": str(tmp)}):
        self.assertEqual(libs.config_dir(), tmp)
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_kicad_libs.py" -v` — Expected: the new disabled tests FAIL (entries still found), override test FAILS.
- [ ] **Step 3: Implement** in `libs.py`: `config_dir()` reads `DEVTOOLS_KICAD_CONFIG`; `_entries` skips `lib.get("disabled") is not None`. Then the `_kicad.py` test config (module level, `atexit` cleanup).
- [ ] **Step 4: Run the full suite** — Expected: all pass/skip, same count as the baseline plus the new tests.
- [ ] **Step 5: Docs** — command card Rules: "Library lookup follows KiCad: disabled libraries are not found; `DEVTOOLS_KICAD_CONFIG` points `kt` at another config folder." Format notes: `(disabled)` / `(hidden)` semantics.
- [ ] **Step 6: Commit** — `kicad: library lookup skips disabled libraries`

---

### Task 2: References per instance, numbering from a start, DNP, visible fields

**Files:**
- Modify: `kicad/kt/schematic.py` (`next_ref`, new `next_refs`, `instance_pages`, `add_symbol`, `symbol`, `power`, `PlacedSymbol`)
- Test: `tests/test_kicad_refs.py` (new)
- Docs: `kicad/CLAUDE.md` (the `kt` block), `kicad/docs/format-notes.md`

**Interfaces:**
- Produces: `Schematic.instance_pages() -> dict[str, str]` — instance path → page number of this sheet file (root: `{"/<root uuid>": "1"}` from `sheet_instances`; sub-sheet: from each parent `(sheet …)` node's `instances`).
- Produces: `Schematic.next_ref(prefix: str, start: int | None = None) -> str` — `start=None`: highest used + 1 (unchanged behaviour); `start=n`: the first number ≥ n not used anywhere in the project (every placed symbol's `Reference` and every `instances … (reference …)`).
- Produces: `Schematic.next_refs(prefix: str, starts: dict[str, int]) -> dict[str, str]` — one reference per instance path, each the first free number ≥ its start, all distinct from each other.
- Produces: `Schematic.add_symbol(lib_id, ref: str | dict[str, str], at, …)` — a `dict` must have exactly this sheet's instance paths as keys and writes one `(path … (reference …))` per instance; a `str` on a sheet with more than one instance raises `ValueError` naming `next_refs`. The `Reference` property holds the first instance's reference (as KiCad writes it).
- Produces: `PlacedSymbol.refs -> dict[str, str]` (instance path → reference); `Schematic.symbol(ref, unit=None)` matches the `Reference` property **or** any instance reference.
- Produces: `Schematic.power(name, at, rot=0)` gives each instance its own `#PWR` / `#FLG` number (distinct, from `next_ref(prefix)` upward).
- Produces: `PlacedSymbol.set_dnp(dnp: bool = True) -> None` (the symbol's `(dnp yes|no)`).
- Produces: `PlacedSymbol.show_field(name: str, visible: bool = True, at: tuple | None = None) -> None` — removes / adds the field's `(hide yes)`; `at` moves the field (default: where it is). `KeyError` for a field the symbol does not have.

- [ ] **Step 1: Write the failing tests** in `tests/test_kicad_refs.py`. Fixture: `copy_demo("complex_hierarchy", d)`, `cli.run("sch", "upgrade", "--force", …)` on both sheets, `p = Project(demo)`, `amp = p.sch("ampli_ht.kicad_sch")`.

```python
def test_instance_pages(self):
    self.assertEqual(sorted(amp.instance_pages().values()), ["2", "3"])
    self.assertEqual(p.sch().instance_pages(), {"/" + p.sch().uuid: "1"})

def test_next_ref_from_start(self):           # RV201 exists in the demo
    self.assertEqual(amp.next_ref("RV", start=201), "RV202")
    self.assertEqual(amp.next_ref("R", start=900), "R900")

def test_next_refs_distinct(self):
    starts = {path: 900 for path in amp.instance_paths}
    self.assertEqual(sorted(amp.next_refs("R", starts).values()), ["R900", "R901"])

def test_dict_refs_reach_netlist(self):
    refs = amp.next_refs("R", {path: int(pg) * 100 + 90 for path, pg in amp.instance_pages().items()})
    amp.add_symbol("Device:R", refs, (25.4, 25.4)); p.save()
    comps = {c.get("ref")[0] for c in cli.netlist(p.root_sch).get("components").all("comp")}
    self.assertTrue({"R290", "R390"} <= comps)

def test_str_ref_on_multi_instance_refused(self):
    before = len(amp.symbols())
    with self.assertRaises(ValueError) as cm: amp.add_symbol("Device:R", "R999", (25.4, 25.4))
    self.assertIn("next_refs", str(cm.exception)); self.assertEqual(len(amp.symbols()), before)

def test_symbol_found_by_instance_ref(self):   # "RV301" is only an instance reference
    self.assertEqual(amp.symbol("RV301").refs[...], "RV301")

def test_power_distinct_per_instance(self):    # save; netlist/ERC: no duplicate #PWR
def test_dnp_round_trip(self):                 # set_dnp(); save; kicad-cli upgrade copy keeps (dnp yes)
def test_show_field(self):                     # new project, Device:R with fields={"Tolerance": "1%"}; show_field("Tolerance"); hide gone; KiCad round trip keeps it
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_kicad_refs.py" -v` — Expected: FAIL (`instance_pages`, `next_refs`, `set_dnp`, `show_field` missing; str ref accepted).
- [ ] **Step 3: Implement** the interfaces above in `schematic.py`. Instance page lookup walks the parent sheets via `sheet_tree`'s data (keep one walk; extend `sheet_tree` or add a sibling helper that also returns each path's page).
- [ ] **Step 4: Run the full suite** — Expected: all pass/skip (existing `test_next_ref`, `test_subsheet_instances` unchanged).
- [ ] **Step 5: Docs** — command card `kt` block: `next_ref("R", start=301)`, `refs = sch.next_refs("R", {...}); sch.add_symbol(..., refs, ...)`, `set_dnp()`, `show_field("Voltage")`; format notes: per-instance references, where pages live.
- [ ] **Step 6: Commit** — `kicad: one reference per sheet instance, numbering from a start`

---

### Task 3: Sub-sheets with sheet pins, reused files

**Files:**
- Create: `kicad/kt/sheets.py` (`Sheet`, sheet-node builder, child-file template, instance propagation)
- Modify: `kicad/kt/schematic.py` (`add_sheet`, `sheet`, `sheet_symbols`, `pin_point`, `junctions_fix`, `_insert_item`)
- Modify: `kicad/kt/project.py` (`Project.next_page`)
- Test: `tests/test_kicad_sheets.py` (new)
- Docs: `kicad/CLAUDE.md`, `kicad/docs/format-notes.md`

**Interfaces:**
- Consumes: `next_ref(prefix, start)`, `instance_pages()`, per-instance `add_symbol` (Task 2).
- Produces: `Schematic.add_sheet(name: str, file: str, at, size, *, pins: list[tuple[str, str, str]] = (), page: str | None = None, ref_start: int | None = None) -> Sheet`
  - `file` relative to this sheet's folder. Missing → a new empty sheet file (KiCad 10 version, `generator "kt"`, new uuid, this sheet's `paper` and `title_block`, empty `lib_symbols`, `embedded_fonts no`; no `sheet_instances`) created through `project.document(path, root=…)` so `save()` checks it.
  - Existing file → a further instance: every symbol on that file gets an instance entry for the new path. References: per prefix, in the order of the symbol's current reference number, the first free numbers from `ref_start` (None → `next_ref(prefix)`), so `R501, R502, C501` with `ref_start=601` become `R601, R602, C601`.
  - `page`: this instance's page number; `None` → `project.next_page()`.
  - `pins`: `(name, shape, side)`, shape ∈ `input|output|bidirectional|tri_state|passive`, side ∈ `left|right|top|bottom`; placed in the given order along their side every 2.54 mm, the first 2.54 mm from the top (left/right) or left (top/bottom) corner.
  - `NotImplementedError` when this sheet itself has more than one instance, or when a reused file contains sheet symbols (not needed this round).
  - `ValueError` for off-grid `at`/`size`, a duplicate sheet name on this sheet, a pin that does not fit on its side, or an unknown shape/side.
- Produces: `class Sheet` in `kt/sheets.py`: `name: str`, `file: str`, `uuid: str`, `at`, `size`, `pins() -> dict[str, tuple[float, float]]`, `pin(name) -> tuple[float, float]`, `add_pin(name: str, shape: str, side: str, offset: float) -> tuple[float, float]` (offset along the side from the corner named above, on grid), `child() -> Schematic`, `pages() -> dict[str, str]` (child instance path → page).
- Produces: `Schematic.sheet(name) -> Sheet`, `Schematic.sheet_symbols() -> list[Sheet]`.
- Produces: `Schematic.pin_point("Name:PIN")` falls back to sheet pins when no symbol has reference `Name`; `junctions_fix` counts sheet pins as connection points.
- Produces: `Project.next_page() -> str` — highest page number in the project + 1.
- Produces: `_insert_item` inserts before `sheet_instances`, or before `embedded_fonts` when a file has no `sheet_instances` (sub-sheets).
- Node layout of `(sheet …)` and `(pin …)` (angle 180 left, 0 right, 90 top, 270 bottom; justify `left`/`right`; `Sheetname` above the box, `Sheetfile` below, font 1.27): copied from the upgraded `complex_hierarchy` / `pic_programmer` files.

- [ ] **Step 1: Write the failing tests** in `tests/test_kicad_sheets.py` (fresh `new_project(d, "t")`; a helper builds root with `Halfbridge_U` → `halfbridge.kicad_sch`, `pins=[("IN", "input", "left"), ("OUT", "output", "right")]`, `page="2"`; in the child: hierarchical labels `IN` (input) and `OUT` (output) wired through one `Device:R` with refs from `next_refs`; root labels `A_U` on `Halfbridge_U:IN`).

```python
def test_new_sheet_file_loads_and_connects(self):
    p.save()
    self.assertTrue((d / "halfbridge.kicad_sch").is_file())
    nets = nets_by_name(p.root_sch)                      # helper as in test_kicad_schematic.py
    self.assertIn(("R201", "1"), nets["A_U"])
    self.assertEqual(cli.erc(p.root_sch).errors, [])

def test_reuse_gives_new_instance_refs(self):
    root.add_sheet("Halfbridge_V", "halfbridge.kicad_sch", (76.2, 25.4), (25.4, 20.32),
                   pins=[("IN", "input", "left"), ("OUT", "output", "right")], page="3", ref_start=301)
    p.save()
    comps = {c.get("ref")[0] for c in cli.netlist(p.root_sch).get("components").all("comp")}
    self.assertTrue({"R201", "R301"} <= comps)
    self.assertEqual(sorted(p.sch("halfbridge.kicad_sch").instance_pages().values()), ["2", "3"])

def test_wire_to_sheet_pin_by_name(self):   # root.wire("Halfbridge_U:OUT", (63.5, 30.48)); netlist: wire end on the OUT net
def test_pin_positions(self):               # left pins at x == at.x, y = at.y + 2.54, + 5.08; right pins at x == at.x + w
def test_next_page(self):                   # "2" on a fresh project; "3" after one sheet
def test_off_grid_and_overflow_refused(self) # (25.0, 25.4) -> ValueError; 10 pins on a 10.16 mm side -> ValueError
def test_nested_reuse_not_implemented(self) # reused file that contains a sheet -> NotImplementedError
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_kicad_sheets.py" -v` — Expected: FAIL (`add_sheet` missing).
- [ ] **Step 3: Implement** `kt/sheets.py` and the `schematic.py` / `project.py` hooks above.
- [ ] **Step 4: Run the full suite** — Expected: all pass/skip.
- [ ] **Step 5: Docs** — command card: `hb = sch.add_sheet("Halfbridge_U", "halfbridge.kicad_sch", (x, y), (w, h), pins=[...], page="5", ref_start=501)`, `sch.wire("Halfbridge_U:IN", ...)`, `hb.child()`; and "Parts on a sheet used several times need one reference per instance (`next_refs`)". Format notes: sheet node, sheet pins, where instance pages live, sub-sheet file tail.
- [ ] **Step 6: Commit** — `kicad: sub-sheets with sheet pins, reused sheet files`

---

### Task 4: Text, text boxes, rectangles, title block, paper

**Files:**
- Modify: `kicad/kt/schematic.py`
- Test: `tests/test_kicad_drawing.py` (new)
- Docs: `kicad/CLAUDE.md`, `kicad/docs/format-notes.md`

**Interfaces:**
- Produces: `Schematic.text(text: str, at, *, size: float = 1.27, rot: int = 0, justify: tuple[str, ...] = ("left", "bottom"), bold: bool = False, italic: bool = False) -> Node`
- Produces: `Schematic.text_box(text: str, at, size, *, font: float = 1.27, border: bool = True) -> Node` (`at` = top-left corner, `size` = (w, h))
- Produces: `Schematic.rect(start, end, *, width: float = 0, style: str = "default") -> Node`, style ∈ `default|solid|dash|dot|dash_dot|dash_dot_dot`, no fill.
- Produces: `Schematic.title_block(*, title: str | None = None, date: str | None = None, rev: str | None = None, company: str | None = None, comments: dict[int, str] | None = None) -> dict` — sets the given fields (comments 1–9), creates `(title_block …)` after `paper` if missing, returns all current fields as `{"title": …, "date": …, "rev": …, "company": …, "comments": {n: …}}`.
- Produces: `Schematic.paper -> tuple[str, bool]` (size, portrait) and `Schematic.set_paper(size: str, portrait: bool = False) -> None`, size ∈ `A5 A4 A3 A2 A1 A0 A B C D E USLetter USLegal USLedger`; anything else → `ValueError`.
- Text in node strings: newline written as KiCad writes it (copy from a KiCad-saved note; check what `kt.sexpr.dump` emits for `\n` and `"`).

- [ ] **Step 1: Write the failing tests** — each saves, then runs `kicad-cli sch upgrade --force` on a copy and parses the copy (KiCad's round trip keeps only what it understood):

```python
def test_text_round_trip_unicode(self):
    sch.text('Überschüssige Footprints "JLC"\nzweite Zeile', (30, 30), size=2, bold=True)
    texts = [n[0] for n in roundtrip(p).all("text")]
    self.assertIn('Überschüssige Footprints "JLC"\nzweite Zeile', texts)

def test_text_box_and_rect(self):         # one text_box, one rect(style="dash") survive the round trip with their sizes
def test_title_block(self):
    sch.title_block(title="Halfbridge", rev="1.0", company="SciMo", comments={1: "AS"})
    tb = roundtrip(p).get("title_block")
    self.assertEqual(tb.get("title")[0], "Halfbridge"); self.assertEqual(tb.get("company")[0], "SciMo")
    self.assertEqual([c[1] for c in tb.all("comment") if c[0] == 1], ["AS"])
def test_paper(self):                     # set_paper("A3") -> round trip paper "A3"; set_paper("A7") -> ValueError
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_kicad_drawing.py" -v` — Expected: FAIL.
- [ ] **Step 3: Implement** the interfaces; node layouts copied from KiCad-written files (a `complex_hierarchy` sheet has `title_block`; for `text`, `text_box`, `rectangle` let KiCad write a sample — or take them from `video` / `pic_programmer` if present).
- [ ] **Step 4: Run the full suite** — Expected: all pass/skip.
- [ ] **Step 5: Docs** — command card lines for `text`, `text_box`, `rect`, `title_block`, `set_paper`.
- [ ] **Step 6: Commit** — `kicad: text, text boxes, rectangles, title block and paper`

---

### Task 5: The project's drawing sheet (`.kicad_pro` as a checked document)

**Files:**
- Modify: `kicad/kt/project.py` (`ProjectFile`, `Project.settings`, `Project.set_drawing_sheet`, `_check_all`)
- Test: `tests/test_kicad_project.py`
- Docs: `kicad/CLAUDE.md`, `kicad/docs/format-notes.md`

**Interfaces:**
- Produces: `class ProjectFile(Document)` for `.kicad_pro`: `data: dict` (parsed JSON), `changed` = data differs from what was loaded, `text()` = `json.dumps(data, indent=2) + "\n"`; registered in `DOCUMENT_TYPES`.
- Produces: `Project.settings() -> ProjectFile`; `Project.set_drawing_sheet(path: str) -> None` sets `schematic.page_layout_descr_file` and `pcbnew.page_layout_descr_file` to `path` exactly as given (e.g. `"${KIPRJMOD}/templates/SciMo.kicad_wks"`), creating the parent objects when missing (a `new_project` file is `{}`).
- Produces: `save()` accepts a changed `.kicad_pro`: the staged copy must parse as JSON, and ERC runs on the staged root (so `kicad-cli` loads the project with the new setting). Locks as for every file: `~<name>.kicad_pro.lck` → `LockedError`.

- [ ] **Step 1: Write the failing tests** in `tests/test_kicad_project.py`:

```python
def test_drawing_sheet_used_by_kicad(self):
    # project with one sheet; templates/mark.kicad_wks = stock pagelayout_default.kicad_wks plus a
    # (bitmap …) holding a 4x4 PNG (format: see Task notes)
    p.set_drawing_sheet("${KIPRJMOD}/templates/mark.kicad_wks"); p.save()
    svg = export_svg(p)                  # kicad-cli sch export svg -o tmp root
    self.assertIn("<image", svg)
    # control: without set_drawing_sheet the same export has no "<image"

def test_drawing_sheet_written_as_given(self):
    data = json.loads(p.pro_path.read_text())
    self.assertEqual(data["schematic"]["page_layout_descr_file"], "${KIPRJMOD}/templates/mark.kicad_wks")
    self.assertEqual(data["pcbnew"]["page_layout_descr_file"], "${KIPRJMOD}/templates/mark.kicad_wks")

def test_drawing_sheet_refused_while_open(self):   # create ~t.kicad_pro.lck -> LockedError, file unchanged
```

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_kicad_project.py" -v` — Expected: FAIL.
- [ ] **Step 3: Implement.** For the test's bitmap find the `.kicad_wks` bitmap syntax KiCad 10 reads (try in order, keep the first the SVG export shows as `<image`): `(bitmap (name "") (pos 10 10) (scale 1) (data "<base64 PNG>"))`, then the older `(bitmap (name "") (pos 10 10) (scale 1) (pngdata (data "89 50 4E …") …))`. Record the working form in format notes — the project's drawing sheet depends on it.
- [ ] **Step 4: Run the full suite** — Expected: all pass/skip.
- [ ] **Step 5: Docs** — command card: `project.set_drawing_sheet("${KIPRJMOD}/templates/X.kicad_wks")`; format notes: the two keys, the bitmap syntax.
- [ ] **Step 6: Commit** — `kicad: project drawing sheet`

---

### Task 6: Whole-branch check and hand-over

- [ ] **Step 1:** Full suite — Expected: all pass/skip; report the counts.
- [ ] **Step 2:** `kicad/docs/handcheck.py` still builds; extend it with one reused sheet so the user can open it in KiCad and see two instances with their own references (`python kicad/docs/handcheck.py <dir>`).
- [ ] **Step 3:** Ask the user to open the hand-check project in KiCad (hierarchy navigator: two instances, references `x01…` per page; no ERC errors) — wait for the answer.
- [ ] **Step 4:** After their OK: merge `kicad-sheets` into `main` locally (`git switch main; git merge --no-ff kicad-sheets`, message `treewide: merge kicad sheets`); the user pushes.

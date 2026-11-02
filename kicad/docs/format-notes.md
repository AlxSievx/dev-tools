# KiCad 10 file format — notes for `kt`

Found while building `kt` against KiCad 10.0.1. Read when a `kt` helper is not
enough and a script has to touch nodes directly.

## Versions

| File | `(version …)` KiCad 10.0.1 writes |
|------|-----------------------------------|
| `.kicad_sch` | 20260306 |
| `.kicad_pcb` | 20260206 |
| `.kicad_mod` | 20260206 |
| `.kicad_sym` | 20251024 |

Most stock demos are older; `devtools kicad upgrade` brings a project's sheets
and board up (`kicad-cli sch|pcb upgrade --force`).

## kicad-cli

- Exit code **3** = could not load the file (malformed, unknown token).
- Loading a hierarchy through the root sheet is **lenient**: a broken sub-sheet
  can still give ERC exit 0. So `kt` loads every changed file by itself —
  `kicad-cli sch|pcb|sym|fp upgrade --force` on a copy — before it writes anything.
- Lock files: `~<file name>.lck` beside the open file (confirmed 2026-11-01 with the
  board editor: `~pic_programmer.kicad_pcb.lck`); an open project also has
  `~<name>.kicad_pro.lck`.
- The schematic editor locks only the sheet it opened (as far as known); `kt`
  treats every sheet of the project as open while any sheet lock exists.
- Messages follow KiCad's UI language (German here): read JSON reports, not text.
- `kicad-cli` writes a `.kicad_prl` beside the project it loads.
- No "Update PCB from Schematic" — that stays F8 in the board editor.
- IPC-D-356 (`pcb export ipcd356`) gives every pad's absolute position:
  relative to the board's `aux_axis_origin`, Y up, units 0.0001 inch;
  net names cut to their last 14 characters.

## Library tables

- An entry can carry `(disabled)`: KiCad does not load it; on a `(type "Table")`
  entry the whole nested table is off. `(hidden)`: loaded and usable, only left out
  of the chooser. A nickname may be registered again on its own beside a disabled
  nested table (e.g. `power` while the stock `KiCad` table is disabled).

## Schematic

- Coordinates in mm, Y down. Library symbols: Y up.
- Pin position on the sheet = symbol `at` + transform(pin `at` in the library):
  flip library Y, **rotate** (counter-clockwise on screen), **then mirror**
  (`(mirror x)` flips about the horizontal axis, `(mirror y)` about the vertical).
  Verified against `kicad-cli` netlists for all four rotations × three mirrors.
- The connection point of a pin is its `(at …)`; the pin body extends `length`
  from there toward the symbol.
- A placed symbol: `lib_id`, `at x y rot`, optional `mirror`, `unit`,
  `body_style`, flags (`exclude_from_sim in_bom on_board in_pos_files dnp`),
  `uuid`, properties, one `(pin "n" (uuid …))` per pin of its unit (and unit 0),
  `instances → project "<name>" → path "/<root uuid>[/<sheet uuid>…]" (reference) (unit)`.
- `lib_symbols` holds a copy of every library symbol used, renamed `Lib:Name`;
  its units keep the bare name: `Name_<unit>_<body style>`, unit 0 = common.
- A derived symbol (`extends`) is embedded flattened: the parent's graphics and
  pins, units renamed to the child's name, the child's properties.
- Library-only properties `ki_keywords`, `ki_fp_filters` are not copied to placed symbols.
- Sheets: a `(sheet …)` node names its file in property `Sheetfile`; a symbol on
  that sheet has instance path `<parent path>/<sheet uuid>`.
- Root `sheet_instances` and `embedded_fonts` come last; new items go before them.
- A sheet file used several times (stock demo `complex_hierarchy`): each placed symbol
  has one `(path …)` per instance under `instances`, each with its own `(reference …)`
  (`RV201` / `RV301`); the `Reference` property shows one of them. Power symbols too
  (`#PWR0207` / `#PWR0307`).
- Page numbers: the root's is `sheet_instances → path "/" → page`; a sub-sheet's is in
  the parent's `(sheet …)` node: `instances → project → path "<parent path>" → page`.
- Hidden field: `(hide yes)` right after the property's `(at …)`; shown = no `hide` node.
  DNP: `(dnp yes)` before the symbol's `uuid`.

## Board

- A footprint's `(at x y rot)` is on the board; its pads, texts and graphics are
  in footprint-local coordinates (rotate by `rot`, counter-clockwise on screen, then add `at`).
- Pad and text angles are stored **absolute** (local angle + footprint rotation):
  turning a footprint means adding the change to every pad/property/fp_text angle.
- Pads carry their net by name: `(net "GND")`.
- Locked: `(locked yes)` right after the footprint name.
- Back side (as KiCad's flip): layer `B.Cu`, every local y negated (graphics,
  pads, texts; not the 3D model), rotation negated, `F.*` ↔ `B.*`, texts get
  `(justify mirror)`. Checked with IPC-D-356 pad positions and a bottom render.
- A board outline is any `gr_*` on `Edge.Cuts`; DRC reports `invalid_outline` without one.
- Footprints added on the board only carry `(attr … board_only)` so schematic parity ignores them.
- A library `.kicad_mod` becomes a board footprint by dropping `version` /
  `generator` / `generator_version`, naming it `Lib:Name`, and adding `uuid` and `at` after `layer`.

# KiCad through dev-tools — command card

Claude changes KiCad 10 projects with `devtools kicad …` (shell; `C:\dev-tools\bin`
on PATH). Scripts use the helper library `kt` and edit the project files directly;
`kicad-cli` checks and draws them. There is no live connection to KiCad: the user
opens the same project in KiCad to check and to work on it.

## The loop

1. `devtools kicad locks` — if a file Claude is about to change is open in KiCad,
   **ask the user to save and close that editor, and wait** for the answer
   (the KiCad project window may stay open). Never ask them to skip saving.
2. `devtools kicad info` — the project in numbers. Read it **before** changing anything.
3. Write the change as a **script file** and `devtools kicad run file.py`.
   `kt` and `project` are already loaded. Nothing is written if the script
   raises; changed files are loaded by `kicad-cli` in a temporary copy first,
   and only replace the originals if that load works.
4. `info`, `erc`, `drc` — **measure**. Numbers decide.
5. `devtools kicad shot …` — prints PDF/PNG paths; open them with Read to **look**.
6. Fix and repeat.

After schematic changes and before placing parts: **ask the user to open the board
editor, press F8 (Update PCB from Schematic), save and close.** `info` says
whether board and schematic are in step; `drc` lists parity findings apart.

## Commands

| Command | Does |
|---------|------|
| `run FILE [--project DIR] [--strict]` | run a script; save and check what it changed |
| `exec CODE [--project DIR]` | a line or two; a trailing expression is printed |
| `info [DIR]` | sheets, symbols, nets, footprints, in step with the board, ERC/DRC counts |
| `erc [DIR]` · `drc [DIR]` | full findings, one line each |
| `shot sch [--pages 1,2]` | schematic PDF |
| `shot pcb top bottom iso …` | 3D render PNG (`front back left right` too; `--size WxH`) |
| `shot pcb-layers F.Cu,F.Silkscreen,Edge.Cuts` | 2D layer plot PDF |
| `shot fp LIB:NAME` · `shot sym LIB:NAME` | one library part as PDF |
| `locks [DIR]` | files open in KiCad right now |
| `upgrade [DIR]` | older sheets/board to KiCad 10's format (a whole-file rewrite: commit it on its own) |

`DIR` defaults to the current directory. Exit codes: **0** done · **1** script
failed, rejected by kicad-cli, or errors with `--strict` · **2** kicad-cli
missing (`DEVTOOLS_KICAD_CLI`) or failed · **5** usage · **6** a file to change
is open in KiCad (nothing written). Output is capped at 200 lines; the log path is printed.

## `kt` in a script

```python
sch = project.sch()                          # root sheet; project.sch("power.kicad_sch"), project.sheets()
u1 = sch.add_symbol("Regulator_Linear:AMS1117-3.3", sch.next_ref("U"), (76.2, 50.8),
                    footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2", rot=0, mirror=None, unit=1)
sch.wire("U1:VO", (96.52, 50.8), "C2:1")     # points or "REF:PIN" (number or unique name)
sch.label("SDA", "U2:5", kind="local")       # local | global | hierarchical
sch.power("GND", (76.2, 66.04)); sch.no_connect("U2:7")
sch.move("R1", (63.5, 63.5)); sch.rotate("R1", 90); sch.delete("R9")
sch.symbol("R1").set_field("Value", "4k7"); u1.pins(); u1.pin("VI")

board = project.document(project.pcb_path)
board.outline_rect(0, 0, 60, 40, r=2)        # or outline_polygon([...])
board.footprint("U1").move(20, 15); board.footprint("U1").rotate_to(90)
board.footprint("C3").flip(); board.footprint("J1").lock()
board.add_footprint("MountingHole:MountingHole_3.2mm_M3", "H1", (4, 4))   # board-only parts

lib = project.footprint_lib("Mine.pretty")   # or an absolute path to a private library
fp = kt.Footprint("SOT-23_X"); fp.pad("1", "smd", "roundrect", (-1.1, -0.95), (1.3, 0.6)); fp.courtyard()
lib.add(fp); lib.copy_from("Resistor_SMD:R_0603_1608Metric", "R_0603_mine")
sym = kt.Symbol("MY_IC", footprint="Mine:SOT-23_X"); sym.pin("1", "IN", "input", "left")
project.symbol_lib("Mine.kicad_sym").add(sym)
kt.register_library("footprint", "Mine", lib.path, "project", project)   # or scope "global"
```

- Units are **mm**. Schematic points must sit on the **1.27 mm** grid (`ValueError` otherwise).
- Rotations: schematic 0/90/180/270; board any angle, counter-clockwise on screen.
- Parts from the schematic reach the board only through the user's F8; `add_footprint` is for board-only parts.
- A library registered in a script is visible to lookups from the next run on.
- The global library tables are never written while KiCad runs; `register_library`
  prints the line for the user to add instead.
- Raw nodes: `kt.Node`, `node.get("at")`, `node.set(i, v)`, `node.append(kt.Node(...))`;
  change nodes only through these methods.

## Rules

- Never change a file the user has open in KiCad; ask them to close it.
- Library lookup follows KiCad: libraries marked disabled in a table are not found
  (hidden ones are). `DEVTOOLS_KICAD_CONFIG` points `kt` at another config folder.
- Non-trivial scripts are files kept in the project (e.g. `scripts/`), not `exec` one-liners.
- How a schematic or a part *should* look is the project's own style, not `kt`'s.

File-format details and pitfalls: [`docs/format-notes.md`](docs/format-notes.md).

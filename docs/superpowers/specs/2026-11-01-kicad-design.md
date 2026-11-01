# dev-tools — KiCad side: design

Date 2026-11-01. Status: draft for approval.

## Intent

Claude works on the user's **KiCad 10** projects the way it works in Fusion:
write a script, run it, **measure** the result in numbers, **look** at a
picture, fix. The user opens the same project in KiCad whenever they like to
check what Claude did and to work on it themselves.

Unlike Fusion, KiCad needs **no live bridge** (user, 2026-11-01). Schematics,
boards and libraries are plain-text S-expression files, and `kicad-cli`
(shipped with KiCad) runs ERC, DRC and every export without the GUI. Claude
changes the files; `kicad-cli` checks and draws them.

What Claude does, in the user's order:

1. **Schematic work** — place symbols, wire nets, set values and footprint
   assignments. The result must be a *reasonable* schematic document a person
   can read, not only a correct netlist.
2. **PCB placement** — put footprints on the board, inside an outline.
3. **The user's private library** — add parts to it (footprints first; a part
   usually also needs a symbol and a 3D model reference).

How a schematic *should* look (grouping, label style, sheet structure) is
house style. It goes into skills the user writes later, not into this tooling
(user, 2026-11-01; matches the repo rule "general tooling only").

## Goals

1. A helper library **`kt`** (the KiCad counterpart of `dt`) that reads and
   writes KiCad files faithfully and offers general building blocks for
   schematics, boards and libraries.
2. **`devtools kicad …`** commands: run a script with `kt` loaded, a compact
   numeric readout, ERC/DRC, and pictures Claude can open.
3. **No clobbering**: Claude never writes a file the user has open in KiCad.
4. A short **command card** `kicad/CLAUDE.md` a project imports, like
   `fusion/CLAUDE.md`, with longer format notes read only when needed.
5. Tests that prove `kt`'s edits are what KiCad itself understands, checked
   through `kicad-cli`, not through `kt`'s own reading.

## Non-goals

- No live connection to a running KiCad (no IPC API, no plugin, no `kicad-python`).
- No routing in this round (tracks, vias, zones). No autorouter.
- No schematic → board update by `kt`; the user presses F8 in KiCad.
- No Fusion link in this round (board outline from Fusion, STEP to the
  enclosure) — a later step.
- No schematic layout style rules in `dev-tools` (later skills, in the project
  or user config).
- No use of KiCad's SWIG `pcbnew` module (being phased out, boards only).
- No generation of 3D models; a footprint may *reference* an existing model.
- Gerber/fab output is available through `kicad-cli` but not wrapped yet.

## Anti-goals (a failure even if it works)

- A file KiCad refuses to open, or opens with silently lost content. Every
  write is checked by `kicad-cli` loading it.
- Overwriting the user's unsaved work in KiCad.
- A result that is not real: if `kicad-cli` is missing or fails, the command
  fails loudly with a non-zero exit code. No fallback, no mock.
- Diffs that rewrite the whole file. Untouched parts of a file stay
  byte-identical, so the user's git history stays readable.

## Constraints

- Windows 11; Claude Code runs Git Bash and PowerShell. Host Python 3.15,
  **standard library only**. Nothing is pip-installed, nothing new is installed:
  KiCad 10.0.1 already ships `kicad-cli`.
- `kicad-cli` is at `C:\Program Files\KiCad\10.0\bin\kicad-cli.exe`; an
  environment variable `DEVTOOLS_KICAD_CLI` overrides the path. Not found →
  exit 2 with a clear message.
- KiCad's global library tables list only the stock libraries
  (`%APPDATA%\kicad\10.0\fp-lib-table`, `sym-lib-table`, each a `Table` entry
  pointing into the install's `template/`). The user is setting up a private
  library now; where it lives is not decided, so `kt` takes the library path
  as an argument and can register a library in either the project's table or
  the global one.
- KiCad's UI language is German; file content and `kicad-cli` options are not
  affected, but `kicad-cli` messages may be.
- `kicad-cli` cannot run "Update PCB from Schematic" (F8); see "Schematic → board".

## Approaches considered

1. **Claude edits the text files directly**, `devtools kicad` only checks and
   exports. Almost nothing to build, but every session re-derives pin
   positions under rotation, embedded symbol copies, UUIDs and instance
   paths by hand; one slip corrupts a file the user then opens.
2. **Scripts plus the `kt` helper library** — a stdlib S-expression reader and
   writer with general helpers; `devtools kicad run script.py`. Same loop as
   Fusion; the fiddly details live in tested code. **Chosen by the user
   (2026-11-01).**
3. `kt` for schematics, KiCad's bundled Python with SWIG `pcbnew` for boards —
   two ways of working, one of them on its way out.

## How Claude uses it

1. `devtools kicad info PROJECT` — the project in numbers (see below). Read it
   before changing anything.
2. Claude writes a script and runs `devtools kicad run script.py --project DIR`.
   Inside, `kt` and `project` are already loaded. Changes are collected in
   memory and **written only if the script finishes without error**.
   Before anything replaces a project file, the changed files are written into
   a temporary copy of the project and loaded there by `kicad-cli` (ERC for
   schematics, DRC for boards, an SVG export for library parts). If
   `kicad-cli` cannot load a file, **nothing is written** and the run ends
   with exit 1 ("rejected by kicad-cli"). If it loads, the files are swapped
   in atomically and the ERC/DRC findings are printed; findings fail the run
   only with `--strict`.
3. `devtools kicad info` again, or `erc` / `drc`, to **measure**.
4. `devtools kicad shot …` writes pictures and prints their paths; Claude opens
   them with Read to **look**.
5. The user opens the project in KiCad to check (see "No clobbering" for what
   happens while a file is open).

### Commands

| Command | Does |
|---------|------|
| `info PROJECT` | sheets; symbols (ref, value, lib id, footprint, unconnected pins); nets with their pins; board outline size; footprints (ref, x/y mm, rotation, side); board in step with schematic or not; ERC/DRC counts |
| `run FILE --project DIR` | run a script with `kt` and `project` preloaded; write on success; check |
| `exec CODE --project DIR` | the same for a line or two |
| `erc PROJECT` / `drc PROJECT` | `kicad-cli` check, findings condensed to one line each |
| `shot sch [SHEET]` | schematic as PDF (all sheets, or one) |
| `shot pcb top\|bottom\|iso…` | `kicad-cli pcb render` PNG |
| `shot pcb-layers LAYERS` | 2D layer plot (e.g. `F.Cu,F.Silkscreen,Edge.Cuts`) |
| `shot fp LIB:NAME` / `shot sym LIB:NAME` | one library part as SVG→ picture (see "Pictures") |
| `locks PROJECT` | which project files are open in KiCad right now |

`PROJECT` is a directory or a `.kicad_pro` file; it defaults to the current
directory. Exit codes follow the Fusion side where they mean the same:
**0** done · **1** script failed (traceback) or check found errors when
`--strict` is given · **2** `kicad-cli` missing or failed to run · **5** usage ·
**6** a file the script would write is open in KiCad (nothing written).
Output is capped like Fusion's; the full output goes to a log file whose
path is printed.

### Pictures

Claude's Read tool opens PNG and PDF, not SVG. Schematics go out as PDF
(`kicad-cli sch export pdf`), boards as PNG (`pcb render`). For 2D layer plots
and single library parts, `kicad-cli` offers SVG and PDF; the builder picks
PDF, or another route to PNG that needs no install. A picture is never
substituted: if the export fails, the command fails.

## No clobbering (decided)

KiCad writes a lock file beside a file it has open in an editor. Before
writing, `kt` checks the lock for every file it is about to write; if one is
locked, nothing is written and the run ends with exit 6, naming the file and
asking the user to save and close it in KiCad. Writes are atomic (temporary
file, then rename). `kt` never writes a file the script did not change.

The exact lock file naming in KiCad 10 is for the builder to confirm by
opening a file in KiCad once.

**Strict (user, 2026-11-01).** KiCad holds the lock as long as the editor has
the file open, saved or not, and from outside Claude cannot tell whether there
is unsaved work. So Claude **asks the user to close the file before it starts
editing**: before a run, Claude checks `devtools kicad locks`; if a file it
means to change is open, it asks the user to save and close that editor (the
KiCad project window may stay open) and waits for the answer. Exit 6 remains
the safety net if a file was opened in between. Writing while open plus
File → Revert was considered and rejected.

**Library tables** have no lock files, and a running KiCad keeps them in
memory and may write them back. `kt` writes a project's tables only under the
same rule as its sheets and board, and never writes the global tables while
any KiCad process is running; then it prints the line for the user to add
instead (Claude's call).

## File versions (Claude's call)

`kt` edits only files whose format version is the one KiCad 10.0 writes. An
older file is refused with a clear message; `devtools kicad upgrade PROJECT`
runs `kicad-cli … upgrade` on it as an explicit, separate step (one whole-file
rewrite the user can commit on its own), after which `kt` edits it with
byte-identical diffs. The round-trip test still parses every stock file of any
version; the editing tests use KiCad 10 files.

## New projects (Claude's call)

`kt` can create a new project: `.kicad_pro`, an empty root schematic and an
empty board, in the formats KiCad 10 writes for a new project (taken from
KiCad's own `template/default`).

## `kt` (decided shape; exact API left to the builder)

**S-expression core.** Parse any KiCad file into a tree that keeps enough of
the original text that unchanged nodes are written back byte-identical, and
new or changed nodes are written in KiCad's own formatting (tabs, one node per
line as KiCad does). Test: every `.kicad_sch`, `.kicad_pcb`, `.kicad_sym` and
`.kicad_mod` under the KiCad install's `demos/`, `symbols/` and a sample of
`footprints/` round-trips unchanged.

**Library lookup.** Resolve `Lib:Name` through the project's library table,
then the global one, following nested `Table` entries and expanding
`${KICAD10_SYMBOL_DIR}`, `${KICAD10_FOOTPRINT_DIR}`, `${KICAD10_3DMODEL_DIR}`,
`${KIPRJMOD}` and the user's variables from `kicad_common.json`. Symbols that
`extends` another symbol are resolved.

**Schematic helpers** (general building blocks):
- add a symbol from a library at a position, rotation, mirror and unit, with
  reference, value, footprint and other fields; the library copy is embedded in
  `lib_symbols` as KiCad does, and the instance data KiCad needs is written;
- the position of any pin of a placed symbol, in sheet coordinates, honouring
  rotation, mirror and unit (library Y points up, sheet Y points down);
- wires between points or pins, with orthogonal bends; junctions where three
  or more wire ends meet;
- net labels, global labels, power symbols, no-connect flags;
- move, rotate, delete, and change fields of placed symbols;
- next free reference for a prefix;
- everything on KiCad's 1.27 mm (50 mil) connection grid, with an error if a
  pin or wire end falls off it.
Works on flat schematics and on every sheet of an existing hierarchy; creating
new sub-sheets is left out of the first round.

**Board helpers:**
- read footprints with position, rotation, side, and pad nets;
- move, rotate, flip footprints; lock/unlock;
- draw a board outline on `Edge.Cuts` (rectangle with optional corner radius,
  or a polygon), mounting holes from a library footprint;
- compare board and schematic (see "Schematic → board"); the update itself
  is the user's F8.

**Library helpers:**
- create a footprint library folder (`.pretty`) or symbol library file if
  missing, and register it in the project or global table;
- build a footprint from general parts: SMD and through-hole pads (rect,
  roundrect, circle, oval), courtyard, fab and silkscreen outlines, reference
  and value text, a 3D model reference; write it as `.kicad_mod`;
- copy and adapt an existing library footprint or symbol into the private
  library;
- build a symbol from pins (number, name, electrical type, side) as a box
  symbol, with fields; write it into a `.kicad_sym`.
How a part *should* look (pad naming habits, silk style) stays out — later skills.

## Schematic → board (decided: the user presses F8)

KiCad has no command-line "Update PCB from Schematic". **The user runs it**
(user, 2026-11-01): after Claude's schematic changes, Claude asks the user to
open the board editor, press F8, save and close; then Claude places. `kt` does
not add footprints to a board from the schematic and does not set pad nets.

To make the hand-off clear, `devtools kicad info` reports whether board and
schematic are in step (footprints missing from the board, on the board but not
in the schematic, or with a different value or footprint), and Claude uses it
to tell the user when F8 is due. `drc` reports KiCad's schematic-parity
findings apart from the rest, since they are expected until the user's F8.

A `kt` sync was considered (Claude finishes schematic-to-placement alone) and
rejected for now: the largest single piece of work, re-implementing part of
KiCad.

## Testing

Without KiCad's GUI (`python -m unittest`, host Python; tests that need
`kicad-cli` are skipped with a clear message if it is missing — never faked):
- S-expression round-trip over the stock files (above).
- Library lookup on a fixture project with its own tables.
- Pin positions: place stock symbols at every rotation and mirror, wire pins
  to labels, export the netlist with `kicad-cli`, and check every pin landed
  on the intended net. This is the main proof that `kt`'s geometry matches
  KiCad's.
- A small schematic built from scratch (regulator + LED + connector) passes
  ERC with zero errors and exports to PDF.
- Footprint built by `kt` loads in `kicad-cli fp export svg` and passes DRC
  when placed on a board; symbol built by `kt` loads in `kicad-cli sym export svg`.
- Board: outline + placement on a board fixture already updated by F8
  (made once by hand in KiCad), DRC run; the in-step report flags a
  footprint added to the schematic after that F8.
- Lock: a fake lock file makes `run` exit 6 and leaves the target untouched.
- CLI: arguments, exit codes, output capping.
All tests build their own projects in a temporary directory; none touch the
user's projects.

Once by hand: open a `kt`-written schematic, board and footprint in KiCad,
check nothing is lost or flagged on load, and confirm the lock file name.

## Layout

```
kicad/
  CLAUDE.md          command card a project imports
  docs/format-notes.md   KiCad file-format knowledge, pitfalls found
  kt/                helper library (imported by `devtools kicad run`)
  README.md          updated from the placeholder
devtools/kicad.py    the CLI side
tests/test_kicad_*.py
```

`kt` runs in the host Python (the CLI's own process or a child process —
builder's call), not in KiCad's bundled Python.

## Calls made by Claude, not yet confirmed by the user

- Exit code 6 for "file open in KiCad".
- Writes happen only after a script succeeds; checks run after every write.
- `DEVTOOLS_KICAD_CLI` as the override for the `kicad-cli` path.
- Pictures as PDF/PNG only, since Claude cannot open SVG.
- Creating new hierarchical sub-sheets is left out of the first round.
- Symbol and 3D-model-reference support included alongside footprints.
- Changed files are checked by `kicad-cli` in a temporary copy before they
  replace anything; a load failure writes nothing (exit 1).
- Only KiCad 10 files are edited; older ones go through an explicit `upgrade`.
- Global library tables are never written while KiCad runs.
- `kt` can create a new project from KiCad's default template.

## Left to the builder

Module split inside `kt` and the CLI, exact `info` layout, the exact `kt`
function names, how pictures for layer plots and library parts become
readable (PDF or other stdlib route), the output cap, log locations,
test file names, and the order of work within each phase.

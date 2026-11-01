# KiCad

Claude edits KiCad 10 projects as files, through scripts that use the helper
library `kt`, and checks them with `kicad-cli` — no live connection to KiCad,
nothing to install beyond KiCad 10.

- [`CLAUDE.md`](CLAUDE.md) — the command card a project imports.
- [`kt/`](kt/) — the helper library (standard library only).
- [`docs/format-notes.md`](docs/format-notes.md) — KiCad 10 file-format knowledge.
- [`docs/handcheck.py`](docs/handcheck.py) — builds a small project to open in KiCad by hand.
- Design: [`docs/superpowers/specs/2026-11-01-kicad-design.md`](../docs/superpowers/specs/2026-11-01-kicad-design.md).

What it covers: schematic work (symbols, wires, labels, power), board placement
(outline, move/rotate/flip, board-only parts), and a private library
(footprints, symbols). Not covered: routing, schematic → board update (the
user's F8 in KiCad), the Fusion link.

`kicad-cli` is found at `C:\Program Files\KiCad\10.0\bin\kicad-cli.exe`, or
wherever `DEVTOOLS_KICAD_CLI` points.

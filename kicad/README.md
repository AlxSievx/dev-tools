# KiCad

Not started. KiCad 10 gets its own brainstorm and design once the Fusion side
works. What is known so far (2026-10-24, KiCad 10.0.1 installed):

- `kicad-cli` runs ERC, DRC and every export (Gerbers, BOM, netlist, STEP)
  without the GUI.
- Schematic and board files are plain-text S-expressions.
- Live control goes through the IPC API (`kicad-python`), mainly the PCB
  editor; the SWIG `pcbnew` module still ships but is on its way out.

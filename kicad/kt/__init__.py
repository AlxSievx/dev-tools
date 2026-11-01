"""kt — KiCad helpers for scripts run by `devtools kicad run`.

Units are millimetres; schematic points sit on KiCad's 1.27 mm grid.
"""
from kt.sexpr import Node, Sym, dump, parse
from kt.project import (FormatError, LockedError, Project, RejectedError, kicad_running, locked,
                        new_project)
from kt.cli import KicadCliMissing, LoadError
from kt.libs import LibTables, register_library
from kt.schematic import PlacedSymbol, Schematic
from kt.board import Board, PlacedFootprint
from kt.info import info
from kt.parts import Footprint, FootprintLib, Symbol, SymbolLib

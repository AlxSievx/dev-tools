import os
import unittest
from pathlib import Path
from unittest import mock

from _kicad import TempDir, needs_kicad, needs_share
from kt import libs
from kt.libs import LibTables, register_library
from kt.project import new_project
from kt.sexpr import dump, parse

MY_SYM = """(kicad_symbol_lib
	(version 20251024)
	(generator "kt")
	(symbol "R"
		(property "Reference" "X" (at 0 0 0))
		(property "Value" "mine" (at 0 0 0))
		(symbol "R_0_1"
			(pin passive line (at 0 0 0) (length 2.54) (name "~") (number "1"))
		)
	)
)
"""


def pins(sym):
    return [p.get("number")[0] for p in sym.walk() if p.head == "pin"]


def prop(sym, name):
    return next(p[1] for p in sym.all("property") if p[0] == name)


@needs_kicad
@needs_share
class LookupTest(unittest.TestCase):
    def test_stock_symbol_via_global_table(self):
        sym = LibTables(None).symbol("Device:R")
        self.assertEqual(sym[0], "R")
        self.assertIn("1", pins(sym))

    def test_extends_flattened(self):
        sym = LibTables(None).symbol("Regulator_Linear:AMS1117-3.3")
        self.assertEqual(sym[0], "AMS1117-3.3")
        self.assertIsNone(sym.get("extends"))
        self.assertEqual(sorted(pins(sym)), ["1", "2", "3"])
        self.assertEqual(prop(sym, "Value"), "AMS1117-3.3")
        units = [s[0] for s in sym.all("symbol")]
        self.assertTrue(units and all(u.startswith("AMS1117-3.3_") for u in units), units)

    def test_footprint(self):
        fp = LibTables(None).footprint("Resistor_SMD:R_0603_1608Metric")
        self.assertEqual(fp.head, "footprint")
        self.assertEqual(len(fp.all("pad")), 2)

    def test_unknown(self):
        with self.assertRaises(KeyError):
            LibTables(None).symbol("Device:NoSuchPart")
        with self.assertRaises(KeyError):
            LibTables(None).symbol("NoSuchLib:R")

    def test_project_table_first(self):
        with TempDir() as d:
            p = new_project(d, "t")
            (d / "my.kicad_sym").write_text(MY_SYM, encoding="utf-8")
            (d / "sym-lib-table").write_text(
                '(sym_lib_table\n\t(version 7)\n\t(lib (name "Device")(type "KiCad")'
                '(uri "${KIPRJMOD}/my.kicad_sym")(options "")(descr ""))\n)\n', encoding="utf-8")
            sym = LibTables(p).symbol("Device:R")
        self.assertEqual(prop(sym, "Value"), "mine")


def _table(path, head, *entries):
    Path(path).write_text(f"({head}\n\t(version 7)\n" + "".join(f"\t{e}\n" for e in entries) + ")\n",
                          encoding="utf-8")


@needs_kicad
@needs_share
class DisabledTest(unittest.TestCase):
    """KiCad does not offer libraries marked (disabled); kt must not find them either."""

    def test_disabled_library_skipped(self):
        with TempDir() as d:
            p = new_project(d, "t")
            (d / "my.kicad_sym").write_text(MY_SYM, encoding="utf-8")
            _table(d / "sym-lib-table", "sym_lib_table",
                   '(lib (name "Mine")(type "KiCad")(uri "${KIPRJMOD}/my.kicad_sym")(options "")(descr "")(disabled))')
            with self.assertRaises(KeyError):
                LibTables(p).symbol("Mine:R")

    def test_disabled_nested_table_skipped(self):
        with TempDir() as d:
            table = d / "sym-lib-table"
            _table(table, "sym_lib_table",
                   '(lib (name "KiCad")(type "Table")(uri "${KICAD10_TEMPLATE_DIR}/sym-lib-table")'
                   '(options "")(descr "")(disabled))')
            with mock.patch.object(libs, "global_table", return_value=table):
                self.assertFalse(LibTables(None).has_symbol("Device:R"))

    def test_enabled_entry_wins_over_disabled_nested(self):
        with TempDir() as d:
            table = d / "sym-lib-table"
            _table(table, "sym_lib_table",
                   '(lib (name "KiCad")(type "Table")(uri "${KICAD10_TEMPLATE_DIR}/sym-lib-table")'
                   '(options "")(descr "")(disabled))',
                   '(lib (name "power")(type "KiCad")(uri "${KICAD10_SYMBOL_DIR}/power.kicad_sym")'
                   '(options "")(descr ""))')
            with mock.patch.object(libs, "global_table", return_value=table):
                t = LibTables(None)
                self.assertTrue(t.has_symbol("power:GND"))
                self.assertFalse(t.has_symbol("Device:R"))

    def test_hidden_library_still_found(self):
        with TempDir() as d:
            p = new_project(d, "t")
            (d / "my.kicad_sym").write_text(MY_SYM, encoding="utf-8")
            _table(d / "sym-lib-table", "sym_lib_table",
                   '(lib (name "Mine")(type "KiCad")(uri "${KIPRJMOD}/my.kicad_sym")(options "")(descr "")(hidden))')
            self.assertEqual(prop(LibTables(p).symbol("Mine:R"), "Value"), "mine")


class ConfigDirTest(unittest.TestCase):
    def test_config_dir_override(self):
        with TempDir() as d:
            with mock.patch.dict(os.environ, {"DEVTOOLS_KICAD_CONFIG": str(d)}):
                self.assertEqual(libs.config_dir(), d)
                self.assertEqual(libs.global_table("symbol"), d / "sym-lib-table")


class ExpandTest(unittest.TestCase):
    def test_expand(self):
        with mock.patch.dict("os.environ", {"MYVAR": "C:/x"}):
            self.assertEqual(libs.expand("${KIPRJMOD}/a/${MYVAR}", Path("C:/p")), "C:/p/a/C:/x")
        self.assertTrue(libs.expand("${KICAD10_SYMBOL_DIR}", None).replace("\\", "/").endswith("share/kicad/symbols"))


class RegisterTest(unittest.TestCase):
    def test_register_project_scope(self):
        with TempDir() as d:
            p = new_project(d, "t")
            msg = register_library("footprint", "Mine", d / "Mine.pretty", "project", p)
            self.assertIn("Mine", msg)
            self.assertEqual(p.changed()[0].path, d / "fp-lib-table")
            # the table itself has no kicad-cli check; save writes it
            p.save()
            text = (d / "fp-lib-table").read_text(encoding="utf-8")
            self.assertIn('(uri "${KIPRJMOD}/Mine.pretty")', text)
            self.assertIn("already registered", register_library("footprint", "Mine", d / "Mine.pretty", "project", p))
            with self.assertRaises(ValueError):
                register_library("footprint", "Mine", d / "Other.pretty", "project", p)

    def test_register_global_while_running(self):
        with TempDir() as d:
            table = d / "fp-lib-table"
            table.write_text("(fp_lib_table\n\t(version 7)\n)\n", encoding="utf-8")
            before = table.read_bytes()
            with mock.patch.object(libs, "global_table", return_value=table), \
                    mock.patch.object(libs, "kicad_running", return_value=True):
                msg = register_library("footprint", "Mine", Path("D:/libs/Mine.pretty"), "global")
            self.assertEqual(table.read_bytes(), before)
            self.assertIn('(lib (name "Mine")', msg)
            self.assertIn("D:/libs/Mine.pretty", msg)

    def test_register_global_not_running(self):
        with TempDir() as d:
            table = d / "sym-lib-table"
            with mock.patch.object(libs, "global_table", return_value=table), \
                    mock.patch.object(libs, "kicad_running", return_value=False):
                register_library("symbol", "Mine", Path("D:/libs/Mine.kicad_sym"), "global")
            root = parse(table.read_text(encoding="utf-8"))
            self.assertEqual(root.head, "sym_lib_table")
            self.assertEqual(root.get("lib").get("name")[0], "Mine")


if __name__ == "__main__":
    unittest.main()

"""Shared helpers for the KiCad tests: kt on sys.path, KiCad install paths, temp dirs."""
import atexit
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KICAD = ROOT / "kicad"
if str(KICAD) not in sys.path:
    sys.path.insert(0, str(KICAD))

KICAD_HOME = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad" / "10.0"
KICAD_SHARE = KICAD_HOME / "share" / "kicad"
DEFAULT_CLI = KICAD_HOME / "bin" / "kicad-cli.exe"


def _cli_present() -> bool:
    return Path(os.environ.get("DEVTOOLS_KICAD_CLI") or DEFAULT_CLI).is_file()


def _test_config() -> None:
    """kt reads library tables from a config of its own, so tests do not depend on the user's
    (whose stock libraries may be disabled): the stock tables, enabled."""
    if os.environ.get("DEVTOOLS_KICAD_CONFIG"):
        return
    cfg = Path(tempfile.mkdtemp(prefix="kt-config-"))
    atexit.register(shutil.rmtree, cfg, True)
    for table, head in (("sym-lib-table", "sym_lib_table"), ("fp-lib-table", "fp_lib_table")):
        (cfg / table).write_text(
            f'({head}\n\t(version 7)\n\t(lib (name "KiCad")(type "Table")'
            f'(uri "${{KICAD10_TEMPLATE_DIR}}/{table}")(options "")(descr ""))\n)\n', encoding="utf-8")
    os.environ["DEVTOOLS_KICAD_CONFIG"] = str(cfg)


_test_config()

needs_kicad = unittest.skipUnless(_cli_present(), "KiCad 10 not found (set DEVTOOLS_KICAD_CLI)")
needs_share = unittest.skipUnless(KICAD_SHARE.is_dir(), f"KiCad 10 library files not found in {KICAD_SHARE}")


class TempDir:
    """`with TempDir() as tmp:` — a fresh directory, removed afterwards."""

    def __enter__(self) -> Path:
        self._dir = tempfile.mkdtemp(prefix="kt-test-")
        return Path(self._dir)

    def __exit__(self, *exc):
        shutil.rmtree(self._dir, ignore_errors=True)


def copy_demo(name: str, dest: Path) -> Path:
    """Copy a stock KiCad demo project into `dest` and return the copy's directory."""
    target = dest / name
    shutil.copytree(KICAD_SHARE / "demos" / name, target)
    return target

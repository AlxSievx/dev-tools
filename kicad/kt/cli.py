"""`kicad-cli`: find it, run it, read its reports.

Its messages follow KiCad's UI language (German here), so only exit codes and
JSON reports are read, never message text.
"""
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from kt.sexpr import parse

DEFAULT = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "KiCad" / "10.0" / "bin" / "kicad-cli.exe"
LOAD_FAILED = 3   # kicad-cli's exit code when it cannot load the input file


class KicadCliMissing(Exception):
    pass


class LoadError(Exception):
    """kicad-cli could not load the file — it is malformed or too new."""

    def __init__(self, path: Path, output: str):
        super().__init__(f"kicad-cli could not load {path}\n{output.strip()}")
        self.path = Path(path)
        self.output = output


class CliFailed(Exception):
    pass


def find() -> Path:
    env = os.environ.get("DEVTOOLS_KICAD_CLI")
    path = Path(env) if env else DEFAULT
    if not path.is_file():
        raise KicadCliMissing(
            f"kicad-cli not found at {path}. Install KiCad 10 or point DEVTOOLS_KICAD_CLI at kicad-cli.exe.")
    return path


def run(*args, input_file: Path | None = None) -> subprocess.CompletedProcess:
    """Run kicad-cli with `args`; exit 3 means `input_file` could not be loaded."""
    cmd = [str(find())] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = proc.stdout + proc.stderr
    if proc.returncode == LOAD_FAILED:
        raise LoadError(input_file or Path(str(args[-1])), output)
    if proc.returncode != 0:
        raise CliFailed(f"kicad-cli {' '.join(str(a) for a in args[:3])} … exited {proc.returncode}\n{output.strip()}")
    return proc


@dataclass
class Finding:
    severity: str
    type: str
    description: str
    items: list[str] = field(default_factory=list)

    def line(self) -> str:
        where = f"  ({', '.join(self.items)})" if self.items else ""
        return f"{self.severity:<8} {self.type:<28} {self.description}{where}"


@dataclass
class Report:
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    parity: list[Finding] = field(default_factory=list)
    unconnected: int = 0
    kind: str = ""

    def summary(self) -> str:
        head = f"{self.kind}: {len(self.errors)} errors, {len(self.warnings)} warnings"
        if self.kind == "DRC":
            head += f", {self.unconnected} unconnected, {len(self.parity)} parity"
        lines = [head] + [f.line() for f in self.errors + self.warnings]
        if self.parity:
            lines.append("schematic parity (expected until F8):")
            lines += ["  " + f.line() for f in self.parity]
        return "\n".join(lines)


def _finding(v: dict) -> Finding:
    items = [i.get("description", "") for i in v.get("items", [])]
    return Finding(v.get("severity", ""), v.get("type", ""), v.get("description", ""), items)


def _sort(report: Report, violations) -> None:
    for v in violations:
        f = _finding(v)
        if f.severity == "error":
            report.errors.append(f)
        elif f.severity == "warning":
            report.warnings.append(f)


def _json_report(cmd: str, path: Path, *extra) -> dict:
    with tempfile.TemporaryDirectory(prefix="kt-cli-") as tmp:
        out = Path(tmp) / "report.json"
        run(*cmd.split(), "--format", "json", "--severity-error", "--severity-warning",
            *extra, "-o", out, path, input_file=path)
        return json.loads(out.read_text(encoding="utf-8"))


def erc(sch: Path) -> Report:
    data = _json_report("sch erc", Path(sch))
    report = Report(kind="ERC")
    for sheet in data.get("sheets", []):
        _sort(report, sheet.get("violations", []))
    return report


def drc(pcb: Path, parity: bool = False) -> Report:
    data = _json_report("pcb drc", Path(pcb), *(["--schematic-parity"] if parity else []))
    report = Report(kind="DRC")
    _sort(report, data.get("violations", []))
    report.parity = [_finding(v) for v in data.get("schematic_parity", [])]
    report.unconnected = len(data.get("unconnected_items", []))
    return report


def netlist(sch: Path):
    with tempfile.TemporaryDirectory(prefix="kt-cli-") as tmp:
        out = Path(tmp) / "netlist.net"
        run("sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch, input_file=Path(sch))
        return parse(out.read_text(encoding="utf-8"))


@dataclass
class Pad:
    net: str        # IPC-D-356 keeps only the last 14 characters
    ref: str
    pad: str
    x: float        # board coordinates, mm
    y: float
    side: str       # top | bottom | both
    rotation: float


_IPC_POS = re.compile(r"A(\d\d)X([+-]\d+)Y([+-]\d+)X(\d+)Y(\d+)R(\d+)")
_SIDES = {"00": "both", "01": "top", "02": "bottom"}


def _aux_origin(pcb: Path) -> tuple[float, float]:
    text = Path(pcb).read_text(encoding="utf-8")
    m = re.search(r"\(aux_axis_origin\s+(-?[\d.]+)\s+(-?[\d.]+)\)", text)
    return (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)


def ipc356(pcb: Path) -> list[Pad]:
    """Every pad's absolute position, as KiCad itself computes it (vias left out).

    IPC-D-356 measures from the board's auxiliary origin with Y pointing up;
    the result is converted back to KiCad board coordinates (Y down).
    """
    ox, oy = _aux_origin(pcb)
    with tempfile.TemporaryDirectory(prefix="kt-cli-") as tmp:
        out = Path(tmp) / "board.d356"
        run("pcb", "export", "ipcd356", "-o", out, pcb, input_file=Path(pcb))
        lines = out.read_text(encoding="utf-8", errors="replace").splitlines()
    unit = 0.00254  # UNITS CUST 0: 0.0001 inch
    pads = []
    for line in lines:
        if line.startswith("P  UNITS CUST 1"):
            unit = 0.001
        if line[:3] not in ("317", "327", "367"):
            continue
        ref = line[20:26].strip()
        if ref == "VIA":
            continue
        m = _IPC_POS.search(line, 32)
        if not m:
            continue
        access, x, y, _, _, rot = m.groups()
        pads.append(Pad(net=line[3:17].strip(), ref=ref, pad=line[27:31].strip(),
                        x=round(ox + int(x) * unit, 4), y=round(oy - int(y) * unit, 4),
                        side=_SIDES.get(access, access), rotation=float(rot)))
    return pads

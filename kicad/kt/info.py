"""`info(project)` — the project in numbers, one line per item."""
from kt import cli
from kt.sexpr import Node

MAX_NETS = 60


def _mm(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def in_step(sch: dict, board: dict) -> str:
    """Compare {ref: (value, footprint)} of schematic and board."""
    missing = sorted(set(sch) - set(board), key=_ref_key)
    extra = sorted(set(board) - set(sch), key=_ref_key)
    differs = []
    for ref in sorted(set(sch) & set(board), key=_ref_key):
        (sv, sf), (bv, bf) = sch[ref], board[ref]
        if sv != bv:
            differs.append(f"{ref} value {sv} ≠ {bv}")
        if sf != bf:
            differs.append(f"{ref} footprint {sf or '-'} ≠ {bf}")
    parts = []
    if missing:
        parts.append("missing on board: " + ", ".join(missing))
    if extra:
        parts.append("not in schematic: " + ", ".join(extra))
    if differs:
        parts.append("differs: " + ", ".join(differs))
    return "; ".join(parts) or "yes"


def _ref_key(ref: str):
    head = ref.rstrip("0123456789")
    tail = ref[len(head):]
    return (head, int(tail) if tail else 0)


def _flag(node: Node, head: str, default: bool = True) -> bool:
    f = node.get(head)
    return default if f is None else str(f[0]) == "yes"


def info(project) -> str:
    lines = [f"project {project.name}  ({project.dir})"]
    sch_parts = {}
    has_sch = project.root_sch.exists()
    if has_sch:
        lines.append("sheets")
        for sheet in project.sheets():
            syms = [s for s in sheet.symbols() if not s.ref.startswith("#")]
            lines.append(f"  {sheet.path.relative_to(project.dir)}  {len(syms)} symbols")
            for s in syms:
                if not _flag(s.node, "on_board"):
                    continue
                for ref in dict.fromkeys(s.refs.values() or [s.ref]):     # one part per sheet instance
                    sch_parts.setdefault(ref, (s.value, s.fields().get("Footprint", ""), s.lib_id))
        netlist = cli.netlist(project.root_sch)
        nets = netlist.get("nets").all("net") if netlist.get("nets") is not None else []
        unconnected = {}
        named, auto = [], []
        for net in nets:
            name = net.get("name")[0]
            nodes = [f"{n.get('ref')[0]}.{n.get('pin')[0]}" for n in net.all("node")]
            if name.startswith("unconnected-"):
                for n in net.all("node"):
                    unconnected[n.get("ref")[0]] = unconnected.get(n.get("ref")[0], 0) + 1
                continue
            (auto if name.startswith("Net-(") else named).append((name.lstrip("/"), nodes))
        lines.append("symbols")
        for ref in sorted(sch_parts, key=_ref_key):
            value, fp, lib_id = sch_parts[ref]
            extra = f"  unconnected pins: {unconnected[ref]}" if ref in unconnected else ""
            lines.append(f"  {ref}  {value}  {lib_id}  {fp or '(no footprint)'}{extra}")
        all_nets = named + auto
        lines.append(f"nets  ({len(all_nets)})")
        for name, nodes in all_nets[:MAX_NETS]:
            shown = [n for n in nodes if not n.startswith("#")]
            lines.append(f"  {name}: {' '.join(shown)}")
        if len(all_nets) > MAX_NETS:
            lines.append(f"  … {len(all_nets) - MAX_NETS} more nets")

    has_pcb = project.pcb_path.exists()
    if has_pcb:
        import kt.board  # noqa: F401  registers the Board document type
        board = project.document(project.pcb_path)
        fps = board.footprints()
        box = board.bbox()
        size = f"{_mm(box[2] - box[0])} × {_mm(box[3] - box[1])} mm" if box else "no outline"
        lines.append(f"board  {size}, {len(fps)} footprints")
        for f in sorted(fps, key=lambda f: _ref_key(f.ref)):
            lines.append(f"  {f.ref}  {_mm(f.at[0])} {_mm(f.at[1])}  rot {_mm(f.rot)}  {f.side}  {f.fp_id}")
        if has_sch:
            board_parts = {f.ref: (f.value, f.fp_id) for f in fps if not f.board_only and f.ref}
            sch_cmp = {r: (v, fp) for r, (v, fp, _) in sch_parts.items()}
            lines.append(f"in step: {in_step(sch_cmp, board_parts)}")
    else:
        lines.append("board  none")

    lines.append("checks")
    if has_sch:
        erc = cli.erc(project.root_sch)
        lines.append(f"  ERC: {len(erc.errors)} errors, {len(erc.warnings)} warnings")
    if has_pcb:
        drc = cli.drc(project.pcb_path, parity=has_sch)
        lines.append(f"  DRC: {len(drc.errors)} errors, {len(drc.warnings)} warnings, "
                     f"{drc.unconnected} unconnected, {len(drc.parity)} parity")
    return "\n".join(lines)

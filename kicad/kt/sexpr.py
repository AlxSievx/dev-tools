"""KiCad S-expressions: read, change, write back.

Untouched text is written back byte for byte: a parsed node remembers where it
came from, and only nodes whose own items were changed (or that are new) are
formatted again, in KiCad's style — tabs, one child list per line, a list of
plain atoms on one line.

Items are Python values: `str` is a quoted string, `Sym` a bare token
(`yes`, `smd`), `int`/`float` numbers, `Node` a child list. Change a node only
through its methods (`set`, `append`, `insert`, `remove`, `replace`).
"""
import re
from typing import Iterator

_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+', re.S)
_INT = re.compile(r"-?\d+\Z")
_FLOAT = re.compile(r"-?(?:\d+\.\d*|\.\d+)\Z")
_UNESCAPE = {"n": "\n", "r": "\r", "t": "\t"}


class Sym(str):
    """A bare (unquoted) token such as `yes`, `smd` or `F.Cu` in old files."""
    __slots__ = ()

    def __repr__(self):
        return f"Sym({str.__repr__(self)})"


def fmt_num(x) -> str:
    """A number the way KiCad writes it: no exponent, trailing zeros trimmed."""
    if isinstance(x, int):
        return str(x)
    s = f"{x:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def quote(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{s}"'


def _atom(raw: str):
    if raw[0] == '"':
        body = raw[1:-1]
        if "\\" in body:
            body = re.sub(r"\\(.)", lambda m: _UNESCAPE.get(m.group(1), m.group(1)), body, flags=re.S)
        return body
    if _INT.match(raw):
        return int(raw)
    if _FLOAT.match(raw):
        return float(raw)
    return Sym(raw)


def _fmt_atom(value) -> str:
    if isinstance(value, Sym):
        return str(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return quote(value)
    if isinstance(value, (int, float)):
        return fmt_num(value)
    raise TypeError(f"not an S-expression atom: {value!r}")


class Node:
    __slots__ = ("head", "items", "_raw", "_parent", "_src", "_start", "_end",
                 "_dirty", "_touched", "_pre", "_post", "_nl")

    def __init__(self, head: str, *items):
        self.head = str(head)
        self.items = []
        self._raw = []
        self._parent = None
        self._src = None
        self._start = self._end = 0
        self._dirty = True
        self._touched = True
        self._pre = self._post = ""
        self._nl = None
        for item in items:
            self._put(len(self.items), item)

    # ---- reading
    def __getitem__(self, i):
        return self.items[i]

    def __len__(self):
        return len(self.items)

    def __repr__(self):
        return f"<Node {self.head} {self.values()[:3]}>"

    def get(self, head: str):
        for item in self.items:
            if isinstance(item, Node) and item.head == head:
                return item
        return None

    def all(self, head: str | None = None) -> list:
        return [i for i in self.items if isinstance(i, Node) and (head is None or i.head == head)]

    def walk(self) -> Iterator["Node"]:
        yield self
        for item in self.items:
            if isinstance(item, Node):
                yield from item.walk()

    def values(self) -> list:
        return [i for i in self.items if not isinstance(i, Node)]

    @property
    def parent(self):
        return self._parent

    @property
    def changed(self) -> bool:
        """True if this node or anything below it differs from what was parsed."""
        return self._touched

    # ---- changing
    def set(self, i: int, value) -> None:
        old = self.items[i]
        if isinstance(old, Node):
            old._parent = None
        self.items.pop(i)
        self._raw.pop(i)
        self._put(i, value)
        self._mark()

    def append(self, item):
        self._put(len(self.items), item)
        self._mark()
        return item

    def insert(self, i: int, item):
        self._put(i, item)
        self._mark()
        return item

    def remove(self, child) -> None:
        for i, item in enumerate(self.items):
            if item is child:
                self.items.pop(i)
                self._raw.pop(i)
                if isinstance(child, Node):
                    child._parent = None
                self._mark()
                return
        raise ValueError(f"{child!r} is not an item of {self!r}")

    def replace(self, old, new) -> None:
        for i, item in enumerate(self.items):
            if item is old:
                self.set(i, new)
                return
        raise ValueError(f"{old!r} is not an item of {self!r}")

    def copy(self) -> "Node":
        """A deep copy with no source text: it is formatted fresh when written."""
        return Node(self.head, *(i.copy() if isinstance(i, Node) else i for i in self.items))

    def _put(self, i, item):
        if isinstance(item, Node):
            if item._parent is not None:
                raise ValueError("node is already part of a tree; insert node.copy() instead")
            _forget_source(item)
            item._parent = self
        else:
            _fmt_atom(item)  # type check now, not at write time
        self.items.insert(i, item)
        self._raw.insert(i, None)

    def _mark(self):
        self._dirty = True
        node = self
        while node is not None and not node._touched:
            node._touched = True
            node = node._parent
        while node is not None:   # already touched: ancestors are too, but be sure
            node._touched = True
            node = node._parent


def _forget_source(node: Node) -> None:
    for n in node.walk():
        n._src = None
        n._dirty = n._touched = True


def parse(text: str) -> Node:
    stack: list[Node] = []
    root = None
    for m in _TOKEN.finditer(text):
        tok = m.group()
        if tok == "(":
            node = Node.__new__(Node)
            node.items, node._raw = [], []
            node._parent = stack[-1] if stack else None
            node._src, node._start = text, m.start()
            node._dirty = node._touched = False
            node._pre = node._post = ""
            node._nl = None
            node.head = None
            stack.append(node)
        elif tok == ")":
            if not stack:
                raise ValueError(f"unbalanced ')' at offset {m.start()}")
            node = stack.pop()
            node._end = m.end()
            if node.head is None:
                raise ValueError(f"empty list at offset {node._start}")
            if stack:
                stack[-1].items.append(node)
                stack[-1]._raw.append(None)
            else:
                if root is not None:
                    raise ValueError("more than one top-level list")
                root = node
        else:
            if not stack:
                raise ValueError(f"atom outside a list at offset {m.start()}")
            top = stack[-1]
            if top.head is None:
                top.head = tok
            else:
                top.items.append(_atom(tok))
                top._raw.append(tok)
    if stack or root is None:
        raise ValueError("unbalanced '(' — the file is cut short")
    root._pre, root._post = text[:root._start], text[root._end:]
    root._nl = "\r\n" if "\r\n" in text else "\n"
    return root


def dump(node: Node) -> str:
    nl = node._nl or "\n"
    return node._pre + _dump(node, 0, nl) + node._post


def _dump(node: Node, depth: int, nl: str) -> str:
    if node._src is not None and not node._touched:
        return node._src[node._start:node._end]
    if node._src is not None and not node._dirty:
        out, pos = [], node._start
        for item in node.items:
            if isinstance(item, Node):
                out.append(node._src[pos:item._start])
                out.append(_dump(item, depth + 1, nl))
                pos = item._end
        out.append(node._src[pos:node._end])
        return "".join(out)
    return _format(node, depth, nl)


def _format(node: Node, depth: int, nl: str) -> str:
    def atom(i):
        raw = node._raw[i]
        return raw if raw is not None else _fmt_atom(node.items[i])

    if not any(isinstance(i, Node) for i in node.items):
        return "(" + " ".join([node.head] + [atom(i) for i in range(len(node.items))]) + ")"
    inner = "\t" * (depth + 1)
    out = ["(" + node.head]
    seen_child = False
    for i, item in enumerate(node.items):
        if isinstance(item, Node):
            seen_child = True
            out.append(nl + inner + _dump(item, depth + 1, nl))
        elif seen_child:
            out.append(nl + inner + atom(i))
        else:
            out.append(" " + atom(i))
    out.append(nl + "\t" * depth + ")")
    return "".join(out)

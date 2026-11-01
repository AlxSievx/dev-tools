import difflib
import os
import unittest

from _kicad import KICAD_SHARE, needs_share
from kt.sexpr import Node, Sym, dump, fmt_num, parse

# Shipped malformed with KiCad 10.0.1: a footprint's `teardrops` list is cut
# off mid-token and a stray ')' closes the board early.
KNOWN_BROKEN = {"RoyalBlue54L-Feather.kicad_pcb"}


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


class RoundTripTest(unittest.TestCase):
    @needs_share
    def test_roundtrip_stock_files(self):
        # ~600 MB in all; DEVTOOLS_KICAD_FULL=1 sweeps everything, the default a
        # sample that still covers every file kind and every demo below 2 MB.
        full = os.environ.get("DEVTOOLS_KICAD_FULL") == "1"
        demos = sorted((KICAD_SHARE / "demos").rglob("*.kicad_sch"))
        demos += sorted((KICAD_SHARE / "demos").rglob("*.kicad_pcb"))
        files = [p for p in demos if full or p.stat().st_size < 2_000_000]
        symbols = sorted((KICAD_SHARE / "symbols").glob("*.kicad_sym"))
        files += symbols if full else symbols[::8]
        files += sorted((KICAD_SHARE / "footprints").rglob("*.kicad_mod"))[:300]
        self.assertGreater(len(files), 300)
        for path in files:
            if path.name in KNOWN_BROKEN:
                continue
            text = read(path)
            with self.subTest(path=path.name):
                self.assertTrue(dump(parse(text)) == text, f"{path} does not round-trip")

    @needs_share
    def test_unchanged_child_kept_when_parent_changes(self):
        text = read(KICAD_SHARE / "demos" / "pic_programmer" / "pic_programmer.kicad_sch")
        root = parse(text)
        paper = root.get("paper")
        paper.set(0, "A4" if paper[0] == "A3" else "A3")
        out = dump(root)
        changed = [l for l in difflib.ndiff(text.splitlines(), out.splitlines()) if l[:1] in "+-"]
        self.assertEqual(len(changed), 2, changed)  # one line removed, one added
        self.assertIn("paper", changed[0])


class NewNodeTest(unittest.TestCase):
    def test_new_node_kicad_style(self):
        n = parse("(a\n\t(b 1)\n)")
        n.append(Node("c", Sym("yes"), "x y", 1.5))
        self.assertEqual(dump(n), '(a\n\t(b 1)\n\t(c yes "x y" 1.5)\n)')

    def test_nested_new_node(self):
        n = parse("(a\n\t(b 1)\n)")
        n.append(Node("c", Node("at", 1, 2), Node("uuid", "u")))
        self.assertEqual(dump(n), '(a\n\t(b 1)\n\t(c\n\t\t(at 1 2)\n\t\t(uuid "u")\n\t)\n)')

    def test_crlf_kept(self):
        n = parse("(a\r\n\t(b 1)\r\n)")
        n.append(Node("c", Node("d", 2)))
        out = dump(n)
        self.assertEqual(out, "(a\r\n\t(b 1)\r\n\t(c\r\n\t\t(d 2)\r\n\t)\r\n)")
        self.assertNotIn("\n", out.replace("\r\n", ""))

    def test_escapes_and_unicode(self):
        src = '(p "a \\"q\\" \\\\ kΩ µ")'
        self.assertEqual(parse(src)[0], 'a "q" \\ kΩ µ')
        self.assertEqual(dump(Node("p", 'a "q" \\ kΩ µ')), src)
        self.assertEqual(dump(Node("p", "line\nbreak")), '(p "line\\nbreak")')

    def test_values_and_types(self):
        n = parse('(at 1.27 -2 90) ')
        self.assertEqual(n.values(), [1.27, -2, 90])
        self.assertIsInstance(n[1], int)
        n = parse('(pad "1" smd roundrect)')
        self.assertEqual(n.values(), ["1", "smd", "roundrect"])
        self.assertIsInstance(n[1], Sym)
        self.assertNotIsInstance(n[0], Sym)

    def test_get_all_walk_remove(self):
        n = parse('(r\n\t(x 1)\n\t(y\n\t\t(x 2)\n\t)\n\t(x 3)\n)')
        self.assertEqual(n.get("x")[0], 1)
        self.assertEqual([c[0] for c in n.all("x")], [1, 3])
        self.assertEqual([c[0] for c in n.walk() if c.head == "x"], [1, 2, 3])
        n.remove(n.all("x")[1])
        self.assertEqual(dump(n), '(r\n\t(x 1)\n\t(y\n\t\t(x 2)\n\t)\n)')

    def test_fmt_num(self):
        self.assertEqual(fmt_num(1.27), "1.27")
        self.assertEqual(fmt_num(2.0), "2")
        self.assertEqual(fmt_num(-0.0), "0")
        self.assertEqual(fmt_num(0.1 + 0.2), "0.3")
        self.assertEqual(fmt_num(1e-7), "0")
        self.assertEqual(fmt_num(-1.5), "-1.5")


if __name__ == "__main__":
    unittest.main()

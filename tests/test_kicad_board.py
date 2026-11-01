import unittest

from _kicad import TempDir, needs_kicad
from kt import cli
from kt.project import Project, new_project
from test_kicad_info import upgraded_demo

TOL = 0.003


def ipc_by_ref(pcb):
    out = {}
    for p in cli.ipc356(pcb):
        if p.pad:
            out.setdefault(p.ref, []).append(p)
    return out


@needs_kicad
class PlacementTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.demo = upgraded_demo(self._tmp.__enter__())
        self.p = Project(self.demo)
        self.board = self.p.document(self.p.pcb_path)

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def assert_matches_kicad(self, refs=None):
        ipc = ipc_by_ref(self.p.pcb_path)
        board = Project(self.demo).document(self.p.pcb_path)
        checked = 0
        for fp in board.footprints():
            if refs and fp.ref not in refs:
                continue
            ours = fp.pad_list()
            for pad in ipc.get(fp.ref, []):
                with self.subTest(ref=fp.ref, pad=pad.pad):
                    near = [q for q in ours if q[0] == pad.pad
                            and abs(q[1] - pad.x) < TOL and abs(q[2] - pad.y) < TOL]
                    self.assertTrue(near, f"{fp.ref} pad {pad.pad}: KiCad {pad.x},{pad.y}; kt {ours}")
                    checked += 1
        self.assertGreater(checked, 0)
        return board

    def test_pads_match_ipc356(self):
        self.assert_matches_kicad()

    def test_move_rotate_flip_match_ipc356(self):
        b = self.board
        r1 = b.footprint("R1")
        x, y = r1.at
        r1.move(x + 10, y + 5)
        b.footprint("U1").rotate_to(90)
        b.footprint("U2").rotate_to(225)
        b.footprint("C1").flip()
        b.footprint("JP1").flip()            # bottom SMD back to top
        self.p.save()
        board = self.assert_matches_kicad({"R1", "U1", "U2", "C1", "JP1"})
        self.assertEqual(board.footprint("R1").at, (x + 10, y + 5))
        self.assertEqual(board.footprint("U2").rot, 225)
        self.assertEqual(board.footprint("C1").side, "bottom")
        self.assertEqual(board.footprint("JP1").side, "top")
        jp1 = [p for p in cli.ipc356(self.p.pcb_path) if p.ref == "JP1"]
        self.assertTrue(jp1 and all(p.side == "top" for p in jp1), jp1)

    def test_flip_keeps_position_and_mirrors_pads(self):
        r1 = self.board.footprint("JP1")      # SMD, on the bottom in the demo
        at = r1.at
        before = {n: (x, y) for n, x, y, _ in r1.pad_list()}
        r1.flip()
        self.assertEqual(r1.at, at)
        after = {n: (x, y) for n, x, y, _ in r1.pad_list()}
        for n, (x, y) in before.items():     # mirrored about the footprint's horizontal axis
            self.assertAlmostEqual(after[n][0], x, places=3)
            self.assertAlmostEqual(after[n][1] - at[1], -(y - at[1]), places=3)

    def test_flip_twice_restores(self):
        u1 = self.board.footprint("U1")
        before = u1.pad_list()
        u1.flip()
        u1.flip()
        self.assertEqual(u1.pad_list(), before)
        self.assertEqual(u1.side, "top")

    def test_locked_flag_written(self):
        self.board.footprint("R1").lock()
        self.p.save()
        text = self.p.pcb_path.read_text(encoding="utf-8")
        self.assertRegex(text, r'\(footprint "[^"]+"\s+\(locked yes\)')
        self.assertTrue(Project(self.demo).document(self.p.pcb_path).footprint("R1").locked)


@needs_kicad
class OutlineTest(unittest.TestCase):
    def test_outline_and_holes_drc(self):
        with TempDir() as d:
            p = new_project(d, "t")
            b = p.document(p.pcb_path)
            b.outline_rect(0, 0, 50, 30, r=2)
            for i, (x, y) in enumerate([(4, 4), (46, 4), (4, 26), (46, 26)], 1):
                b.add_footprint("MountingHole:MountingHole_3.2mm_M3", f"H{i}", (x, y))
            self.assertEqual(b.bbox(), (0, 0, 50, 30))
            result = p.save()
            drc = result.reports[p.pcb_path]
            self.assertNotIn("invalid_outline", [f.type for f in drc.errors], drc.summary())
            self.assertEqual(drc.errors, [], drc.summary())

    def test_outline_replaces_and_polygon(self):
        with TempDir() as d:
            p = new_project(d, "t")
            b = p.document(p.pcb_path)
            b.outline_rect(0, 0, 50, 30)
            b.outline_polygon([(0, 0), (40, 0), (40, 20), (20, 30), (0, 30)])
            self.assertEqual(len(b.edge_items()), 1)
            self.assertEqual(b.bbox(), (0, 0, 40, 30))
            result = p.save()
            self.assertNotIn("invalid_outline", [f.type for f in result.reports[p.pcb_path].errors])


if __name__ == "__main__":
    unittest.main()

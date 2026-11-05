"""BNAL の現状把握（bnal_status.py）: target ごとの問題の検出と、記録つきの整理。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import anal_status
import bnal_status
import server_anal

ANAL = "A@@2026_01{:02d}_1200_00_S1_10k_Sample#{:03d}.tdms"
BNAL = "B@@2026_01{:02d}_1300_S1_10k_Sample#{:03d}.tdms"
RAW = "S1_10k_Sample#{:03d} D_20250401_1040 UNAN#2_x_EXSV#1_20250401_1000_SGM  D0.540nm.tdms"


class BnalStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name) / "ex" / "S1" / "S1_10k_Sample" / "T"
        (self.t / "ANAL").mkdir(parents=True)
        self.logs = Path(self.tmp.name) / "logs"
        for module, name in ((bnal_status, "BNAL_LOGS"), (anal_status, "ANAL_LOGS"), (anal_status, "T_LOGS"),
                             (server_anal, "T_STOCK_LOGS")):
            p = mock.patch.object(module, name, self.logs)
            p.start()
            self.addCleanup(p.stop)
        for n in (1, 2, 3):
            (self.t / RAW.format(n)).write_bytes(b"raw")
            (self.t / "ANAL" / ANAL.format(1, n)).write_bytes(b"a")

    def tearDown(self):
        self.tmp.cleanup()

    def b(self, n, day=2, target="seq", content=b"b", result_folder=False):
        folder = self.t / f"BNAL@{target}"
        folder.mkdir(exist_ok=True)
        p = folder / BNAL.format(day, n)
        p.write_bytes(content)
        if result_folder:
            (folder / p.name.replace("_1300_", "_1300@").replace(".tdms", "")).mkdir()
        return p

    def kinds(self, target="seq"):
        return {i["kind"]: i for i in bnal_status.survey(self.t, 0) if i["target"] in (target, None)}

    def test_finished_target_has_nothing_to_do(self):
        for n in (1, 2, 3):
            self.b(n)
        self.assertEqual(self.kinds(), {})

    def test_pending_counts_anal_without_b(self):
        self.b(1)
        found = self.kinds()
        self.assertEqual((found["pending"]["count"], found["pending"]["action"]), (2, None))

    def test_empty_and_older_b_are_deleted_with_their_result_folder(self):
        old = self.b(1, day=2, result_folder=True)
        new = self.b(1, day=3)
        empty = self.b(2, content=b"")
        self.b(3)
        found = self.kinds()
        self.assertEqual(found["old_b"]["files"], [str(old)])
        self.assertEqual(found["empty_b"]["files"], [str(empty)])
        bnal_status.apply(self.t, "seq", "old_b")
        bnal_status.apply(self.t, "seq", "empty_b")
        self.assertTrue(new.exists() and not old.exists() and not empty.exists())
        kept = list((self.t / "BNAL@seq" / ".整理履歴").rglob("*"))
        self.assertTrue(any(p.name == old.name for p in kept) and any(p.is_dir() for p in kept))

    def test_b_without_anal_goes_to_old_and_b_before_a_reanalysis_is_only_reported(self):
        stale = self.b(1, day=1)  # B@@ 2026_0101_1300 is newer than A@@ 2026_0101_1200: fine
        self.assertNotIn("stale_b", self.kinds())
        (self.t / "ANAL" / ANAL.format(5, 1)).write_bytes(b"a2")  # #001 analysed again on 01-05
        orphan = self.b(9)
        found = self.kinds()
        # made from an earlier A@@: information for the sample only, never moved from here
        self.assertEqual((found["stale_b"]["files"], found["stale_b"]["action"], found["stale_b"]["target"]),
                         ([str(stale)], None, None))
        self.assertEqual(found["orphan_b"]["files"], [str(orphan)])
        with self.assertRaises(ValueError):
            bnal_status.apply(self.t, "seq", "stale_b")
        bnal_status.apply(self.t, "seq", "orphan_b")
        self.assertTrue(stale.exists() and (self.t / "BNAL@seq" / "old" / orphan.name).exists())
        self.assertEqual(self.kinds()["pending"]["count"], 2)  # #002, #003 (the stale #001 still counts as made)

    def test_sample_level_anal_stocked_and_empty_folders(self):
        (self.t / "ANAL" / "stocked").mkdir()
        (self.t / "ANAL" / "stocked" / ANAL.format(1, 4)).write_bytes(b"a")
        (self.t / "BNAL@nothing").mkdir()
        found = {i["kind"]: i for i in bnal_status.survey(self.t, 0)}
        self.assertEqual(found["anal_stocked"]["action"], "restore_anal")
        self.assertEqual(found["empty_folders"]["count"], 1)
        bnal_status.apply(self.t, None, "restore_anal")
        self.assertTrue((self.t / "ANAL" / ANAL.format(1, 4)).exists())

    def test_renumbering_in_anal_comes_first(self):
        (self.t / RAW.format(1).replace("EXSV#1", "EXSV#7")).write_bytes(b"other measurement")
        found = {i["kind"]: i for i in bnal_status.survey(self.t, 0)}
        self.assertIn("renumber", found)
        self.assertTrue(bnal_status.blocking(list(found.values())))


if __name__ == "__main__":
    unittest.main()

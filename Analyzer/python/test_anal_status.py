"""ANAL の現状把握（anal_status.py）: 問題の検出と、ここでできる対処（記録つきの移動・元に戻せる削除）。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import anal_status
import server_anal
from shared import file_moves

RAW = "S1_10k_Sample#{:03d} D_20250401_1040 UNAN#2Pex1n2_x_EXSV#{}_20250401_{:04d}_SGM  D0.540nm.tdms"
ANAL = "A@@2026_01{:02d}_1200_00_S1_10k_Sample#{:03d}.tdms"


class AnalStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name) / "ex" / "S1" / "S1_10k_Sample" / "T"
        (self.t / "ANAL").mkdir(parents=True)
        self.logs = Path(self.tmp.name) / "logs"
        for target in ("ANAL_LOGS", "T_LOGS"):
            p = mock.patch.object(anal_status, target, self.logs)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(server_anal, "T_STOCK_LOGS", self.logs)
        p.start()
        self.addCleanup(p.stop)
        # the raw file behind an A@@ is read from its AR Table; here it is the name the test gives
        self.sources = {}
        p = mock.patch.object(server_anal, "anal_source", lambda path: self.sources.get(Path(path).name))
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def raw(self, n, run=1, content=b"raw", where=""):
        p = self.t / where / RAW.format(n, run, 1000 + run)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return p

    def anal(self, n, day=1, where="ANAL", source=None, content=b"anal"):
        p = self.t / where / ANAL.format(day, n)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        self.sources[p.name] = source or RAW.format(n, 1, 1001)
        return p

    def kinds(self):
        return {i["kind"]: i for i in anal_status.survey(self.t, 0)}

    def test_a_tidy_sample_has_only_its_pending_files(self):
        for n in (1, 2, 3):
            self.raw(n)
        self.anal(1)
        self.anal(2)
        found = self.kinds()
        self.assertEqual(list(found), ["pending"])
        self.assertEqual(found["pending"]["count"], 1)

    def test_results_in_t_move_to_anal_and_can_be_undone(self):
        self.raw(1)
        stray = self.anal(1, where="")  # an A@@ directly in T
        found = self.kinds()
        self.assertIn("results_in_t", found)
        lines = anal_status.apply(self.t, "results_in_t", 0)
        self.assertTrue((self.t / "ANAL" / stray.name).exists() and not stray.exists(), lines)
        self.assertNotIn("results_in_t", self.kinds())
        journal = sorted(self.logs.glob("*.json"))[-1]
        file_moves.execute_moves(file_moves.undo_plan(journal), self.logs)
        self.assertTrue(stray.exists())

    def test_empty_raw_goes_to_broken(self):
        empty = self.raw(1, content=b"")
        self.assertIn("empty_raw", self.kinds())
        anal_status.apply(self.t, "empty_raw", 0)
        self.assertTrue((self.t / "broken" / empty.name).exists())

    def test_reanalyses_of_one_raw_file_keep_the_newest_and_are_restorable(self):
        self.raw(1)
        old, new = self.anal(1, day=1), self.anal(1, day=2)
        zero = self.anal(2, day=1, content=b"")
        found = self.kinds()
        self.assertEqual(sorted(found["old_anal"]["files"]), sorted([str(old), str(zero)]))
        self.assertNotIn("renumber_anal", found)
        anal_status.apply(self.t, "old_anal", 0)
        self.assertTrue(new.exists() and not old.exists() and not zero.exists())
        self.assertTrue(list((self.t / "ANAL" / ".整理履歴").rglob(old.name)))

    def test_one_number_for_two_measurements_must_be_renumbered_by_hand(self):
        self.raw(1, run=1, content=b"first")
        self.raw(1, run=2, content=b"second")
        found = self.kinds()
        self.assertIn("renumber", found)
        self.assertIsNone(found["renumber"]["action"])
        self.assertEqual(anal_status.blocking(list(found.values()))[0]["kind"], "renumber")
        # A@@ of different raw files with one number: also by hand
        self.anal(2, day=1, source="X#002 a.tdms")
        self.anal(2, day=2, source="X#002 b.tdms")
        self.assertIn("renumber_anal", self.kinds())

    def test_identical_copies_keep_one(self):
        keep = self.raw(1, run=1, content=b"same")
        copy = self.raw(1, run=2, content=b"same")
        found = self.kinds()
        self.assertEqual(found["raw_copy"]["files"], [str(copy)])
        anal_status.apply(self.t, "raw_copy", 0)
        self.assertTrue(keep.exists() and (self.t / "duplicate" / copy.name).exists())

    def test_stocked_folders_go_back(self):
        self.raw(1, where="stocked")
        self.anal(1)
        back = self.anal(2, where="ANAL/stocked")
        self.raw(2)
        found = self.kinds()
        self.assertIn("t_stocked", found)
        self.assertEqual(found["anal_stocked"]["files"], [str(back)])
        anal_status.apply(self.t, "restore_t", 0)
        anal_status.apply(self.t, "restore_anal", 0)
        self.assertTrue((self.t / RAW.format(1, 1, 1001)).exists() and (self.t / "ANAL" / back.name).exists())

    def test_anal_without_raw_is_reported(self):
        self.anal(1)
        self.assertIn("解析用コピー", self.kinds()["no_raw"]["detail"])

    def test_redo_sets_every_anal_aside(self):
        self.raw(1)
        self.anal(1)
        self.anal(2, where="ANAL/stocked")
        anal_status.redo(self.t)
        self.assertEqual(anal_status.redo_files(self.t), [])
        self.assertEqual(self.kinds()["pending"]["count"], 1)


if __name__ == "__main__":
    unittest.main()

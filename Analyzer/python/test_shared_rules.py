"""Analyzer が shared/（実験ファイルDB・移動の記録・測定番号の規則）と同じ取り決めで動くことの確認。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import server_anal
import server_bnal
from shared import REPO_ROOT, catalog, file_moves, rack_store


class SharedRulesTests(unittest.TestCase):
    def test_measurement_numbers_match_the_database(self):
        for name in ("KSN3_10k_Sample#001 D_20250401_1040 UNAN#2Pex1n2_EXSV#10_x.tdms",
                     "A@@2026_0902_0701_00_KSN1_10k_Sample#012.tdms", "old#7.tdms"):
            self.assertEqual(server_anal.original_key(name), rack_store.measurement_key(name))
            self.assertEqual(server_anal.anal_key(name), rack_store.measurement_key(name))

    def test_one_measurement_one_key_across_name_styles(self):
        key = rack_store.measurement_key
        # LabVIEW ANAL keeps the full original name, the Python ANAL its first word: the same measurement
        raw = "L3D1tryptophan#013 D_20191121_0824 TOAN#1Pex1n1_LD-tryptophan_EXSV#1_x_DV_.tdms"
        self.assertEqual({key(raw), key("A@@2019_1121_1806_02_" + raw), key("A@@2026_1030_2138_47_L3D1tryptophan#013.tdms")},
                         {"L3D1tryptophan#013"})
        # series numbered again from #001 stay apart
        self.assertNotEqual(key("A@@2026_1104_1250_36_ABex1T01#001.tdms"), key("ABex1T02#001 20131105_x_001.lvm.tdms"))
        self.assertEqual(key("A@@2026_1104_1250_36_ABex1T01#001.tdms"), key("ABex1T01#001 20131105_x_001.lvm.tdms"))
        # the usual sample numbering stays a number, for A@@ and B@@ alike
        self.assertEqual({key("KSN3_10k_Sample#007 D_x UNAN#2Pex1n2_EXSV#10_x.tdms"),
                          key("A@@2026_0902_0701_00_KSN3_10k_Sample#007.tdms"),
                          key("B@@2026_0902_0735_KSN3_10k_Sample#007.tdms")}, {7})

    def test_roots_and_db_are_the_shared_ones(self):
        self.assertEqual(server_anal.identity(r"\\QTserver\analysis" + "\\"), catalog.root_identity(r"\\qtserver\Analysis"))
        self.assertEqual(Path(server_anal.DEFAULT_DB), REPO_ROOT / "database" / "inventory.sqlite3")

    def test_journals_go_to_the_file_manager_history(self):
        self.assertEqual(server_anal.T_STOCK_LOGS, REPO_ROOT / "filemanager" / "t_stock_logs")
        self.assertEqual(server_anal.ANAL_LOGS, REPO_ROOT / "filemanager" / "anal_logs")

    def test_t_stocked_restore_is_journaled_and_undoable(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = dict(root=tmp, experiment="ex", sample="KSN1")
            t = server_anal.t_dir(r)
            (t / "stocked").mkdir(parents=True)
            for n in (1, 2):
                (t / "stocked" / f"KSN1_10k_Sample#{n:03d} D_x.tdms").write_bytes(b"data")
                (t / "stocked" / f"KSN1_10k_Sample#{n:03d} D_x.tdms_index").write_bytes(b"i")
            plan, skipped, text = server_anal.restore_plan(r)
            self.assertEqual((len(plan["operations"]), skipped), (4, []))
            journal = server_anal.execute_restore(plan, Path(tmp) / "logs")
            self.assertEqual(sorted(p.name for p in t.iterdir() if p.is_file())[0], "KSN1_10k_Sample#001 D_x.tdms")
            self.assertEqual(json.loads(Path(journal).read_text(encoding="utf-8"))["status"], "完了")
            file_moves.execute_moves(file_moves.undo_plan(journal), Path(tmp) / "logs")
            self.assertEqual(len(list((t / "stocked").iterdir())), 4)


class JudgmentTests(unittest.TestCase):
    """The ANAL / BNAL states shown here follow shared.progress (the database's ANAL進捗 / BNAL進捗)."""

    def load(self, *samples):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "inventory.sqlite3"
            con = sqlite3.connect(db)
            try:
                rack_store.ensure_tables(con)
                con.executemany("INSERT INTO rack_samples(root,experiment,sample,t_tdms,t_stocked_tdms,anal_tdms,"
                                "anal_stocked_tdms,anal_missing_checked) VALUES(?,?,?,?,?,?,?,1)",
                                [(root, "ex", sample, t, t_st, anal, anal_st)
                                 for sample, root, t, t_st, anal, anal_st in samples])
                con.commit()
            finally:
                con.close()
            rows = server_anal.load_samples(db)
        return {(r["sample"], r["root"]): r for r in rows}

    def test_states_include_excess_and_unknown(self):
        rows = self.load(("over", "r1", 50, 0, 100, 0), ("nothing_counted", "r1", None, None, 5, 0),
                         ("all_stocked", "r1", 0, 10, 6, 0), ("copy", "r1", 0, 0, 6, 0), ("empty", "r1", 0, 0, 0, 0))
        self.assertEqual({k[0]: r["state"] for k, r in rows.items()},
                         dict(over=server_anal.EXCESS, nothing_counted=server_anal.UNKNOWN,
                              all_stocked=server_anal.RUNNING, copy=server_anal.COPY, empty=server_anal.EMPTY))

    def test_sample_warnings_mark_the_right_share(self):
        rows = self.load(("s", "main", 10, 0, 10, 0), ("s", "other", 12, 0, 12, 0), ("s", "copy", 0, 0, 7, 0))
        self.assertEqual(rows[("s", "copy")]["flags"], ["差異", "コピー不足"])
        self.assertEqual(rows[("s", "main")]["flags"], ["差異"])

    def test_information_warnings_do_not_stop_automatic_start(self):
        r = dict(state=server_anal.RUNNING, reachable=True, flags=["差異", "コピー不足"])
        self.assertTrue(server_anal.auto_pick(r))
        self.assertFalse(server_anal.auto_pick(dict(r, flags=["混在"])))

    def test_bnal_states_never_hide_uncounted_or_missing_anal(self):
        r = dict(anal_tdms=10, anal_stocked_tdms=2, bnal_folders=2)
        self.assertEqual(server_bnal.combo_state(r, "a", {"a": 12, "b": 3}), "完了")
        self.assertEqual(server_bnal.combo_state(r, "b", {"a": 12, "b": 3}), "進行中")
        self.assertEqual(server_bnal.combo_state(r, "a", {"a": None, "b": 3}), server_bnal.UNCOUNTED)
        self.assertEqual(server_bnal.combo_state(r, "c", {"a": 12, "b": 3}), server_bnal.UNSTARTED)
        self.assertEqual(server_bnal.combo_state(r, "c", {"a": 12}), server_bnal.UNCONFIRMED)
        self.assertEqual(server_bnal.combo_state(dict(r, anal_tdms=None), "a", {"a": 20, "b": 3}), server_bnal.NO_ANAL)


if __name__ == "__main__":
    unittest.main()

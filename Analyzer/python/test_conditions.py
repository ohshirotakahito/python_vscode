"""条件セット（conditions.py）: 既定値・日付付きの追加・「現在の条件」の切り替えと履歴。"""
import tempfile
import unittest
from datetime import date
from pathlib import Path

import conditions


class ConditionSetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "conditions.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_is_current_until_a_set_is_added(self):
        for kind in conditions.KINDS:
            cur = conditions.current(kind, self.path)
            self.assertEqual(cur["name"], conditions.DEFAULT_NAMES[kind])
            self.assertEqual(cur["values"], conditions.DEFAULT_VALUES[kind])
        self.assertEqual(conditions.DEFAULT_VALUES["anal"]["u_th_xn"], 6.0)
        self.assertEqual(conditions.DEFAULT_VALUES["bnal"]["right_read_min"], 5)

    def test_new_set_is_dated_current_and_logged_and_old_sets_stay(self):
        values = dict(conditions.DEFAULT_VALUES["anal"], u_th_xn=5.5)
        s = conditions.add_set("anal", "U Th 5.5", values, "ノイズの多い装置用", path=self.path)
        self.assertEqual(s["since"], date.today().isoformat())
        self.assertEqual(conditions.current("anal", self.path)["values"]["u_th_xn"], 5.5)
        self.assertEqual(conditions.differences("anal", s["values"]), [("u_th_xn", 5.5, 6.0)])
        conditions.set_current("anal", conditions.DEFAULT_NAMES["anal"], self.path)
        self.assertEqual(conditions.current("anal", self.path)["name"], conditions.DEFAULT_NAMES["anal"])
        self.assertEqual([x["name"] for x in conditions.sets("anal", self.path)],
                         [conditions.DEFAULT_NAMES["anal"], "U Th 5.5"])
        self.assertEqual([h["action"] for h in conditions.load(self.path)["history"]], ["追加", "現在にする"])
        # the other kind is untouched
        self.assertEqual(conditions.current("bnal", self.path)["name"], conditions.DEFAULT_NAMES["bnal"])

    def test_names_are_unique_and_values_checked(self):
        with self.assertRaises(ValueError):
            conditions.add_set("anal", conditions.DEFAULT_NAMES["anal"], conditions.DEFAULT_VALUES["anal"], path=self.path)
        with self.assertRaises(ValueError):
            conditions.add_set("anal", "bad", dict(conditions.DEFAULT_VALUES["anal"], bl_selection="?"), path=self.path)
        with self.assertRaises(ValueError):
            conditions.add_set("bnal", "bad", dict(conditions.DEFAULT_VALUES["bnal"], noise_max=1000), path=self.path)

    def test_bnal_set_keeps_the_bc_file_per_list_kind(self):
        values = dict(conditions.DEFAULT_VALUES["bnal"], right_read_min=6, bc_files={"RNA": "BC_RNA.csv"})
        conditions.add_set("bnal", "RR 6", values, path=self.path)
        cur = conditions.current("bnal", self.path)["values"]
        self.assertEqual((cur["right_read_min"], cur["bc_files"]), (6, {"RNA": "BC_RNA.csv"}))


if __name__ == "__main__":
    unittest.main()

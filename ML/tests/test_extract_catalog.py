"""保存済み特徴量の形式と抽出イベント数を検証する。"""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.extract_catalog import extracted_event_counts, record_extraction, load_history
from common.incremental_save import merge_new_events


class ExtractCatalogTests(unittest.TestCase):
    def test_source_details_and_legacy_history_survive_append(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch("common.extract_catalog.paths.feature_dir", return_value=root):
                source = "//server/analysis/experiment/001/A@@2025_0402_0943_36_001_10k_Sample#007.tdms"
                record_extraction("rmb", "server", "analysis", "experiment", "001",
                                  [(source, 1, 3)], n_total=3)
                saved = pd.read_csv(root / "extraction_files.csv", dtype={"sample": str})
                self.assertEqual(saved.loc[0, "file_number"], 7)
                self.assertEqual(saved.loc[0, "sample"], "001")
                self.assertEqual(saved.loc[0, "server"], "server")
                self.assertEqual(saved.loc[0, "ex"], "experiment")
                self.assertEqual(saved.loc[0, "anal_path"], source)
                self.assertTrue(saved.loc[0, "extracted_at"].endswith("+09:00"))
                saved.drop(columns="file_number").to_csv(root / "extraction_files.csv", index=False)
                _, legacy = load_history("rmb")
                self.assertEqual(legacy.loc[0, "file_number"], 7)
                record_extraction("rmb", "other", "analysis", "other_ex", "001",
                                  [("unknown.tdms", 0, 0)], n_total=0)
                _, files = load_history("rmb")
                self.assertEqual(len(files), 2)
                self.assertEqual(files.loc[0, "sample"], "001")
                self.assertEqual(files.loc[0, "file_number"], 7)
                self.assertTrue(pd.isna(files.loc[1, "file_number"]))
                self.assertEqual(files.loc[1, "server"], "other")

    def counts_for(self, array):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            np.save(root / "BBB_10k_Sample_ANAL_rmb.npy", array)
            with patch("common.extract_catalog.paths.feature_dir", return_value=root):
                return extracted_event_counts("rmb", "BBB")

    def test_empty_extraction_can_be_scanned(self):
        array, _, added, skipped = merge_new_events(None, [])
        self.assertEqual((added, skipped), (0, 0))
        self.assertEqual(self.counts_for(array), {})

    def test_empty_two_dimensional_array(self):
        self.assertEqual(self.counts_for(np.empty((0, 18))), {})

    def test_counts_are_separated_by_experiment(self):
        rows = [
            [0, "BBB_10k_Sample#001", "20250325_1044_extra"],
            [1, "BBB_10k_Sample#001", "20250325_1044_other"],
            [2, "BBB_10k_Sample#001", "20250401_1200_extra"],
        ]
        expected = {("BBB_10k_Sample#001", "20250325_1044"): 2,
                    ("BBB_10k_Sample#001", "20250401_1200"): 1}
        for dtype in (str, object):
            with self.subTest(dtype=dtype):
                self.assertEqual(self.counts_for(np.array(rows, dtype=dtype)), expected)

    def test_invalid_shapes_warn_without_crashing(self):
        for array in (np.array([1, 2, 3]), np.ones((2, 2)), np.array(1)):
            with self.subTest(shape=array.shape), patch("builtins.print") as warning:
                self.assertEqual(self.counts_for(array), {})
                warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import experiment_data_counts


class DataCountsTest(unittest.TestCase):
    def test_direct_tdms_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(experiment_data_counts(folder), (0, 0))
            ten = folder / 'EXSV' / 'T'
            hundred = folder / 'EXZSV'
            ten.mkdir(parents=True)
            hundred.mkdir()
            for name in ('D_1.tdms', 'PM_2.TDMS', 'other.txt'):
                (ten / name).touch()
            (hundred / 'Z_1.tdms').touch()
            (folder / 'EXSV' / 'outside.tdms').touch()
            for parent in (ten, hundred):
                (parent / 'nested.tdms').mkdir()
                (parent / 'nested.tdms' / 'hidden.tdms').touch()
            self.assertEqual(experiment_data_counts(folder), (2, 1))

    def test_unreadable_directory_is_not_zero(self):
        with patch.object(Path, 'iterdir', side_effect=PermissionError):
            self.assertEqual(experiment_data_counts(Path('experiment')),
                             ('取得不可', '取得不可'))


if __name__ == '__main__':
    unittest.main()

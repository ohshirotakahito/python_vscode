from datetime import datetime
from pathlib import Path
import tempfile
import unittest
import source_cache


class SourceCacheTest(unittest.TestCase):
    def test_save_and_load_per_machine_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            self.assertIsNone(source_cache.load('AXN3', 'Recent_Data', path))
            source_cache.save('AXN3', 'Recent_Data', r'\server\backup', [('20261015_1155 A', (2, 16)), ('B', (0, '取得不可'))],
                              path, now=datetime(2026, 10, 17, 9, 5, 3))
            source_cache.save('AXN3', 'Archived_Data', r'\server\sq', [('C', (1, 8))], path)
            entry = source_cache.load('AXN3', 'Recent_Data', path)
            self.assertEqual(entry['updated'], '2026/10/17 09:05:03')
            self.assertEqual(entry['root'], r'\server\backup')
            self.assertEqual(entry['folders'], [['20261015_1155 A', 2, 16], ['B', 0, '取得不可']])
            self.assertEqual(source_cache.load('AXN3', 'Archived_Data', path)['folders'], [['C', 1, 8]])
            self.assertIsNone(source_cache.load('Other', 'Recent_Data', path))

    def test_broken_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            path.write_text('{broken', encoding='utf-8')
            self.assertIsNone(source_cache.load('AXN3', 'Recent_Data', path))
            source_cache.save('AXN3', 'Recent_Data', 'r', [], path)
            self.assertEqual(source_cache.load('AXN3', 'Recent_Data', path)['folders'], [])


if __name__ == '__main__':
    unittest.main()

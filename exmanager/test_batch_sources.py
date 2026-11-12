import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import refresh_all_sources
import source_cache


class BatchSourcesTest(unittest.TestCase):
    def test_scope_counts_and_failure_keeps_cache_and_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            recent, archived = base / 'recent', base / 'archived'
            (recent / 'Experiment' / 'EXSV' / 'T').mkdir(parents=True)
            (recent / 'Experiment' / 'EXSV' / 'T' / 'one.tdms').touch()
            (archived / 'Older').mkdir(parents=True)
            machines = {'broken': {'Tmp_backup': str(base / 'missing'), 'SQ_backup': ''},
                        'good': {'Tmp_backup': str(recent), 'SQ_backup': str(archived)}}
            cache = base / 'cache.json'
            save = source_cache.save
            save('broken', 'Recent_Data', 'old', [('keep', (2, 3))], path=cache)
            events = []
            with patch.object(source_cache, 'save', side_effect=lambda *a: save(*a, path=cache)):
                results = refresh_all_sources(machines, notify=lambda *e: events.append(e))
                self.assertEqual([ok for ok, _ in results], [False, True])
                self.assertIsNone(source_cache.load('good', 'Archived_Data', path=cache))
                self.assertEqual(source_cache.load('good', 'Recent_Data', path=cache)['folders'],
                                 [['Experiment', 1, 0]])
                self.assertEqual(source_cache.load('broken', 'Recent_Data', path=cache)['folders'],
                                 [['keep', 2, 3]])
                results = refresh_all_sources(machines, include_archived=True)
                self.assertEqual([ok for ok, _ in results], [False, False, True, True])
                self.assertEqual(source_cache.load('good', 'Archived_Data', path=cache)['folders'],
                                 [['Older', 0, 0]])
            self.assertEqual(events[-1][0], 'progress')
            self.assertEqual(events[-1][2:4], (2, 2))

    def test_save_failure_does_not_stop_next_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            machines = {name: {'Tmp_backup': tmp} for name in ('A', 'B')}
            with patch.object(source_cache, 'save', side_effect=[PermissionError('locked'), {'updated': 'now'}]):
                results = refresh_all_sources(machines)
            self.assertEqual([ok for ok, _ in results], [False, True])

import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

from chipdb.cleanup import HEADER, preview, apply, merge_files, canonical, reconcile_registry


def write(path, rows, code='chip', gap='1'):
    header = list(HEADER)
    header[0], header[6] = code, gap
    content = [header, *rows, ['Break', '-', '-', '-', '', '', ''], ['END', '-', '-', '-', '', '', '']]
    path.write_bytes(''.join(','.join(row) + '\r\n' for row in content).encode('cp932'))


def event(value='', second='', number='1'):
    return ['Experiment', 'OK', '20261116_1200', 'User', 'EX1', 'FC#' + number, '1', value, second]


class CleanupTests(unittest.TestCase):
    def test_fills_columns_and_keeps_unique_history_and_greatest_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / 'chip.csv', root / 'chip (2).csv'
            write(first, [event('', 'extra')], gap='1')
            write(second, [event('2', ''), event('3', number='2')], gap='5')
            body, _ = merge_files([first, second], 'chip')
            rows = [line.split(',') for line in body.decode('cp932').splitlines()]
            self.assertEqual(rows[0][6], '5')
            self.assertEqual(rows[1][7:], ['2', 'extra'])
            self.assertEqual(len([row for row in rows if row[0] == 'Experiment']), 2)

    def test_does_not_guess_conflicting_nonempty_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / 'chip.csv', [event('2')])
            write(root / 'chip (2).csv', [event('9')])
            plans = preview(root)
            self.assertIn('相違', plans[0]['error'])
            self.assertEqual(len(list(root.glob('*.csv'))), 2)

    def test_preserves_repeated_records_inside_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / 'chip.csv', [event('2'), event('2')])
            write(root / 'chip (2).csv', [event('2')])
            body, _ = merge_files([root / 'chip.csv', root / 'chip (2).csv'], 'chip')
            self.assertEqual(body.count(b'Experiment'), 2)

    def test_apply_backs_up_originals_and_removes_duplicate_only_after_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'DB'
            root.mkdir()
            write(root / 'chip.csv', [event('2')])
            write(root / 'chip_DESKTOP-TEST_Conflict.csv', [event('3', number='2')])
            originals = {p.name: p.read_bytes() for p in root.glob('*.csv')}
            session = apply(root, preview(root))
            self.assertEqual([p.name for p in root.glob('*.csv')], ['chip.csv'])
            self.assertEqual((root / 'chip.csv').read_bytes().count(b'Experiment'), 2)
            for name, body in originals.items():
                self.assertEqual((session / 'originals' / name).read_bytes(), body)
            self.assertEqual(preview(root), [])

    def test_external_change_stops_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'DB'
            root.mkdir()
            write(root / 'chip.csv', [event('2')])
            write(root / 'chip (2).csv', [event('2')])
            plans = preview(root)
            (root / 'chip.csv').write_bytes(b'changed by another process')
            with self.assertRaisesRegex(ValueError, '変更'):
                apply(root, plans)
            self.assertEqual((root / 'chip.csv').read_bytes(), b'changed by another process')
            self.assertTrue((root / 'chip (2).csv').exists())

    def test_approved_identifiers_and_suffixes(self):
        for source, target in [('sc49-3310-51', 'SC49-3310-51'),
                               ('SC91-00000', 'SC91-0000-10'),
                               ('SC30-2315-51', 'SC30-2315-35'),
                               ('SC25-2115-34', 'SC25-2115-33'),
                               ('SC91-4419-05 (3)', 'SC91-4419-05'),
                               ('chip_DESKTOP-ABC_conflict', 'chip')]:
            self.assertEqual(canonical(source), target)

    def test_exact_copy_with_unrelated_filename_is_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root / 'chip.csv', [event('2')])
            write(root / 'chip (2).csv', [event('3', number='2')])
            (root / '1126.csv').write_bytes((root / 'chip (2).csv').read_bytes())
            plan = preview(root)[0]
            self.assertEqual(len(plan['sources']), 3)
            self.assertEqual(len(plan['remove']), 2)

    def test_known_corrupt_file_keeps_all_readable_experiments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'SC77-4219-23.csv'
            content = [['\0' * 20, '', '', '', '', '', '0', '', '', '', '', '', ''],
                       event('', ''), event('', ''), ['\0' * 20, '', '', '', '', '', '', '', '', '', '', '', '']]
            path.write_bytes(''.join(','.join(row) + '\r\n' for row in content).encode('cp932'))
            plans = preview(root)
            self.assertEqual(plans[0]['error'], '')
            body = plans[0]['body']
            self.assertNotIn('\0', body)
            self.assertTrue(body.startswith('SC77-4219-23,Status,Date'))
            self.assertEqual(body.count('Experiment'), 2)
            self.assertTrue(body.splitlines()[-1].startswith('END,'))

    def test_registry_alias_removed_and_manual_data_retained(self):
        from chipdb.registry import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'ChipDB' / 'DB'
            root.mkdir(parents=True)
            write(root / 'chip.csv', [event('2')])
            write(root / 'chip (2).csv', [event('3', number='2')])
            store = Store(root)
            store.path.parent.mkdir(parents=True)
            original = dict(schema_version=1, revision=1, chips=[
                dict(key='a', code='chip', state='未設定', location=''),
                dict(key='b', code='chip (2)', state='未設定', location='Shelf A')])
            store.path.write_text(json.dumps(original), encoding='utf-8')
            session = apply(root, preview(root))
            updated = store.load()
            self.assertEqual(updated['revision'], 2)
            self.assertEqual(len(updated['chips']), 1)
            self.assertEqual(updated['chips'][0]['location'], 'Shelf A')
            self.assertEqual(updated['chips'][0]['code'], 'chip')
            self.assertEqual(updated['chips'][0]['aliases'], ['chip (2)'])          # 直す前の表記は別名に残る
            self.assertEqual(json.loads((session / 'chip_info_before.json').read_text(encoding='utf-8')), original)
            self.assertFalse(store.path.with_suffix('.lock').exists())

    def test_rename_by_alias_keeps_old_spelling_as_alias(self):
        from chipdb import cleanup
        from chipdb.registry import Store
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(cleanup.ALIASES, {'sc080-4307-12': 'SC80-4307-12'}):
            root = Path(directory) / 'ChipDB' / 'DB'
            root.mkdir(parents=True)
            write(root / 'SC080-4307-12.csv', [event('2')], code='SC080-4307-12')
            store = Store(root)
            store.path.parent.mkdir(parents=True)
            store.path.write_text(json.dumps(dict(schema_version=1, revision=1, chips=[
                dict(key='a', code='SC080-4307-12', state='未設定', origin='SCREEN')])), encoding='utf-8')
            apply(root, preview(root))
            self.assertEqual(sorted(p.name for p in root.glob('*.csv')), ['SC80-4307-12.csv'])
            self.assertTrue((root / 'SC80-4307-12.csv').read_bytes().startswith(b'SC80-4307-12,'))
            chip = store.load()['chips'][0]
            self.assertEqual((chip['code'], chip['aliases'], chip['origin']), ('SC80-4307-12', ['SC080-4307-12'], 'SCREEN'))

    def test_registry_manual_conflict_stops_without_overwriting_registry(self):
        from chipdb.registry import Store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'ChipDB' / 'DB'
            root.mkdir(parents=True)
            write(root / 'chip.csv', [event('2')])
            write(root / 'chip (2).csv', [event('3', number='2')])
            plans = preview(root)
            store = Store(root)
            store.path.parent.mkdir(parents=True)
            original = dict(schema_version=1, revision=1, chips=[
                dict(key='a', code='chip', state='保管中', location='Shelf A'),
                dict(key='b', code='chip (2)', state='保管中', location='Shelf B')])
            body = json.dumps(original).encode('utf-8')
            store.path.write_bytes(body)
            with self.assertRaisesRegex(ValueError, '管理情報が競合'):
                reconcile_registry(root, plans, Path(directory))
            self.assertEqual(store.path.read_bytes(), body)


if __name__ == '__main__':
    unittest.main()

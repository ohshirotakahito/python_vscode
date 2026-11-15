import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from shared import ex_info_store as store


def initial():
    return dict(schema_version=1, revision=4, owner_names=['Alice'], extra={'keep': True},
        projects=[dict(key='P', name='Project', owner_names=['Alice'], description='original', active=True)],
        samples=[dict(key='S', project_key='P', name='Sample', medium='water', description='sample')],
        locations=[dict(key='L', project_key='P', sample_key='S', root_path=r'\\Server\analysis',
                        experiment_folder='Exp', sample_folder='S')])


class ExInfoStoreTests(TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'ex_info.json'
        self.original = initial()
        self.path.write_text(json.dumps(self.original), encoding='utf-8')

    def test_edit_preserves_identifiers_locations_unknown_fields_and_backup(self):
        before = self.path.read_bytes()
        result = store.save_changes(self.original, {('projects', 'P'): dict(owner_names=['Bob']),
            ('samples', 'S'): dict(description='new explanation')}, self.path)
        self.assertEqual(result['revision'], 5)
        self.assertEqual(result['projects'][0]['owner_names'], ['Bob'])
        self.assertEqual(result['samples'][0]['description'], 'new explanation')
        self.assertEqual(result['samples'][0]['medium'], 'water')
        self.assertEqual(result['locations'], self.original['locations'])
        self.assertEqual(result['extra'], {'keep': True})
        self.assertIn('Bob', result['owner_names'])
        (backup,) = (self.path.parent / 'backups').glob('*.json')
        self.assertEqual(backup.read_bytes(), before)
        self.assertFalse(self.path.with_suffix('.lock').exists())

    def test_unrelated_concurrent_changes_are_merged_and_noop_fields_do_not_revert_them(self):
        store.save_changes(self.original, {('projects', 'P'): dict(description='changed elsewhere')}, self.path)
        result = store.save_changes(self.original, {('projects', 'P'):
            dict(description='original', owner_names=['Bob'])}, self.path)
        self.assertEqual(result['projects'][0]['description'], 'changed elsewhere')
        self.assertEqual(result['projects'][0]['owner_names'], ['Bob'])
        self.assertEqual(result['revision'], 6)

    def test_same_field_conflict_does_not_overwrite(self):
        store.save_changes(self.original, {('projects', 'P'): dict(owner_names=['Bob'])}, self.path)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '同じ項目'):
            store.save_changes(self.original, {('projects', 'P'): dict(owner_names=['Carol'])}, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_suffix('.lock').exists())

    def test_axn_lock_and_sync_conflict_copy_block_saving(self):
        lock = self.path.with_suffix('.lock')
        lock.write_text('locked', encoding='utf-8')
        with self.assertRaisesRegex(OSError, '保存中'):
            store.save_changes(self.original, {('projects', 'P'): dict(description='x')}, self.path)
        self.assertEqual(lock.read_text(encoding='utf-8'), 'locked')
        lock.unlink()
        (self.path.parent / 'ex_info_Conflict.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(OSError, '競合'):
            store.save_changes(self.original, {('projects', 'P'): dict(description='x')}, self.path)
        self.assertEqual(store.load(self.path)['revision'], 4)

    def test_replace_failure_keeps_original_and_releases_lock(self):
        before = self.path.read_bytes()
        with patch.object(store.os, 'replace', side_effect=PermissionError('open')):
            with self.assertRaises(PermissionError):
                store.save_changes(self.original, {('samples', 'S'): dict(description='x')}, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_suffix('.lock').exists())
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_noop_does_not_write_and_protected_fields_are_rejected(self):
        before = self.path.read_bytes()
        store.save_changes(self.original, {('projects', 'P'): dict(description='original')}, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.path.parent / 'backups').exists())
        with self.assertRaises(ValueError):
            store.save_changes(self.original, {('projects', 'P'): dict(name='Renamed')}, self.path)
        with self.assertRaises(ValueError):
            store.save_changes(self.original, {('projects', 'P'): dict(owner_names=['bad,name'])}, self.path)

    def test_unknown_schema_is_not_written(self):
        original = copy.deepcopy(self.original)
        original['schema_version'] = 2
        self.path.write_text(json.dumps(original), encoding='utf-8')
        with self.assertRaises(ValueError):
            store.save_changes(self.original, {('samples', 'S'): dict(description='x')}, self.path)


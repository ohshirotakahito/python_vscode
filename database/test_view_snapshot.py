"""ユーザー端末の閲覧用DB: 小さい表だけを写し、② の一覧を横に置く。ユーザー端末は書き込まない。"""
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from database import app, view_snapshot


class SnapshotTests(TestCase):
    def test_small_tables_are_copied_and_file_tables_left_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'inventory.sqlite3'
            with closing(sqlite3.connect(source)) as db:
                db.execute('CREATE TABLE files (source_id INTEGER, relative_path TEXT)')
                db.execute('CREATE TABLE rack_samples (root TEXT, experiment TEXT, sample TEXT)')
                db.execute('CREATE INDEX rack_index ON rack_samples(experiment)')
                db.execute("INSERT INTO files VALUES (1, 'a.tdms')")
                db.execute("INSERT INTO rack_samples VALUES ('r', 'E', 'S')")
                db.execute('PRAGMA user_version=2')
                db.commit()
            destination = Path(directory) / 'out' / view_snapshot.FILE
            inventory = [[], [], [], [dict(group='Recent_Data')], [dict(name='ID')], [], {}]
            self.assertEqual(view_snapshot.export(source, inventory, destination), 1)
            with closing(sqlite3.connect(destination)) as db:
                self.assertEqual(db.execute('SELECT * FROM rack_samples').fetchall(), [('r', 'E', 'S')])
                self.assertEqual(db.execute('SELECT count(*) FROM files').fetchone(), (0,))
                self.assertEqual(db.execute('PRAGMA user_version').fetchone(), (2,))
                self.assertIn('rack_index', [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")])
            saved = json.loads(view_snapshot.cache_path(destination).read_text(encoding='utf-8'))
            self.assertEqual(saved['inventory'][4], [dict(name='ID')])
            self.assertTrue(view_snapshot.published_at(destination).endswith('UTC'))
            self.assertFalse(list(destination.parent.glob('*.tmp')))

    def test_user_terminals_are_read_only_and_the_list_can_be_searched(self):
        with patch.dict('os.environ', {}, clear=False) as env:
            env.pop('PYVSCODE_DATABASE_VIEWER', None)
            with patch('shared.terminal_profile.load', return_value={'role': 'user', 'apps': ['database']}):
                self.assertTrue(app.read_only_terminal())
            with patch('shared.terminal_profile.load', return_value={'role': 'developer', 'apps': []}):
                self.assertFalse(app.read_only_terminal())
            env['PYVSCODE_DATABASE_VIEWER'] = '1'
            with patch('shared.terminal_profile.load', return_value={'role': 'developer', 'apps': []}):
                self.assertTrue(app.read_only_terminal())
        inventory = [[], [], [], [dict(root='a')], [dict(name='20261110 UNAN'), dict(name='other')], [], {}]
        self.assertEqual(app.filter_inventory(inventory, 'unan')[4], [dict(name='20261110 UNAN')])
        self.assertIs(app.filter_inventory(inventory, ''), inventory)

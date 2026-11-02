"""Check that UI actions enqueue only the intended server/experiment scopes."""
from types import SimpleNamespace
import queue
import threading
from unittest.mock import Mock, patch
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from shared import catalog
from database.app import App, sq_entry_tag


class SelectionTests(unittest.TestCase):
    def test_sq_entry_colors_prioritize_pending_tdms_over_acquisition_status(self):
        for state, exsv, exzsv, expected in (
            ('未着手', None, None, 'sq_no_tdms'),
            ('未着手', 35, 280, 'sq_needs_action'),
            ('未着手', None, 12, 'sq_needs_action'),
            ('未着手', 0, None, 'sq_needs_action'),
            ('着手済み', 35, 280, 'sq_started'),
            ('着手済み', None, None, 'sq_started'),
            ('確認不可', 35, 280, 'sq_unknown'),
            ('', '', '', 'sq_unknown'),
        ):
            with self.subTest(state=state, exsv=exsv, exzsv=exzsv):
                self.assertEqual(sq_entry_tag(dict(analysis_state=state, exsv_t_tdms=exsv,
                                                   exzsv_tdms=exzsv, status='完了')), expected)

    def test_refresh_returns_while_database_is_still_loading(self):
        self.check_startup(False)

    def test_saved_view_is_published_before_database_finishes_loading(self):
        self.check_startup(True)

    def check_startup(self, cached):
        started, release = threading.Event(), threading.Event()
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / 'inventory.sqlite3'
        catalog.initialize(path)
        saved = [[], [], [], [], [], [], {}]
        if cached:
            path.with_suffix('.view.json').write_text(json.dumps(
                dict(format=1, search='', inventory=saved)), encoding='utf-8')

        def read(*args):
            started.set()
            release.wait(5)
            return 'loaded'

        app = SimpleNamespace(refresh_version=0, db_path=path,
                              search=Mock(), configured=[], db_label=Mock(),
                              read_progress=Mock(), read_status=Mock(),
                              events=queue.Queue(), read_inventory=read)
        app.search.get.return_value = ''
        try:
            App.refresh(app)
            self.assertTrue(started.wait(2))
            self.assertTrue(app.reading)
            app.read_progress.start.assert_called_once_with(15)
            if cached:
                self.assertEqual(app.events.get(timeout=2), ('cached_refresh', (1, saved, None)))
            self.assertEqual(app.events.get(timeout=2), ('saved_sources', (1, [])))
            release.set()
            self.assertEqual(app.events.get(timeout=2), ('refresh', (1, 'loaded', None)))
        finally:
            release.set()

    def test_old_refresh_error_is_ignored_after_new_search(self):
        app = SimpleNamespace(refresh_version=2)
        with patch('database.app.messagebox.showerror') as error:
            App.finish_refresh(app, 1, None, 'old error')
        error.assert_not_called()

    def test_read_failure_stops_progress_and_reports_failure(self):
        import time
        app = SimpleNamespace(refresh_version=1, reading=True,
                              read_started=time.monotonic(), read_progress=Mock(),
                              read_status=Mock(), db_label=Mock(), db_path='inventory.sqlite3')
        with patch('database.app.messagebox.showerror') as error:
            App.finish_refresh(app, 1, None, 'database error')
        self.assertFalse(app.reading)
        app.read_progress.stop.assert_called_once()
        self.assertIn('読み込み失敗', app.read_status.set.call_args.args[0])
        error.assert_called_once()

    def test_one_server_enqueues_only_its_shares(self):
        shares = [dict(server='A', root='D:/A/one', kind='analysis'),
                  dict(server='A', root='D:/A/two', kind='analysis'),
                  dict(server='B', root='D:/B/one', kind='analysis')]
        app = SimpleNamespace(configured=shares, target_server=Mock(), run_jobs=Mock())
        app.target_server.get.return_value = 'A'
        App.update_server(app)
        self.assertEqual(app.run_jobs.call_args.args[0], [(shares[0], '.'), (shares[1], '.')])

    def test_sample_rows_deduplicate_experiment_without_merging_servers(self):
        rows = [dict(root='D:/A', experiment='E'), dict(root='D:/A', experiment='E'),
                dict(root='D:/B', experiment='E')]
        tree = Mock()
        tree.selection.return_value = ('0', '1', '2')
        view = dict(rows=rows, tree=tree)
        app = SimpleNamespace(db_path=catalog.DEFAULT_DB, views=dict(analysis=view, sq_entries={}),
                              active_view=lambda: view, run_jobs=Mock(),
                              configured_source=lambda root: dict(root=root, kind='analysis', server=root))
        App.update_selected(app)
        jobs = app.run_jobs.call_args.args[0]
        self.assertEqual([(s['root'], scope) for s, scope in jobs], [('D:/A', 'E'), ('D:/B', 'E')])

    def test_empty_experiment_input_does_not_trigger_full_scan(self):
        app = SimpleNamespace(target_root=Mock(), target_scope=Mock(), run_jobs=Mock(),
                              configured_source=lambda _: dict(root='D:/A', server='A', kind='analysis'))
        app.target_scope.get.return_value = ''
        with patch('database.app.messagebox.showerror') as error:
            App.update_scope(app)
        app.run_jobs.assert_not_called()
        error.assert_called_once()


if __name__ == '__main__':
    unittest.main()

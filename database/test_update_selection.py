"""Check that UI actions enqueue only the intended server/experiment scopes."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from database import catalog
from database.app import App


class SelectionTests(unittest.TestCase):
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

"""GUI regressions for the shared chip selection (run with a Tk-capable Python)."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
import json

from chipdb.app import App
from chipdb.summary import summarize


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        folder = root / 'ChipDB' / 'DB'
        folder.mkdir(parents=True)
        for name, gap in [('chip-a', 1), ('chip-b', 100)]:
            (folder / f'{name}.csv').write_bytes(
                f'{name},,,,,,{gap}\r\nExperiment,OK,20261113_1000,User,EX1,FC#1,1,1.5\r\nBreak\r\nEND\r\n'.encode('cp932'))
        try:
            self.app = App()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(self.close_app)
        self.initial_tab = self.app.tabs.select()
        self.app.root_path = root
        deadline = time.monotonic() + .2
        while time.monotonic() < deadline:
            self.app.update()
            time.sleep(.005)
        self.paths = self.app.paths
        self.all = self.app.all_view
        self.all.records = []
        for path in self.paths:
            record = summarize([line.split(',') for line in path.read_text(encoding='cp932').splitlines()])
            record['_path'] = str(path)
            self.all.records.append(record)
        self.all.refresh()
        self.app.tabs.select(self.all)
        self.app.update()

    def close_app(self):
        for callback in self.app.tk.call('after', 'info'):
            self.app.tk.call('after', 'cancel', callback)
        self.app.destroy()

    def test_select_from_either_list_and_keep_tab(self):
        self.all.view.selection_set(str(self.paths[1]))
        self.app.update()
        self.assertEqual(self.app.document.path, self.paths[1])
        self.assertEqual(self.app.registry_view.view.selection(), (str(self.paths[1]),))
        self.assertEqual(self.app.tabs.select(), str(self.all))
        self.app.registry_view.view.selection_set(str(self.paths[0]))
        self.app.update()
        self.assertEqual(self.app.document.path, self.paths[0])
        self.assertEqual(self.all.view.selection(), (str(self.paths[0]),))
        self.assertEqual(self.app.title_label.cget('text'), 'chip-a')

    def test_sort_filter_and_double_click_keep_identity(self):
        path = self.paths[1]
        self.all.view.selection_set(str(path))
        self.app.update()
        self.all.sort('GAP#')
        self.app.update()
        self.assertEqual(self.all.view.selection(), (str(path),))
        self.assertEqual(self.app.document.path, path)
        self.app.query.set('chip-a')
        self.app.update()
        self.assertEqual(self.app.registry_view.view.selection(), ())
        self.assertEqual(self.app.document.path, path)
        self.app.query.set('')
        self.app.update()
        self.assertEqual(self.app.registry_view.view.selection(), (str(path),))
        self.all.search.set('chip-a')
        self.app.update()
        self.assertEqual(self.all.view.selection(), ())
        self.assertEqual(self.app.document.path, path)
        self.all.search.set('')
        self.app.update()
        self.assertEqual(self.all.view.selection(), (str(path),))
        self.all.open_selected()
        self.app.update()
        self.assertEqual(self.app.tabs.select(), str(self.app.overview))

    def test_declared_id_of_another_chip_does_not_mislabel_selected_file(self):
        first, second = self.paths
        first.write_bytes(first.read_bytes().replace(b'chip-a,', b'chip-b,', 1))
        record = summarize([line.split(',') for line in first.read_text(encoding='cp932').splitlines()])
        record['_path'] = str(first)
        self.all.records[0] = record
        self.all.refresh()
        self.assertEqual(self.all.view.set(str(first), 'ChipID'), 'chip-a')
        self.assertEqual(self.all.view.set(str(first), 'CSV内ChipID'), 'chip-b')
        self.assertIn('不一致', self.all.view.set(str(first), 'ID確認'))
        self.all.view.selection_set(str(second))
        self.app.update()
        self.assertEqual(self.app.document.path, second)
        self.assertEqual(self.app.title_label.cget('text'), 'chip-b')
        self.all.view.selection_set(str(first))
        self.app.update()
        self.assertEqual(self.app.document.path, first)
        self.assertEqual(self.app.title_label.cget('text'), 'chip-a')
        self.assertIn('CSV内IDは chip-b', self.app.metrics.cget('text'))

    def wait_for(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.update()
            if condition():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for the updated inventory')

    def test_registry_is_entry_point_without_sidebar(self):
        self.assertFalse(hasattr(self.app, 'library'))
        self.assertEqual(self.initial_tab, str(self.app.registry_view))
        self.assertEqual(self.app.tabs.tabs()[0], str(self.app.registry_view))
        self.app.registry_view.view.selection_set(str(self.paths[0]))
        self.app.update()
        self.app.registry_view.open_selected()
        self.app.update()
        self.assertEqual(self.app.tabs.select(), str(self.app.overview))
        self.assertEqual(self.app.title_label.cget('text'), 'chip-a')

    def test_registry_search_and_open_chip_without_history(self):
        store = self.app.registry_view.store
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps(dict(schema_version=1, revision=1, chips=[
            dict(key='new', code='chip-new', state='保管中', location='Shelf A', user='Alice')])), encoding='utf-8')
        self.app.scan()
        self.app.query.set('Alice')
        pending = store.db / 'chip-new.csv'
        self.assertEqual(self.app.registry_view.view.get_children(), (str(pending),))
        self.app.registry_view.view.selection_set(str(pending))
        self.app.update()
        self.app.registry_view.open_selected()
        self.app.update()
        self.assertIsNone(self.app.document)
        self.assertEqual(self.app.title_label.cget('text'), 'chip-new')
        self.assertEqual(self.app.tabs.select(), str(self.app.overview))
        self.app.query.set('Shelf A')
        self.assertEqual(self.app.registry_view.view.get_children(), (str(pending),))
        self.app.query.set('保管中')
        self.assertEqual(self.app.registry_view.view.get_children(), (str(pending),))

    def test_unified_update_discovers_new_history_and_preserves_selection(self):
        selected = self.paths[0]
        self.app.select_path(selected)
        new = selected.parent / 'chip-c.csv'
        new.write_bytes(b'chip-c,,,,,,1\r\nExperiment,OK,20261114_1200,User,EX1,FC#1,1,1.5\r\nBreak\r\nEND\r\n')
        self.app.scan()
        self.wait_for(lambda: len(self.all.records) == 3 and not self.app.index_loading)
        self.assertTrue(self.app.registry_view.view.exists(str(new)))
        self.assertTrue(self.all.view.exists(str(new)))
        self.assertEqual(self.app.selected_path, selected)
        self.assertEqual(self.app.title_label.cget('text'), selected.stem)
        self.app.query.set('2026-11-14')
        self.assertEqual(self.app.registry_view.view.get_children(), (str(new),))
        self.assertEqual(self.app.selected_path, selected)

    def test_edited_comparison_is_marked_stale(self):
        self.all.mark_stale()
        self.assertIn('古い集計', self.all.state.cget('text'))
        self.all.search.set('chip-a')
        self.assertIn('古い集計', self.all.state.cget('text'))


if __name__ == '__main__':
    unittest.main()

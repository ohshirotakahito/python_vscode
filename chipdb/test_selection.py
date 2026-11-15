"""GUI regressions for the shared chip selection (run with a Tk-capable Python)."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest

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
        self.assertEqual(self.app.library.selection(), ('1',))
        self.assertEqual(self.app.tabs.select(), str(self.all))
        self.app.library.selection_set('0')
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
        self.assertEqual(self.app.library.selection(), ())
        self.assertEqual(self.app.document.path, path)
        self.app.query.set('')
        self.app.update()
        self.assertEqual(self.app.library.selection(), ('1',))
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


if __name__ == '__main__':
    unittest.main()

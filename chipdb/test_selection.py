"""GUI regressions for the unified chip ledger (run with a Tk-capable Python)."""
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest import mock

from chipdb.app import App
from chipdb.ledger_view import COLUMN_SETS, NO_HISTORY, ledger_rows, row_matches, sort_rows
from chipdb.search import date_query


def chip_csv(name, gap, day='20261113'):
    return f'{name},,,,,,{gap}\r\nExperiment,OK,{day}_1000,User,EX1,FC#1,1,1.5\r\nBreak\r\nEND\r\n'.encode('cp932')


class LedgerRowTests(unittest.TestCase):
    """画面なしで、台帳の行の組み立てと検索を確かめる。"""

    def test_id_fix_column_links_old_and_new_names_while_both_exist(self):
        table = dict(entries=[dict(old='SC25-2115-9', new='SC25-2115-09', status='予定', merge=True),
                              dict(old='SC080-4307-12', new='SC80-4307-12', status='進行中', merge=False)])
        folder = Path('db')
        paths = [folder / f'{c}.csv' for c in ('SC25-2115-9', 'SC25-2115-09', 'SC080-4307-12', 'SC33-2425-12')]
        rows = {r['チップID']: r for r in ledger_rows(paths, [], {}, set(), table)}
        self.assertEqual(rows['SC25-2115-9']['ID修正'], '→ SC25-2115-09（予定・統合）')
        self.assertEqual(rows['SC25-2115-09']['ID修正'], '旧名 SC25-2115-9')
        self.assertEqual(rows['SC080-4307-12']['ID修正'], '→ SC80-4307-12（進行中）')
        self.assertEqual(rows['SC33-2425-12']['ID修正'], '')
        self.assertIn('ID修正', COLUMN_SETS['管理情報'])
        hit = [r['チップID'] for r in rows.values() if row_matches(r, 'SC25-2115-09', None)]
        self.assertEqual(sorted(hit), ['SC25-2115-09', 'SC25-2115-9'])   # 新名で探すと旧名のチップも出る

    def test_rows_join_registry_and_history_and_search_both(self):
        from chipdb.library import Entry
        from chipdb.summary import summarize
        folder = Path('db')
        measured, pending = folder / 'chip-a.csv', folder / 'chip-new.csv'
        record = summarize([line.split(',') for line in chip_csv('chip-a', 5).decode('cp932').splitlines()])
        entries = {measured: Entry(1, 1, record, ['2026-11-13'])}
        chips = [dict(code='chip-a', state='使用中', location='Shelf B', user='Bob', origin='SCREEN', aliases=['chip_A-old'],
                      organization='Taniguchi lab'),
                 dict(code='chip-new', state='保管中', description='spare')]
        rows = {r['チップID']: r for r in ledger_rows([measured, pending], chips, entries, {measured})}
        self.assertEqual(rows['chip-a']['GAP#'], '5')
        self.assertEqual(rows['chip-a']['保管場所'], 'Shelf B')
        self.assertEqual(rows['chip-a']['ID確認'], '一致')
        self.assertEqual(rows['chip-new']['履歴'], NO_HISTORY)
        self.assertEqual(rows['chip-new']['GAP#'], '')
        match = lambda q: sorted(r['チップID'] for r in rows.values() if row_matches(r, q, date_query(q)))  # noqa: E731
        self.assertEqual((rows['chip-a']['製作者'], rows['chip-a']['主体']), ('SCREEN', 'Taniguchi lab'))
        self.assertEqual(match('Taniguchi'), ['chip-a'])      # 主体
        self.assertEqual(match('chip_A-old'), ['chip-a'])     # 直す前の表記（別名）
        self.assertEqual(match('SCREEN'), ['chip-a'])         # 製作者
        self.assertIn('製作者', COLUMN_SETS['管理情報'])
        self.assertIn('主体', COLUMN_SETS['管理情報'])
        self.assertEqual(match('Bob'), ['chip-a'])            # 管理情報
        self.assertEqual(match('spare'), ['chip-new'])        # 説明（列にない管理情報）
        self.assertEqual(match('User'), ['chip-a'])           # 集計（担当者）
        self.assertEqual(match('2026-11-13'), ['chip-a'])     # 使用日
        self.assertEqual(match(''), ['chip-a', 'chip-new'])
        for columns in COLUMN_SETS.values():
            self.assertEqual(columns[0], 'チップID')
        for reverse in (False, True):                          # 空欄（測定前）はどちらの向きでも最後
            self.assertEqual([r['チップID'] for r in sort_rows(list(rows.values()), 'GAP#', reverse)][-1], 'chip-new')


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        patcher = mock.patch.dict(os.environ, {'LOCALAPPDATA': str(root / 'local'), 'AXN_LABVIEW_ROOT': str(root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        folder = root / 'ChipDB' / 'DB'
        folder.mkdir(parents=True)
        for name, gap in [('chip-a', 1), ('chip-b', 100)]:
            (folder / f'{name}.csv').write_bytes(chip_csv(name, gap))
        try:
            self.app = App()
        except tk.TclError as exc:
            self.skipTest(f'Tk unavailable: {exc}')
        self.addCleanup(self.close_app)
        self.initial_tab = self.app.tabs.select()
        self.app.root_path = root
        self.app.scan()
        self.ledger = self.app.ledger
        self.wait_for(lambda: not self.app.index_loading and len(self.app.entries) == 2)
        self.paths = [p for p in self.app.paths if p in self.app.history_paths]

    def close_app(self):
        for callback in self.app.tk.call('after', 'info'):
            self.app.tk.call('after', 'cancel', callback)
        self.app.destroy()

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.update()
            if condition():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for the chip index')

    def test_one_ledger_shows_registry_and_summary_and_is_the_entry_point(self):
        self.assertFalse(hasattr(self.app, 'all_view'))
        self.assertFalse(hasattr(self.app, 'registry_view'))
        self.assertEqual(self.initial_tab, str(self.ledger))
        self.assertEqual(self.app.tabs.tabs()[0], str(self.ledger))
        self.assertEqual(self.ledger.view.set(str(self.paths[1]), 'GAP#'), '100')
        self.assertEqual(self.ledger.view.set(str(self.paths[1]), '履歴'), 'あり')
        self.ledger.view.selection_set(str(self.paths[0]))
        self.app.update()
        self.assertEqual(self.app.document.path, self.paths[0])
        self.ledger.open_selected()
        self.app.update()
        self.assertEqual(self.app.tabs.select(), str(self.app.overview))
        self.assertEqual(self.app.title_label.cget('text'), 'chip-a')

    def test_ledger_view_shows_stage_values_and_fits_columns(self):
        columns = COLUMN_SETS['管理情報']
        for col in ('FC Last', 'MA Last', 'TA Last', 'CAL Last'):
            self.assertIn(col, columns)
        self.assertEqual(self.ledger.view.set(str(self.paths[0]), 'FC Last'), '1.5')
        self.assertEqual(self.ledger.view.heading('FC Last', 'text'), 'FC 最新')
        total = sum(int(self.ledger.view.column(c, 'width')) for c in columns)
        self.assertLess(total, 1500)                     # 詰めた幅で、ふつうの画面に全部の列が入る
        self.assertEqual(str(self.ledger.view.column('GAP#', 'anchor')), 'e')

    def test_column_sets_switch_without_losing_selection(self):
        path = self.paths[1]
        self.ledger.view.selection_set(str(path))
        self.app.update()
        for name, columns in COLUMN_SETS.items():
            self.ledger.column_set.set(name)
            self.ledger.apply_columns()
            self.assertEqual(tuple(self.ledger.view.cget('displaycolumns')), columns)
            self.assertEqual(self.ledger.view.selection(), (str(path),))

    def test_sort_and_search_keep_identity(self):
        path = self.paths[1]
        self.ledger.view.selection_set(str(path))
        self.app.update()
        self.ledger.sort('GAP#')
        self.ledger.sort('GAP#')
        self.assertEqual(self.ledger.view.get_children()[0], str(path))       # 100 が先（降順）
        self.assertEqual(self.ledger.view.selection(), (str(path),))
        self.app.query.set('chip-a')
        self.app.update()
        self.assertEqual(self.ledger.view.selection(), ())
        self.assertEqual(self.app.document.path, path)
        self.app.query.set('')
        self.app.update()
        self.assertEqual(self.ledger.view.selection(), (str(path),))

    def test_declared_id_of_another_chip_is_flagged(self):
        first, second = self.paths
        first.write_bytes(first.read_bytes().replace(b'chip-a,', b'chip-b,', 1))
        self.app.update_index()
        self.wait_for(lambda: not self.app.index_loading)
        self.assertEqual(self.ledger.view.set(str(first), 'チップID'), 'chip-a')
        self.assertEqual(self.ledger.view.set(str(first), 'CSV内ChipID'), 'chip-b')
        self.assertIn('不一致', self.ledger.view.set(str(first), 'ID確認'))
        self.ledger.view.selection_set(str(first))
        self.app.update()
        self.assertEqual(self.app.title_label.cget('text'), 'chip-a')
        self.assertIn('CSV内IDは chip-b', self.app.metrics.cget('text'))
        self.assertEqual(self.ledger.view.set(str(second), 'ID確認'), '一致')

    def test_registry_only_chip_and_search(self):
        store = self.ledger.store
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text(json.dumps(dict(schema_version=1, revision=1, chips=[
            dict(key='new', code='chip-new', state='保管中', location='Shelf A', user='Alice')])), encoding='utf-8')
        self.app.scan()
        self.wait_for(lambda: not self.app.index_loading)
        pending = store.db / 'chip-new.csv'
        self.assertEqual(self.ledger.view.set(str(pending), '履歴'), NO_HISTORY)
        for query in ('Alice', 'Shelf A', '保管中'):
            self.app.query.set(query)
            self.assertEqual(self.ledger.view.get_children(), (str(pending),), query)
        self.ledger.view.selection_set(str(pending))
        self.app.update()
        self.ledger.open_selected()
        self.app.update()
        self.assertIsNone(self.app.document)
        self.assertEqual(self.app.title_label.cget('text'), 'chip-new')

    def test_update_reads_only_changes_and_preserves_selection(self):
        selected = self.paths[0]
        self.app.select_path(selected)
        new = selected.parent / 'chip-c.csv'
        new.write_bytes(chip_csv('chip-c', 1, day='20261114'))
        self.app.scan()
        self.wait_for(lambda: not self.app.index_loading and len(self.app.entries) == 3)
        self.assertIn('読み直し 1', self.ledger.index_state.cget('text'))
        self.assertIn('変更なし 2', self.ledger.index_state.cget('text'))
        self.assertTrue(self.ledger.view.exists(str(new)))
        self.assertEqual(self.app.selected_path, selected)
        self.app.query.set('2026-11-14')
        self.assertEqual(self.ledger.view.get_children(), (str(new),))
        self.assertEqual(self.app.selected_path, selected)

    def test_trend_list_selects_one_chip_or_compares_several(self):
        trend = self.app.trend
        first, second = (str(p) for p in self.paths)
        self.assertEqual(trend.chips.get_children(), (first, second))
        trend.chips.selection_set(second)                       # 1 つ: アプリ全体の選択になる
        self.app.update()
        self.assertEqual(self.app.selected_path, self.paths[1])
        self.assertEqual(self.app.title_label.cget('text'), 'chip-b')
        self.assertEqual(self.ledger.view.selection(), (second,))
        self.assertEqual(trend.kind.get(), 'FC')
        trend.chips.selection_set((first, second))              # 複数: 重ねて比べる。アプリの選択はそのまま
        self.app.update()
        self.assertEqual(trend.chosen, [self.paths[1], self.paths[0]])
        self.assertEqual(self.app.selected_path, self.paths[1])
        self.assertEqual([name for name, _, _ in trend.series()], ['chip-b', 'chip-a'])
        trend.search.set('chip-a')                              # 検索で隠れても比較からは外れない
        self.app.update()
        self.assertEqual(len(trend.chosen), 2)
        trend.search.set('')
        self.app.update()
        self.assertEqual(set(trend.chips.selection()), {first, second})
        self.ledger.view.selection_set(first)                   # 台帳で比較中のチップを選んでも比較は続く
        self.app.update()
        self.assertEqual(len(trend.chosen), 2)
        for axis in ('回数（記録順）', '経過時間', '日時'):
            trend.x_axis.set(axis)
            trend.plot()
            self.assertTrue(trend.chart.find_all())

    def test_clicking_a_chosen_chip_takes_it_out_of_the_comparison(self):
        trend = self.app.trend
        first, second = (str(p) for p in self.paths)
        trend.chips.selection_set(first)
        self.app.update()
        trend.chips.selection_set((first, second))
        self.app.update()
        self.assertEqual(len(trend.chosen), 2)
        self.assertTrue(trend.unselect(first))                     # 選んでいる行のクリック: 外れる
        self.app.update()
        self.assertEqual(trend.chips.selection(), (second,))
        self.assertEqual(trend.chosen, [self.paths[1]])
        self.assertEqual(self.app.selected_path, self.paths[1])   # 上部のチップ名も残っている方へ
        self.assertFalse(trend.unselect(second))                   # 最後の 1 つは外さない
        self.assertEqual(trend.chips.selection(), (second,))

    def test_trend_list_shows_and_sorts_by_the_operation_count(self):
        trend = self.app.trend
        first, second = (str(p) for p in self.paths)
        second_path = Path(second)
        second_path.write_bytes(second_path.read_bytes().replace(
            b'Break', b'Experiment,OK,20261114_1000,User,EX2,FC#2,1,1.2\r\nExperiment,OK,20261114_1100,User,EX2,MA#1,1,9\r\nBreak'))
        self.app.update_index()
        self.wait_for(lambda: not self.app.index_loading)
        trend.kind.set('FC')
        trend.kind_changed()
        self.assertEqual(trend.chips.heading('count', 'text'), 'FC 回数')
        self.assertEqual((trend.chips.set(first, 'count'), trend.chips.set(second, 'count')), ('1', '2'))
        trend.sort_list('count')                                 # 1 回目は多い順
        self.assertEqual(trend.chips.get_children(), (second, first))
        trend.sort_list('count')
        self.assertEqual(trend.chips.get_children(), (first, second))
        trend.kind.set('MA')
        trend.kind_changed()
        self.assertEqual((trend.chips.set(first, 'count'), trend.chips.set(second, 'count')), ('0', '1'))
        self.assertIn('MA', trend.kind.cget('values'))

    def test_operation_comparison_selects_chips_and_links_points_to_records(self):
        comparison = self.app.comparison
        for index, path in enumerate(self.paths):
            path.write_bytes(path.read_bytes().replace(b'Break',
                f'Experiment,OK,20261113_1000,User,EX1,MA#1,2,{10+index},0,20261113_10:02\r\nBreak'.encode()))
        originals = {p: p.read_bytes() for p in self.paths}
        self.app.update_index()
        self.wait_for(lambda: not self.app.index_loading)
        self.app.tabs.select(comparison)
        comparison.chips.selection_set(tuple(map(str, self.paths)))
        self.wait_for(lambda: len(comparison.histories) == 2 and len(comparison.points) == 2)
        self.assertEqual(comparison.chips.set(str(self.paths[0]), 'MA'), '1')
        self.app.update()
        self.assertEqual(len(comparison.representatives.get_children()), 2)
        comparison.point_size.set('12')
        self.app.update()
        oval = next(item for item in comparison.chart.find_all() if comparison.chart.type(item) == 'oval')
        x0, y0, x1, y1 = comparison.chart.coords(oval)
        self.assertAlmostEqual(x1-x0, 12)
        comparison.count_enabled.set(True)
        comparison.plot()
        self.assertEqual(comparison.points, [])
        comparison.count_threshold.set('0')
        comparison.select_by_count()
        self.wait_for(lambda: len(comparison.points) == 2)
        comparison.ymax.set('10.5')
        comparison.plot()
        self.app.update()
        self.assertEqual(len(comparison.points), 1)
        self.assertEqual(len(comparison.representatives.get_children()), 1)
        self.assertTrue(all(r.chip == comparison.points[0]['chip'] for r in comparison.records))
        target = Path(self.temporary.name) / 'comparison.csv'
        with mock.patch('chipdb.comparison_view.filedialog.asksaveasfilename', return_value=str(target)):
            comparison.export()
        import csv
        with target.open(encoding='utf-8-sig', newline='') as stream:
            exported = list(csv.reader(stream))
        self.assertEqual(len(exported), 2)
        self.assertEqual(exported[1][0], comparison.points[0]['chip'])
        comparison.ymin.set('12')
        comparison.plot()
        self.assertFalse(comparison.valid_configuration)
        with mock.patch('chipdb.comparison_view.filedialog.asksaveasfilename') as choose:
            comparison.export()
            choose.assert_not_called()
        comparison.reset_ranges()
        self.assertEqual(len(comparison.points), 2)
        comparison.focus_chip(0)
        self.app.update()
        self.assertEqual({p.chip for p in comparison.records}, {comparison.points[0]['chip']})
        point = comparison.records[-1]
        left, top, right, bottom = comparison.scatter_box
        self.assertAlmostEqual(right-left, bottom-top)
        for mode in ('密度', '散布図＋密度', '散布図'):              # 表示の切り替え: 密度はマスの塗り
            comparison.display_mode.set(mode)
            self.app.update()
            self.assertEqual(bool(comparison.chart.find_withtag('density')), mode != '散布図')
            self.assertTrue(comparison.valid_configuration)
        self.assertGreater(comparison.data_tabs.winfo_rootx(), comparison.chart.winfo_rootx())
        self.assertLess(abs(comparison.data_tabs.winfo_rooty()-comparison.chart.winfo_rooty()), 10)
        self.app.update()
        comparison.focus_record(point)
        self.assertEqual(comparison.details.selection(), (str(comparison.records.index(point)),))
        # 代表値・点をクリックすると上部のチップになり、比較の複数選択はそのまま。ダブルクリックで概要へ
        for index in (1, 0):
            comparison.representatives.selection_set(str(index))
            self.app.update()
            chip = comparison.points[index]['chip']
            self.assertEqual(self.app.selected_path.stem, chip)
            self.assertEqual(self.app.title_label.cget('text'), chip)
            self.assertEqual(len(comparison.chosen), 2)
        comparison.open_point('1')
        self.assertEqual(self.app.tabs.select(), str(self.app.overview))
        self.assertEqual(self.app.selected_path.stem, comparison.points[1]['chip'])
        self.assertEqual(len(comparison.chosen), 2)
        self.app.tabs.select(comparison)
        comparison.go_to(self.app.ledger)
        self.assertEqual(self.app.tabs.select(), str(self.app.ledger))
        self.app.tabs.select(comparison)
        comparison.search.set('chip-a')
        self.app.update()
        self.assertEqual(len(comparison.chosen), 2)
        comparison.clear()
        self.app.update()
        self.assertEqual(comparison.points, [])
        self.assertEqual({p: p.read_bytes() for p in self.paths}, originals)

    def test_saving_history_updates_that_chip_without_a_full_scan(self):
        path = self.paths[0]
        self.app.select_path(path)
        rows = [list(r) for r in self.app.document.rows]
        rows[0][6] = '42'
        time.sleep(.02)                                   # 更新日時が確実に変わるように
        self.app.document.save(rows)
        self.app.update_index()
        self.wait_for(lambda: not self.app.index_loading)
        self.assertEqual(self.ledger.view.set(str(path), 'GAP#'), '42')
        self.assertIn('読み直し 1', self.ledger.index_state.cget('text'))


if __name__ == '__main__':
    unittest.main()


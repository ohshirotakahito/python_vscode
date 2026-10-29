import tempfile
import unittest
from pathlib import Path
from csv_editor import read_document, save_document, split_header, move_selected_rows, sort_rows, missing_history_rows
from core import HEADERS
from csv_editor import duplicate_history_groups


class CsvDocumentTest(unittest.TestCase):
    def test_duplicates_require_all_fields_and_preserve_input(self):
        record = ['2026/10/17_10:00'] + ['value'] * 16
        changed = record[:-1] + ['different']
        rows = [record, changed, record.copy(), [], [''] * 17,
                record + ['extra'], record.copy(), [''] * 17, record[:-1]]
        snapshot = [row.copy() for row in rows]
        self.assertEqual(duplicate_history_groups(rows), [[0, 2, 6]])
        self.assertEqual(rows, snapshot)
        self.assertEqual(duplicate_history_groups([record, changed]), [])

    def test_import_legacy_into_python_preserves_header_and_source(self):
        import csv
        with tempfile.TemporaryDirectory() as folder:
            legacy = Path(folder) / 'Uploader.csv'
            local = Path(folder) / 'Uploader_python.csv'
            existing = ['2026/10/15_14:59'] + ['既存'] * 16
            added = ['2026/10/17_10:00'] + ['追加'] * 16
            with legacy.open('w', encoding='cp932', newline='') as stream:
                csv.writer(stream).writerows([existing, added, added])
            with local.open('w', encoding='utf-8-sig', newline='') as stream:
                csv.writer(stream).writerows([HEADERS, existing])
            source_snapshot = legacy.read_bytes()
            original, encoding, rows, newline = read_document(local)
            header, rows = split_header(rows, HEADERS)
            missing = missing_history_rows(rows, legacy, HEADERS)
            self.assertEqual(missing, [added])
            merged = sort_rows(rows + missing, 0, descending=True)
            save_document(local, original, encoding, [header] + merged, newline)
            self.assertEqual(read_document(local)[2], [HEADERS, added, existing])
            self.assertEqual(legacy.read_bytes(), source_snapshot)
            self.assertEqual(missing_history_rows(merged, legacy, HEADERS), [])

    def test_import_missing_history_preserves_legacy_format_and_backup(self):
        import csv
        with tempfile.TemporaryDirectory() as folder:
            legacy = Path(folder) / 'Uploader.csv'
            source = Path(folder) / 'Uploader_python.csv'
            existing = ['2026/10/14_16:39', 'TO'] + ['既存'] * 15
            added = ['2026/10/15_14:59', 'TO'] + ['追加'] * 15
            with legacy.open('w', encoding='cp932', newline='') as stream:
                csv.writer(stream).writerow(existing)
            with source.open('w', encoding='utf-8-sig', newline='') as stream:
                csv.writer(stream).writerows([HEADERS, existing, added, added])
            original, encoding, rows, newline = read_document(legacy)
            self.assertEqual(missing_history_rows(rows, source, HEADERS), [added])
            self.assertEqual(rows, [existing])
            self.assertEqual(legacy.read_bytes(), original)
            rows += missing_history_rows(rows, source, HEADERS)
            self.assertEqual(missing_history_rows(rows, source, HEADERS), [])
            save_document(legacy, original, encoding, rows, newline)
            self.assertEqual(read_document(legacy)[1:], ('cp932', [existing, added], '\r\n'))
            self.assertEqual(next(Path(folder).glob('*.bak')).read_bytes(), original)

    def test_import_compares_all_fields_and_rejects_invalid_source(self):
        import csv
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'python.csv'
            existing = ['2026/10/15_14:59'] + ['value'] * 16
            changed = existing[:-1] + ['different']
            with source.open('w', encoding='utf-8', newline='') as stream:
                csv.writer(stream).writerows([HEADERS, changed])
            self.assertEqual(missing_history_rows([existing], source, HEADERS), [changed])
            source.write_text('broken,row\n', encoding='utf-8')
            with self.assertRaises(ValueError):
                missing_history_rows([existing], source, HEADERS)

    def test_sort_numeric_rows_stable_and_empty_last(self):
        rows = [['10', 'a'], ['2', 'b'], ['', 'empty'], ['2', 'c'], ['-1.5', 'd'], []]
        self.assertEqual(sort_rows(rows, 0), [rows[4], rows[1], rows[3], rows[0], rows[2], rows[5]])
        self.assertEqual(sort_rows(rows, 0, True), [rows[0], rows[1], rows[3], rows[4], rows[2], rows[5]])
        self.assertEqual(rows[0], ['10', 'a'])

    def test_sort_machine_and_date_columns(self):
        rows = [['AXN10', '2026/10/01_12:00'], ['AXN2', '2026/09/30_12:00']]
        self.assertEqual(sort_rows(rows, 0), [rows[1], rows[0]])
        self.assertEqual(sort_rows(rows, 1, True), rows)

    def test_move_rows_preserves_block_order_and_selection(self):
        rows = list('ABCDE')
        selected, moved = move_selected_rows(rows, [1, 2], -1)
        self.assertEqual((rows, selected, moved), (list('BCADE'), [0, 1], True))
        selected, moved = move_selected_rows(rows, selected, -1)
        self.assertFalse(moved)
        self.assertEqual(rows, list('BCADE'))
        selected, moved = move_selected_rows(rows, selected, 1)
        self.assertEqual((rows, selected, moved), (list('ABCDE'), [1, 2], True))

    def test_move_separate_rows_and_bottom_boundary(self):
        rows = list('ABCDE')
        selected, moved = move_selected_rows(rows, [1, 3, 4], 1)
        self.assertEqual((rows, selected, moved), (list('ACBDE'), [2, 3, 4], True))
        selected, moved = move_selected_rows(rows, selected, 1)
        self.assertFalse(moved)
        self.assertEqual(move_selected_rows(rows, [], -1), ([], False))

    def test_machine_header_preserved_in_cp932(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Machine_N.csv'
            path.write_bytes('Machine DBCode,Machine selection,施設\r\nAXN1,AXN#1,OU\r\n'.encode('cp932'))
            original, encoding, rows, newline = read_document(path)
            header, data = split_header(rows, (), first_row_header=True)
            self.assertEqual(header, ['Machine DBCode', 'Machine selection', '施設'])
            self.assertEqual(data, [['AXN1', 'AXN#1', 'OU']])
            data[0][2] = 'TU'
            save_document(path, original, encoding, [header] + data, newline)
            self.assertEqual(read_document(path)[2], [header, ['AXN1', 'AXN#1', 'TU']])

    def test_history_header_preserved_after_edit_and_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Uploader_python.csv'
            record = ['2026/09/29_16:55'] + ['value'] * 16
            path.write_text(','.join(HEADERS) + '\n' + ','.join(record) + '\n', encoding='utf-8-sig')
            original, encoding, rows, newline = read_document(path)
            header, data = split_header(rows, HEADERS)
            self.assertEqual(header, HEADERS)
            self.assertEqual(data, [record])
            data[0][1] = 'TO'
            original = save_document(path, original, encoding, [header] + data, newline)
            self.assertEqual(read_document(path)[2][0], HEADERS)
            self.assertEqual(read_document(path)[2][1][1], 'TO')
            save_document(path, original, encoding, [header], newline)
            self.assertEqual(read_document(path)[2], [HEADERS])

    def test_legacy_first_record_remains_data(self):
        record = ['2026/09/29_16:55'] + ['value'] * 16
        header, data = split_header([record], HEADERS)
        self.assertIsNone(header)
        self.assertEqual(data, [record])

    def test_encoding_multiline_backup_and_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'Operator.csv'
            original = '担当者,TO\r\n"複数\r\n行",SS\r\n'.encode('cp932')
            path.write_bytes(original)
            snapshot, encoding, rows, newline = read_document(path)
            self.assertEqual(rows[1][0], '複数\r\n行')
            rows[0][0] = '変更'
            saved = save_document(path, snapshot, encoding, rows, newline)
            self.assertEqual(read_document(path)[2], rows)
            self.assertEqual(next(Path(folder).glob('*.bak')).read_bytes(), original)
            path.write_bytes(b'external change')
            with self.assertRaises(ValueError):
                save_document(path, saved, encoding, rows, newline)
            self.assertEqual(path.read_bytes(), b'external change')

    def test_headerless_uneven_rows_and_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'data.csv'
            path.write_bytes('名前,TO\n\n単独\n'.encode('utf-8-sig'))
            snapshot, encoding, rows, newline = read_document(path)
            save_document(path, snapshot, encoding, rows, newline)
            self.assertEqual(path.read_bytes(), snapshot)


if __name__ == '__main__':
    unittest.main()

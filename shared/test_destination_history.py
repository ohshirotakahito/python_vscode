import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from shared import destination_history as dh


A = r'\\Server\analysis\Exp\A'
B = r'\\Server\analysis\Exp\B'
C = r'\\Other\analysis\Exp2\C'
T0, T1, T2, T3 = [f'2026-11-13T0{hour}:00:00+00:00' for hour in range(4)]


class DestinationHistoryTests(TestCase):
    def test_chained_moves_and_path_boundaries(self):
        moves = [dh.Move(A, B, dh.timestamp(T1)), dh.Move(B, C, dh.timestamp(T2))]
        self.assertEqual(dh.trace(A, T0, moves), (C, True, False))
        self.assertEqual(dh.trace(A + '2', T0, moves), (A + '2', False, False))
        self.assertEqual(dh.trace(A, T3, moves), (A, False, False))
        path, changed, unsure = dh.trace(A.upper(), T0, moves)
        self.assertEqual((path, changed, unsure), (C, True, False))

    def test_restore_is_ordered_against_transfer_time(self):
        moves = [dh.Move(A, B, dh.timestamp(T1)), dh.Move(B, A, dh.timestamp(T2))]
        self.assertEqual(dh.trace(A, T0, moves), (A, True, False))
        self.assertEqual(dh.trace(B, T1, moves), (A, True, False))
        self.assertEqual(dh.trace(A, T3, moves), (A, False, False))

    def test_uncertain_operations_do_not_invent_a_current_path(self):
        for move in [dh.Move(A, B, None), dh.Move(A, B, dh.timestamp(T1), False)]:
            self.assertEqual(dh.trace(A, T0, [move]), (A, False, True))
        self.assertEqual(dh.trace(A, '', [dh.Move(A, B, dh.timestamp(T1))]), (A, False, True))

    def test_read_sample_and_move_journals_without_writes(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            sample = dict(version=1, created_at=T1, steps=[
                dict(source=A + r'\x.txt', target=B + r'\x.txt', identity=[1, 2, 3, 4], state='done'),
                dict(source=A, target=B, identity=[1, 2, None, None], state='restored',
                     moved_at=T1, restored_at=T3)])
            (root / 'sample.json').write_text(json.dumps(sample), encoding='utf-8')
            move = dict(created_at=T2, operations=[dict(source=B, target=C, dir=True,
                        status='完了', moved_at=T2)])
            (root / 'move.json').write_text(json.dumps(move), encoding='utf-8')
            (root / 'previews').mkdir()
            (root / 'previews' / 'bad.json').write_text('{', encoding='utf-8')
            before = {p: p.read_bytes() for p in root.rglob('*.json')}
            moves = dh.load_moves(root)
            self.assertEqual([(m.source, m.target) for m in moves], [(A, B), (B, C), (B, A)])
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob('*.json')})

    def test_old_restore_without_timestamp_is_uncertain(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'sample.json'
            path.write_text(json.dumps(dict(version=1, created_at=T1, steps=[
                dict(source=A, target=B, identity=[1, 2, None, None], state='restored')])), encoding='utf-8')
            self.assertTrue(dh.trace(B, T1, dh.load_moves(folder))[2])

    def test_missing_directory_and_broken_journal(self):
        with TemporaryDirectory() as folder:
            self.assertEqual(dh.load_moves(Path(folder) / 'absent'), [])
            (Path(folder) / 'broken.json').write_text('{', encoding='utf-8')
            self.assertTrue(dh.trace(A, T0, dh.load_moves(folder))[2])

    def test_legacy_csv_cannot_prove_that_a_rename_was_not_undone(self):
        import csv
        with TemporaryDirectory() as folder:
            with (Path(folder) / 'old.csv').open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['old_path', 'new_path', 'item_type', 'renamed_at'])
                writer.writerow([A, B, 'folder', T1])
            moves = dh.load_moves(folder)
            self.assertTrue(dh.trace(A, T0, moves)[2])
            self.assertTrue(dh.trace(B, T3, moves)[2])

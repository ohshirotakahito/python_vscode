import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from shared import destination_history as dh

A, B, C = [rf'\\Server\analysis\Exp\{name}' for name in 'ABC']
T0, T1, T2, T3 = [f'2026-11-13T0{hour}:00:00+00:00' for hour in range(4)]


def journal(source=A, target=B, at=T1):
    return dict(version=1, created_at=at, root=source, steps=[
        dict(source=source, target=target, identity=[1, 2, None, None], state='done', moved_at=at)])


class SharedDestinationHistoryTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.pc1, self.pc2, self.shared = [self.base / name for name in ('pc1', 'pc2', 'shared')]
        self.pc1.mkdir()
        self.pc2.mkdir()

    def publish(self, name, record):
        path = self.pc1 / (name + '.json')
        path.write_text(json.dumps(record), encoding='utf-8')
        self.assertTrue(dh.share_journal(path, record, self.shared))
        return path

    def remote(self):
        return dh.load_moves(self.pc2, self.shared)

    def test_two_pcs_contribute_without_rewriting_each_others_records(self):
        record = journal()
        first = self.publish('one', record)
        before = {p: p.read_bytes() for p in self.shared.glob('*.json')}
        second = self.pc2 / 'two.json'
        second_record = journal(B, C, T2)
        second.write_text(json.dumps(second_record), encoding='utf-8')
        self.assertTrue(dh.share_journal(second, second_record, self.shared))
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        moves = dh.load_moves(self.base / 'publisher', self.shared)
        self.assertEqual(dh.trace(A, T0, moves), (C, True, False))
        self.assertEqual(json.loads(first.read_text(encoding='utf-8')), record)

    def test_newest_revision_replaces_pending_and_local_and_shared_are_not_replayed_twice(self):
        record = journal()
        record['steps'][0]['state'] = 'pending'
        path = self.publish('one', record)
        self.assertTrue(dh.trace(A, T0, self.remote())[2])
        record['steps'][0]['state'] = 'done'
        self.publish('one', record)
        self.assertEqual(dh.trace(A, T0, self.remote()), (B, True, False))
        self.assertEqual(len(dh.load_moves(self.pc1, self.shared)), 1)
        self.assertEqual(len(list(self.shared.glob('*.json'))), 2)
        self.assertTrue(dh.share_journal(path, record, self.shared))
        self.assertEqual(len(list(self.shared.glob('*.json'))), 2)

    def test_restoration_is_seen_on_another_pc(self):
        record = journal()
        self.publish('one', record)
        record['steps'][0].update(state='restored', restored_at=T3)
        self.publish('one', record)
        self.assertEqual(dh.trace(B, T2, self.remote()), (A, True, False))
        self.assertEqual(dh.trace(A, T0, self.remote()), (A, True, False))

    def test_offline_share_leaves_an_outbox_and_retries_idempotently(self):
        path, record = self.pc1 / 'one.json', journal()
        path.write_text(json.dumps(record), encoding='utf-8')
        warnings = []
        with patch.object(dh, '_send', side_effect=PermissionError('offline')):
            self.assertFalse(dh.share_journal(path, record, self.shared, warnings.append))
        self.assertTrue(warnings)
        self.assertEqual(len(list(self.pc1.glob('.tracking/*.json'))), 1)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), record)
        self.assertTrue(dh.flush_pending(self.pc1, self.shared))
        self.assertTrue(dh.flush_pending(self.pc1, self.shared))
        self.assertEqual(len(list(self.shared.glob('*.json'))), 1)
        self.assertEqual(dh.trace(A, T0, self.remote()), (B, True, False))

    def test_conflicting_same_revision_and_broken_files_require_review(self):
        self.publish('one', journal())
        (path,) = self.shared.glob('*.json')
        duplicate = json.loads(path.read_text(encoding='utf-8'))
        duplicate['moves'][0]['target'] = C
        dh._send(duplicate, self.shared)
        self.assertTrue(dh.trace(A, T0, self.remote())[2])
        self.assertTrue(dh.trace(A, T0, dh.load_moves(self.pc1, self.shared))[2])
        for conflict in self.shared.glob('*.json'):
            if conflict != path:
                conflict.unlink()
        (self.shared / 'broken.json').write_text('{', encoding='utf-8')
        self.assertTrue(dh.trace(A, T0, self.remote())[2])

    def test_valid_json_corruption_is_detected_and_sender_can_repair_its_snapshot(self):
        source = self.publish('one', journal())
        (path,) = self.shared.glob('*.json')
        corrupt = json.loads(path.read_text(encoding='utf-8'))
        corrupt['moves'][0]['target'] = C
        path.write_text(json.dumps(corrupt), encoding='utf-8')
        self.assertTrue(dh.trace(A, T0, self.remote())[2])
        self.assertTrue(dh.share_journal(source, journal(), self.shared))
        self.assertEqual(dh.trace(A, T0, self.remote()), (B, True, False))

    def test_only_directory_paths_are_shared_and_local_drive_letters_are_not(self):
        record = journal()
        record['steps'].append(dict(source=A + r'\file.txt', target=B + r'\file.txt',
                                    identity=[1, 2, 10, 20], state='done'))
        self.publish('one', record)
        (path,) = self.shared.glob('*.json')
        snapshot = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(len(snapshot['moves']), 1)
        self.assertNotIn('identity', snapshot['moves'][0])
        local = journal(r'C:\analysis\Exp\A', r'C:\analysis\Exp\B')
        self.publish('local', local)
        self.assertEqual(len(list(self.shared.glob('*.json'))), 1)

    def test_explicit_sync_shares_old_local_journals_but_skips_preview_and_outbox(self):
        (self.pc1 / 'one.json').write_text(json.dumps(journal()), encoding='utf-8')
        (self.pc1 / 'previews').mkdir()
        (self.pc1 / 'previews' / 'bad.json').write_text('{', encoding='utf-8')
        self.assertTrue(dh.sync_journals(self.pc1, self.shared))
        self.assertTrue(dh.sync_journals(self.pc1, self.shared))
        self.assertEqual(len(list(self.shared.glob('*.json'))), 1)
        self.assertEqual(dh.trace(A, T0, self.remote()), (B, True, False))

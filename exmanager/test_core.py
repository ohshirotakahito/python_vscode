import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import core


class CopyTest(unittest.TestCase):
    def test_new_copy_hashes_during_copy_existing_reads_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root/'source'
            src.mkdir()
            source = src/'D_test.txt'
            source.write_bytes(b'test content')
            dst = root/'target'/'D_test.txt'
            stat = source.stat()
            item = core.Item(source, dst, '', '新規', False, (stat.st_size, stat.st_mtime_ns))
            p = core.Plan([item], [], src, root/'target', {})
            with patch.object(core, 'signature', wraps=core.signature) as hashed:
                core.execute(p, lambda s: None)
                paths = [call.args[0] for call in hashed.call_args_list]
                self.assertEqual(len(paths), 1)
                self.assertEqual(paths[0].suffix, '.partial')
            self.assertEqual(dst.read_bytes(), source.read_bytes())
            self.assertEqual(item.sha256, core.digest(source))
            with patch.object(core, 'signature', wraps=core.signature) as hashed:
                core.execute(p, lambda s: None)
                self.assertEqual([call.args[0] for call in hashed.call_args_list], [source, dst])

    def test_failed_postcopy_verification_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root/'source'
            src.mkdir()
            source = src/'D_test.txt'
            source.write_bytes(b'test')
            dst = root/'target'/'D_test.txt'
            p = core.Plan([core.Item(source, dst, '', '新規')], [], src, root/'target', {})
            with patch.object(core, 'signature', return_value='incorrect'):
                with self.assertRaises(ValueError):
                    core.execute(p, lambda s: None)
            self.assertFalse(dst.exists())
            self.assertEqual(list(dst.parent.glob('*.partial')), [])

    def test_pair_raw_duplicate_restore_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root/'20260731_1739_test', root/'analysis'/'Sakano_00'/'OXT'
            for folder in [src/'EXSV'/'T', src/'EXZSV', src/'empty', dst/'OXT_10k_Sample'/'T', dst/'OXT_100k_Sample']:
                folder.mkdir(parents=True)
            for n in (10,2):
                (src/'EXSV'/f'D_EXSV#{n}.txt').write_text(f'txt{n}')
                (src/'EXSV'/'T'/f'D_EXSV#{n}.tdms').write_text(f'tdms{n}')
            (src/'EXZSV'/'Z_EXZSV#1.tdms').write_text('100k')
            for prefix in ('PM_', 'PML_', 'R_'):
                (src/'EXSV'/f'{prefix}EXSV#1.txt').write_text('auxiliary')
            (src/'EXSV'/'T'/'PM_only.tdms').write_text('auxiliary tdms')
            updates = []
            p = core.plan(src,dst,'OXT','Sample', progress=lambda *args: updates.append(args), verify=True)
            self.assertEqual(updates[-1][1:3], (len(p.items), len(p.items)))
            self.assertTrue(any('MiB' in event[3] for event in updates))
            self.assertFalse((dst/'raw data').exists())
            self.assertEqual(p.warnings, [])
            analysis_items = [i for i in p.items if 'raw data' not in i.destination.parts]
            self.assertEqual(len(analysis_items), 5)
            self.assertTrue(any(i.source.name == 'Z_EXZSV#1.tdms' for i in analysis_items))
            auxiliary = [i for i in p.items if i.source.name.startswith(('PM_', 'PML_', 'R_'))]
            self.assertEqual(len(auxiliary), 4)
            self.assertTrue(all('raw data' in i.destination.parts for i in auxiliary))
            self.assertTrue(any(i.destination.name=='OXT_10k_Sample#001 D_EXSV#2.txt' for i in p.items))
            updates.clear()
            core.execute(p, lambda s: None, progress=lambda *args: updates.append(args))
            self.assertEqual(updates[-1][1:3], (len(p.items), len(p.items)))
            self.assertTrue(any(event[0] == 'ファイルコピー' for event in updates))
            self.assertTrue((dst/'raw data'/src.name/'empty').is_dir())
            self.assertTrue(all(i.status==('既存（内容未検証）' if i.raw else 'コピー済み') for i in core.plan(src,dst,'OXT','Sample', verify=True).items))
            missing = dst/'OXT_10k_Sample'/'T'/'OXT_10k_Sample#001 D_EXSV#2.tdms'
            missing.unlink()
            retry = core.plan(src,dst,'OXT','Sample')
            self.assertEqual([i.destination for i in retry.items if i.status=='新規'], [missing])
            core.execute(retry, lambda s: None)
            missing.write_text('conflict')
            with self.assertRaises(ValueError):
                core.execute(core.plan(src,dst,'OXT','Sample'), lambda s: None)

    def test_history_merge(self):
        with tempfile.TemporaryDirectory() as directory:
            old, new = Path(directory)/'old.csv', Path(directory)/'new.csv'
            row = ['2026/09/28_10:42'] + ['x']*16
            core.write_csv(old, ['']*17, [row,row])
            core.write_csv(new, core.HEADERS, [row])
            self.assertEqual(len(core.history(old,new)), 1)

    def test_batch_fast_preview_numbering_and_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'analysis'/'OXT'
            for rate in ('10k', '100k'):
                (target/f'OXT_{rate}_Sample').mkdir(parents=True)
            (target/'OXT_10k_Sample'/'T').mkdir()
            sources = []
            for name in ('20260731_1739_A', '20260801_1739_B'):
                src = root/name
                (src/'EXSV'/'T').mkdir(parents=True)
                (src/'EXZSV').mkdir()
                (src/'EXSV'/f'D_{name}.txt').write_text('txt')
                (src/'EXSV'/'T'/f'D_{name}.tdms').write_text('tdms')
                sources.append(src)
            legacy, local = root/'legacy.csv', root/'logs'/'history.csv'
            core.write_csv(legacy, ['']*17, [])
            context = dict(operator='TO', uploader='SS', machine='AXN3', selection='Recent_Data', kind='Sample')
            specs = [dict(sources=[src], target=target, context=context, legacy=legacy, local=local) for src in sources]
            with patch.object(core, 'signature', side_effect=AssertionError('preview must not hash')):
                jobs = core.plan_batch(specs)
            self.assertEqual([p.numbers['10k'] for _,p,_ in jobs], [[1],[2]])
            self.assertFalse(local.exists())
            results = core.execute_batch(jobs, lambda s: None)
            self.assertEqual([r[3] for r in results], ['完了','完了'])
            self.assertEqual(len(core.history(legacy, local)), 2)
            with self.assertRaises(ValueError):
                core.plan_batch([specs[0], specs[0]])
            # Existing equal-sized but different content is detected only on execution.
            saved = next((target/'OXT_10k_Sample').glob('*001*.txt'))
            saved.write_text('BAD')
            jobs = core.plan_batch(specs)
            self.assertFalse(any(i.status=='競合' for _,p,_ in jobs for i in p.items))
            results = core.execute_batch(jobs, lambda s: None)
            self.assertEqual([r[3] for r in results], ['失敗（以降停止）','未実行'])
            self.assertEqual(saved.read_text(), 'BAD')
            other = root/'other'/'OXT'
            for sub in ('OXT_10k_Sample/T', 'OXT_100k_Sample'):
                (other/sub).mkdir(parents=True)
            second_destination = dict(specs[0], target=other)
            jobs = core.plan_batch([specs[0], second_destination])
            self.assertEqual(jobs[1][1].numbers['10k'], [1])
            self.assertEqual(core.execute_batch([jobs[1]], lambda s: None)[0][3], '完了')

    def test_raw_files_are_not_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root/'source', root/'analysis'/'OXT'
            for folder in [src/'EXSV'/'T', src/'EXZSV', dst/'OXT_10k_Sample'/'T', dst/'OXT_100k_Sample']:
                folder.mkdir(parents=True)
            (src/'notes.txt').write_text('raw only')
            with patch.object(core, 'signature', side_effect=AssertionError('raw must not be hashed')):
                p = core.plan(src, dst, 'OXT', 'Sample')
                self.assertEqual(p.items[0].sha256, '')
                core.execute(p, lambda s: None)
                saved = dst/'raw data'/'source'/'notes.txt'
                saved.write_text('different existing content')
                p = core.plan(src, dst, 'OXT', 'Sample')
                self.assertEqual(p.items[0].status, '既存（内容未検証）')
                core.execute(p, lambda s: None)
                self.assertEqual(saved.read_text(), 'different existing content')

    def test_source_change_and_missing_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root/'source', root/'analysis'/'OXT'
            for folder in [src/'EXSV'/'T', src/'EXZSV', dst/'OXT_10k_Sample'/'T', dst/'OXT_100k_Sample']:
                folder.mkdir(parents=True)
            f = src/'EXSV'/'D_EXSV#1.txt'
            f.write_text('one')
            p = core.plan(src,dst,'OXT','Sample')
            self.assertTrue(p.warnings)
            self.assertEqual(len(p.items), 1)  # raw only
            (src/'EXSV'/'T'/'D_EXSV#1.tdms').write_text('pair')
            p = core.plan(src,dst,'OXT','Sample')
            f.write_text('changed')
            with self.assertRaises(ValueError):
                core.execute(p, lambda s: None)
            self.assertFalse((dst/'raw data').exists())


if __name__ == '__main__':
    unittest.main()

"""ローカルの一時フォルダだけでGUIの処理を検証する。"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app
import create_folders
import data_transfer_common as common
import file_rename
import rename_replace


class AppTests(unittest.TestCase):
    def test_stocked_restore_conflict_and_repreview(self):
        with tempfile.TemporaryDirectory() as temp:
            def local_path(server, share, *parts):
                return str(Path(temp, server, share, *parts))
            root = Path(local_path('Rackstation', 'analysis', 'E', 'S', 'S_10k_Sample', 'T'))
            stocked = root / 'stocked'
            stocked.mkdir(parents=True)
            (stocked / 'a.tdms').write_bytes(b'a')
            (stocked / 'b.tdms').write_bytes(b'b')
            (root / 'b.tdms').write_bytes(b'keep')
            dest = Path(local_path('Rackstation', 'RT_server'))
            dest.mkdir(parents=True)
            data = dict(kind='copy', server='Rackstation', dest_server='QTserver', share='analysis',
                        destination='RT_server', experiment='E', samples='S', suffix='_10k_Sample',
                        target_text=False, target_raw=True, target_anal=False, target_bnal=False, preview=True)
            with patch.object(common, 'server_path', local_path), contextlib.redirect_stdout(io.StringIO()):
                app.run_job(data)
                self.assertTrue((stocked / 'a.tdms').exists())
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    app.run_job({**data, 'stocked_action': 'restore',
                                 'stocked_selected': [str(stocked / 'a.tdms'), str(stocked / 'b.tdms')]})
                self.assertIn('コピー予定: 2件', output.getvalue())
                self.assertIn('同名あり', output.getvalue())
            self.assertEqual((root / 'a.tdms').read_bytes(), b'a')
            self.assertFalse((stocked / 'a.tdms').exists())
            self.assertEqual((stocked / 'b.tdms').read_bytes(), b'b')
            self.assertEqual((root / 'b.tdms').read_bytes(), b'keep')
            self.assertEqual(list(dest.iterdir()), [])

    def test_copy_progress_and_partial_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            source, dest = Path(temp, 'source'), Path(temp, 'dest')
            source.mkdir()
            dest.mkdir()
            (source / 'new.tdms').write_bytes(b'data' * 1024)
            (source / 'existing.tdms').write_bytes(b'new')
            (dest / 'existing.tdms').write_bytes(b'keep')
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                app.copy_with_progress([(str(source), str(dest), '.tdms')])
            progress = [json.loads(line[len(app.PROGRESS_PREFIX):]) for line in output.getvalue().splitlines()
                        if line.startswith(app.PROGRESS_PREFIX)]
            self.assertEqual(progress[-1]['done'], 4096)
            self.assertEqual(progress[-1]['total'], 4096)
            self.assertEqual(progress[-1]['completed'], 1)
            self.assertEqual(progress[-1]['skipped'], 1)
            self.assertEqual((dest / 'existing.tdms').read_bytes(), b'keep')
            (source / 'failure.tdms').write_bytes(b'content')
            with patch.object(app.shutil, 'copymode', side_effect=PermissionError('denied')), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(PermissionError):
                    app.copy_with_progress([(str(source), str(dest), '.tdms')])
            self.assertFalse((dest / 'failure.tdms').exists())
            self.assertFalse(list(dest.glob('*.part')))
        window = app.App()
        window.withdraw()
        try:
            window.show_copy_progress(dict(done=100, total=200, completed=0, count=1, skipped=0, elapsed=10))
            self.assertEqual(float(window.progress['value']), 50)
            self.assertIn('残り約 00:00:10', window.timing.get())
            self.assertIn('終了見込み', window.timing.get())
        finally:
            window.destroy()

    def test_actionable_errors_and_worker_error_protocol(self):
        import errno
        for exc, expected in (
                (PermissionError(errno.EACCES, 'denied', 'target/path'), 'アクセスが拒否'),
                (OSError(errno.ENOSPC, 'full', 'target/path'), '空き容量'),
                (FileNotFoundError(errno.ENOENT, 'missing', 'target/path'), '見つかりません')):
            detail = app.describe_error(exc)
            self.assertIn(expected, detail)
            self.assertIn('target/path', detail)
            self.assertIn('確認事項', detail)
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp, 'missing.csv'))
            result = subprocess.run([sys.executable, '-X', 'utf8', str(app.BASE / 'app.py'), '--worker'],
                                    input=json.dumps(dict(kind='undo', log=path)), capture_output=True,
                                    text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            line = next(line for line in result.stdout.splitlines() if line.startswith(app.ERROR_PREFIX))
            detail = json.loads(line[len(app.ERROR_PREFIX):])
            self.assertIn(path, detail)
            self.assertIn('見つかりません', detail)

    def test_replacement_preview_execute_and_undo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'old_dir'
            folder.mkdir()
            (folder / 'old.txt').write_text('sample', encoding='utf-8')
            data = dict(kind='rename', root=temp, mode='置換', old='old', new='new',
                        include_folders=True, limit='', preview=True)
            with contextlib.redirect_stdout(io.StringIO()), patch.object(rename_replace, 'LOG_DIR', str(root / 'logs')):
                app.run_job(data)
                self.assertTrue((folder / 'old.txt').exists())
                app.run_job({**data, 'preview': False})
                self.assertEqual((root / 'new_dir' / 'new.txt').read_text(), 'sample')
                log = next((root / 'logs').glob('*.csv'))
                app.run_job(dict(kind='undo', log=str(log)))
                self.assertTrue((folder / 'old.txt').exists())

    def test_copy_and_folders_on_local_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            def local_path(server, share, *parts):
                return str(Path(temp, server, share, *parts))
            source = Path(temp, 'Rackstation', 'source', 'experiment', 'S1', 'S1_10k_Sample', 'T')
            source.mkdir(parents=True)
            (source / 'data.tdms').write_bytes(b'example')
            (source / 'other.txt').write_text('skip')
            Path(temp, 'Rackstation', 'destination').mkdir(parents=True)
            data = dict(kind='copy', server='Rackstation', dest_server='QTserver', share='source', experiment='experiment',
                        samples='S1', destination='destination', extension='.tdms',
                        suffix='_10k_Sample', subpath='T', keyword='', tree='T')
            import transfer_copy
            with patch.object(common, 'server_path', local_path), patch.object(transfer_copy, 'server_path', local_path), contextlib.redirect_stdout(io.StringIO()):
                app.run_job(data)
                target = Path(temp, 'Rackstation', 'destination', 'experiment', 'S1', 'S1_10k_Sample', 'T', 'data.tdms')
                self.assertEqual(target.read_bytes(), b'example')
                target.write_bytes(b'existing')
                app.run_job(data)
                self.assertEqual(target.read_bytes(), b'existing')
                self.assertFalse(target.with_name('other.txt').exists())
                with patch.object(create_folders, 'fo_xx') as make:
                    app.run_job(dict(kind='folders', server='Rackstation', share='source', experiment='experiment', samples='S1,S2'))
                    self.assertEqual(make.call_count, 2)

    def test_worker_protocol_and_utf8(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp, '旧.txt')
            file.write_text('unchanged')
            data = dict(kind='rename', root=temp, mode='置換', old='旧', new='新',
                        include_folders=False, limit='2', preview=True)
            result = subprocess.run([sys.executable, '-X', 'utf8', str(app.BASE / 'app.py'), '--worker'],
                                    input=json.dumps(data), capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('新.txt', result.stdout)
            self.assertTrue(file.exists())

    def test_format_preview_execute_and_undo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'AB@1234_5678_9012_ID01-extra.tdms'
            source.write_bytes(b'data')
            target = root / 'AB@1234_5678_9012_ID01.tdms'
            data = dict(kind='rename', root=temp, mode='整形', pattern=file_rename.DEFAULT_PATTERN,
                        extension='.tdms', limit='', preview=True)
            with contextlib.redirect_stdout(io.StringIO()), patch.object(file_rename, 'LOG_DIR', str(root / 'logs')):
                app.run_job(data)
                self.assertTrue(source.exists())
                self.assertFalse(target.exists())
                app.run_job({**data, 'preview': False})
                self.assertEqual(target.read_bytes(), b'data')
                app.run_job(dict(kind='undo', log=str(next((root / 'logs').glob('*.csv')))))
                self.assertTrue(source.exists())

    def test_invalid_inputs(self):
        base = dict(kind='folders', server='test', share='analysis', experiment='E', samples='S')
        for changes in ({'experiment': '../escape'}, {'samples': ''}, {'share': 'C:\\data'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                app.validate({**base, **changes})

    def test_selected_targets_and_standard_structure(self):
        with tempfile.TemporaryDirectory() as temp:
            def local_path(server, share, *parts):
                return str(Path(temp, server, share, *parts))
            root = Path(local_path('Rackstation', 'analysis', 'E', 'S', 'S_100k_Blank'))
            files = ['notes.txt', 'skip.tdms', 'T/raw.tdms', 'T/skip.txt',
                     'T/ANAL/processed.tdms', 'T/BNAL@anything/b.tdms',
                     'T/BNAL@123/c.tdms', 'T/BNAL/wrong.tdms', 'T/ANAL_extra/wrong.tdms']
            for name in files:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'data')
            Path(local_path('Rackstation', 'RT_server')).mkdir(parents=True)
            data = dict(kind='copy', server='Rackstation', dest_server='QTserver',
                        share='analysis', destination='RT_server', experiment='E', samples='S',
                        frequency='100kHz', sample_kind='Blank', suffix='stale',
                        target_text=True, target_raw=True, target_anal=True, target_bnal=True)
            before = {p.relative_to(temp).as_posix() for p in Path(temp).rglob('*')}
            preview_output = io.StringIO()
            with patch.object(common, 'server_path', local_path), contextlib.redirect_stdout(preview_output):
                app.run_job({**data, 'preview': True})
            self.assertEqual(before, {p.relative_to(temp).as_posix() for p in Path(temp).rglob('*')})
            self.assertIn('=== コピー予定の概要 ===', preview_output.getvalue())
            self.assertIn('コピー予定: 5件', preview_output.getvalue())
            self.assertIn('スキップ予定: 0件', preview_output.getvalue())
            self.assertIn('[コピー] notes.txt', preview_output.getvalue())
            self.assertEqual(preview_output.getvalue().count('コピー元フォルダ:'), 5)
            with patch.object(common, 'server_path', local_path), contextlib.redirect_stdout(io.StringIO()):
                destination = Path(local_path('Rackstation', 'RT_server', 'E', 'S', 'S_100k_Blank'))
                app.run_job({**data, 'target_text': False, 'target_raw': False, 'target_anal': False,
                             'bnal_mode': '一覧から選択', 'bnal_selection': json.dumps([['S', 'BNAL@123']])})
                self.assertEqual({p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()},
                                 {'T/BNAL@123/c.tdms'})
                app.run_job(data)
                actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
                self.assertEqual(actual, {'notes.txt', 'T/raw.tdms', 'T/ANAL/processed.tdms',
                                          'T/BNAL@anything/b.tdms', 'T/BNAL@123/c.tdms'})
                self.assertTrue((destination / 'stocked').is_dir())
                self.assertTrue((destination.parent / 'S_10k_Sample' / 'T' / 'ANAL').is_dir())
                self.assertFalse(Path(temp, 'QTserver').exists())
                (destination / 'notes.txt').write_bytes(b'existing')
                app.run_job(data)
                self.assertEqual((destination / 'notes.txt').read_bytes(), b'existing')
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    app.run_job({**data, 'preview': True})
                self.assertIn('コピー予定: 0件', output.getvalue())
                self.assertIn('スキップ予定: 5件', output.getvalue())
            with self.assertRaises(ValueError):
                app.validate({**data, **{'target_' + key: False for key, *_ in app.COPY_TARGETS}})
            with self.assertRaises(ValueError):
                app.validate({**data, 'bnal_mode': '一覧から選択', 'bnal_selection': '[]'})

    def test_destination_pairs_and_manual_override(self):
        window = app.App()
        window.withdraw()
        try:
            fields = window.fields['copy']
            expected = [('Rackstation', 'QTserver', 'RT_server'),
                        ('Rackstation', 'QDserver', 'RD_server'),
                        ('Rackstation', 'QKserver', 'RK_server'),
                        ('QDserver', 'QTserver', 'DT_server'),
                        ('QDserver', 'QKserver', 'DK_server'),
                        ('QTserver', 'QKserver', 'TK_server')]
            for first, second, share in expected:
                for source, dest in ((first, second), (second, first)):
                    fields['server'].set(source)
                    window.copy_server_changed()
                    self.assertNotIn(source, window.dest_server_box.cget('values'))
                    fields['dest_server'].set(dest)
                    window.update_copy_destination()
                    self.assertEqual(fields['destination'].get(), share)
            fields['destination_mode'].set('直接入力')
            window.update_copy_destination()
            fields['destination'].set('custom_share')
            fields['server'].set('Rackstation')
            window.copy_server_changed()
            self.assertEqual(fields['destination'].get(), 'custom_share')
            fields['server'].set(fields['dest_server'].get())
            window.copy_server_changed()
            self.assertNotEqual(fields['server'].get(), fields['dest_server'].get())
        finally:
            window.destroy()

    def test_sync_staging_copy_preserves_names(self):
        import transfer_copy
        with tempfile.TemporaryDirectory() as temp:
            def local_path(server, share, *parts):
                return str(Path(temp, server, share, *parts))
            source = Path(local_path('QDserver', 'analysis', 'analysis_ex', 'S', 'S_10k_Sample', 'T', 'ANAL_analysis'))
            source.mkdir(parents=True)
            (source / 'analysis.tdms').write_bytes(b'original')
            Path(local_path('QDserver', 'DT_server')).mkdir(parents=True)
            data = dict(kind='copy', server='QDserver', dest_server='QTserver',
                        destination_mode='自動入力', destination='stale_value', share='analysis',
                        experiment='analysis_ex', samples='S', extension='.tdms', suffix='_10k_Sample',
                        subpath='T', keyword='ANAL', tree='T')
            with patch.object(common, 'server_path', local_path), patch.object(transfer_copy, 'server_path', local_path), contextlib.redirect_stdout(io.StringIO()):
                app.run_job(data)
            target = Path(local_path('QDserver', 'DT_server', 'analysis_ex', 'S', 'S_10k_Sample', 'T', 'ANAL_analysis', 'analysis.tdms'))
            self.assertEqual(target.read_bytes(), b'original')
            self.assertFalse(Path(local_path('QTserver', 'DT_server')).exists())
            with self.assertRaises(ValueError):
                app.validate({**data, 'dest_server': 'QDserver'})

    def test_gui_preview_gate(self):
        window = app.App()
        window.withdraw()
        try:
            self.assertEqual(len(window.tabs.tabs()), 3)
            fields = window.fields['rename']
            fields['root'].set('C:/fixture')
            fields['old'].set('old')
            fields['new'].set('new')
            with patch.object(app.messagebox, 'showinfo') as info, patch.object(window, 'start') as start:
                window.submit('rename')
                info.assert_called_once()
                start.assert_not_called()
                window.submit('rename', True)
                self.assertTrue(start.call_args.args[0]['preview'])
                window.preview_signature = start.call_args.args[1]
                fields['new'].set('changed')
                start.reset_mock()
                window.submit('rename')
                start.assert_not_called()
        finally:
            window.destroy()

    def test_copy_folder_selection_keeps_share_and_sets_experiment(self):
        window = app.App()
        window.withdraw()
        try:
            fields = window.fields['copy']
            self.assertTrue(window.apply_copy_folder(
                'share', r'\\Rackstation\analysis\Kumamoto_N2', 'Rackstation', 'analysis'))
            self.assertEqual(fields['share'].get(), 'analysis')
            self.assertEqual(fields['experiment'].get(), 'Kumamoto_N2')
            with patch.object(app.messagebox, 'showerror'):
                for path in ('//QDserver/analysis/E', '//Rackstation/analysis/E/sample', 'C:/local'):
                    self.assertFalse(window.apply_copy_folder('share', path, 'Rackstation', 'analysis'))
                self.assertEqual(fields['experiment'].get(), 'Kumamoto_N2')
            self.assertTrue(window.apply_copy_folder('share', '//Rackstation/analysis', 'Rackstation', 'analysis'))
            self.assertEqual(fields['share'].get(), 'analysis')
            self.assertEqual(fields['experiment'].get(), '')
            self.assertTrue(window.apply_copy_folder('share', '//Rackstation/archives/E', 'Rackstation', 'analysis'))
            self.assertEqual(fields['share'].get(), 'archives')
            self.assertEqual(fields['experiment'].get(), 'E')
        finally:
            window.destroy()


if __name__ == '__main__':
    unittest.main()

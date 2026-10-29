import tempfile
import unittest
from pathlib import Path
from core import inspect_set_data, sampled_signature, signature, destination_warnings


class SetDataTest(unittest.TestCase):
    def test_destination_archive_and_analysis_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'sample'
            folder = target / 'sample_10k_Sample'
            for relative in ('stocked/nested', 'T/stocked', 'T/ANAL'):
                (folder / relative).mkdir(parents=True)
            self.assertEqual(destination_warnings(target, 'Sample'), [])
            (folder / 'stocked/nested/old.txt').touch()
            (folder / 'T/stocked/old.tdms').touch()
            (folder / 'T/ANAL/result.TDMS').touch()
            warnings = destination_warnings(target, 'Sample')
            self.assertEqual(len(warnings), 3)
            self.assertEqual(sum('退避ファイルあり' in w for w in warnings), 2)
            self.assertIn('解析進行中', warnings[2])
            self.assertEqual(destination_warnings(target, 'Blank'), [])

    def test_quick_check_is_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'first', Path(tmp) / 'second'
            content = bytearray(1024 * 1024)
            first.write_bytes(content)
            second.write_bytes(content)
            self.assertEqual(sampled_signature(first), sampled_signature(second))
            content[100000] = 1  # Outside the sampled regions.
            second.write_bytes(content)
            self.assertEqual(sampled_signature(first), sampled_signature(second))
            self.assertNotEqual(signature(first), signature(second))
            content[0] = 1
            second.write_bytes(content)
            self.assertNotEqual(sampled_signature(first), sampled_signature(second))

    def test_renamed_duplicate_and_same_size_different_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, target = root / 'experiment', root / 'sample'
            ten, hundred = source / 'EXSV/T', source / 'EXZSV'
            dest10, dest100 = target / 'sample_10k_Sample/T', target / 'sample_100k_Sample'
            for folder in (ten, hundred, dest10, dest100):
                folder.mkdir(parents=True)
            (ten / 'D_1.TDMS').write_bytes(b'abc')
            (ten / 'ignored.txt').touch()
            (ten / 'nested').mkdir()
            (ten / 'nested/ignored.tdms').touch()
            (hundred / 'Z_1.tdms').write_bytes(b'def')
            (dest10 / 'renamed.tdms').write_bytes(b'abc')
            (dest100 / 'Z_1.tdms').write_bytes(b'xyz')
            spec = dict(sources=[source], target=target, context=dict(kind='Sample'))
            counts, duplicates = inspect_set_data(spec, True)
            self.assertEqual(counts, (1, 1, 1, 1))
            self.assertEqual(duplicates, [('10k', ten / 'D_1.TDMS', dest10 / 'renamed.tdms')])
            self.assertEqual(inspect_set_data(spec), (counts, []))
            spec['context']['kind'] = 'Blank'
            self.assertEqual(inspect_set_data(spec, True), ((1, 0, 1, 0), []))

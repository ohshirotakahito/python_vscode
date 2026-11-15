import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core
from shared import ex_info_links


class MultiSampleBatchTests(unittest.TestCase):
    def test_multiple_measurements_per_sample_and_multiple_samples_execute_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            analysis = root / 'analysis'
            analysis.mkdir()
            core.create_destinations(analysis, 'experiment', ['sample-A', 'sample-B'])
            sources = []
            for index in range(3):
                source = root / f'measurement-{index}'
                (source / 'EXSV' / 'T').mkdir(parents=True)
                (source / 'EXZSV').mkdir()
                (source / 'EXSV' / f'D_{index}.txt').write_text(f'notes-{index}')
                (source / 'EXSV' / 'T' / f'D_{index}.tdms').write_text(f'data-{index}')
                sources.append(source)
            legacy, local = root / 'legacy.csv', root / 'logs' / 'history.csv'
            core.write_csv(legacy, [''] * 17, [])
            context = dict(uploader='SS', machine='AXN3', selection='Recent_Data', kind='Sample')
            specs = [dict(sources=sources[:2], target=analysis / 'experiment' / 'sample-A',
                          context=dict(context), legacy=legacy, local=local),
                     dict(sources=sources[2:], target=analysis / 'experiment' / 'sample-B',
                          context=dict(context), legacy=legacy, local=local)]
            jobs = core.plan_batch(specs)
            self.assertEqual([number for number, _, _ in jobs], [1, 1, 2])
            self.assertTrue(all(any(item.raw for item in plan.items) for _, plan, _ in jobs))
            for _, plan, spec in jobs:
                self.assertTrue(all(item.destination.is_relative_to(spec['target']) for item in plan.items))
            with patch.object(ex_info_links, 'TRANSFERS', root / 'Transfers.jsonl'):
                results = core.execute_batch(jobs, lambda message: None)
            self.assertEqual([result[3] for result in results], ['完了'] * 3)
            self.assertEqual(len(core.history(legacy, local)), 3)
            for _, plan, _ in jobs:
                self.assertTrue(all(item.destination.exists() for item in plan.items))


if __name__ == '__main__':
    unittest.main()

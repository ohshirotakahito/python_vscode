from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'app_tk'),
               str(Path(__file__).resolve().parents[1])]
import training_queue as queue_module
from training_queue import TrainingQueue, execute


def settings(name='first'):
    return dict(pipeline=dict(module=name, tsfresh=False), combinations=[[name, 'reference']],
                training_settings=dict(balance='under', tune=False, n_trials=1),
                feature_versions={name: 'fixed-version'}, file_filter_rules=[['events', '>=', '100']])


class TrainingQueueTests(unittest.TestCase):
    def test_each_job_keeps_an_independent_snapshot(self):
        queue = TrainingQueue()
        original = settings()
        queue.add(original)
        original['feature_versions']['first'] = 'changed'
        original['file_filter_rules'][0][2] = '999'
        job = queue.rows()[0]
        self.assertEqual(job['settings']['feature_versions']['first'], 'fixed-version')
        self.assertEqual(job['settings']['file_filter_rules'][0][2], '100')
        job['settings']['combinations'][0].clear()
        self.assertEqual(len(queue.rows()[0]['settings']['combinations'][0]), 2)

    def test_runs_all_sets_in_order_with_their_own_settings_and_pinned_versions(self):
        queue, events = TrainingQueue(), []
        first, second = settings('first'), settings('second')
        second['training_settings']['n_trials'] = 7
        queue.add(first)
        queue.add(second)

        def run(samples, **kwargs):
            events.append(('run', samples[0], kwargs['feature_versions']))
            return samples[0] + '-result'
        with patch.object(queue_module, 'load_pipeline', return_value=SimpleNamespace(run_analysis=run)), \
                patch('common.training_core.reset') as reset, \
                patch('common.training_core.configure', side_effect=lambda **kw: events.append(('config', kw['n_trials']))):
            results, errors, cancelled = execute(queue, Mock(), threading.Event())
        self.assertEqual([event[:2] for event in events], [('config', 1), ('run', 'first'), ('config', 7), ('run', 'second')])
        self.assertEqual(events[1][2], {'first': 'fixed-version'})
        self.assertEqual(len(results), 2)
        self.assertFalse(errors or cancelled)
        self.assertEqual([job['state'] for job in queue.rows()], ['完了', '完了'])
        self.assertEqual(reset.call_count, 2)

    def test_new_set_added_during_training_runs_automatically(self):
        queue = TrainingQueue()
        queue.add(settings('first'))

        def run(samples, **kwargs):
            if samples[0] == 'first':
                queue.add(settings('added'))
            return 'result'
        with patch.object(queue_module, 'load_pipeline', return_value=SimpleNamespace(run_analysis=run)):
            results, _, _ = execute(queue, Mock(), threading.Event())
        self.assertEqual([samples[0] for samples, _ in results], ['first', 'added'])

    def test_failed_set_does_not_block_following_sets(self):
        queue = TrainingQueue()
        queue.add(settings('broken'))
        queue.add(settings('good'))
        with patch.object(queue_module, 'load_pipeline', side_effect=[
                RuntimeError('cannot load pipeline'), SimpleNamespace(run_analysis=lambda *args, **kwargs: 'result')]):
            results, errors, _ = execute(queue, Mock(), threading.Event())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual([job['state'] for job in queue.rows()], ['失敗', '完了'])

    def test_each_tsfresh_set_applies_its_own_mode_and_selection_flag(self):
        queue, observed = TrainingQueue(), []
        module = SimpleNamespace(dp=SimpleNamespace(), FC_PARAMETERS_MODE='initial')

        def run(*args, **kwargs):
            observed.append((module.FC_PARAMETERS, module.dp.FC_PARAMETERS,
                             module.FC_PARAMETERS_MODE, module.USE_TSFRESH_FEATURE_SELECTION))
            return 'result'
        module.run_analysis = run
        for mode, selection in [('minimal', True), ('curated', False)]:
            job = settings(mode)
            job.update(fc_mode=mode, use_selection=selection)
            job['pipeline']['tsfresh'] = True
            queue.add(job)
        with patch.object(queue_module, 'load_pipeline', return_value=module), \
                patch('common.fc_parameters.fc_parameters_for', side_effect=lambda mode: {'mode': mode}):
            execute(queue, Mock(), threading.Event())
        self.assertEqual(observed, [({'mode': 'minimal'}, {'mode': 'minimal'}, 'minimal', True),
                                    ({'mode': 'curated'}, {'mode': 'curated'}, 'curated', False)])

    def test_cancellation_keeps_unstarted_sets_waiting(self):
        queue, stop = TrainingQueue(), threading.Event()
        queue.add(settings('first'))
        queue.add(settings('second'))

        def run(samples, progress_callback, **kwargs):
            stop.set()
            progress_callback(1, 2, 'cancel')
        with patch.object(queue_module, 'load_pipeline', return_value=SimpleNamespace(run_analysis=run)):
            _, _, cancelled = execute(queue, Mock(), stop)
        self.assertTrue(cancelled)
        self.assertEqual([job['state'] for job in queue.rows()], ['中止', '待機'])

    def test_running_set_cannot_be_removed_or_requeued(self):
        queue = TrainingQueue()
        running = queue.add(settings())
        pending = queue.add(settings('pending'))
        queue.take()
        queue.remove({running, pending})
        queue.retry({running})
        self.assertEqual([(job['id'], job['state']) for job in queue.rows()], [(running, '実行中')])


if __name__ == '__main__':
    unittest.main()

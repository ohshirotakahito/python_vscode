"""Thread-safe, sequential training jobs with independent settings snapshots."""
import copy
import importlib
import threading


class TrainingCancelled(Exception):
    pass


def load_pipeline(name):
    return importlib.import_module(name)


class TrainingQueue:
    def __init__(self):
        self.jobs = []
        self.lock = threading.Lock()
        self.next_id = 1

    def add(self, settings):
        with self.lock:
            job = dict(id=self.next_id, settings=copy.deepcopy(settings), state='待機', detail='')
            self.next_id += 1
            self.jobs.append(job)
            return job['id']

    def rows(self):
        with self.lock:
            return copy.deepcopy(self.jobs)

    def take(self):
        with self.lock:
            for job in self.jobs:
                if job['state'] == '待機':
                    job['state'] = '実行中'
                    return copy.deepcopy(job)

    def finish(self, job_id, state, detail=''):
        with self.lock:
            job = next(job for job in self.jobs if job['id'] == job_id)
            job.update(state=state, detail=detail)

    def remove(self, ids):
        with self.lock:
            self.jobs[:] = [job for job in self.jobs if job['id'] not in ids or job['state'] == '実行中']

    def retry(self, ids):
        with self.lock:
            for job in self.jobs:
                if job['id'] in ids and job['state'] != '実行中':
                    job.update(state='待機', detail='')


def execute(queue, progress, cancelled):
    results, errors = [], []
    while not cancelled.is_set():
        job = queue.take()
        if job is None:
            break
        settings = job['settings']
        combinations = settings['combinations']
        job_errors = 0
        try:
            module = load_pipeline(settings['pipeline']['module'])
            if settings['pipeline']['tsfresh']:
                from common.fc_parameters import fc_parameters_for
                parameters = fc_parameters_for(settings['fc_mode'])
                module.FC_PARAMETERS = parameters
                module.dp.FC_PARAMETERS = parameters
                if hasattr(module, 'FC_PARAMETERS_MODE'):
                    module.FC_PARAMETERS_MODE = settings['fc_mode']
                module.USE_TSFRESH_FEATURE_SELECTION = settings['use_selection']
            from common import training_core
            training_core.reset()
            training_core.configure(**settings['training_settings'])
            for index, samples in enumerate(combinations):
                def inner(current, total, message='', index=index, samples=samples):
                    if cancelled.is_set():
                        raise TrainingCancelled()
                    fraction = (index + min(current, total) / max(total, 1)) / len(combinations)
                    progress(int(fraction * 1000), 1000,
                             f"セット{job['id']} [{index + 1}/{len(combinations)}] {'-'.join(samples)}: {message}")
                try:
                    inner(0, 1, '開始')
                    directory = module.run_analysis(samples, progress_callback=inner,
                        feature_versions=settings['feature_versions'], file_filter_rules=settings['file_filter_rules'])
                    results.append((samples, directory))
                except TrainingCancelled:
                    raise
                except Exception as exc:
                    errors.append((samples, exc))
                    job_errors += 1
                    print(f'[ERROR] セット{job["id"]} {samples}: {exc}')
            queue.finish(job['id'], '一部失敗' if job_errors else '完了', f'{len(combinations)-job_errors}/{len(combinations)}件完了')
        except TrainingCancelled:
            queue.finish(job['id'], '中止', '未着手のセットは待機のまま保持')
            break
        except Exception as exc:
            queue.finish(job['id'], '失敗', str(exc))
            errors.append(([f'セット{job["id"]}'], exc))
            print(f'[ERROR] セット{job["id"]}: {exc}')
    return results, errors, cancelled.is_set()

"""Immutable extraction versions and per-training input selection (no UI dependencies)."""

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import inspect
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import uuid

from common import paths

_inputs = ContextVar("feature_inputs", default=None)
MANIFEST = "extraction_manifest.json"
JST = timezone(timedelta(hours=9))


class EmptyExtractionResult(ValueError):
    """Inputs were processed, but no events met the extraction requirements."""


def _write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _component(value):
    value = str(value)
    if not value or value in (".", "..") or any(c in value for c in '<>:"/\\|?*') or value.endswith((".", " ")):
        raise ValueError(f"Invalid sample name: {value!r}")
    return value


def fingerprint(path):
    path = Path(path)
    stat = path.stat()
    return {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _artifacts(root, sample, feature_set):
    stem = f"{sample}_10k_Sample_ANAL"
    if feature_set == "rmb":
        candidates = [root / f"{stem}_rmb.npy"]
    else:
        candidates = []
        for suffix in ("meta", "tsfresh_input"):
            candidates.append(next((root / f"{stem}_{suffix}{ext}" for ext in (".parquet", ".csv")
                                    if (root / f"{stem}_{suffix}{ext}").is_file()), root / f"{stem}_{suffix}.csv"))
        if feature_set == "rmd":
            candidates.append(root / f"{stem}_chronos_emb.csv")
    return candidates if all(p.is_file() and p.stat().st_size for p in candidates) else []


def list_versions(feature_set, sample, root=None, experiment=None, condition_id=None):
    """Newest completed versions first; current flat files remain an explicit legacy choice."""
    root = Path(root) if root is not None else paths.feature_dir(feature_set)
    versions = []
    for manifest_path in (root / _component(sample)).glob(f"*/{MANIFEST}"):
        try:
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                continue
            if (record.get("status") != "complete" or record.get("sample") != sample
                    or (feature_set is not None and record.get("feature_set") != feature_set)):
                continue
            artifacts = _artifacts(manifest_path.parent, sample, record.get("feature_set"))
            if (not artifacts or not isinstance(record.get("extracted_at"), str)
                    or not isinstance(record.get("run_id"), str)):
                continue
            if [fingerprint(p) for p in artifacts] != record.get('outputs'):
                continue
            if experiment is not None and record.get("ex") != experiment:
                continue
            if condition_id is not None and record.get("condition_id") != condition_id:
                continue
            versions.append({**record, "directory": str(manifest_path.parent.resolve())})
        except (OSError, ValueError, TypeError):
            continue
    versions.sort(key=lambda v: (v["extracted_at"], v["run_id"]), reverse=True)
    artifacts = _artifacts(root, sample, feature_set) if feature_set is not None else []
    if artifacts and experiment is None and condition_id is None:
        versions.append({"run_id": "legacy", "sample": sample, "feature_set": feature_set,
                         "extracted_at": None, "ex": None, "conditions": None,
                         "status": "legacy", "directory": str(root.resolve()),
                         "outputs": [fingerprint(p) for p in artifacts]})
    return versions


def resolve_version(feature_set, sample, run_id=None, root=None, **filters):
    versions = list_versions(feature_set, sample, root, **filters)
    for version in versions:
        if run_id in (None, "latest") or version["run_id"] == run_id:
            return version
    raise FileNotFoundError(f"{sample}: usable extraction not found ({run_id or 'latest'})")


def sample_directory(data_root, sample):
    """Use a pinned training version, or resolve latest for other consumers."""
    root = Path(data_root)
    active = _inputs.get()
    key = (str(root.resolve()), str(sample))
    if active is not None and key in active:
        return Path(active[key]["directory"])
    # Explicit version directories are already resolved.
    if (root / MANIFEST).exists():
        return root
    if (root / str(sample)).is_dir():
        versions = list_versions(None, sample, root=root)
        if versions:
            return Path(versions[0]["directory"])
    return root


def inspection_inputs(feature_set, samples, root, feature_versions=None):
    """Resolve and pin inspection inputs, including explicit legacy selections."""
    from contextlib import contextmanager

    @contextmanager
    def pinned():
        selected = {(str(Path(root).resolve()), str(sample)): resolve_version(
            feature_set, sample, (feature_versions or {}).get(sample), root=root)
            for sample in samples}
        token = _inputs.set(selected)
        try:
            yield list(selected.values())
        finally:
            _inputs.reset(token)

    return pinned()


def training_inputs():
    active = _inputs.get()
    selected = list(active.values()) if active is not None else []
    for version in selected:
        for output in version['outputs']:
            if fingerprint(output['path']) != output:
                raise RuntimeError(f"{version['sample']}: selected features changed during training")
    return selected


def _read_warnings(directory):
    import pandas as pd
    outcomes = directory / 'extraction_files.csv'
    if not outcomes.exists():
        return []
    files = pd.read_csv(outcomes).drop_duplicates('anal_path', keep='last')
    return [{'path': str(row.anal_path),
             'reason': str(row.check) if row.check != 'OK' else '抽出条件を満たすイベントなし'}
            for row in files.itertuples() if row.check != 'OK' or row.events == 0]


def recover_partial_version(directory):
    """Recover only results rejected by the old overly strict partial-read check."""
    import numpy as np
    directory = Path(directory)
    manifest = directory / MANIFEST
    record = json.loads(manifest.read_text(encoding='utf-8'))
    if (record.get('status') != 'failed' or not record.get('error', '').endswith(
            'some input files could not be read; result excluded from latest')):
        raise ValueError('This result was not rejected by the partial-read check')
    artifacts = _artifacts(directory, record['sample'], record['feature_set'])
    if not artifacts or record.get('event_count', 0) <= 0:
        raise ValueError('No usable saved extraction')
    if record['feature_set'] == 'rmb':
        array = np.load(artifacts[0], mmap_mode='r')
        if array.ndim != 2 or len(array) != record['event_count']:
            raise ValueError('Saved array does not match the recorded event count')
    record['previous_error'] = record.pop('error')
    record.update(status='complete', warnings=_read_warnings(directory),
                  outputs=[fingerprint(p) for p in artifacts],
                  recovered_at=datetime.now(JST).isoformat())
    _write(manifest, record)
    return record


def versioned_training(feature_set):
    """Pin all inputs before calculation; restore context even on failure/cancellation."""
    def decorate(function):
        @wraps(function)
        def wrapped(smns, *args, feature_versions=None, feature_experiment=None,
                    feature_condition_id=None, file_filter_rules=None, **kwargs):
            smns = list(smns)
            root = function.__globals__.get("DATA_ROOT")
            root = Path(root) if root is not None else paths.feature_dir(feature_set)
            selected = {(str(root.resolve()), str(sample)): resolve_version(
                feature_set, sample, (feature_versions or {}).get(sample), root=root,
                experiment=feature_experiment, condition_id=feature_condition_id) for sample in smns}
            token = _inputs.set(selected)
            from common.training_file_filter import _selection, prepare_selection
            filter_token = _selection.set(None)
            try:
                if file_filter_rules:
                    versions = {v['sample']: v for v in selected.values()}
                    _selection.set(prepare_selection(feature_set, smns, versions, file_filter_rules))
                return function(smns, *args, **kwargs)
            finally:
                _selection.reset(filter_token)
                _inputs.reset(token)
        return wrapped
    return decorate


def versioned_extraction(feature_set):
    """Run each sample in a fresh directory; preserve failed outputs but never select them."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, extraction_conditions=None, resume_run_ids=None, **kwargs):
            bound = inspect.signature(function).bind(*args, **kwargs)
            bound.apply_defaults()
            options = dict(bound.arguments)
            root = Path(options.get("output_dir") or paths.feature_dir(feature_set))
            samples = list(options["samples"])
            from common.tdms_io import (Analtfoler, tdmslist_files, SAMPLE_RATE_HZ,
                                       MIN_DURATION_SAMPLES, WAVE_SEGMENTS)
            for index, sample in enumerate(samples):
                sample = _component(sample)
                specified = options.get("files_by_sample")
                sources = specified.get(sample, []) if specified is not None else []
                if specified is None:
                    sources = [str(Path(folder) / name) for folder in sorted(Analtfoler(
                        options["server"], options["keyfolder"], options["ex"], sample))
                        for name in sorted(tdmslist_files(folder))]
                sources = sorted(set(map(str, sources)))
                if not sources:
                    reason = '選択されたANALファイルがありません' if specified is not None else 'ANAL済みファイルがありません'
                    print(f'[{sample}] スキップ: {reason}')
                    progress = options.get('progress_callback')
                    if progress is not None:
                        progress(index + 1, len(samples), f'{sample}: スキップ（{reason}）')
                    continue
                source_info = [fingerprint(p) for p in sources]
                now = datetime.now(JST)
                run_id = now.strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
                code_paths = [Path(inspect.getfile(function)), Path(__file__).with_name("tdms_io.py"),
                              Path(__file__).with_name("incremental_save.py")]
                hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in code_paths}
                conditions = {"method": code_paths[0].stem, "code_sha256": hashes,
                              "settings": extraction_conditions or {},
                              "waveform": {"sample_rate_hz": SAMPLE_RATE_HZ,
                                           "duration_min_samples_exclusive": MIN_DURATION_SAMPLES,
                                           "segments": WAVE_SEGMENTS,
                                           "normalization": "(raw_signal * 1000 - baseline) / event_signal"},
                              "runtime": {"python": platform.python_version()}}
                packages = ['numpy', 'pandas', 'npTDMS']
                if feature_set == 'rmd':
                    packages += ['torch', 'chronos-forecasting']
                for package in packages:
                    try:
                        conditions['runtime'][package] = version(package)
                    except PackageNotFoundError:
                        conditions['runtime'][package] = 'unknown'
                # Chronos settings are actual arguments, not just a user label.
                for key in ("chronos_model", "chronos_device"):
                    if key in options:
                        conditions[key] = options[key]
                condition_id = hashlib.sha256(json.dumps(conditions, sort_keys=True).encode()).hexdigest()[:16]
                record = {"schema_version": 1, "run_id": run_id, "sample": sample,
                          "feature_set": feature_set, "extracted_at": now.isoformat(),
                          "server": options["server"], "share": options["keyfolder"], "ex": options["ex"],
                          "conditions": conditions, "condition_id": condition_id,
                          "inputs": source_info, "status": "running"}
                resume_id = (resume_run_ids or {}).get(sample)
                if resume_id is not None:
                    run_id = _component(resume_id)
                directory = root / sample / run_id
                manifest = directory / MANIFEST
                if resume_id is not None:
                    previous = json.loads(manifest.read_text(encoding='utf-8'))
                    keys = ('sample', 'feature_set', 'server', 'share', 'ex', 'condition_id', 'inputs')
                    if previous.get('status') not in ('failed', 'running') or any(
                            previous.get(key) != record[key] for key in keys):
                        raise ValueError(f"{sample}: cannot resume with different inputs or conditions")
                    record = {**previous, 'status': 'running', 'resumed_at': now.isoformat()}
                    record.pop('error', None)
                else:
                    directory.mkdir(parents=True, exist_ok=False)
                _write(manifest, record)
                call = {**options, "samples": [sample], "output_dir": directory,
                        "files_by_sample": {sample: sources}}
                callback = options.get("progress_callback")
                if callback is not None:
                    def progress(done, total, message="", index=index):
                        callback(int((index + min(done, total) / max(total, 1)) / len(samples) * 1000),
                                 1000, message)
                    call["progress_callback"] = progress
                try:
                    function(**call)
                    if [fingerprint(p) for p in sources] != source_info:
                        raise RuntimeError(f"{sample}: input files changed during extraction")
                    artifacts = _artifacts(directory, sample, feature_set)
                    # Keep the file outcomes and event counts inside this immutable version.
                    history = directory / "extraction_runs.csv"
                    if history.exists():
                        import pandas as pd
                        latest = pd.read_csv(history).iloc[-1]
                        record["event_count"] = int(latest["total_after"])
                        record['warnings'] = _read_warnings(directory)
                        for warning in record['warnings']:
                            print(f"[WARN] {sample}: {warning['reason']} -> {warning['path']}")
                    elif feature_set == "rmd":
                        import pandas as pd
                        record["event_count"] = len(pd.read_csv(artifacts[0])) if artifacts else 0
                    if record.get("event_count", 0) <= 0:
                        raise EmptyExtractionResult(f"{sample}: 抽出条件を満たすイベントがありません（0件）")
                    if not artifacts:
                        raise ValueError(f"{sample}: extraction produced no usable result")
                    record.update(status="complete", outputs=[fingerprint(p) for p in artifacts])
                except EmptyExtractionResult as error:
                    record.update(status="failed", error=str(error))
                    _write(manifest, record)
                    print(f"[WARN] {error}。履歴を保存し、次のサンプルに進みます。")
                    if callback is not None:
                        callback(int((index + 1) / len(samples) * 1000), 1000, str(error))
                    continue
                except BaseException as error:
                    record.update(status="failed", error=f"{type(error).__name__}: {error}")
                    _write(manifest, record)
                    raise
                _write(manifest, record)
            return root
        return wrapped
    return decorate

# -*- coding: utf-8 -*-
"""
common/data_pipeline.py

【このモジュールについて】
以下の3ファイルに重複していた「meta/tsfreshデータ読込 → キャッシュ判定 →
tsfresh特徴量抽出 → 結合 → 特徴量選択」のパイプライン部分を統合したもの。
  - TOP_LightGBM_tsfresh_rmc_20260803.py
  - TOP_XGboost_tsfresh_classification_20260804_2.py
  - TOP_XGboost_tsfresh_classification_20260804_3.py（統合のベースに採用）

3ファイルを比較した結果、_run_extract_features / apply_tsfresh_feature_selection /
extract_features_for_sample / get_sample_cache_path は完全一致、それ以外の
_read_table / load_meta_data / load_long_data / _sample_paths 等は
XGboost_..._3.py が最も改良されたバージョン（pyarrowで直接parquet読込、
relative_signal/absolute_signal列の追加、より安全なマスク処理）だったため、
これを採用してこのモジュールに一本化した。
→ 結果として、LightGBM側のスクリプトもこの改良版の恩恵を受ける。

【設定値の上書き方法】
このモジュールはデフォルト設定を持つが、呼び出し側スクリプトの事情
（対象とする特徴量セットやフィルタ条件の違いなど）に合わせて、import後に
モジュール変数を上書きすることを想定している。以前は各スクリプトが
'az_data' 等のフォルダ名を直接書いていたが、common/paths.py に統一した
ことで、常に「どの特徴量セットを使うか」を明示的に指定する形になっている。

    import common.data_pipeline as dp
    import common.paths as paths
    dp.DATA_ROOT = paths.feature_dir('rmc')   # 特徴量セット名で明示的に指定する
    dp.CACHE_DIR = paths.cache_dir('rmc')
    dp.FC_PARAMETERS = CURATED_FC_PARAMETERS  # 各スクリプト固有のパラメータ辞書

各関数は data_root / cache_dir / fc_parameters 等を明示的に渡すこともでき、
その場合はモジュール変数より引数が優先される。
"""

import gc
import time
import math
import hashlib
import pickle
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from tsfresh import extract_features, select_features
from tsfresh.utilities.dataframe_functions import impute

import common.paths as paths
from common.filters import apply_filters


# ============================================================
# デフォルト設定（呼び出し側スクリプトからモジュール変数として上書き可能）
# 未上書きの場合にimportエラー等で気付かず古い場所を読みに行かないよう、
# デフォルトも common/paths.py の規約に沿った場所を指す。
# ただし通常は呼び出し側で必ず paths.feature_dir('<feature_set>') 等に
# 上書きすること（特徴量セット名を省略した「代表フォルダ」は存在しないため）。
# ============================================================
DATA_ROOT = paths.DATA_ROOT
CACHE_DIR = paths.DATA_ROOT / '_cache'

DURATION_LIMIT = (5, 1000)
BASELINE_LIMIT = (-300, 1000)
SIGNAL_LIMIT = (0, 1000)

N_JOBS = 4
CHUNKSIZE = 10
BATCH_SIZE_EVENTS = 20000
BATCH_EVENT_THRESHOLD = 20000

# 呼び出し側で必ず設定すること（MinimalFCParameters() やカスタム辞書など）
FC_PARAMETERS = None

# 条件付きデータ選択（file_number/machine_no/measured_at等での絞り込み）。
# 未設定(None)ならload_meta_data()は何も絞り込まない（従来通りの挙動）。
# 書式・詳細は common/filters.py の docstring を参照。
FILTERS = None


# ============================================================
# パス解決・キャッシュキー
# ============================================================

def _resolve_path(data_root: Path, base_name: str):
    parquet_path = data_root / f"{base_name}.parquet"
    csv_path = data_root / f"{base_name}.csv"
    if parquet_path.exists():
        return parquet_path
    return csv_path


def _sample_paths(smn, data_root=None):
    if data_root is None:
        data_root = DATA_ROOT
    data_root = Path(data_root)  # 文字列で渡された場合もPathに正規化する
    meta_path = _resolve_path(data_root, f"{smn}_10k_Sample_ANAL_meta")
    tsfresh_path = _resolve_path(data_root, f"{smn}_10k_Sample_ANAL_tsfresh_input")
    return meta_path, tsfresh_path


def _file_fingerprint(path: Path):
    """ファイルサイズ+更新日時から簡易指紋を作る。
    巨大なtsfresh_inputファイルの中身を読まずにキャッシュキーを決めるため。"""
    st = path.stat()
    return f"{st.st_size}_{int(st.st_mtime)}"


def _read_table(path: Path):
    """拡張子に応じてparquet/csvを読み分ける。
    parquetはpd.read_parquetではなくpyarrowで直接読む
    （プロセス内で複数回read_parquetすると発生する
      ArrowKeyError: pandas.period already defined を避けるため）。"""
    if path.suffix == '.parquet':
        return pq.read_table(path).to_pandas()
    return pd.read_csv(path)


def _sample_cache_key(smn, n_events, tsfresh_fingerprint, fc_parameters):
    key_src = f"{smn}|{n_events}|{tsfresh_fingerprint}|{type(fc_parameters).__name__}"
    return hashlib.md5(key_src.encode('utf-8')).hexdigest()[:12]


def get_sample_cache_path(smn, n_events, tsfresh_path, fc_parameters, cache_dir=None):
    if cache_dir is None:
        cache_dir = CACHE_DIR
    fp = _file_fingerprint(tsfresh_path)
    cache_key = _sample_cache_key(smn, n_events, fp, fc_parameters)
    cache_path = cache_dir / f"tsfresh_{smn}_{cache_key}.pkl"
    return cache_key, cache_path


# ============================================================
# データ読込
# ============================================================

def load_meta_data(smn, data_root=None,
                    duration_limit=None, baseline_limit=None, signal_limit=None):
    """meta（軽い）だけを読み込む。tsfresh_input（巨大）には一切触れない。
    キャッシュがあるかどうかの判定を、巨大ファイルを読む前に行うため。"""
    if data_root is None:
        data_root = DATA_ROOT
    duration_limit = duration_limit or DURATION_LIMIT
    baseline_limit = baseline_limit or BASELINE_LIMIT
    signal_limit = signal_limit or SIGNAL_LIMIT

    meta_path, tsfresh_path = _sample_paths(smn, data_root=data_root)

    if not meta_path.exists():
        print(f"⚠ 見つかりません: {meta_path}")
        return None, None
    if not tsfresh_path.exists():
        print(f"⚠ 見つかりません: {tsfresh_path}")
        return None, None

    try:
        meta_df = _read_table(meta_path)
    except Exception as e:
        print(f"❌ meta読み込み失敗 ({smn}): {type(e).__name__}: {e}")
        return None, None

    duration = meta_df['duration'].astype(np.int32)
    baseline = meta_df['baseline'].astype(np.float32)
    signal = meta_df['signal'].astype(np.float32)

    mask = (
        duration.between(duration_limit[0] + 1, duration_limit[1] - 1)
        & baseline.between(baseline_limit[0] + 1, baseline_limit[1] - 1)
        & signal.between(signal_limit[0] + 1e-12, signal_limit[1] - 1e-12)
    )
    meta_df = meta_df[mask].copy()
    meta_df['duration'] = duration[mask]
    meta_df['baseline'] = baseline[mask]
    meta_df['signal'] = signal[mask]

    # 'relative_signal': ベースラインからの相対的な信号値（= signal そのもの）
    # 'absolute_signal': ベースラインを足し戻した絶対値（= signal + baseline）
    meta_df['relative_signal'] = meta_df['signal']
    meta_df['absolute_signal'] = meta_df['signal'] + meta_df['baseline']

    meta_df['global_id'] = smn + '__' + meta_df['event_id'].astype(str)

    # 条件付きデータ選択（file_number/machine_no/measured_at等）。
    # FILTERSが未設定(None/{})なら何もしない（従来通りの挙動）。
    if FILTERS:
        meta_df = apply_filters(meta_df, FILTERS)

    return meta_df, tsfresh_path


def load_long_data(smn, tsfresh_path, valid_ids):
    """tsfresh_input（巨大）を読み込み、有効なイベントだけに絞り込む。
    キャッシュが無い場合にのみ呼ばれる。"""
    long_df = _read_table(tsfresh_path)
    long_df = long_df[long_df['id'].isin(valid_ids)].copy()
    long_df['id'] = smn + '__' + long_df['id'].astype(str)

    if 'value' in long_df.columns:
        long_df['value'] = long_df['value'].astype('float32')
    if 'time' in long_df.columns:
        try:
            long_df['time'] = pd.to_numeric(long_df['time'], downcast='integer')
        except Exception:
            pass

    return long_df


# ============================================================
# tsfresh特徴量抽出
# ============================================================

def _run_extract_features(sub_df, fc_parameters, n_jobs, chunksize):
    feats = extract_features(
        sub_df,
        column_id='id',
        column_sort='time',
        column_value='value',
        default_fc_parameters=fc_parameters,
        n_jobs=n_jobs,
        chunksize=chunksize,
        disable_progressbar=False,
    )
    return impute(feats)


def extract_features_for_sample(smn, long_df, n_events, use_cache=True,
                                 n_jobs=None, chunksize=None,
                                 batch_size_events=None,
                                 batch_event_threshold=None,
                                 fc_parameters=None,
                                 cache_dir=None,
                                 cache_key=None, cache_path=None):
    n_jobs = N_JOBS if n_jobs is None else n_jobs
    chunksize = CHUNKSIZE if chunksize is None else chunksize
    batch_size_events = BATCH_SIZE_EVENTS if batch_size_events is None else batch_size_events
    batch_event_threshold = BATCH_EVENT_THRESHOLD if batch_event_threshold is None else batch_event_threshold
    if fc_parameters is None:
        fc_parameters = FC_PARAMETERS
    if cache_dir is None:
        cache_dir = CACHE_DIR

    cache_dir.mkdir(parents=True, exist_ok=True)

    if cache_key is None or cache_path is None:
        cache_key = _sample_cache_key(smn, n_events, len(long_df), fc_parameters)
        cache_path = cache_dir / f"tsfresh_{smn}_{cache_key}.pkl"

    if use_cache and cache_path.exists():
        print(f"[{smn}] キャッシュを利用します: {cache_path}")
        with open(cache_path, 'rb') as f:
            feats = pickle.load(f)
        print(f"[{smn}] キャッシュから読み込み完了: {feats.shape}")
        return feats

    if n_events <= batch_event_threshold:
        print(f"[{smn}] tsfresh特徴量抽出を開始します "
              f"(events={n_events}, rows={len(long_df)}, n_jobs={n_jobs}, "
              f"chunksize={chunksize}, fc={type(fc_parameters).__name__})")
        t0 = time.time()
        feats = _run_extract_features(long_df, fc_parameters, n_jobs, chunksize)
        print(f"[{smn}] tsfresh特徴量抽出 完了: {feats.shape} ({time.time() - t0:.1f}秒)")

    else:
        batch_dir = cache_dir / f"{smn}_batches"
        batch_dir.mkdir(parents=True, exist_ok=True)

        unique_ids = long_df['id'].unique().tolist()
        n_batches = math.ceil(len(unique_ids) / batch_size_events)
        print(f"[{smn}] イベント数 {n_events} が閾値 {batch_event_threshold} を超えるため "
              f"{n_batches} バッチ（1バッチ最大 {batch_size_events} イベント）に分割して処理します")

        feats_list = []
        for b in range(n_batches):
            batch_ids = unique_ids[b * batch_size_events:(b + 1) * batch_size_events]
            batch_ids_set = set(batch_ids)

            batch_cache_key = hashlib.md5(
                f"{cache_key}|batch{b}|{len(batch_ids)}".encode('utf-8')
            ).hexdigest()[:12]
            batch_cache_path = batch_dir / f"batch_{b:04d}_{batch_cache_key}.pkl"

            if use_cache and batch_cache_path.exists():
                print(f"[{smn}] バッチ {b + 1}/{n_batches}: キャッシュ利用 ({batch_cache_path.name})")
                with open(batch_cache_path, 'rb') as f:
                    feats_b = pickle.load(f)
            else:
                t0 = time.time()
                sub_df = long_df[long_df['id'].isin(batch_ids_set)]
                print(f"[{smn}] バッチ {b + 1}/{n_batches}: "
                      f"events={len(batch_ids)}, rows={len(sub_df)} を抽出中...")
                feats_b = _run_extract_features(sub_df, fc_parameters, n_jobs, chunksize)
                print(f"[{smn}] バッチ {b + 1}/{n_batches} 完了: "
                      f"{feats_b.shape} ({time.time() - t0:.1f}秒)")

                if use_cache:
                    with open(batch_cache_path, 'wb') as f:
                        pickle.dump(feats_b, f)

                del sub_df
                gc.collect()

            feats_list.append(feats_b)

        feats = pd.concat(feats_list, axis=0, sort=False)
        if feats.isna().any().any():
            feats = impute(feats)
        del feats_list
        gc.collect()

    if use_cache:
        with open(cache_path, 'wb') as f:
            pickle.dump(feats, f)
        print(f"[{smn}] キャッシュ保存: {cache_path}")

    del long_df
    gc.collect()

    return feats


def clear_cache(cache_dir=None):
    """fc_parameters等の設定を変更した後、古いキャッシュを使い回さないよう明示的にクリアする"""
    if cache_dir is None:
        cache_dir = CACHE_DIR
    shutil.rmtree(cache_dir, ignore_errors=True)
    print(f"キャッシュを削除しました: {cache_dir}")


# ============================================================
# 全サンプル結合 + 特徴量選択
# ============================================================

def build_combined_dataset(smns, data_root=None, use_cache=True,
                            n_jobs=None, chunksize=None,
                            fc_parameters=None, cache_dir=None):
    """サンプルごとに逐次tsfresh特徴量を抽出してメタ特徴量と結合する。
    全サンプルに対して必ず同じfc_parametersを使うこと。"""
    if data_root is None:
        data_root = DATA_ROOT
    if fc_parameters is None:
        fc_parameters = FC_PARAMETERS
    if cache_dir is None:
        cache_dir = CACHE_DIR

    cache_dir.mkdir(parents=True, exist_ok=True)

    all_meta = []
    per_sample_features = []

    for smn in smns:
        meta_df, tsfresh_path = load_meta_data(smn, data_root=data_root)
        if meta_df is None or meta_df.empty:
            print(f"⚠ [{smn}] 有効なメタデータがないためスキップします")
            continue

        n_events = meta_df['event_id'].nunique()

        cache_key, cache_path = get_sample_cache_path(
            smn, n_events, tsfresh_path, fc_parameters, cache_dir=cache_dir
        )

        if use_cache and cache_path.exists():
            print(f"[{smn}] events(meta)={len(meta_df)} / "
                  f"キャッシュを利用します（巨大ファイルは読み込みません）: {cache_path}")
            with open(cache_path, 'rb') as f:
                feats = pickle.load(f)
            print(f"[{smn}] キャッシュから読み込み完了: {feats.shape}")
        else:
            valid_ids = set(meta_df['event_id'])
            long_df = load_long_data(smn, tsfresh_path, valid_ids)
            print(f"{smn}: events(meta)={len(meta_df)}, "
                  f"events(tsfresh入力)={long_df['id'].nunique()}, rows={len(long_df)}")

            if long_df.empty:
                print(f"⚠ [{smn}] tsfresh入力データが空のためスキップします")
                continue

            feats = extract_features_for_sample(
                smn, long_df, n_events,
                use_cache=use_cache, n_jobs=n_jobs, chunksize=chunksize,
                fc_parameters=fc_parameters, cache_dir=cache_dir,
                cache_key=cache_key, cache_path=cache_path
            )

        all_meta.append(meta_df)
        per_sample_features.append(feats)

    if not all_meta:
        raise RuntimeError(
            "有効なデータが1件も読み込めませんでした。data_root と smns を確認してください。"
        )

    meta_all = pd.concat(all_meta, axis=0, ignore_index=True)
    meta_all = meta_all.set_index('global_id')

    col_sets = [set(f.columns) for f in per_sample_features]
    base_cols = col_sets[0]
    mismatched = [i for i, c in enumerate(col_sets) if c != base_cols]
    if mismatched:
        mismatched_smns = [smns[i] for i in mismatched]
        raise RuntimeError(
            "サンプル間でtsfresh特徴量の列が一致していません: "
            f"{mismatched_smns}。'{cache_dir}' 以下の古いキャッシュを削除してから再実行してください。"
        )

    tsfresh_features = pd.concat(per_sample_features, axis=0, sort=False)

    combined = meta_all.join(tsfresh_features, how='inner')
    tsfresh_feature_cols = list(tsfresh_features.columns)
    print(f"\n結合後データ: {combined.shape}")

    return combined, tsfresh_feature_cols


def apply_tsfresh_feature_selection(dnf, tsfresh_feature_cols, n_jobs=None,
                                     chunksize=None):
    n_jobs = N_JOBS if n_jobs is None else n_jobs
    chunksize = CHUNKSIZE if chunksize is None else chunksize

    y_for_selection = pd.Series(
        [_.split('_')[0] for _ in dnf['sample']], index=dnf.index
    )

    X_tsfresh = dnf[tsfresh_feature_cols]

    gc.collect()

    print(f"\ntsfresh特徴量選択の実行前: {X_tsfresh.shape[1]} 列 "
          f"(n_jobs={n_jobs}, chunksize={chunksize})")
    X_selected = select_features(
        X_tsfresh, y_for_selection, n_jobs=n_jobs, chunksize=chunksize
    )
    print(f"tsfresh特徴量選択の実行後: {X_selected.shape[1]} 列")

    selected_cols = list(X_selected.columns)
    return selected_cols


# ============================================================
# FC_PARAMETERS変更前の事前見積もり
# ============================================================
# 元々 TOP_LightGBM_rmc.py にのみ存在した関数。tsfresh特徴量抽出のコストは
# fc_parameters（抽出する特徴量の種類・数）に強く依存し、かつ本体データ全件に
# 対して行うと時間がかかるため、少数イベントで試験抽出してから概算するための
# 関数。data_pipeline.py の他関数（load_meta_data/load_long_data/
# _run_extract_features）のみに依存しており、モデルの種類（XGBoost/LightGBM）
# に関係なく使えるため、ここに配置している。

def estimate_fc_parameters_cost(smns, data_root=None, fc_parameters=None,
                                 sample_smn=None, n_event_sample=2000,
                                 n_jobs=None, chunksize=None):
    """
    本番の全データ抽出を実行する前に、1サンプルの一部イベントだけで
    tsfresh特徴量抽出を試し、かかった時間から全体の所要時間を概算する。
    """
    data_root = DATA_ROOT if data_root is None else data_root
    fc_parameters = FC_PARAMETERS if fc_parameters is None else fc_parameters
    n_jobs = N_JOBS if n_jobs is None else n_jobs
    chunksize = CHUNKSIZE if chunksize is None else chunksize

    if sample_smn is None:
        sample_smn = smns[0]

    print(f"\n===== 事前見積もり: 『{sample_smn}』の先頭 {n_event_sample} イベントで "
          f"fc_parameters={type(fc_parameters).__name__ if not isinstance(fc_parameters, dict) else 'カスタム辞書'} "
          f"を試験抽出します =====")

    meta_df, tsfresh_path = load_meta_data(sample_smn, data_root=data_root)
    if meta_df is None or meta_df.empty:
        print(f"⚠ [{sample_smn}] 有効なメタデータがないため見積もりを中止します")
        return None

    sample_event_ids = set(meta_df['event_id'].astype(str).head(n_event_sample))
    long_df = load_long_data(sample_smn, tsfresh_path, sample_event_ids)

    if long_df.empty:
        print(f"⚠ [{sample_smn}] 試験用データが空のため見積もりを中止します")
        return None

    n_events_tested = long_df['id'].nunique()
    n_rows_tested = len(long_df)

    t0 = time.time()
    _ = _run_extract_features(long_df, fc_parameters, n_jobs, chunksize)
    elapsed = time.time() - t0

    per_event_sec = elapsed / max(n_events_tested, 1)

    total_events = 0
    for smn in smns:
        m_df, _ = load_meta_data(smn, data_root=data_root)
        if m_df is not None:
            total_events += len(m_df)

    est_total_sec = per_event_sec * total_events

    print(f"試験抽出: events={n_events_tested}, rows={n_rows_tested}, "
          f"所要時間={elapsed:.1f}秒 ({per_event_sec*1000:.2f}ミリ秒/イベント)")
    print(f"全サンプル合計イベント数: {total_events}")
    print(f"→ 全データ抽出の概算所要時間: 約 {est_total_sec/60:.1f} 分"
          f"（{est_total_sec/3600:.2f} 時間）※n_jobs={n_jobs}での概算、"
          f"実際はバッチ分割・ディスクIO等で前後します")

    return {
        'n_events_tested': n_events_tested,
        'elapsed_sec': elapsed,
        'per_event_sec': per_event_sec,
        'total_events': total_events,
        'estimated_total_sec': est_total_sec,
    }

# -*- coding: utf-8 -*-
"""
common/paths.py

【このモジュールについて】
特徴量生成スクリプト（TOP_Feex_*.py）と機械学習スクリプト（TOP_LightGBM_*.py /
TOP_XGboost_*.py）の保存先フォルダを一元管理する。

これまで各スクリプトが個別に 'clips_rmb' / 'clips_rmc' / 'analysis_outputs' /
'clips_tsfresh' のようなフォルダ名を直接書いており、
  ・TOP_XGboost_rmc.py の結果だけ何故か 'clips_tsfresh' に保存される
  ・tsfreshキャッシュ('_tsfresh_cache')が特徴量データと同じ階層に混在する
  ・「どの特徴量セットを使った結果か」がフォルダ名から分からない
といったズレが発生していた。このモジュールを介すことで、フォルダ命名規則を
1箇所に集約し、今後 特徴量抽出手法（rmb/rmc/rmd/...）や MLアルゴリズム
（xgboost/lightgbm/...）が増えても、同じ関数を呼ぶだけで一貫した場所に
保存されるようにする。

【ディレクトリ構成】
    data/features/<feature_set>/          … 特徴量データ本体（TOP_Feex_*.py の出力）
    data/features/<feature_set>/_cache/   … 特徴量エンジニアリングの中間キャッシュ
    models/<feature_set>/<algorithm>/<smns_tag>/
                                           … 学習済みモデル一式（TOP_XGboost_mix_*.py 等が
                                             保存する Booster/Scaler/LabelEncoder/manifest。
                                             results/ とは別ツリーにして、「毎回の実行結果
                                             （消して良い）」と「学習済みモデルという資産
                                             （残すべきもの）」を分離している）
    results/<feature_set>/<algorithm>/<timestamp>_<smn1-smn2-...>/
                                           … 学習・評価結果（TOP_LightGBM_*.py / TOP_XGboost_*.py の出力）

【使い方】
    import common.paths as paths

    # 特徴量生成スクリプト側
    OUTPUT_DIR = paths.feature_dir('rmb')

    # 機械学習スクリプト側
    dp.DATA_ROOT = paths.feature_dir('rmc')
    dp.CACHE_DIR = paths.cache_dir('rmc')
    RUN_DIR, RUN_TIMESTAMP = paths.new_run('rmc', 'xgboost', smns=smns)

    # 学習済みモデルの保存先（mix系スクリプトなど、モデルを保存・再利用する側）
    MODEL_DIR = paths.model_dir('rmc', 'xgboost', '-'.join(smns))
"""

from datetime import datetime
from pathlib import Path
import json

# プロジェクトルート（common/ の1つ上の階層）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 特徴量データの置き場所（TOP_Feex_*.py の出力先はこの下に統一する）
DATA_ROOT = PROJECT_ROOT / 'data' / 'features'

# 学習済みモデルの置き場所（results/ とは別ツリー。モデルという「資産」を
# 実行結果という「ログ」から分離するために独立させている）
MODELS_ROOT = PROJECT_ROOT / 'models'

# 学習・評価結果の置き場所（TOP_LightGBM_*.py / TOP_XGboost_*.py の出力先はこの下に統一する）
RESULTS_ROOT = PROJECT_ROOT / 'results'


def feature_dir(feature_set: str) -> Path:
    """特徴量セットごとの保存先フォルダを返す（無ければ作成する）。

    feature_set: 'rmb' / 'rmc' / 'rmd' など、TOP_Feex_*.py の系統名。
                 新しい抽出手法を追加する際は、ここに渡す文字列を
                 新規に決めるだけでよい（新しいフォルダ名をこのモジュールに
                 追加登録する必要はない）。

    例: feature_dir('rmc') -> <project_root>/data/features/rmc
    """
    d = DATA_ROOT / feature_set
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir(feature_set: str) -> Path:
    """特徴量セットに対応する中間キャッシュ（tsfresh特徴量抽出キャッシュ等）の
    保存先フォルダを返す（無ければ作成する）。

    特徴量データ本体（feature_dir）と同じ階層に混在させず、
    '_cache' サブフォルダに隔離することで、「消して良いもの」と
    「消してはいけないもの」を区別しやすくする。

    例: cache_dir('rmc') -> <project_root>/data/features/rmc/_cache
    """
    d = feature_dir(feature_set) / '_cache'
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_dir(feature_set: str, algorithm: str, smns_tag: str) -> Path:
    """学習済みモデル一式（Booster/Scaler/LabelEncoder/manifest等）の保存先
    フォルダを返す（無ければ作成する）。

    results/ 配下（毎回の実行結果=消しても再現できるログ）とは別のツリー
    （models/ 配下）に置くことで、「学習済みモデルという資産」を実行結果の
    量産フォルダに埋もれさせず、バックアップ対象としても区別しやすくする。

    feature_set: 'rmb' / 'rmc' など。
    algorithm  : 'xgboost' / 'lightgbm' など。
    smns_tag   : 学習に使ったクラスの組み合わせを表す文字列
                 （例: '-'.join(smns) = 'Guanine-OMeG'）。
                 呼び出し側でバージョン管理用のサブフォルダ（latest.txt や
                 タイムスタンプ付きフォルダ）をこの下にさらに作る想定。

    例: model_dir('rmb', 'xgboost', 'Guanine-OMeG')
        -> <project_root>/models/rmb/xgboost/Guanine-OMeG
    """
    d = MODELS_ROOT / feature_set / algorithm / smns_tag
    d.mkdir(parents=True, exist_ok=True)
    return d

def data_check_dir(server: str) -> Path:
    """check_available_tdms_data.py が出力する、サーバーごとの
    「TDMSデータ棚卸し結果」の保存先フォルダを返す(無ければ作成する)。

    data/features/(特徴量本体)や results/(学習結果)とは別の、
    「元データがサーバー上にどれだけ存在するか」を確認した記録専用の場所。

    実行するたびにタイムスタンプ付きCSVを追加保存していく想定
    （履歴として過去の状態も追える）。加えて、同じ内容を
    latest.csv としても上書き保存することで、「今の最新状態」を
    毎回同じファイル名で参照できるようにする
    （model_dir() で使っている latest.txt と同じ考え方）。

    server: 'Rackstation' / 'QTserver' / 'QDserver' など。

    例: data_check_dir('QTserver')
        -> <project_root(=ML/)>/data_checks/QTserver
    """
    d = PROJECT_ROOT / 'data_checks' / server
    d.mkdir(parents=True, exist_ok=True)
    return d

def new_run(feature_set: str, algorithm: str, smns=None, timestamp: str = None,
            run_type: str = None):
    """機械学習の実行ごとに、一意な結果保存フォルダを作成して返す。

    フォルダ構成: results/<feature_set>/<algorithm>/<timestamp>_<run_type>_<smn1-smn2-...>/
    （run_type省略時は従来通り results/<feature_set>/<algorithm>/<timestamp>_<smns>/）
    こうしておくことで、「どの特徴量セット」を「どのアルゴリズム」で学習した
    結果かが、パスをたどるだけで分かるようになる
    （従来の create_run_output_dir() / make_run_dir() を統合したもの）。

    feature_set: 'rmc' など。dp.DATA_ROOT に渡した文字列と揃えること。
    algorithm  : 'lightgbm' / 'xgboost' など。新しいアルゴリズムを追加する
                 場合も、ここに渡す文字列を決めるだけでよい。
    smns       : サンプル名のリスト（フォルダ名に含める）。省略時は None 扱い。
    timestamp  : 省略時は現在時刻から自動生成する。
    run_type   : この実行が何をしたものかを表す短いタグ（例: 'train', 'mix'）。
                 同じ feature_set/algorithm/smns の組み合わせでも、
                 「純粋分子の識別モデル学習」なのか「混合サンプルの比率予測」
                 なのかをフォルダ名だけで区別できるようにするためのもの。
                 省略時はフォルダ名に含めない（従来通りの挙動）。

    戻り値: (run_dir: Path, timestamp: str)
    """
    timestamp = timestamp or datetime.now().strftime('%Y%m%d_%H%M%S')
    label = '-'.join(smns) if smns else 'run'

    if run_type:
        folder_name = f"{timestamp}_{run_type}_{label}"
    else:
        folder_name = f"{timestamp}_{label}"

    run_dir = RESULTS_ROOT / feature_set / algorithm / folder_name
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"結果保存フォルダを作成しました: {run_dir}")
    return run_dir, timestamp


def algorithm_base_dir(feature_set: str, algorithm: str) -> Path:
    """特定の特徴量セット×アルゴリズムの結果を横断的に眺めたいとき用の、
    タイムスタンプ抜きのベースフォルダ（results/<feature_set>/<algorithm>/）。

    関数のデフォルト引数など、実行前に確定させておきたい箇所でのみ使う想定。
    実際の保存には new_run() を使うこと。

    ※ 学習済みモデルの保存先には使わないこと（results/ 配下になってしまう）。
       モデルの保存先が欲しい場合は model_dir() を使う。
    """
    d = RESULTS_ROOT / feature_set / algorithm
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_run_manifest(run_dir, run_timestamp, smns, config=None,
                        execution_time=None, comparison_df=None, extra_info=None):
    """「このフォルダの結果はどの設定・データで実行したものか」を後から
    追跡できるよう、実行条件をJSONで保存する（元々TOP_LightGBM_rmc.pyに
    あった関数を、モデル固有の設定に依存しない形に汎化したもの）。

    run_dir       : 保存先フォルダ（new_run()の戻り値）。
    run_timestamp : new_run()が返したタイムスタンプ文字列。
    smns          : 学習に使ったクラスの組み合わせ。
    config        : 実行条件として残したい設定値の辞書（例:
                     {'fc_parameters_mode': 'curated', 'n_meta_features': 15,
                      'use_tsfresh_feature_selection': True}）。
                     呼び出し側スクリプトごとに設定項目が異なるため、
                     このモジュールでは中身を規定せずそのまま記録する。
    comparison_df : compare_feature_sets()（Step1）が返す比較表。渡された場合は
                    特徴量セットごとの列数・Macro F1もまとめて記録する。
    extra_info    : その他、manifestに追加したい任意の情報の辞書。
    """
    run_dir = Path(run_dir)

    manifest = {
        'run_timestamp': run_timestamp,
        'run_datetime_iso': datetime.now().isoformat(),
        'smns': list(smns),
        'execution_time_sec': execution_time,
        'output_dir': str(run_dir.resolve()),
    }

    if config:
        manifest.update(config)

    if comparison_df is not None:
        manifest['feature_set_results'] = {
            feature_set: {
                'n_features': int(row['n_features']),
                'macro_f1': float(row['macro_f1']),
            }
            for feature_set, row in comparison_df.iterrows()
        }

    if extra_info:
        manifest.update(extra_info)

    manifest_path = run_dir / 'run_manifest.json'
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n実行条件をmanifestとして保存しました: {manifest_path}")
    return manifest_path

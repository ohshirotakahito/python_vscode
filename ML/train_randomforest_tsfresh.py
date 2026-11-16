"""rmc + RandomForest with the shared feature selection, split and model format."""
import functools
import shutil
import time

from common import data_pipeline as dp, paths, training_core
from common.data_pipeline import apply_tsfresh_feature_selection, build_combined_dataset
from common.event_limits import DURATION_LIMIT, BASELINE_LIMIT, SIGNAL_LIMIT
from common.eval_viz import conmtx
from common.fc_parameters import fc_parameters_for
from common.feature_store import versioned_training
from common.ml_analysis import compare_feature_sets, plot_feature_importance
from common.trained_models import save_model
from common.training_features import RMC_FEATURES, select_feature_groups

FEATURE_SET = 'rmc'
ALGORITHM = 'randomforest'
FC_PARAMETERS_MODE = 'minimal'
FC_PARAMETERS = fc_parameters_for(FC_PARAMETERS_MODE)
USE_TSFRESH_FEATURE_SELECTION = True
N_JOBS = 4
CHUNKSIZE = 10


def train_rf_classifier(dnf, feature_columns, feature_set_name='model', n_jobs=N_JOBS, split=None):
    split = split or training_core.make_split(dnf)
    return training_core.train_classifier(dnf, feature_columns, ALGORITHM, split,
                                          feature_set_name=feature_set_name, n_jobs=n_jobs)


@versioned_training(FEATURE_SET)
def run_analysis(smns, progress_callback=None):
    started = time.monotonic()
    def report(current, message):
        if progress_callback:
            progress_callback(current, 5, message)
    report(0, 'rmc特徴量とtsfreshデータを構築中…')
    dnf, tsfresh_columns = build_combined_dataset(
        smns, data_root=paths.feature_dir(FEATURE_SET), fc_parameters=FC_PARAMETERS,
        cache_dir=paths.cache_dir(FEATURE_SET), n_jobs=N_JOBS, chunksize=CHUNKSIZE,
        progress_callback=lambda current, total, message: report(current / max(total, 1), message))
    meta_columns, tsfresh_columns = select_feature_groups(
        RMC_FEATURES, tsfresh_columns, training_core.current_settings()['selected_features'])
    dnf = training_core.prepare_dataset(dnf)
    split = training_core.make_split(dnf)
    report(1, '学習用データ内でtsfresh特徴量を選択中…')
    if USE_TSFRESH_FEATURE_SELECTION and tsfresh_columns:
        tsfresh_columns = apply_tsfresh_feature_selection(
            dnf.iloc[split['train']], tsfresh_columns, n_jobs=N_JOBS, chunksize=CHUNKSIZE)
    if not meta_columns and not tsfresh_columns:
        raise ValueError('学習に使える特徴量がありません。特徴量の選択・自動選択設定を確認してください。')
    directory, timestamp = paths.new_run(FEATURE_SET, ALGORITHM, smns=smns, run_type='train')
    report(2, 'RandomForestを交差検証・学習中…')
    results, _, names = compare_feature_sets(
        dnf, meta_columns, tsfresh_columns,
        train_fn=functools.partial(train_rf_classifier, split=split), n_jobs=N_JOBS, output_dir=directory)
    main = results[names['combined']]
    training_core.save_training_details(results, directory)
    training = training_core.manifest_config(split, main)
    report(3, '学習器・混同行列・重要度を保存中…')
    save_model(directory, main, ALGORITHM, FEATURE_SET, smns, fc_parameters_mode=FC_PARAMETERS_MODE,
               details={'use_tsfresh_feature_selection': USE_TSFRESH_FEATURE_SELECTION, 'training': training,
                        'importance_method': 'mean_decrease_in_impurity'})
    conmtx(main['y_test'], main['y_pred'], main['le'], save_dir=directory,
           classification_config=training_core.current_settings())
    plot_feature_importance(main['clf'], main['feature_columns'], top_n=30, save_dir=directory,
                            importance_label='Mean decrease in impurity')
    paths.write_run_manifest(directory, timestamp, smns, config={
        'fc_parameters_mode': FC_PARAMETERS_MODE,
        'use_tsfresh_feature_selection': USE_TSFRESH_FEATURE_SELECTION,
        'tsfresh_feature_selection_fit': '学習用イベントのみ',
        'n_meta_features': len(meta_columns), 'training': training,
        'importance_method': 'mean_decrease_in_impurity',
        'event_limits': {'duration': DURATION_LIMIT, 'baseline': BASELINE_LIMIT, 'signal': SIGNAL_LIMIT},
    }, execution_time=time.monotonic() - started)
    report(4, '結果をZIP保存中…')
    shutil.make_archive(str(directory), 'zip', root_dir=directory.parent, base_dir=directory.name)
    report(5, '完了')
    return directory

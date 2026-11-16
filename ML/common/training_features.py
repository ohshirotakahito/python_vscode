"""Manual feature allowlists shared by the training UI and pipelines."""

RMC_FEATURES = ['absolute_signal', 'relative_signal', 'duration'] + [f'wave_{i}' for i in range(12)]
RMB_FEATURES = ['relative_signal', 'absolute_signal', 'signal_time'] + [f'f{i}' for i in range(1, 13)]


def feature_catalog(feature_set, fc_mode='minimal'):
    if feature_set == 'rmb':
        return list(RMB_FEATURES)
    if feature_set != 'rmc':
        raise ValueError(f'未対応の特徴量セット: {feature_set}')
    from common.fc_parameters import fc_parameters_for
    from tsfresh.utilities.string_manipulation import convert_to_output_format
    columns = list(RMC_FEATURES)
    for calculator, parameters in fc_parameters_for(fc_mode).items():
        for parameter in parameters if parameters is not None else [None]:
            suffix = '__' + convert_to_output_format(parameter) if parameter else ''
            columns.append(f'value__{calculator}{suffix}')
    return list(dict.fromkeys(columns))


def validate_selection(selected):
    if selected is not None and (not isinstance(selected, list) or not selected or
                                 any(not isinstance(name, str) or not name for name in selected) or
                                 len(set(selected)) != len(selected)):
        raise ValueError('学習に使う特徴量を1つ以上選択してください（重複は指定できません）。')


def select_feature_groups(meta, tsfresh, selected=None):
    """Apply the allowlist before automatic selection, preserving dataset order."""
    validate_selection(selected)
    if selected is None:
        return list(meta), list(tsfresh)
    missing = set(selected) - set(meta) - set(tsfresh)
    if missing:
        raise ValueError(f'指定した特徴量がデータにありません: {sorted(missing)}')
    allowed = set(selected)
    return [c for c in meta if c in allowed], [c for c in tsfresh if c in allowed]

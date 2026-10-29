# -*- coding: utf-8 -*-
"""tsfresh特徴量の抽出設定（全tsfreshパイプラインで共通）。

軽い順に6段階。Curated と Efficient の間の3段階は、Efficient で学習したモデル
（ALTNA/CLTNA/GLTNA/TLTNAr, 2026-10-20, macro F1 0.979 / Curated は 0.533）の重要度を
tsfresh計算器ごとに集計した結果に基づいて決めている。

  集計結果（Efficient 選択後 697 特徴量）:
    fft_coefficient k=50〜99      : gain の約92%, SHAP の約56%（Curated は k=0〜4 しか持たない）
    permutation_entropy / change_quantiles / agg_linear_trend / ar_coefficient /
    partial_autocorrelation / cwt系 / lempel_ziv / fourier_entropy / energy_ratio_by_chunks /
    index_mass_quantile / length 等 : 上記と合わせて gain の約98%, SHAP の約90%
    残りの計算器（augmented_dickey_fuller, friedrich_coefficients, symmetry_looking 等）
                                   : ほぼ寄与なし

'minimal'         : MinimalFCParameters（列あたり約10特徴量）。最速・最軽量。
'curated'         : 物理カテゴリ（電流強度/イベント時間/波形非対称性/周波数成分/ピーク数/
                    立ち上がり立ち下がり/局所変動/自己相関）を横断的にカバーするよう手動で選んだ
                    中間セット。
'curated_fft_abs' : Curated + FFT振幅スペクトル k=0〜99。位相に依存しない高周波成分だけを足す。
'curated_fft'     : Curated + FFT係数 k=0〜99 の全成分（real/imag/abs/angle）。
                    FFTは1系列1回の計算で全係数が出るので、特徴量数の割に抽出は軽い。
'extended'        : curated_fft + Efficient で寄与が確認できた非FFT計算器（パラメータは
                    EfficientFCParameters と同じ）。寄与のなかった計算器は除く。
'efficient'       : EfficientFCParameters（列あたり数百特徴量）。最も網羅的だが、抽出時間・
                    メモリともに大幅に増えるため、estimate_fc_parameters_cost() で見積もってから使う。
"""
import copy

FC_PARAMETER_MODES = {
    'minimal': 'Minimal（高速・約10特徴量/列）',
    'curated': 'Curated（中間・約60特徴量/列）',
    'curated_fft_abs': 'Curated+FFT振幅（中間・約150特徴量/列）',
    'curated_fft': 'Curated+FFT全成分（中速・約450特徴量/列）',
    'extended': 'Extended（やや低速・約670特徴量/列）',
    'efficient': 'Efficient（低速・約780特徴量/列）',
}

CURATED_FC_PARAMETERS = {
    # --- 電流強度 ---
    'abs_energy': None,
    'root_mean_square': None,
    'mean': None,
    'median': None,
    'maximum': None,
    'minimum': None,
    'sum_values': None,
    'quantile': [{'q': 0.1}, {'q': 0.25}, {'q': 0.75}, {'q': 0.9}],
    # --- 波形非対称性 ---
    'skewness': None,
    'kurtosis': None,
    # --- 周波数成分 ---
    'fft_coefficient': [{'coeff': k, 'attr': a} for k in range(5) for a in ['real', 'imag', 'abs']],
    'fft_aggregated': [{'aggtype': a} for a in ['centroid', 'variance', 'skew', 'kurtosis']],
    # --- ピーク数 ---
    'number_peaks': [{'n': n} for n in [1, 3, 5]],
    'number_cwt_peaks': [{'n': n} for n in [1, 5]],
    # --- 立ち上がり・立ち下がり ---
    'linear_trend': [{'attr': a} for a in ['slope', 'intercept', 'rvalue']],
    'agg_linear_trend': [{'attr': 'slope', 'chunk_len': cl, 'f_agg': 'mean'} for cl in [5, 10]],
    'first_location_of_maximum': None,
    'last_location_of_maximum': None,
    'first_location_of_minimum': None,
    'last_location_of_minimum': None,
    'longest_strike_above_mean': None,
    'longest_strike_below_mean': None,
    # --- 局所変動 ---
    'variance': None,
    'standard_deviation': None,
    'mean_abs_change': None,
    'absolute_sum_of_changes': None,
    'variance_larger_than_standard_deviation': None,
    # --- 自己相関 ---
    'autocorrelation': [{'lag': l} for l in [1, 2, 3, 5]],
    'c3': [{'lag': l} for l in [1, 2, 3]],
    'time_reversal_asymmetry_statistic': [{'lag': l} for l in [1, 2, 3]],
}

# tsfresh の fft_coefficient が返せる係数の上限（EfficientFCParameters と同じ k=0〜99）
FFT_MAX_COEFF = 100

# Efficient モデルで寄与が確認できた非FFT計算器（'extended' で追加。パラメータは Efficient のまま）
EXTENDED_EXTRA_CALCULATORS = [
    'permutation_entropy', 'change_quantiles', 'agg_linear_trend', 'ar_coefficient',
    'partial_autocorrelation', 'cwt_coefficients', 'lempel_ziv_complexity', 'fourier_entropy',
    'energy_ratio_by_chunks', 'index_mass_quantile', 'length',
    'ratio_value_number_to_time_series_length',
    'percentage_of_reoccurring_values_to_all_values',
    'percentage_of_reoccurring_datapoints_to_all_datapoints',
]


def _curated_with_fft(attrs):
    params = copy.deepcopy(CURATED_FC_PARAMETERS)
    params['fft_coefficient'] = [{'coeff': k, 'attr': a} for k in range(FFT_MAX_COEFF) for a in attrs]
    return params


def fc_parameters_for(mode):
    """モード名から tsfresh の fc_parameters を返す（呼び出し側で書き換えても共有辞書は汚れない）。"""
    if mode == 'minimal':
        from tsfresh.feature_extraction import MinimalFCParameters
        return MinimalFCParameters()
    if mode == 'curated':
        return CURATED_FC_PARAMETERS
    if mode == 'curated_fft_abs':
        return _curated_with_fft(['abs'])
    if mode == 'curated_fft':
        return _curated_with_fft(['real', 'imag', 'abs', 'angle'])
    if mode == 'extended':
        from tsfresh.feature_extraction import EfficientFCParameters
        efficient = EfficientFCParameters()
        params = _curated_with_fft(['real', 'imag', 'abs', 'angle'])
        for name in EXTENDED_EXTRA_CALCULATORS:
            params[name] = copy.deepcopy(efficient[name])
        return params
    if mode == 'efficient':
        from tsfresh.feature_extraction import EfficientFCParameters
        return EfficientFCParameters()
    raise ValueError(f"未知の FC_PARAMETERS_MODE: {mode}")

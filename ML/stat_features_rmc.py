# -*- coding: utf-8 -*-
"""RMC のメタ特徴量について、学習せずにヒストグラムと統計量を出力する。

入力:
    ML/data/features/rmc/<sample>_10k_Sample_ANAL_meta.csv (または .parquet)

出力:
    ML/hist_results/<実行日時>_<sample1>-<sample2>-.../
        histogram_<feature>.png
        hist_stats.csv
        summary_stats.csv
        used_sample_names.csv

実行例:
    python stat_features_rmc.py LPhe LIle
    python stat_features_rmc.py LPhe LIle --bins 50
    python stat_features_rmc.py LPhe LIle --upper-limit absolute_signal=200
    python stat_features_rmc.py LPhe LIle --lower-limit absolute_signal=-50
    python stat_features_rmc.py LPhe LIle LVal --colors blue orange green
"""

from __future__ import annotations

import argparse
from datetime import datetime
import difflib
from pathlib import Path
import re

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.colors import is_color_like

import common.paths as paths
from common.ml_analysis import data_stat


WAVE_COLUMNS = [f"wave_{i}" for i in range(12)]
FEATURE_COLUMNS = ["absolute_signal", "relative_signal", "duration", *WAVE_COLUMNS]

# train_xgboost_tsfresh.py と同じイベント抽出後フィルタ
DURATION_LIMIT = (5, 200)
BASELINE_LIMIT = (-300, 200)
SIGNAL_LIMIT = (0, 100)

DEFAULT_SAMPLES = ['Lys']
#DEFAULT_SAMPLES = ['vasopressin', 'AA19LCys','AA33LTyr','AA28LPhe','AA20LGln','AA17LAsn','AA29LPro','AA16LArg','AA22Gly']#oxytocin-vasopressin
#DEFAULT_SAMPLES = ['oxytocin', 'AA19LCys','AA33LTyr','AA24LIso','AA20LGln','AA17LAsn','AA29LPro','AA25LLeu','AA22Gly']#oxytocin-vasopressin

#DEFAULT_SAMPLES = ['AA33LTyr']

DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent / "hist_results"
DEFAULT_UPPER_LIMITS = {"relative_signal": 80.0, "duration": 150.0}
DEFAULT_LOWER_LIMITS: dict[str, float] = {}
DEFAULT_COLOR_PALETTE = "Set1"

# distanceによるデータ選択
# 選択しない場合は、3項目すべてをNoneにする。
# 特定値を選ぶ例#:
# DEFAULT_DISTANCE_VALUES = [0.56]
# 範囲を選ぶ例  :
# DEFAULT_DISTANCE_MIN = 0.50 / DEFAULT_DISTANCE_MAX = 0.60
DEFAULT_DISTANCE_VALUES: list[float] | None = None
DEFAULT_DISTANCE_MIN: float | None = None
DEFAULT_DISTANCE_MAX: float | None = None

# True: サンプルごとに使用する sample_name の選択画面を表示する
# False: 選択画面を表示せず、すべての sample_name を使用する（通常設定）
INTERACTIVE_FILE_SELECTION = None


def _sample_name_sort_key(name: str) -> tuple[str, int, str]:
    """sample_name の末尾番号を考慮して自然順に並べる。"""
    match = re.search(r"^(.*?)(\d+)$", name)
    if match is None:
        return name, -1, name
    return match.group(1), int(match.group(2)), name


def _parse_number_selection(answer: str) -> list[int]:
    """「1-5, 7, 10-13」形式の入力を番号一覧へ変換する。"""
    numbers: set[int] = set()
    for part in answer.split(","):
        part = part.strip()
        if not part:
            raise ValueError("空の項目があります")
        if "-" not in part:
            numbers.add(int(part))
            continue

        range_parts = [value.strip() for value in part.split("-")]
        if len(range_parts) != 2 or not all(range_parts):
            raise ValueError(f"範囲の形式が不正です: {part}")
        start, end = map(int, range_parts)
        if start > end:
            raise ValueError(f"範囲は小さい番号から指定してください: {part}")
        numbers.update(range(start, end + 1))
    return sorted(numbers)


def select_sample_names(frame: pd.DataFrame, sample: str) -> set[str] | None:
    """対話入力で使用または除外する sample_name を選ぶ。None は全件使用を表す。"""
    sample_names = sorted(
        frame["sample_name"].dropna().astype(str).unique(),
        key=_sample_name_sort_key,
    )
    if not sample_names:
        raise ValueError(f"{sample}: sample_name の候補がありません。")

    print(f"\n[{sample}] 使用するファイルを選択してください。")
    print("  1: すべて使う")
    print("  2: 使用するファイルを選択する")
    print("  3: 取り除くファイルを選択する")
    while True:
        mode = input("選択 (1、2 または 3): ").strip()
        if mode == "1":
            return None
        if mode in {"2", "3"}:
            break
        print("1、2 または 3 を入力してください。")

    print(f"\n[{sample}] sample_name 一覧")
    for number, sample_name in enumerate(sample_names, start=1):
        print(f"  {number}: {sample_name}")

    action = "使用する" if mode == "2" else "取り除く"
    while True:
        answer = input(
            f"{action}番号を入力 (例: 1,2,3 または 1-5,7,10-13): "
        ).strip()
        try:
            numbers = _parse_number_selection(answer)
        except ValueError as exc:
            print(
                "番号を 1,2,3 または 1-5,7,10-13 のように入力してください。"
                f" ({exc})"
            )
            continue

        invalid = [number for number in numbers if not 1 <= number <= len(sample_names)]
        if not numbers:
            print(f"1～{len(sample_names)} の番号を1つ以上指定してください。")
            continue
        if invalid:
            invalid_text = ", ".join(map(str, invalid))
            print(
                f"範囲外の番号があります: {invalid_text} "
                f"（指定できる番号: 1～{len(sample_names)}）"
            )
            continue

        chosen = {sample_names[number - 1] for number in numbers}
        if mode == "2":
            selected = chosen
        else:
            selected = set(sample_names) - chosen
            if not selected:
                print("すべてのファイルを取り除くことはできません。選び直してください。")
                continue

        print(f"[{sample}] {len(selected)} ファイルを使用します。")
        return selected


def make_sample_colors(
    samples: list[str], palette: str, specified_colors: list[str] | None = None
) -> dict[str, object]:
    """サンプルの指定順に色を割り当てる。サンプル名には依存しない。"""
    unique_samples = list(dict.fromkeys(samples))
    if specified_colors is not None:
        if len(specified_colors) != len(unique_samples):
            raise ValueError(
                "--colors の色数はサンプル数と一致させてください "
                f"(samples={len(unique_samples)}, colors={len(specified_colors)})"
            )
        return dict(zip(unique_samples, specified_colors))

    try:
        color_map = plt.get_cmap(palette)
    except ValueError as exc:
        suggestions = difflib.get_close_matches(palette, list(plt.colormaps()), n=3)
        hint = f" 近い候補: {', '.join(suggestions)}" if suggestions else ""
        raise ValueError(
            f"Matplotlibに存在しないカラーパレットです: {palette!r}.{hint} "
            "一覧は --list-palettes で確認できます。"
        ) from exc

    base_colors = getattr(color_map, "colors", None)
    if base_colors:
        colors = [base_colors[i % len(base_colors)] for i in range(len(unique_samples))]
    else:
        denominator = max(len(unique_samples) - 1, 1)
        colors = [color_map(i / denominator) for i in range(len(unique_samples))]
    return dict(zip(unique_samples, colors))


def _find_meta_path(data_root: Path, sample: str) -> Path:
    """Parquet を優先し、なければ CSV を返す。"""
    stem = f"{sample}_10k_Sample_ANAL_meta"
    parquet_path = data_root / f"{stem}.parquet"
    csv_path = data_root / f"{stem}.csv"

    if parquet_path.exists():
        return parquet_path
    if csv_path.exists():
        return csv_path
    raise FileNotFoundError(
        f"{sample} のメタデータが見つかりません: "
        f"{parquet_path} または {csv_path}"
    )


def load_sample_features(
    sample: str,
    data_root: Path,
    distance_values: list[float] | None = None,
    distance_min: float | None = None,
    distance_max: float | None = None,
) -> pd.DataFrame:
    """1サンプルのメタCSVから、ヒストグラム用15特徴量を読み込む。"""
    meta_path = _find_meta_path(data_root, sample)
    if meta_path.suffix == ".parquet":
        frame = pd.read_parquet(meta_path)
    else:
        frame = pd.read_csv(meta_path)

    use_distance_filter = bool(distance_values) or distance_min is not None or distance_max is not None
    required = {"sample", "sample_name", "signal", "baseline", "duration", *WAVE_COLUMNS}
    if use_distance_filter:
        required.add("distance")
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{meta_path.name} に必要な列がありません: {missing}")

    all_sample_names = sorted(
        frame["sample_name"].dropna().astype(str).unique(),
        key=_sample_name_sort_key,
    )
    selected_sample_names = (
        select_sample_names(frame, sample)
        if INTERACTIVE_FILE_SELECTION
        else None
    )
    used_sample_names = (
        all_sample_names
        if selected_sample_names is None
        else sorted(selected_sample_names, key=_sample_name_sort_key)
    )

    duration = pd.to_numeric(frame["duration"], errors="coerce")
    baseline = pd.to_numeric(frame["baseline"], errors="coerce")
    signal = pd.to_numeric(frame["signal"], errors="coerce")
    distance = pd.to_numeric(frame["distance"], errors="coerce") if use_distance_filter else None

    mask = (
        duration.between(DURATION_LIMIT[0] + 1, DURATION_LIMIT[1] - 1)
        & baseline.between(BASELINE_LIMIT[0] + 1, BASELINE_LIMIT[1] - 1)
        & signal.between(SIGNAL_LIMIT[0] + 1e-12, SIGNAL_LIMIT[1] - 1e-12)
    )
    if selected_sample_names is not None:
        mask &= frame["sample_name"].astype(str).isin(selected_sample_names)
    if distance_values:
        selected_distance = np.zeros(len(frame), dtype=bool)
        for value in distance_values:
            selected_distance |= np.isclose(distance, value, rtol=1e-7, atol=1e-9)
        mask &= selected_distance
    if distance_min is not None:
        mask &= distance >= distance_min
    if distance_max is not None:
        mask &= distance <= distance_max
    result = frame.loc[mask, ["sample", "duration", *WAVE_COLUMNS]].copy()
    result["duration"] = duration.loc[mask]
    result["relative_signal"] = signal.loc[mask]
    result["absolute_signal"] = signal.loc[mask] + baseline.loc[mask]

    print(f"[{sample}] {len(frame):,}件中 {len(result):,}件を使用: {meta_path.name}")
    final_result = result[["sample", *FEATURE_COLUMNS]]
    final_result.attrs["used_sample_names"] = used_sample_names
    return final_result


def print_available_distances(samples: list[str], data_root: Path) -> None:
    """各サンプルに含まれるdistanceと件数を表示する。"""
    for sample in samples:
        meta_path = _find_meta_path(data_root, sample)
        if meta_path.suffix == ".parquet":
            distance_frame = pd.read_parquet(meta_path, columns=["distance"])
        else:
            distance_frame = pd.read_csv(meta_path, usecols=["distance"])
        distance = pd.to_numeric(distance_frame["distance"], errors="coerce").dropna()
        counts = distance.value_counts().sort_index()
        print(f"\n[{sample}] distance (件数)")
        for value, count in counts.items():
            print(f"  {value:g} ({count:,})")


def create_histograms(
    samples: list[str],
    data_root: Path,
    output_root: Path,
    bins: str | int = "auto",
    upper_limits: dict[str, float] | None = None,
    lower_limits: dict[str, float] | None = None,
    sample_colors: dict[str, str] | None = None,
    distance_values: list[float] | None = None,
    distance_min: float | None = None,
    distance_max: float | None = None,
) -> Path:
    """指定サンプルを結合し、15特徴量のヒストグラムを保存する。"""
    if not samples:
        raise ValueError("サンプルを1つ以上指定してください。")

    frames = [
        load_sample_features(
            sample,
            data_root,
            distance_values=distance_values,
            distance_min=distance_min,
            distance_max=distance_max,
        )
        for sample in samples
    ]
    combined = pd.concat(frames, ignore_index=True)
    if combined.empty:
        raise RuntimeError("フィルタ後にヒストグラム対象のデータが残りませんでした。")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    distance_tag = ""
    if distance_values:
        values_tag = "-".join(f"{value:g}" for value in distance_values)
        distance_tag += f"_distance-{values_tag}"
    if distance_min is not None:
        distance_tag += f"_distance-min-{distance_min:g}"
    if distance_max is not None:
        distance_tag += f"_distance-max-{distance_max:g}"
    output_dir = output_root / f'{timestamp}_{"-".join(samples)}{distance_tag}'
    output_dir.mkdir(parents=True, exist_ok=False)

    used_file_records = [
        {"sample": sample, "sample_name": sample_name}
        for sample, frame in zip(samples, frames)
        for sample_name in frame.attrs["used_sample_names"]
    ]
    pd.DataFrame(used_file_records).to_csv(
        output_dir / "used_sample_names.csv",
        index=False,
        encoding="utf-8-sig",
    )

    plt.rcParams["font.family"] = "Arial"
    plt.rcParams["axes.unicode_minus"] = False

    data_stat(
        combined,
        FEATURE_COLUMNS,
        bins=bins,
        save_dir=output_dir,
        feature_upper_limits=upper_limits,
        feature_lower_limits=lower_limits,
        sample_colors=sample_colors,
    )
    print(f"ヒストグラムと統計CSVを保存しました: {output_dir.resolve()}")
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RMC特徴量のヒストグラムを、モデル学習なしで作成します。"
    )
    parser.add_argument(
        "samples",
        nargs="*",
        default=DEFAULT_SAMPLES,
        help=f"比較するサンプル名（省略時: {' '.join(DEFAULT_SAMPLES)}）",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=paths.feature_dir("rmc"),
        help="RMCメタデータの保存フォルダ",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="hist_results の出力先",
    )
    parser.add_argument(
        "--bins",
        default="auto",
        help="ビン数（整数）または auto",
    )
    parser.add_argument(
        "--distance",
        type=float,
        nargs="+",
        default=DEFAULT_DISTANCE_VALUES,
        metavar="VALUE",
        help="指定したdistanceだけを使用（省略時はコード内の設定を使用）",
    )
    parser.add_argument(
        "--list-distances",
        action="store_true",
        help="指定サンプルに含まれるdistanceと件数を表示して終了",
    )
    parser.add_argument(
        "--distance-min",
        type=float,
        default=DEFAULT_DISTANCE_MIN,
        help="使用するdistanceの下限（この値を含む）",
    )
    parser.add_argument(
        "--distance-max",
        type=float,
        default=DEFAULT_DISTANCE_MAX,
        help="使用するdistanceの上限（この値を含む）",
    )
    parser.add_argument(
        "--upper-limit",
        action="append",
        default=None,
        metavar="FEATURE=VALUE",
        help=(
            "横軸上限を特徴量ごとに指定（複数回指定可能）。"
            "例: --upper-limit absolute_signal=200 --upper-limit wave_0=1.0"
        ),
    )
    parser.add_argument(
        "--lower-limit",
        action="append",
        default=None,
        metavar="FEATURE=VALUE",
        help=(
            "横軸下限を特徴量ごとに指定（複数回指定可能）。"
            "例: --lower-limit absolute_signal=-50 --lower-limit wave_0=0"
        ),
    )
    parser.add_argument(
        "--palette",
        default=DEFAULT_COLOR_PALETTE,
        help=f"自動配色に使うMatplotlibパレット（省略時: {DEFAULT_COLOR_PALETTE}）",
    )
    parser.add_argument(
        "--list-palettes",
        action="store_true",
        help="この環境で使用可能なMatplotlibパレットを一覧表示して終了",
    )
    parser.add_argument(
        "--colors",
        nargs="+",
        default=None,
        metavar="COLOR",
        help="サンプルの指定順に色を列挙。例: --colors blue orange green red",
    )
    parser.add_argument(
        "--color",
        action="append",
        default=None,
        metavar="SAMPLE=COLOR",
        help=(
            "サンプルの色を指定（複数回指定可能）。"
            '例: --color "LPhe=#0072B2" --color "LIle=orange"'
        ),
    )
    args = parser.parse_args()

    if args.list_palettes:
        print("\n".join(sorted(plt.colormaps(), key=str.lower)))
        parser.exit()

    if (
        args.distance_min is not None
        and args.distance_max is not None
        and args.distance_min > args.distance_max
    ):
        parser.error("--distance-min は --distance-max 以下にしてください。")

    if args.bins != "auto":
        try:
            args.bins = int(args.bins)
        except ValueError as exc:
            parser.error("--bins は正の整数または auto を指定してください。")
        if args.bins <= 0:
            parser.error("--bins は正の整数を指定してください。")

    args.upper_limits = DEFAULT_UPPER_LIMITS.copy()
    for setting in args.upper_limit or []:
        try:
            feature, value_text = setting.split("=", maxsplit=1)
            value = float(value_text)
        except ValueError:
            parser.error(
                f"--upper-limit の形式が不正です: {setting!r}（FEATURE=VALUEで指定）"
            )
        if feature not in FEATURE_COLUMNS:
            parser.error(
                f"横軸上限を指定できない特徴量です: {feature!r}\n"
                f"指定可能: {', '.join(FEATURE_COLUMNS)}"
            )
        args.upper_limits[feature] = value

    args.lower_limits = DEFAULT_LOWER_LIMITS.copy()
    for setting in args.lower_limit or []:
        try:
            feature, value_text = setting.split("=", maxsplit=1)
            value = float(value_text)
        except ValueError:
            parser.error(
                f"--lower-limit の形式が不正です: {setting!r}（FEATURE=VALUEで指定）"
            )
        if feature not in FEATURE_COLUMNS:
            parser.error(
                f"横軸下限を指定できない特徴量です: {feature!r}\n"
                f"指定可能: {', '.join(FEATURE_COLUMNS)}"
            )
        args.lower_limits[feature] = value

    for feature in set(args.lower_limits) & set(args.upper_limits):
        if args.lower_limits[feature] >= args.upper_limits[feature]:
            parser.error(
                f"{feature}: 横軸下限は上限より小さい値を指定してください "
                f"({args.lower_limits[feature]} >= {args.upper_limits[feature]})"
            )

    if args.colors:
        invalid_colors = [color for color in args.colors if not is_color_like(color)]
        if invalid_colors:
            parser.error(f"Matplotlibで使用できない色です: {invalid_colors}")
    try:
        args.sample_colors = make_sample_colors(args.samples, args.palette, args.colors)
    except ValueError as exc:
        parser.error(str(exc))

    # 従来の SAMPLE=COLOR 指定も個別上書き用として利用可能。
    for setting in args.color or []:
        try:
            sample, color = setting.split("=", maxsplit=1)
        except ValueError:
            parser.error(f"--color の形式が不正です: {setting!r}（SAMPLE=COLORで指定）")
        if not sample or not color:
            parser.error(f"--color の形式が不正です: {setting!r}（SAMPLE=COLORで指定）")
        if not is_color_like(color):
            parser.error(f"Matplotlibで使用できない色です: {color!r}")
        args.sample_colors[sample] = color
    return args


def main() -> None:
    args = parse_args()
    if args.list_distances:
        print_available_distances(args.samples, args.data_root)
        return
    create_histograms(
        samples=args.samples,
        data_root=args.data_root,
        output_root=args.output_root,
        bins=args.bins,
        upper_limits=args.upper_limits,
        lower_limits=args.lower_limits,
        sample_colors=args.sample_colors,
        distance_values=args.distance,
        distance_min=args.distance_min,
        distance_max=args.distance_max,
    )


if __name__ == "__main__":
    main()

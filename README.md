# Python_VScode — 実験データ管理・ML特徴量抽出・分類

センサ信号(TDMS)データの転送・管理・可視化と、複数の特徴量抽出手法
(従来型 / tsfresh / Chronos)による XGBoost / LightGBM 分類を行うプロジェクト。

---

## 📁 フォルダ構成

```
Python_VScode/
├── ML/                     # 機械学習パイプライン本体
│   ├── app/                # Streamlit UI(streamlit run ML/app/Home.py)
│   ├── app_tk/             # Tkinter UI(python ML/app_tk/main.py)
│   ├── common/             # 共通処理(パス・データ読込・前処理・クラスタリング等)
│   ├── docs/               # 個別機能の説明(特徴量履歴・統計・最適化・クラスタリング)
│   ├── tests/              # pytest
│   │
│   ├── check_available_tdms_data.py      # 新規TDMSデータの有無を確認
│   ├── extract_features_traditional.py   # 特徴量抽出: 従来型(rmb)
│   ├── extract_features_tsfresh.py       # 特徴量抽出: tsfresh(rmc)
│   ├── extract_features_chronos.py       # 特徴量抽出: Chronos(rmd)
│   ├── stat_features_rmc.py              # 特徴量の分布・統計ヒストグラム
│   ├── stat_features_rmc_by_group.py     # 距離・サンプル別の特徴量比較
│   ├── train_xgboost_traditional.py      # 学習: XGBoost × 従来型
│   ├── train_xgboost_tsfresh.py          # 学習: XGBoost × tsfresh
│   ├── train_xgboost_tsfresh_optimized.py# 学習: XGBoost × tsfresh(ハイパーパラメータ最適化)
│   ├── train_lightgbm_tsfresh.py         # 学習: LightGBM × tsfresh
│   ├── predict_mix_xgboost_traditional.py# 混合予測: 従来型
│   ├── predict_mix_xgboost_tsfresh.py    # 混合予測: tsfresh
│   └── redraw_confusion_matrix.py        # 既存の学習結果から混同行列を再描画
│
├── ML_N/                   # 元TDMSからのsignal検出・特徴量保存の新パイプライン(pyproject管理)
├── Viewer/                 # TDMS波形の可視化・ノイズ推定(python Viewer/view_tdms_signal_tk.py)
├── database/               # 各サーバーのファイル保存状況をSQLiteで管理(python database/app.py)
├── exmanager/              # 実験データの転送・履歴管理(python exmanager/app.py)
└── filemanager/            # フォルダ作成・転送・リネーム・ZIP化(python filemanager/app.py)
```

Tkinter アプリ(exmanager / ML パネル / filemanager / Viewer / database)は、
ルートの `launch_apps.bat`(ランチャー画面 `launcher.py`)から起動できる。
画面の配色・ヘッダーはルートの `ui_theme.py`(共通ダークテーマ、標準ライブラリのみ)で統一している。

各フォルダの詳細は、それぞれの `README.md`(ML は `ML/docs/`)を参照。
ML/ 直下のスクリプトは UI から `importlib` でモジュール名指定で読み込まれるため、
移動・改名する場合は `ML/app/pages/` と `ML/app_tk/tab_*.py` の参照も合わせて変更すること。

---

## 🏷 命名規則

### 特徴量抽出方式の略称対応表

| 略称 | 正式名称 | 説明 |
|---|---|---|
| `rmb` | traditional | 従来型の手動設計特徴量(波形12点特徴量) |
| `rmc` | tsfresh | `tsfresh`ライブラリによる自動特徴量抽出 |
| `rmd` | chronos | Chronosモデルによる埋め込み表現を特徴量として使用 |

### ファイル命名の基本形

```
{目的}_{モデル/対象}_{特徴量手法}.py
```

- `extract_` : 特徴量抽出スクリプト
- `stat_`    : 特徴量の統計・分布確認
- `train_`   : モデル学習スクリプト
- `predict_` : 学習済みモデルによる予測
- `view_`    : 可視化・確認用スクリプト
- モデル名・ライブラリ名は小文字で統一(`xgboost`, `lightgbm` など)

### `mix`について

純粋分子サンプルで学習したモデルを使い、複数分子が混ざった「混合サンプル」の
各イベントを分類して、クラスごとの割合を集計する処理を指す。
結果は `ML/mix_results/` に保存される。

---

## ⚙️ 環境構築

Windows + Anaconda(conda)環境で使用している。
`database/`・`exmanager/`・`filemanager/` は Python 標準ライブラリ(Tkinter)のみで動作する。
ML/ は numpy / pandas / tsfresh / xgboost / lightgbm / streamlit 等が必要。
`ML_N/` は `pip install -e ML_N` でインストールする。

---

## ▶️ 実行手順(ML)

UI から一通り実行できる。

```bash
python ML/app_tk/main.py        # Tkinter 版
streamlit run ML/app/Home.py    # Streamlit 版
```

スクリプトを直接実行する場合:

1. `ML/data/` に入力データを配置(データの入手方法は別途共有)
2. 特徴量抽出: `python ML/extract_features_traditional.py`
3. 学習: `python ML/train_xgboost_traditional.py`
4. 結果は `ML/results/{手法}/{アルゴリズム}/{タイムスタンプ}_...` 以下に自動保存される

---

## 🧪 テスト

各フォルダに同名モジュール(`app.py` 等)があるため、フォルダごとに実行する。

```bash
for d in ML/tests database exmanager filemanager Viewer; do python -m pytest -q $d; done
```

---

## 🌿 ブランチ運用

- `main` : 常に動作する状態を維持する
- 作業は `feature/xxx` ブランチを切って行い、Pull Requestで`main`にマージする
- 例: `feature/add-chronos-extraction`, `fix/xgboost-mix-bug`

---

## 🚫 Git管理対象外(.gitignoreで除外)

- `ML/data/` — 入力データ(容量が大きいため。配布方法は別途相談)
- `ML/models/` — 学習済みモデル
- `ML/results/`, `ML/mix_results/`, `ML/cluster_results/`, `ML/hist_results/` — 実行結果
- `ML_N/data/`, `ML_N/output/`, `ML_N/results/` — ML_N の入出力
- `Viewer/clips_pick/`, `Viewer/output_pdf/` など — 可視化の生成物
- `.tmp/`, `__pycache__/`, `.pytest_cache/`, `.venv/` — 一時ファイル・Python環境関連

---

## 📝 TODO

- [ ] `requirements.txt`(または conda の `environment.yml`)の作成
- [ ] データ配布方法の決定(共有ドライブ / クラウド等)

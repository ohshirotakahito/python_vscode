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
├── Analyzer/               # LabVIEW Total Analysis の Python 版: 1st/2nd Step 解析(PyQt6)
├── ML_N/                   # 元TDMSからのsignal検出・特徴量保存の新パイプライン(pyproject管理)
├── Plotting/               # 配列アセンブリ: BNAL の位置ごとの分布・アセンブリ・q値・修飾率の可視化(python Plotting/app.py)
├── Viewer/                 # TDMS波形の可視化・ノイズ推定(python Viewer/view_tdms_signal_tk.py)
├── database/               # 各サーバーのファイル保存状況をSQLiteで管理・ファイル操作(コピー・リネーム・フォルダ作成)(python database/app.py)
├── exmanager/              # 実験データの転送・履歴管理(python exmanager/app.py)
├── scoring/                # Scoring App: EXSV / AC / Gap Generation スコアの一覧・再計算（AXN の関数を共有）と、Experimental ID で計画→計測→解析をつなぐ台帳(python scoring/app.py)
├── shared/                 # アプリ間で共有する部品(テーマ・サーバー選択・DBの窓口など)
├── launcher.py             # アプリランチャー(launch_apps.bat から起動)
└── conftest.py             # pytest 用: どこから実行しても shared/ を読めるようにする
```

アプリ(exmanager / ML パネル / Viewer / database / Plotting / Analyzer)は、
ルートの `launch_apps.bat`(ランチャー画面 `launcher.py`)から起動できる。Analyzer だけ PyQt6、ほかは Tkinter。

アプリ同士は直接 import せず、共有するものはすべて `shared/` を通す(画面の配色は
`shared/ui_theme.py`(PyQt6 は `shared/qt_theme.py`)、実験ファイルDBの読み書きは `shared/catalog.py`・
`shared/rack_store.py`、ファイル移動の記録は `shared/file_moves.py` など)。
そのため各アプリのフォルダは単独で作り直し・差し替えができる。守るべき取り決めと、
新しいアプリを加える手順は [shared/README.md](shared/README.md) を参照。

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
`database/`・`exmanager/` は Python 標準ライブラリ(Tkinter)のみで動作する。
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

## ▶️ 波形ビューアの操作

ランチャーの Viewer、または次のコマンドで起動する。

```bash
python Viewer/view_tdms_signal_tk.py
```

「波形」タブでは、次の操作で表示と保存範囲を調整できる。

| 操作 | 方法 |
|---|---|
| 縦方向の拡大・縮小 | 「縦に拡大」「縦に縮小」を押す |
| 横方向の拡大・縮小 | 「横に拡大」「横に縮小」を押す。表示の中心を基準に時間幅を変更する |
| 上下左右への移動 | 「つかんで移動」にチェックを入れ、波形上を左ボタンでドラッグする |
| 保存範囲の指定 | 「つかんで移動」のチェックを外し、波形上をドラッグする。または「保存する範囲」の開始・長さを入力して「範囲を選択」を押す |
| 軸の自動調整 | 「自動スケール」を押す。現在の表示区間内で、軸の手動指定を解除する |
| 保存内容の確認 | 範囲を指定して「保存プレビューへ」を押す |

ドラッグ移動と横方向の拡大・縮小では、指定済みの保存範囲と斜線表示を保持する。
選択範囲が画面外に出ても、範囲は保持される。範囲指定をやり直すときは
「つかんで移動」を外して新しい範囲を選び、解除するときは「選択解除」を押す。
上下左右の移動はドラッグで行い、専用の方向ボタンは設けていない。

保存プレビューでは、保存範囲を確認・調整し、CSV・NPY・PNGを保存できる。

---

## 🧪 テスト

各フォルダに同名モジュール(`app.py` 等)があるため、フォルダごとに実行する。

```bash
for d in shared ML/tests database exmanager Viewer Plotting Analyzer/python scoring; do python -m pytest -q $d; done
```

---

## 🌿 ブランチ運用

### アプリごとのバージョン管理

Quantum Workbench本体と、ランチャー内の8アプリは、それぞれ独立した
`major.minor.patch` のバージョン番号を持つ。管理開始版は全アプリ `0.1.0`。
番号・リリース日・変更内容の正本は [`app_versions.json`](app_versions.json)。
ランチャーのカードと各アプリのタイトルバーにバージョンを表示し、
ランチャー下部の「バージョン・更新履歴」から全アプリの履歴を確認できる。
直接スクリプトを起動した場合も同じ番号を表示する。

```powershell
python tools/app_version.py
python tools/app_version.py viewer --bump patch --note "選択範囲の保持を修正"
```

不具合修正は `patch`、機能追加は `minor`、互換性を壊す変更は `major` を更新する。
更新対象のアプリだけを上げ、ソース変更と `app_versions.json` を同じコミットに含める。
共通部品の動作変更は、影響する各アプリの番号と履歴も更新する。
Gitでリリースを残す場合は `viewer-v0.1.0` のようにアプリIDを付けたタグを使い、
GitHub Releaseにも対象アプリの変更内容と確認結果を記載する。
この番号はアプリのリリース番号であり、DB・解析結果・設定ファイルの形式番号とは別に管理する。
実験データや既存設定の形式は、この仕組みの導入では変更しない。

アプリID: `workbench`, `chipdb`, `exmanager`, `database`, `analyzer`, `viewer`, `ml`, `plotting`, `scoring`。

専用アイコンを設定した起動ショートカットは、次のコマンドでリポジトリ直下に作成できる。
作成された `Quantum Workbench.lnk` は任意の場所へ移動できる（リポジトリ本体を移したら再作成）。

```powershell
powershell -ExecutionPolicy Bypass -File tools/create_workbench_shortcut.ps1
```

### Gitでの運用

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
# 開発端末・ユーザー端末

端末ごとの利用アプリ指定とGitHub更新の運用手順は
[Quantum Workbenchの端末設定](docs/workbench_terminals.md)を参照してください。
このPCは開発端末として全アプリを利用でき、ユーザー端末は指定したアプリだけを表示・起動します。

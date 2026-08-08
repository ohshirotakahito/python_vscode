# Python_VScode — ML特徴量抽出・分類パイプライン

センサ信号データに対して、複数の特徴量抽出手法(従来型 / tsfresh / Chronos)を用いて
特徴量を作成し、XGBoost / LightGBMで分類モデルを学習・評価するプロジェクト。

---

## 📁 フォルダ構成

```
PYTHON_VSCODE/
├── filemanager/        # ファイル・フォルダ操作系ユーティリティ
│   ├── cleanup_folders.py
│   ├── data_transfer_common.py
│   ├── foldermaking.py
│   ├── transfer_copy.py
│   └── zip_stocked.py
│
├── ML/                  # 機械学習パイプライン本体
│   ├── common/           # 共通処理(データ読込・前処理など)
│   ├── data/              # 入力データ(Git管理外)
│   ├── models/            # 学習済みモデル(Git管理外)
│   ├── results/           # 実行結果・レポート(Git管理外)
│   │
│   ├── extract_features_traditional.py   # 特徴量抽出(従来型 / 旧rmb)
│   ├── extract_features_tsfresh.py       # 特徴量抽出(tsfresh / 旧rmc)
│   ├── extract_features_chronos.py       # 特徴量抽出(Chronos / 旧rmd)
│   │
│   ├── train_xgboost_traditional.py      # 学習: XGBoost × 従来型特徴量
│   ├── train_xgboost_tsfresh.py          # 学習: XGBoost × tsfresh特徴量
│   ├── train_lightgbm_tsfresh.py         # 学習: LightGBM × tsfresh特徴量
│   ├── train_xgboost_mix_traditional.py  # 学習: XGBoost × 従来型(mix)
│   └── train_xgboost_mix_tsfresh.py      # 学習: XGBoost × tsfresh(mix)
│
└── Viewer/               # 可視化・データ確認用ツール
    ├── clips_pick/
    └── view_tdms_signal.py
```

> **Note:** 上記のファイル名は移行後の命名案です。移行が完了するまでは
> 実際のファイル名(`TOP_XGboost_rmb.py` 等)と異なる場合があります。

---

## 🏷 命名規則

### 特徴量抽出方式の略称対応表

| 現行コード | 正式名称 | 説明 |
|---|---|---|
| `rmb` | traditional | 従来型の手動設計特徴量 |
| `rmc` | tsfresh | `tsfresh`ライブラリによる自動特徴量抽出 |
| `rmd` | chronos | Chronosモデルによる埋め込み表現を特徴量として使用 |

### ファイル命名の基本形

```
{目的}_{モデル/対象}_{特徴量手法}.py
```

- `extract_` : 特徴量抽出スクリプト
- `train_`   : モデル学習スクリプト
- `view_`    : 可視化・確認用スクリプト
- モデル名・ライブラリ名は小文字で統一(`xgboost`, `lightgbm` など)

### `mix`について

*(要記入: mixが何を混合しているかの説明をここに追記してください)*

---

## ⚙️ 環境構築

```bash
# 仮想環境の作成
python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # Mac/Linux

# 依存パッケージのインストール
pip install -r requirements.txt
```

> `requirements.txt` は `pip freeze > requirements.txt` で書き出して
> リポジトリに含めてください(まだ作成していない場合)。

---

## ▶️ 実行手順

1. `ML/data/` に入力データを配置(データの入手方法は別途共有)
2. 特徴量抽出スクリプトを実行
   ```bash
   python ML/extract_features_traditional.py
   ```
3. 学習スクリプトを実行
   ```bash
   python ML/train_xgboost_traditional.py
   ```
4. 結果は `ML/results/{手法}/{アルゴリズム}/{タイムスタンプ}_...` 以下に
   自動保存される(このフォルダはGit管理外)

---

## 🌿 ブランチ運用

- `main` : 常に動作する状態を維持する
- 作業は `feature/xxx` ブランチを切って行い、Pull Requestで`main`にマージする
- 例: `feature/add-chronos-extraction`, `fix/xgboost-mix-bug`

---

## 🚫 Git管理対象外(.gitignoreで除外)

- `ML/data/` — 入力データ(容量が大きいため。配布方法は別途相談)
- `ML/models/` — 学習済みモデル
- `ML/results/` — 実行結果・レポート・zip等の生成物
- `Viewer/clips_pick/` — 抽出クリップ等の生成物
- `__pycache__/`, `*.pyc`, `.venv/` — Python環境関連

---

## 📝 TODO

- [ ] ファイル名の実移行(旧`TOP_`系 → 新命名規則)
- [ ] `requirements.txt` の作成
- [ ] `mix` の意味をこのREADMEに追記
- [ ] データ配布方法の決定(共有ドライブ / クラウド等)

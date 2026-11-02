# Analyzer

LabVIEW「Total Analysis_2020_0820」（MCBJ 信号解析）の Python 移植版です。
LabVIEW と同じ形式の A@@ / B@@ ファイルを書き出し、LabVIEW の出力と照合しながら移植しています。

Python_VScode のアプリの1つとして、ルートの `launch_apps.bat`（ランチャー）の「4 シグナル解析」から起動します。
単独のリポジトリ（https://github.com/ohshirotakahito/Analyzer）は統合前の状態で凍結しており、以後の開発はここで行います。

## ほかのアプリとの取り決め

実験ファイルDB・ファイル移動の記録・測定番号の規則・画面テーマは `shared/` を通して使い、
`database/` や `filemanager/` を直接 import しません（取り決めは [shared/README.md](../shared/README.md)）。

| Analyzer の処理 | 使う shared の部品 |
| --- | --- |
| サンプル一覧・ANAL 進捗の読み込み | `catalog.DEFAULT_DB`（`database/inventory.sqlite3`）、`catalog.root_identity` |
| 解析後の DB 更新 | `rack_store.refresh_sample` |
| T/stocked → T、ANAL/stocked → ANAL、重複の退避 | `t_stock`、`file_moves`（記録は filemanager の履歴画面から元に戻せる） |
| 測定番号 `#NNN` の読み取り | `rack_store.SAMPLE_NUMBER` / `NUMBER`（番号なしの名前の扱いだけ Analyzer 独自） |
| ANAL・BNAL の状態と注意（超過・確認不可・未計数・ANALなし・差異・コピー不足 を含む） | `progress`（実験ファイルDBの ANAL進捗・BNAL進捗と同じ判定） |
| SEQlist・BC の表 | `bnal_lists`（`D:\My_Dropbox\ou&t\machinedb` の SEQlist・BC を直接読み書き。核酸・アミノ酸は中身で判定） |
| BNAL に回すかの振り分け | `sample_plan`（machinedb の `SamplePlan/sample_plan.json`。実験＋サンプルごと） |
| 画面の色・ヘッダー・アイコン | `qt_theme`（PyQt6 版。色は `ui_theme.ANALYZER`） |

これらの取り決めは `python/test_shared_rules.py` で確認しています（`python -m pytest -q Analyzer/python`）。

## 構成

| パス | 内容 |
| --- | --- |
| `python/first_step.py`, `first_step_io.py` | 1st Step（シグナル検出）→ A@@...tdms |
| `python/second_step.py`, `second_step_io.py` | 2nd Step（塩基・アミノ酸の割り当て）→ BNAL@<sample>/B@@...tdms |
| `python/ni_peak.py`, `ni_peak_filters.npz` | NI Peak Detector / Histogram / Skewness and Kurtosis の移植 |
| `python/result_folder.py` | LabVIEW の結果フォルダ（A@<time>@<sample>） |
| `python/server_anal.py`, `server_bnal.py` | サーバー上の T / ANAL / BNAL フォルダの一括処理 |
| `python/gui_app.py` | GUI（PyQt6 + pyqtgraph） |
| `python/validate_*.py` | LabVIEW 出力との照合スクリプト |
| `python/test_shared_rules.py` | shared/ との取り決めのテスト |
| `logs/` | 実行ログ（Git 管理外） |
| `File transfer/`, `Signal Analysis/`, `NPLC/` | 移植元の LabVIEW VI |
| `docs/` | LabVIEW ブロック図の書き出し（HTML + PNG） |

## 使い方

Anaconda の Python（`%USERPROFILE%\anaconda3`）を前提にしています。

```
pip install -r requirements.txt
```

- GUI: ランチャーの「4 シグナル解析」、または `TotalAnalysis_GUI.bat`
- コマンドメニュー: `TotalAnalysis_Launcher.bat`
- 1st Step 単体: `python python/first_step_io.py <raw .tdms またはフォルダ> [-o OUTDIR] [--csv]`
- LabVIEW 出力との照合: `python python/validate_first_step.py <LabVIEW の A@@...tdms>`

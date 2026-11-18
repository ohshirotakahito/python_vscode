# Scoring App（スコア）

EXSV / AC / Gap Generation（Auto GG）のスコアを AXN の外で扱い、Experimental ID（測定フォルダ名）1 つで
計画 → 計測 → データ → 解析結果をつなぐアプリ。AXN 側からの引き継ぎは AXN リポジトリの
`docs/scoring_app_handoff.md`（2026-11-15）。

```powershell
python scoring/app.py                       # ランチャー launch_apps.bat のカード「Scoring · スコア」(8) からも
python -m scoring.rescore --machine AXN2 --measured                      # 計測時の Score.csv を取り込む
python -m scoring.rescore --machine AXN2 --experiment "20240220_0833 ANAN#2Pex1n1_LE"   # 1 実験を再構成
python -m scoring.rescore --machine AXN2 --all --dry-run                 # 一覧にある未計算の実験を計算だけ
python -m scoring.rescore --machine all --all                            # 全装置の未計算の実験（25 件ごとに保存。止めても続きから）
python -m pytest -q scoring
```

AXN リポジトリが必要（`D:\GitHub\AXN`。別の場所なら環境変数 `PYVSCODE_AXN`）。anaconda の Python（3.11）で動く。
Tk・matplotlib のほかに追加のライブラリはいらない。

## 決めたこと（引き継ぎ資料に沿って）

| 項目 | ここでの実装 |
|---|---|
| 計算 | AXN の `ac.score` / `exsv_score.score` / `gg_review.evaluate` / `gg_review.aggregate` をそのまま import（`axn_bridge.py`）。コピーしない |
| Auto GG の 1 回分 | FC・MA・TA・AC・CAL（AMP Check・Gap Check は除く）。欠けた工程は 0 点にせず、ある工程の平均。欠けた工程は `Stages missing` 列に必ず出す |
| 手動 GG の区切り | その実験の EXSV の時刻で区切り、区間ごとに各工程の最後の試行を 1 回分とする。最初の区間 =「最初の EXSV より前の、各工程の最後の試行」。EXSV の後の CAL だけのやり直しも 1 回分（1 工程、4 工程欠け）として見える |
| 保存先 | `D:\Qstatus\database\Scores\`（計測時の Score.csv とは別）。`ac_scores.csv` / `exsv_scores.csv` / `autogg_scores.csv`、台帳 `experiment_scores.csv` |
| 行の属性 | `Source`（計測時 / 再構成）、`Era`（LabVIEW / Python / 不明）、`Criteria`（AXN のコミット + 基準の定数の指紋 + gg の版）、`Files`（読んだ場所）、`Computed at UTC`、`Computed by` |
| 時代 | 1 回ごとにファイルの目印で判定（`rescore.run_era`）。同じ装置でも実験ごと、1 実験の中でも LabVIEW と Python が入れ替わるため、日付の境目は使わない。結果ファイルに `AC Score` / `EXSV Score` の行 → Python。FC〜CAL の 50 ms ログの時刻が 0.05 秒の刻みどおり → LabVIEW、刻みから外れる行が 1 割以上 → Python（LabVIEW は回数 × 0.05、Python は実時刻を書く）。EXSV は同じ実験の直前の Gap Generation の時代を引き継ぐ。2026-10-29 より前は必ず LabVIEW。Gap Generation の 1 回分に両方あれば「混在」、目印が無ければ「不明」。計測時の Score.csv の行は、同じ回の再構成の時代に合わせる（Auto GG は Python 版だけが書くので Python） |
| 実験の探し方 | AXN の `axn/data_locations.py` と同じ順（その PC → Tmp_backup → SQ_backup、それぞれフォルダか zip）。zip は展開せず必要なメンバーだけ読む。順番が同じことはテストで AXN の関数と突き合わせる |
| SQserver | 読むだけ。書かない |
| 計画・転送 | 計画は `shared.ex_info_links.load_plans`（`sample_key` で同一）、転送・ANAL は OUNmain03 が毎時書く `sq_transfers.csv`、そこに無い実験は `machinedb\SEQ\Transfers.jsonl` |

## 画面

- **① 実験**: 1 行 = 1 実験 × 転送先 1 サンプル。計画・転送・ANAL と、EXSV（回数・平均・最高）・AC（最終）・GG（最新の総合点と未実施工程）のまとめ。
  再構成の行があればそれを、なければ計測時の行を使い、「スコアの元」に出す。行を選ぶと下にその実験の全スコア。
- **② スコア**: EXSV / AC / Auto GG の推移（各 run の点、20 run の移動平均、全体の平均。計測時 = 青、再構成 = 橙）と一覧。
  AXN の Score History と同じく 70 / 80 / 90 以上を緑の濃さで色分け。列見出しで並べ替え。
- **③ 再計算**: 装置を選ぶと SQserver の一覧（`D:\Qstatus\database\Upload\<装置> SQ inventory.csv` と archive inventory）の実験を出す。
  選んだ実験、または未計算の全実験を裏のスレッドで再構成し保存する（中止できる。中止までの分は保存）。
  「計測時の Score.csv を取り込む」で machinedb の 3 つの Score.csv を `Source = 計測時` として同じ保存先に入れる。

## ファイル

| ファイル | 内容 |
|---|---|
| `axn_bridge.py` | AXN の場所探し・import・基準の版 `criteria` |
| `machine_data.py` | Machine_N.csv、各工程の data.csv、計測時の Score.csv、SQserver の一覧（Upload）、Experimental ID → 装置・日時 |
| `experiment_files.py` | 実験フォルダ / zip の探索と、R_（結果）・PM_（50 ms）・PML_（0.5 s）の読み込み |
| `rescore.py` | AC・EXSV の再計算、GG の 1 回分の再構成、計測時の取り込み、CLI |
| `score_store.py` | Scores フォルダの CSV（列・鍵・一時ファイル経由の書き込み）、時代の表 |
| `experiment_ledger.py` | Experimental ID × サンプルの台帳（計画・転送・ANAL・スコアのまとめ）と `experiment_scores.csv` |
| `app.py` | Tkinter の画面 |
| `test_scoring.py` | 合成した実験フォルダ / zip でのテスト（ネットワーク不要。AXN が無ければ計算のテストは skip） |

## 確認したこと（2026-11-15、SQserver の実データ）

- `20240220_0833 ANAN#2Pex1n1_LE`（SQ_backup のフォルダ、LabVIEW 時代）: AC#1 = 88.2（引き継ぎ資料の値と一致）、EXSV 46 回、GG 1 回分
- `20240105_0938 ANAN#2Pex1n1_KUHCC-70`（SQ_backup の zip 5.8 GB）: zip のまま AC 2・EXSV 18・GG 1 回分を数秒で
- `20261111_1352 UNAN#2Pex3n2_test`（Tmp_backup、Python 時代）: AC 59.8 / 60.0 / 50.0、EXSV 23.6 / 29.5 / 62.1 / 37.3、
  GG 1 回目 74.9・3 回目 60.3 が計測時の Score.csv と一致（同じ関数・同じログ）

## まだ決まっていないこと

- 範囲はユーザーの決定で「SQserver にある全部」（2026-11-18）。AXN 側には同じ範囲を一覧に出してもらう（引き継ぎ資料「過去の実験の範囲」）。
- 基準は 2026 年の AXN2（Python 時代）で作られている。LabVIEW 時代の点数、特に AC は参考値として時代で分けて見る。
- 保存先の列名は AXN の Score.csv に合わせた英語。台帳 `experiment_scores.csv` だけ他の台帳と同じ日本語の列名。

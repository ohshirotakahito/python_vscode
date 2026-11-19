# 引き継ぎ：Chip Atlas の台帳統合・チップIDの修正・同期の競合コピー（2026-11-18）

Claude Code から Codex への引き継ぎ。ユーザーとのやり取りは日本語で行う。
前回の引き継ぎ（[HANDOFF_scoring_app.md](HANDOFF_scoring_app.md)）の後、2026-11-18 に行った作業のまとめ。

## Codexの継続作業（2026-11-18）

- 引き継ぎ後の確認: 同期競合コピー0件。テスト339 passed・2 skipped、リリース関連13件成功。未コミット変更は保持し、コミット・pushはしていない。
- ユーザー確認により、CFDの1組目 `CFD-25060894` → `CFD25060894` を同一チップとして統合。操作履歴24件を保持、計測記録3ファイル9行と保存スコア2行のDevice Codeのみ修正。ウェハー・ロットは推定せず保持。対応表は46項目完了・revision 44へ進んだ。バックアップ等の詳細は `docs/chip_id_cleanup.md` の末尾。
- CFD全体の命名規則は引き続き未確定。残りも1組ずつ材料を示して判断を受ける。既存の `record_ids.plan` はCFDのハイフン変更からウェハー・ロットも算出するため、そのまま適用しないこと。今回の作業用スクリプトは `.tmp/merge_cfd_25060894.py`（Git管理外）。
- 2組目 `CFD-25060918` → `CFD25060918` も同一とユーザーが確認して統合済み。操作履歴15件、計測記録・スコアの修正対象0件。対応表47項目完了・revision 49。次にユーザーはFC/MA/TA/CALなど異なる操作間の相関可視化について提案を求めている（実装はまだ）。
- ユーザーが「複数チップの全履歴を上下表示＋1点1チップの代表値散布図」から始める方針を承認。`chipdb/operation_comparison.py` と `comparison_view.py` を追加し、Appに「操作間比較」タブを接続。FC/MA/TA/CAL、Info1〜6、期間指定、任意のAXN有効範囲、中央値・平均・記録順の最初/最後、Pearson/Spearman（3チップ以上）、点と履歴明細の連動、CSV出力に対応。時間グラフはInfo3の実施時刻を優先し、実験開始日時を代用した記録は明示。計測同士の自動ペア作成はまだ行わない。Chip Atlasは0.13.0へ更新。検証はChip Atlas 70 passed・1 skipped、リリース関連13件成功。共有データは変更せず、コミット・pushもしていない。

## まず知っておくこと

追記（0.13.3）: 操作間比較にX/Y代表値それぞれの下限・上限を追加。空欄は制限なし、境界を含む。代表値算出後に範囲外の点を除外し、散布図・代表値一覧・明細・相関・CSVを同期。解除で復帰し、元データ・チップ選択は保持。上下限の逆転・NaN・無限値・非数値はエラー表示しCSV出力を止める。

追記: ユーザーのレイアウト変更指示により「操作間比較」は代表値散布図のみへ変更（0.13.1）。プロット領域は正方形、代表値一覧・履歴明細は右側。上下の全履歴グラフ・表示モード切り替えは削除。代表値は引き続き全履歴（または指定期間）から計算する。

追記（0.13.2）: 点の直径2〜30 px（初期6 px）と件数条件を追加。X/Y両方・Xのみ・Yのみ・いずれかに対し `>` / `>=` で絞り込み（初期条件 >5、チェックで有効化）。左一覧の「件数条件に合うチップを選択」で表示中の候補を全履歴件数から選択。散布図・相関係数・CSVは期間・有効範囲を適用した数値件数で絞り込んだ点に対応。

- **今日の変更はすべて未コミット。** `git status` で見える 30 余りのファイルが今日の分（下の「変更したファイル」）。
  コミット・push はユーザーが頼んだときだけ。push は下の「push の注意」の方法で。
- **ルートの [AGENTS.md](AGENTS.md) の決まり:** アプリを変えたら `tools/app_version.py <アプリ> --bump patch|minor|major --note "日本語の変更内容"`
  で版を上げ、`python -m unittest shared.test_app_release shared.test_app_update` を流す。今日の版は chipdb 0.12.2、scoring 0.4.0。
- **共有フォルダ `D:\Qstatus` に書くデータの決まり（ユーザーの決定）:**
  - サーバー（SQserver・解析サーバー）の実験データは書き換えない。読むだけ。
  - 計測記録（`machinedb\<工程>\<装置> <工程> data.csv`）を書き換える前に、その装置が計測していないこと
    （直近 1 時間に他から書き込みがないこと）を確かめる。LabVIEW と AXN が計測のたびに書き足すファイル。
  - 書き換える前に元のファイルを必ず保存し、何を変えたかを残す。
- **ユーザーの進め方:** 判断が要るものは 1 つずつ材料（日時・装置・使用者・GAP など）を出して選んでもらう。推測で決めない。

## 1. Chip Atlas（`chipdb/`、0.6.0 → 0.12.2）

| 変更 | ファイル |
|---|---|
| 「チップ台帳」と「全チップ比較」を 1 つの表に（1 チップ 1 行、管理情報＋履歴CSVの集計）。「表示する列」で 管理情報 / 測定の集計 / すべて。分布・装置・担当者と読み込みエラーは同じ画面のタブ | `chipdb/ledger_view.py`（新）。`registry_view.py` と `summary_view.AllView` は削除 |
| 履歴CSVを変わったファイルだけ読み直す索引（更新日時とサイズ。PC ごとの控え `%LOCALAPPDATA%\QuantumWorkbench\chip_atlas_cache\`。集計の決まり summary.py・search.py が変わると全件読み直し。FORMAT 2 で操作ごとの回数 kinds を追加） | `chipdb/library.py`（新） |
| 解析・トレンド: 左にチップ一覧（ID 絞り込み・数字順・回数の列・見出しで並べ替え）。Ctrl/Shift で最大 8 チップを重ねて比較。選んだ行をもう一度クリックで外す。横軸 回数 / 日時、縦軸は切りのよい目盛り | `chipdb/trend_view.py`・`chipdb/axis.py`（新） |
| 台帳に FC・MA・TA・CAL の最新値、製作者（origin）・主体（organization。新しい項目）、ID修正の列。列幅を中身に合わせて詰める。空欄は並べ替えで常に最後 | `ledger_view.py`、`registry.py`（FIELDS に organization） |
| 上部の重複したボタン列を削除（タブだけで切り替え） | `app.py` |

- `organization` は AXN の共有台帳 `machinedb\PLAN\chip_info.json` に保存される。AXN は台帳をまるごと複製して保存するので、
  知らない項目も消えないことを AXN のコード（`axn/chip_registry.py` の `Store.save`）で確認済み。
- 実データ（3501 チップ）で、索引は初回 約 3〜4 秒、2 回目以降 約 0.2 秒。

## 2. チップIDの修正（[docs/chip_id_cleanup.md](docs/chip_id_cleanup.md)）

決まり `SC2桁-4桁-2桁` に合わない SC の ID を直した。手順・判断・記録はすべてこの文書にある。

- **対応表:** `D:\Qstatus\machinedb\PLAN\chip_aliases.json`（`shared/chip_aliases.py`）。旧名 → 新名、状態、段階ごとの日時、
  `accepted`（決まりの例外としてこの名前のまま使うID）。保存は鍵・版の確認・バックアップつき、同期で拒否されたら再試行。
  サーバーの実験データには旧名が残り続けるので、対応表は消さない。
- **結果:** 45 項目すべて完了（revision 39）。名前の修正 38、統合 7（`SC25-2115-9`・`SC68-3914-16_`・`SC77-4219--04`・
  `SC080-4307-06`・`SC080-4307-53`・`SC914418-03`・`SC89-1142`）。決まりの例外 4 個
  （`SC25-2115-32-2`、`SC25-2118-48-3`、`SC25-2118-48-4`、`SC32-2416-30-2`。別のチップとユーザーが判断）。
  今、決まりに合わない SC はこの 4 個だけ。
- **書き換えた場所:** 履歴ファイル `ChipDB\DB\<ID>.csv`（1 行目のIDと名前）、チップ台帳（旧名を `aliases` に残す）、
  計測記録の Device Code・Device Wafer・Device Lot（完全一致のセルだけ。`chipdb/record_ids.py`）。
  バックアップと直したセルの一覧は `D:\Qstatus\ChipDB\WorkbenchBackups\cleanup_*`・`chipid_records_*\journal.json`。
  元に戻すときは `chipdb.record_ids.restore(<フォルダ>)`。
- **道具:** `tools/chip_id_rename.py "理由" 旧=新 [--merge]` が段階 2〜5 をまとめて行う（対応表に登録 → 履歴・台帳 →
  計測記録 → 確認 → 段階を記録）。統合の前には両方の履歴の日時・装置を突き合わせ、同じチップかをユーザーに確かめる。
- 「重複・IDを整理…」（`chipdb/cleanup.py`）は共有の対応表も読む（`shared_aliases`）。`sc49-3310-51` の正しい表記は `SC49-3310-51`
  に変わった（以前の「小文字のまま」の指定はユーザーが変更）。
- **Scoring** は Device Code を対応表で新名に置き換える（`scoring/rescore.py` の `current_device`）。保存済みのスコアも置き換え済み。
- **残り:**
  - **AXN 側への依頼**（文書の最後の節「AXN 側への依頼」）。ユーザーが AXN の担当に渡す。General PSET で旧名が入力されたら新名にする、など。
  - **CFD の 41 個**（5 種類の書き方）。担当者に決まりを確認してから。それまで触らない。
  - ほかの書き方の揺れ（`ou`／`OU`、`T-MCBJ-` の桁数、`MCBJ-C001`／`mcbj-c001` の大文字小文字）は未着手。
  - Chip Atlas の「ID修正」列は、例外（accepted）のチップには何も出さない。出すなら `ledger_view.id_fix_text`。

## 3. 同期の競合コピー（[docs/qstatus_conflicts.md](docs/qstatus_conflicts.md)）

- `shared/sync_conflicts.py`（判断と統合）、`tools/qstatus_conflicts.py`（実行・タスク登録）。
- 2026-11-18 に 132 個を片付け、残り 0。本体に行を戻す必要があったものはなかった（どれも本体に全行があるか、
  TA data.csv の古い 1 行の繰り返しだけ）。片付けたものは `D:\Qstatus\database\ConflictBackups\<日時>\`。
- **毎晩の自動実行:** タスク スケジューラ「Qstatus Conflict Merge」、毎日 2:20、StartWhenAvailable（時刻を逃したら起動後すぐ）。
  このPC（DESKTOP-BGP3R6D）のほかのタスクは「Qstatus Ledger Publish」（毎時 :05）と「AXN Upload Inventory」（毎時 :35）。
- 結果は `D:\Qstatus\database\ConflictBackups\latest.json` と `database/logs/conflicts.log`（Git 管理外）。
  **次にやること:** 翌朝以降、latest.json の要確認（review）を見てユーザーに報告する。
- 自動で戻すのは、競合コピーにしかない行が少ないとき（200 行以下かつ本体の行数以下）だけ。前後の行と同じ位置に差し込む。
  見出しが違う・本体がない・差が大きい・30 分以内に書かれた本体は、理由を出すだけで触らない。
- 保管場所（ConflictBackups）は消していない。古いものを消す仕組みはまだない（必要ならユーザーに確認して）。
- 原因の一つ: 大きな共有ファイルを短い間隔で何度も書き直すと競合コピーができる（Scores を 25 件ごとに書き直していた）。
  Scoring の一括再計算の保存間隔は 250 件にした。共有フォルダの大きなファイルを頻繁に書き直さないこと。

## 4. Scoring（`scoring/`、0.3.0 → 0.4.0）

- 時代（LabVIEW / Python）は 1 回ごとにファイルの目印で判定（前回までに実装）。全 2425 実験の再計算は済み
  （AC 3658・EXSV 46137・Gap Generation 2879 行。計測時の Score.csv と重なる回はすべて一致）。
- 今日: Device Code を対応表で新名に。保存間隔を 250 件に。

## 変更したファイル（未コミット）

- Chip Atlas: `chipdb/app.py`・`bulk_view.py`・`cleanup.py`・`registry.py`・`summary_view.py`・`README.md`、
  新規 `ledger_view.py`・`library.py`・`trend_view.py`・`axis.py`・`record_ids.py`、テスト `test_*.py`、削除 `registry_view.py`
- 共有部品: 新規 `shared/chip_aliases.py`・`shared/sync_conflicts.py`（とテスト）、`shared/README.md`
- Scoring: `scoring/rescore.py`・`score_store.py`・`test_scoring.py`・`README.md`
- 道具・文書: `tools/qstatus_conflicts.py`・`tools/chip_id_rename.py`、`docs/chip_id_cleanup.md`・`docs/qstatus_conflicts.md`、
  `app_versions.json`、この文書

テスト（2026-11-18）: `shared` 93、`chipdb` 62（＋skip 2）、`scoring` 18、`database` 166 すべて通過。

```powershell
& "$env:USERPROFILE\anaconda3\python.exe" -m pytest -q shared chipdb scoring database
& "$env:USERPROFILE\anaconda3\python.exe" -m unittest shared.test_app_release shared.test_app_update
```

## 落とし穴

- **push の注意:** この PC の Git Credential Manager は `quantumsequencer`（AXN 用）でログインしていて、
  `ohshirotakahito/python_vscode` には書けない（403）。gh には両方のアカウントでログイン済みなので、1 回だけ持ち主のログインで push する:
  `GH_TOKEN="$(gh auth token --user ohshirotakahito)" git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin main`
  （Git の設定は変えない）。`quantumsequencer` を共同編集者に加えるかはユーザーが決める。
- Chip Atlas の GUI テストは、Tk の起動に失敗するとたまに skip になる（`Can't find a usable init.tcl`）。もう一度流せば通る。
- 共有フォルダに続けて保存すると、同期が一瞬ファイルを掴んで `PermissionError`（WinError 5）になることがある。
  `shared/chip_aliases.replace_with_retry`・`sync_conflicts.replace_with_retry` は再試行する。
- 保管場所の名前が長すぎると Windows のパスの上限（260 文字）を超える。`sync_conflicts` は保管するときに名前を短くする。
- 計測記録には見出しのないもの（BL・RMS・SR の `Data.csv`）、列が足された新しい形のもの（ChipDB の工程別 CSV）、
  LabVIEW が古い行を書き足し続ける `TA data.csv`（既知の不具合）がある。
- 文字コード: machinedb と ChipDB は cp932、対応表・Scores は UTF-8。計測記録の書き換えはバイトのまま扱い、行末（CRLF）も保つ。
- Claude Code の Bash ツールからは SQserver の UNC パスに届かないことがあった（PowerShell からは届く）。Codex の環境は不明。

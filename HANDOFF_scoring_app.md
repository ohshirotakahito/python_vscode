# 引き継ぎ：Scoring App（`scoring/`）

2026-11-15 作成。Claude Code から Codex への引き継ぎ。ユーザーとのやり取りは日本語で行う。
コミット・push・PR はユーザーの指示があってから行う（ユーザーの決まり）。

## 状態

- main の d7d9c3d に Scoring App の初版が入っている（push 済み、未コミットの変更なし。`database/analysis_server_all.csv` は毎時の台帳出力が作る生成物で Git 管理外）。
- 元になった仕様は AXN リポジトリ（`D:\GitHub\AXN`、96ca3d9 時点）の `docs/scoring_app_handoff.md`。**作業前に `git -C D:\GitHub\AXN pull --ff-only origin main` して、この文書に追記がないか見る。** AXN 側の担当者はコミットとこの文書で連絡してくる。
- 仕様・画面・ファイル構成・確認済みの値は [scoring/README.md](scoring/README.md) にまとめてある。この文書は「決めたこと」「まだのこと」「落とし穴」だけ。

## 使い方の最短

```powershell
& "$env:USERPROFILE\anaconda3\python.exe" -m pytest -q scoring            # 14 件（AXN が無ければ計算のテストは skip）
& "$env:USERPROFILE\anaconda3\python.exe" scoring\app.py                   # 画面（ランチャーのカード 8 からも）
& "$env:USERPROFILE\anaconda3\python.exe" -m scoring.rescore --machine AXN2 --measured          # 計測時 Score.csv の取り込み
& "$env:USERPROFILE\anaconda3\python.exe" -m scoring.rescore --machine AXN2 --experiment "<Experimental ID>" --dry-run
```

anaconda の Python 3.11 で動く（AXN のコードは `X | None` を使うので 3.10 以上が必要。`.venv` は不要）。
Bash から `python` と打つと Windows Store のスタブで何も起きないので、anaconda のフルパスを使う。

## ユーザーが決めたこと（2026-11-15）

- Scoring App は Python_VScode 内に作る（AXN2 にある古い python_vscode は使わない）。
- データの置き場は `D:\Qstatus\database`。Scoring App は `D:\Qstatus\database\Scores\` を使う。
- Auto GG の 1 回分 = FC・MA・TA・AC・CAL（AMP Check・Gap Check は除く）。欠けた工程は 0 点にせず「ある工程の平均」。どの工程が欠けたかを必ず見せる（`Stages missing` 列）。
- 計算は AXN と同じ関数を共有する（コピー禁止）。後から計算したスコアは計測時の Score.csv とは別に保存し、各行に計算元・時代・基準の版を持たせる。
- SQserver は読むだけ。変更は必ずユーザーに確認する。

## 自分で決めたこと（ユーザーに報告済み。変えるなら README と AXN 側へ）

- 手動 GG の「1 回分」の区切り：その実験の EXSV の時刻で区切り、区間ごとに各工程の最後の試行を 1 回分とする。最初の区間 = 引き継ぎ資料の候補「最初の EXSV より前の、各工程の最後の試行」。EXSV の後に CAL だけやり直した場合も 1 回分（CAL のみ、4 工程欠け）として出る。
- 時代（LabVIEW / Python）：2026-11-18 に日付表（era.json）をやめ、1 回ごとにファイルの目印で判定する方式にした（同じ装置でも実験ごと、1 実験の中でも入れ替わるため）。目印は scoring/README.md の「時代」。AXN の Score.csv にある 2026-01〜02 の AXN2 の行は LabVIEW 時代の計測を AXN の `score-backfill` で採点したもの。
- 基準の版 `Criteria` = `axn <コミット>; ac <定数の指紋>; exsv <定数の指紋>; gg <gg_review.VERSION>`（`axn_bridge.Modules.criteria`）。AXN の定数が変わると指紋が変わるので、古い基準の行を見分けられる。
- 保存先 CSV の列名は AXN の Score.csv と同じ英語名 + 由来の 7 列（`Machine, Source, Era, Criteria, Files, Computed at UTC, Computed by`）。台帳 `experiment_scores.csv` だけ他の台帳（`sq_transfers.csv` など）に合わせて日本語の列名。
- 実験フォルダの探し方は AXN の `axn/data_locations.py` `candidates` と同じ順（その PC → Tmp_backup → SQ_backup、それぞれフォルダ → zip）。共有せず同じ順を自前で持ち、`test_candidates_follow_the_same_order_as_axn` が AXN の関数と突き合わせる。AXN 側で順番が変わればこのテストが落ちる。

## まだ決まっていないこと（ユーザー・AXN 側と）

1. **過去の実験の範囲。** 2026-11-18 にユーザーが「SQserver にある全部」と決定。AXN 側に同じ範囲を一覧に出してもらう。
2. （解決）時代は 1 回ごとに判定するので、切替日は不要になった。
3. （解決）同上。

## 今の保存先の中身（D:\Qstatus\database\Scores、2026-11-15 に書いた）

- 計測時の取り込み：AXN2（AC 87・EXSV 1440・AutoGG 10）、AXN3（各 2）。
- 再構成：確認に使った 3 実験だけ（`20240220_0833 ANAN#2Pex1n1_LE`、`20240105_0938 ANAN#2Pex1n1_KUHCC-70`、`20261111_1352 UNAN#2Pex3n2_test`）。
- `experiment_scores.csv`：2426 行（SQserver の一覧にある全実験 × 転送先）。
- 消してよいもの。`score_store.remove()` か、ファイルごと消せば画面は空から始まる。

## 落とし穴

- **Claude Code の Bash ツールのサンドボックスでは、Python から UNC パスを `\\SQserver\...` で開けず（WinError 3）、`//SQserver/...` の形だけ通る。** `pathlib` は `\\` に直すので実質 SQserver に届かない。実データの確認は PowerShell ツール（サンドボックス外）で行った。Codex の環境で同じかは不明。ユーザーが手で動かす分には問題ない。
- machinedb の CSV は cp932（LabVIEW が書く）。Score.csv は ASCII。Scores フォルダと台帳は UTF-8 BOM 付き。
- `machinedb\TA\<m> TA data.csv` は TA の結果ではない（LabVIEW が古い FC 行を書く）。TA は実験フォルダの `R_TA` から読む。
- AXN の `axn/ac.py` などは `axn.devices` を import するが、PySide6 や NI ドライバなしで import できる（anaconda で確認済み）。`axn.data_locations` も同様。
- R_EXSV の列名は末尾に空白があるもの（`EXSV Set Distance [nm] `）がある。AXN の `history_row` が strip するのでそのまま渡す。
- `Experiment` は zip の namelist をメンバー名で索引し、`R_`/`PM_`/`PML_` だけ読む。`D_` 波形は読まない（1 本約 50 MB）。
- `scoring/app.py` の再計算は裏スレッドで動き、Tk には queue 経由で返す。中止すると、そこまでの分を保存する。
- `database/test_*` などほかのフォルダのテストは触っていない。ルート README のテスト手順に `scoring` を足してある。

## 次にやるとよいこと（優先順）

1. ユーザーに過去の範囲を聞き、決まったら `python -m scoring.rescore --machine AXN2 --all` 相当を範囲付きで動かせるよう、`--since YYYYMMDD` のような絞り込みを足す（今は一覧の全部か、指定した実験だけ）。
2. AXN 側が「同じ範囲を一覧に出す」ための連絡：Experimental ID の一覧か範囲の決め方を AXN の `docs/scoring_app_handoff.md` に追記してもらう。
3. ① 実験タブの Python 時代の行で EXSV 回数が空のものは、AXN の Score.csv にまだ無い実験（AXN2 PC が書いていない）。③ で再構成すれば埋まる。
4. 計測時と再構成の両方がある run はスコアが一致するはず（同じ関数・同じログ）。差が出たら基準の版（`Criteria`）か era を疑う。差分を一覧する小さな確認コマンドがあると便利。

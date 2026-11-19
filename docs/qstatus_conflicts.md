# 共有フォルダの同期の競合コピー（自動の片付け・統合）

2026-11-18 開始。共有フォルダ `D:\Qstatus` では、2 台の PC が同じファイルを同時に変えると、同期の仕組みが片方を
`<名前>_<PC名>_<月-日-時分秒-年>_Conflict.csv` として残す（ほかに `CaseConflict`、`conflict_current`、
「<人> の競合コピー <日付>」の形もある）。計測記録・チップ履歴は行を書き足すだけのファイルなので、
競合コピーにしかない行を本体に戻せば、どちらの書き込みも失わない。

## 仕組み

- 判断と統合: `shared/sync_conflicts.py`。実行: `tools/qstatus_conflicts.py`。
- 毎晩 2:20 にタスク スケジューラの「Qstatus Conflict Merge」が `--apply` で動く（このPC、DESKTOP-BGP3R6D）。
  その時刻に PC が止まっていたら、起動後すぐに動く（StartWhenAvailable）。
- 結果: `D:\Qstatus\database\ConflictBackups\latest.json`（全項目と、触らなかったものの理由）と、
  このリポジトリの `database/logs/conflicts.log`。
- 片付けた競合コピーと、統合する前の本体は `D:\Qstatus\database\ConflictBackups\<日時>\`（copies・originals、journal.json）に残す。

```powershell
& "$env:USERPROFILE\anaconda3\python.exe" tools\qstatus_conflicts.py                 # 確かめるだけ
& "$env:USERPROFILE\anaconda3\python.exe" tools\qstatus_conflicts.py --apply         # 片付け・統合
& "$env:USERPROFILE\anaconda3\python.exe" tools\qstatus_conflicts.py --register      # 毎晩のタスクを登録し直す
& "$env:USERPROFILE\anaconda3\python.exe" tools\qstatus_conflicts.py --unregister    # 止める
```

## 自動で行うこと

| 場合 | すること |
|---|---|
| 競合コピーの行がすべて本体にある（チップIDの対応表で旧名を新名にそろえて比べる） | 競合コピーを保管場所へ |
| 本体にない行が、本体にある行の繰り返しだけ（LabVIEW が TA data.csv に古い FC の行を書き足し続ける既知の不具合など） | 競合コピーを保管場所へ |
| 見出しが違う（列が足された新しい形の本体など）が、先頭から共通の 10 列以上で見て、競合コピーの行がすべて本体にある | 競合コピーを保管場所へ |
| 競合コピーにしかない行が少しある（200 行以下、かつ本体の行数以下） | その行を、競合コピーでの前後の行と同じ位置に本体へ差し込み、競合コピーを保管場所へ |
| 集計の出力（`database/Scores`）。作り直せる | 本体を正として競合コピーを保管場所へ |

## 自動では触らないこと（要確認として latest.json とログに出す）

- 本体が 30 分以内に書き込まれている（計測中かもしれない。次の回に回す）
- 本体がない、名前から本体を割り出せない
- 見出しが違い、本体にない行がある
- 競合コピーにしかない行が多い（本体が作り直された可能性）
- 行を書き足すだけのファイルと決められないもの（JSON、`machinedb\SEQ` などの設定の CSV）
- 保管場所（`backups`・`WorkbenchBackups`・`ConflictBackups`）の中は見ない

## 記録

- 2026-11-18: 132 個を片付け（統合で行を戻したものは 0。どれも本体に全行があるか、繰り返しの行だけ）。残り 0。
  保管場所 `ConflictBackups\20261118_204410`・`204612`・`204630`。毎晩のタスクを登録し、手動で 1 回動かして結果 0 を確認。

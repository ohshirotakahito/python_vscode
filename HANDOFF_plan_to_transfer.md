# 引き継ぎ：計測の計画を転送・解析まで通す（ブランチ `plan-to-transfer`）

2026-11-13 作成。Claude Code から Codex への引き継ぎ。ユーザーとのやり取りは日本語で行う。

## 背景

計測（AXN リポジトリ、`D:\GitHub\AXN`）→ 実験データ整理（`exmanager/`）→ 実験ファイルDB（`database/`）→ シグナル解析（`Analyzer/`）の流れを整理し、ボトルネックを ①〜⑧ に分けた（下の「課題一覧」）。ユーザーの方針は「すべての課題に順に取り組む。①から」。

①：計測時に AXN の Ex Info・General PSET で決めた送り先（実験・サンプル・解析サーバー）が、exmanager の転送で途切れている。下流は (実験名, サンプル名) の文字列だけで全サーバーを探している。

- 測定フォルダ名 = G PSET の Experimental ID = SQserver 上の元実験名。これは一意なので、測定の特定には使ってよい。
- 計画：`D:\Qstatus\machinedb\PLAN\MeasurementLinks.jsonl`（AXN の `axn/ex_info.py` の `link_writes` が書く）と `PLAN\ex_info.json`（Project / Sample / locations。内部キーは UUID）。
- 送り先の決め方：リンクの `location_key` → リンクの `destination` 文字列 → そのサンプルのメインの location（`is_main`、なければ唯一の location）。AXN の `axn/upload_check.py` の `read_plans` と同じ規則にしてある。変えるときは両方そろえる。

## このブランチでやったこと（未コミット）

`git status` で見える変更がすべて。コミット・push・PR はユーザーの指示があってから行う（ユーザーの決まり）。

| ファイル | 内容 |
|---|---|
| `shared/ex_info_links.py`（新規） | `load_plans()`：Experimental ID → `Planned`（読むだけ）。`Transfer` と `transfer_record()`・`append_transfer()`・`load_transfers()`：`machinedb\SEQ\Transfers.jsonl`（追記だけ、schema_version 1） |
| `shared/test_ex_info_links.py`（新規） | 上のテスト 12 件 |
| `exmanager/core.py` | `plan_text` / `plan_groups` / `plan_mismatches` / `plan_state` / `missing_destinations`。`record()` が 17 列 CSV の後に Transfers.jsonl へ 1 行書く（失敗は `plan.ledger_error` に入れ、`execute_batch` がログに出す。コピーと 17 列の履歴は有効のまま） |
| `exmanager/app.py` | コピー元一覧に「計画（Ex Info の送り先）」列、ボタン「計画どおりにセットへ追加」（`add_planned_sets`）、②で計画と違う送り先を選んだときの確認、登録表に「計画との照合」列。セットの `context['plans']` に `Planned` を持たせる |
| `exmanager/test_plan_links.py`（新規）, `exmanager/test_core.py` | 判定のテスト。`test_core` は `setUpModule` で記録の書き先を一時フォルダへ向ける |
| `database/transfer_ledger.py` | `build_rows(..., records=())`：記録のある転送は記録した共有だけで ANAL を判定する。見つからず別の共有にあれば注意「記録と別の共有」を付ける。末尾に 3 列（転送先の共有（記録）・計画どおり・Ex Info サンプルキー）を追加 |
| `database/publish.py` | `export_transfers` が `ex_info_links.load_transfers()` を渡す（読めなければ警告を出し、記録なしで続ける） |
| `database/test_transfer_ledger.py` | 追加テスト 3 件 |
| `conftest.py` | autouse fixture で `ex_info_links.TRANSFERS` を tmp に向ける（テストが本番の D:\Qstatus に書かないため） |
| README（`exmanager/`・`database/`・`shared/`） | 仕様を追記 |

ユーザーが決めたこと：
- 計画の送り先フォルダがないときは、一覧で確認してから作る（`core.create_destinations`、ツリーは `shared/folder_layout.py`）。
- 転送の記録は `machinedb\SEQ\Transfers.jsonl`。書くのは転送担当 PC だけ。17 列の Uploader CSV は変えない。

自分で決めたこと（ユーザーに報告済み）：
- 計画の送り先の共有が exmanager の解析ルート一覧（`.local/AnalysisRoots.csv`）にないものは、自動では入れない。Ex Info には `\\QDserver\backup` や `\\Rackstation\archive` も location として入っているため。

## 確認の状況

- `C:\Users\ohshi\anaconda3\python.exe -m pytest -q database shared exmanager` の結果は 249 passed、1 failed。
  - 失敗は `exmanager/test_core.py::CopyTest::test_raw_files_are_not_hashed` で、**変更前から落ちている**（`git stash` で確認済み）。原因は未調査。
- exmanager を起動し、画面が組み上がること、本番の計画 1 件を読むことを確認した。
- **未確認：計画つきの測定を実際に転送する通しの確認。** 本番の MeasurementLinks は 1 件（`20261111_1351 UNAN#2Pex3n1_LAB`、TIT/LAB）だけで、それも保存先が未定。保存先まで決めた測定が出たら、ユーザーと一緒に最初の 1 件を確かめる。
- `Transfers.jsonl` は、最初の本番の転送で作られる。

## 次にやること

追記（Ex Info閲覧・編集、Codex）：

- ユーザーから「実験ファイルDBでもEx Infoを見て編集したい」と依頼。`database/ex_info_view.py` を追加し、DBの先頭に「Ex Info」タブを設置。
- AXNと同じ `PLAN/ex_info.json` を読む。研究・サンプル・Owner・両方の説明・Medium・登録した保存場所・表示設定を一覧にし、Ownerと文字列で絞り込む。サンプルがない研究も表示。
- 初期実装の編集対象は研究Owner・研究の説明・サンプルの説明。名前や保存場所、UUIDは変更しない。Ownerは研究内の全サンプルに共通。保存後はPlanのOwnerも再読み込みする。
- `shared/ex_info_store.py` を追加。AXNと同じ `ex_info.lock`・revision・backupsの方式で保存。最新台帳へ変更した項目だけをマージし、同じ項目の変更・同期競合コピーでは保存を止める。未知の項目・他の変更・キー・保存場所を保持。同期前の別PC同時編集を完全に排他するものではない。
- Owner表示の部品 `shared/ex_info_owners.py` とPlan側のOwner列・フィルターも実装済み。登録された共有・実験・サンプルの場所で対応付け、名前だけでは推測しない。対応なしと複数候補は明示。
- 検証：一時台帳で実際の非表示Tk画面の読込→編集→保存→再表示と、AXN側Storeでの読込・保存・Python_VScode側での独立項目マージを確認。スクリプトは `.tmp/verify_ex_info_ui.py` と `.tmp/verify_ex_info.py`（Git管理外）。本番台帳は変更していない。
- 通常環境（Tcl使用可）で全体テスト **279 passed, 1 failed**。残る1件は以前からの `test_raw_files_are_not_hashed`。前回までのTcl由来6件は通常環境で通過した。サンドボックス内でのTcl読み込み制約とユーザー環境での故障を混同しないこと。
- READMEを更新。コミット・pushは未実施。

追記（起動エラー対応）：ランチャーの `pyvscode_launcher_2_35536_0.log` で、`catalog.initialize()` の無条件な初期化書き込みによる `database is locked` を確認。`shared/catalog.py` は既存のスキーマ・追加列・インデックス・user_version・WALがそろうDBでは読み取り確認だけで戻るよう変更。別接続がBEGIN IMMEDIATE中でも初期化できることと、不足インデックスの修復をテスト。一時DBでの関連28件は通過。DB削除・更新プロセスの強制停止は行っていない。

### Codex の追記（2026-11-13）

- `exmanager/test_plan_links.py` に、一時フォルダだけを使う通しのテストを追加。計画JSONの読み取り → セット作成 → 未作成フォルダの確認 → 標準フォルダ作成 → 実ファイルのコピー → 17列CSV・Transfers.jsonl・manifest保存 → databaseの集計まで確認した。共有上の本番転送・GUI操作の確認とは別。
- 上記と `shared/test_ex_info_links.py`・`database/test_transfer_ledger.py` の関連テストは **27 passed**。
- `D:\GitHub\AXN` の4ファイルを更新済み（未コミット）：`axn/upload_check.py`、`tests/test_upload_check.py`、`docs/upload_check.md`、`docs/plan_ledger.md`。
  - `read_transfers` が「転送先の共有（記録）」「計画どおり」を読む。照合結果があればそれを優先し、「一部」は不一致扱い。空欄なら従来の名前照合に戻り、共有の記録があれば共有も比較する。
  - 引き継ぎ時点のAXNの `read_plans` は実際には `location_key` を参照していなかったため、Python_VScode側と優先順位をそろえた。
  - 旧CSVの互換性を保つ。PySide6未導入時にテストがGUIを先にimportして失敗する順序も修正。
  - AXNの `tests/test_upload_check.py` は **13 passed, 3 skipped**（PySide6未導入のためGUIをスキップ）。Downloads側のAXNコピーは変更していない。
- Python_VScode全体の対象テストを再実行：**244 passed, 7 failed**。既知のraw再試行テスト1件に加え、この実行環境ではTclの読み込みに起因するfileopsの6件が失敗。TCL_LIBRARY/TK_LIBRARY指定でも解消しなかった。全件通過とは扱わない。
- 本番の `D:\Qstatus` への書き込み、コミット・push、2台への反映は実施していない。本番確認に使うExperimental ID・確定した送り先・Sample/Blankをユーザーに確認中。
- ①の残り：本番の通し確認、2台への反映、移動・名前変更のジャーナルとの対応付け。下の①-3（AXN変更）はローカル実装済み。

### ①の残り
追記（Codex、2026-11-13、第3回：追跡記録の共有）：

- ユーザーが「machinedb\SEQ に追跡用の記録を共有する」を選択。共有先を `machinedb\SEQ\DestinationMoves\` として実装した。復元用の完全な履歴は各PCに残す。
- `shared/destination_history.py` の `share_journal()` がUNCフォルダ操作のパス・時刻・確定状態だけを共有。共有JSONは `schema_version=1`、履歴キー・版番号・内容ハッシュごとの別ファイル。正常な他PCの記録や過去の版を書き戻さない。ファイル単体の情報やidentityは共有しない。
- `sample_rename.execute/restore` と `file_moves.execute_moves` の履歴保存後に共有処理を追加。未確定状態も送るため、途中停止を完了と誤解しない。
- 先にローカル履歴フォルダの `.tracking/` に送信待ちを永続保存する。共有失敗は警告を出して操作と復元用履歴を保持し、次の同種操作・復元や台帳出力で再送する。ローカル送信待ち自体の保存に失敗しても元の操作履歴は残るので、下記の明示的同期で再生成できる。
- 履歴を持つPCで既存JSON履歴を初めて共有する／手動再送するコマンド：リポジトリ直下から `C:\Users\ohshi\anaconda3\python.exe -m shared.destination_history --sync`。**これは本番共有先へ書くコマンド。本セッションでは実行していない。**
- `load_moves()` はローカル履歴と共有記録の最新版を合わせて読む。同じ履歴がローカルにもあれば二重に適用しない。内容ハッシュで破損を検出し、最新版の同一版が競合した場合も要確認。送信元は自分の破損した共有ファイルを正しい内容で再送できる。
- `publish.export_transfers()` はローカル送信待ちを再送後、共有記録も読み、別PCでの操作後の保存先を使ってANALを集計する。
- テスト：Python_VScodeは **261 passed, 7 failed**（前回と同じ既知1件＋Tcl環境6件）。共有追跡とpublishの **20件はunittestでも通過**。2台分の一時フォルダで別PCの変更・復元、共有失敗後の再送、古い版との混在、重複適用防止、競合・破損、共有記録だけからCSV出力を確認した。pytest/unittestとも共有書き込み先を一時フォルダへ隔離する。
- Python_VScode・AXNの文書を更新済み。AXNのCSV読み取り処理の追加変更は不要（前回追加した現在の保存先・追跡状態の列をそのまま使う）。Downloads側のAXNコピーは変更していない。
- 残り：各操作PCとOUNmain03へのコード反映、必要な既存履歴の初回共有、実際のSynology同期を含む確認、本番の計画つき転送の確認。対象・送り先は引き続き未定。旧CSV履歴の共有・記録なしの手作業移動の追跡は未対応。
- 本番共有への書き込み、コミット・push、他PCへの反映は行っていない。下の第2回の「共有方法は未実装／方針確認中」はこの追記で更新される。

追記（Codex、2026-11-13、第2回）：

- ユーザーの回答：本番通し確認の対象・送り先は「未定」。本番確認は決まり次第行う。
- `shared/destination_history.py` を追加。台帳出力PCの `database/file_logs/` を読み、転送後の確定したフォルダ操作だけをたどる。サンプル名変更JSONと `file_moves` のディレクトリ操作に対応。転送時点の名前・共有・計画照合結果は保持する。
- `sample_rename.py` に操作ごとの `moved_at` と `restored_at`、`shared/file_moves.py` に `moved_at` を追加。名前変更後に転送してから復元するケースも時系列で追う。
- `database/transfer_ledger.py` と `publish.py` が追跡後のパスでANALを検索。移動済みなら元の場所に再作成された同名サンプルに戻って検索しない。CSVに「現在の保存先（履歴）」「保存先の追跡」を追加。
- 復元時刻のない古い履歴、未確定操作、旧CSVのフォルダ名変更は「保存先の追跡要確認」。壊れた履歴も無視せず不確実として扱う。ファイル単体の整理はサンプルの保存先変更とみなさない。
- AXNも新列を読み、詳細に `[current: …]` / `[location needs checking]` を出す。文書を更新。
- テスト：Python_VScodeは **253 passed, 7 failed**（前回と同じ既知1件＋Tcl環境6件）。AXN Upload Checkは **14 passed, 3 skipped**（PySide6未導入）。一時フォルダで実際の名前変更→履歴読取→復元→再読取と、履歴からCSV出力まで確認。
- 制限：履歴はPCローカル。別PCだけにある履歴、記録を残さない移動は追えない。「履歴なし」は移動していない証明ではない。追跡用の記録だけを `machinedb\SEQ` に共有するか、当面はローカルのみとするか、ユーザーに方針を確認中。
- 本番共有への書き込み、コミット・push、2台への反映は行っていない。

1. 上の通しの確認（ユーザーと一緒に）。
2. 反映：転送担当 PC の exmanager と、OUNmain03 のタスク「Qstatus Ledger Publish」（`database/publish.py`、毎時 :05）の両方で pull が必要。
3. AXN 側（別リポジトリ、`D:\GitHub\AXN`）：
   - `docs/upload_check.md` の sq_transfers 列の一覧と、`docs/plan_ledger.md` の「python_vscode で合わせること」を更新する。
   - Upload Check（`axn/upload_check.py` の `read_transfers`）で、新しい列「計画どおり」「転送先の共有（記録）」を使う。今は自前で `plan_matches` を計算している。
4. サーバー上でサンプル名を変えたり移したりしたときの対応付け。Analyzer / database のリネームは `shared/file_moves.py` のジャーナル（`database/file_logs/`）に残るので、それと Transfers.jsonl を突き合わせて追う案がある。

### 課題一覧（②以降はまだ手を付けていない）
- ② サンプル台帳が二重：AXN の `PLAN/ex_info.json` と Python_VScode の `SamplePlan/sample_plan.json`。ほかに旧形式の `plan.json`・`Assignments.csv` がある。解析ルートは 3 か所、フォルダ作成も 2 か所にある。案：Ex Info を定義の元にし、sample_plan は `sample_key` で解析設定を付ける。
- ③ 転送が手動で 1 台に依存している。未転送だと AXN の「Move to SQ_backup」が止まる。LabVIEW 版と Python 版の Uploader CSV が二重。
- ④ 状態の把握が「走査 → SQLite（3.7 GB）→ CSV → Synology 同期」で最大 1〜2 時間遅れる。OUNmain03 が単一障害点。
- ⑤ 共有 JSON 台帳の同時書き込み（競合コピーが実際に発生）。リポジトリ間の CSV の列の取り決めにテストがない。
- ⑥ 解析コードの多重化：1st Step は `Analyzer/python/first_step.py` と AXN の `axn/analyzer/`（手でコピー、現在は同一）にある。ほかに AXN の offline 解析と signal_core / ML_N。ASZ の ANAL とサーバー側の ANAL で二重計算している。
- ⑦ 計測スコア（EXSV / AC / GG、AXN 側）と解析成果（ANAL / BNAL）を結び付ける画面がない。
- ⑧ 実行環境が分かれている（PySide6 .venv / Tkinter + PyQt6 Anaconda / Python 3.14 venv）。`database/analysis_server_all.csv` がリポジトリ内で未追跡のまま。

## 作業上の注意

- Python は `C:\Users\ohshi\anaconda3\python.exe`（3.11）。このPCには AXN の `.venv` がない。
- `shared/README.md` の取り決めを守る：アプリ同士を import しない。共有するものは `shared/` に置く。人が決める情報は machinedb の 1 か所だけに置く。
- テストやスクリプトから `D:\Qstatus` に書かない（本番の共有フォルダで、各 PC へ同期される）。
- Git Bash の heredoc でファイルに書くと `\\` が `\` に減ることがあった。UNC パスを含むコードはエディタ機能で書く。
- `sq_transfers.csv` の列は AXN が列名で読む。足すのはよいが、名前を変える・消すときは両方のリポジトリを直す。

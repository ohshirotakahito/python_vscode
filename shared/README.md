# shared — アプリ間で共有する部品

ランチャーから起動する各アプリ（exmanager / ML / Viewer / database / Plotting / Analyzer）は、
**互いを直接 import しない**。共有するものはすべてこのフォルダを通す。
こうしておくと、アプリのフォルダを丸ごと作り直したり、別リポジトリで開発したものと差し替えたりしても、
ここにある関数の形と意味が変わらない限り他のアプリは壊れない。

## 中身と利用アプリ

| ファイル | 内容 | 使うアプリ |
|---|---|---|
| `ui_theme.py` | 共通ダークテーマ（色・フォント・`apply()`・`header()`）、ランチャーのアプリ別アクセント色 | 全アプリ・launcher |
| `app_release.py` | `app_versions.json` の検証・バージョン取得、Tkアプリのタイトル表示とアイコン設定 | 全アプリ・launcher |
| `app_icons.py` | ヘッダーと同じ記号・色のアプリ別ICOと、Windowsタスクバーのアプリ識別子 | 全アプリ・launcher |
| `app_update.py`, `update_ui.py` | Git追跡先の更新確認、安全なfast-forward更新、Tk/Qtの更新・再起動操作 | 全アプリ・launcher |
| `qt_theme.py`, `qt_icons/` | `ui_theme` の色・ヘッダー・アイコンを PyQt6 で再現したもの（`apply()`・`Header`・`window_icon()`） | Analyzer |
| `server_browse.py` | 計測サーバー → 共有 → 実験 → サンプルを一覧から選ぶダイアログ | ML, Viewer |
| `folder_layout.py` | 解析サーバー上の標準サンプルフォルダ構成 `create_sample_folders()` | exmanager, database |
| `machinedb.py` | machinedb の場所（`D:\Qstatus\machinedb`。Qstatus のない PC は従来の `D:\My_Dropbox\ou&t\machinedb`。決め打ちは環境変数 `PYVSCODE_MACHINEDB`） | 全アプリ |
| `seq_records.py` | SEQ の装置一覧CSV（`Machine_N.csv`）と転送履歴CSV（LabVIEW版 `Uploader.csv` ＋ Python版） | exmanager（書く）, database（読む） |
| `seq_targets.py` | SEQlist の target CSV（BNAL 解析対象）の名前の読み込み | database |
| `bnal_lists.py` | SEQlist（配列）と BC（コンダクタンス値）の読み込み、核酸・アミノ酸の判定（中身で判定） | Analyzer, database |
| `ex_info_links.py` | 計測の計画（AXN の Ex Info・G PSET が書く `machinedb\PLAN\MeasurementLinks.jsonl`・`ex_info.json`。読むだけ）と、転送の記録 `machinedb\SEQ\Transfers.jsonl`（1転送1行、解析サーバーの共有・Ex Info のキーつき。追記だけ） | exmanager（計画を読む・記録を書く）, database（記録を読む） |
| `sample_plan.py` | サンプル台帳（試料の種類・BNAL に回すか・使う SEQlist / BC）。machinedb に置き各 PC で共有 | Analyzer（今後 exmanager・database） |
| `catalog.py` | 実験ファイルDB の基本テーブル（sources / files / folders / updates）と接続 `connect()` | database, Plotting, Analyzer |
| `rack_store.py` | 解析サーバーのサンプル集計（rack_samples / rack_roots / rack_bnals）、測定番号の規則 `measurement_key()` | database, Plotting, Analyzer |
| `file_moves.py` | 記録（ジャーナル）つきのファイル移動・取り消し、記録の保存先 `journal_dir()`・`log_dir()`（`database/file_logs/`）、ANAL の一覧 | database, Analyzer |
| `destination_history.py` | ローカル履歴と `SEQ/DestinationMoves/` の追跡記録から現在の保存先を求める。UNCフォルダ操作の追跡用記録を共有し、送信待ちを再送する。復元用履歴は各PCに保持 | database, Analyzer |
| `t_stock.py` | Data 退避（T ⇄ T/stocked）の計画 | Analyzer |
| `progress.py` | ANAL・BNAL の進捗の状態（未着手・進行中・完了・超過・解析用コピー・確認不可）と注意（欠番・退避・混在・差異・要更新・コピー不足）の判定 | database, Analyzer |

## 取り決め（守ること）

### バージョンと更新

小幅修正はpatch（0.1.0→0.1.1）、中規模はminor（0.1.1→0.2.0）、大幅はmajor（0.2.0→1.0.0）。
変更完了時に `python tools/app_version.py analyzer --bump patch --note "変更内容"` のように記録します。
共通部品の変更では影響する各アプリの版を上げます。詳細はルートの `AGENTS.md` を参照してください。

各アプリ上部の「更新チェック」はGitの追跡先をfetchし、起動中・手元・配布元の版を表示します。
「更新する」はリポジトリ全体を確認済みのコミットへfast-forward更新します（アプリ単体の配布ではありません）。
ローカル変更や分岐がある場合は更新を停止し、変更を上書きしません。更新後は実行中の各アプリで
「更新チェック」→「再起動」を行います。再起動時は通常の終了処理を通します。作業は先に保存してください。
Gitが利用可能で、ブランチに追跡先が設定され、配布元に `app_versions.json` を含む版が公開されている必要があります。
起動中の版と手元の版だけが異なる場合も「再起動」が有効になります。

1. **アプリから他のアプリを import しない。** 2つ以上のアプリで必要になった処理は `shared/` に移す。
   `shared/` からアプリのフォルダを import することもしない。
2. **`shared/` は画面を持たない**（`ui_theme.py`・`server_browse.py`・`update_ui.py` の汎用部品は例外）。
   アプリ固有の画面・表示用の集計はアプリ側に置く。
3. **関数の名前・引数・戻り値の意味を変えない。** 変えたいときは新しい関数を足し、
   使っているアプリ（上の表）をすべて移行してから古いものを消す。
4. **実験ファイルDB（`database/inventory.sqlite3`）の取り決め**
   - テーブル・列を追加するときは `catalog.initialize()` / `rack_store.ensure_tables()` の中で
     `ALTER TABLE ... ADD COLUMN` によって行い、古いDBもそのまま開けるようにする。列の削除・意味の変更はしない。
   - `root` は `catalog.root_identity()`（大文字小文字・区切り文字を揃えた形）で比べる。
   - 測定番号は `rack_store.measurement_key()` だけで数える（ANAL 進捗・Data 退避・BNAL 退避で同じ規則）。
     `_Sample#NNN` は番号 NNN、それ以外は名前の最初の語（`ABex1T01#001`。A@@/B@@ の日時は除く）で、
     LabVIEW の長い A@@ 名と Python の短い A@@ 名が同じ測定になる。再開地点・欠番・退避で番号として扱うのは
     `rack_store.as_numbers()` が数字にしたものだけ（T 側・ANAL 側それぞれで系列が1つのとき）。
   - T 直下の A@@・B@@ は元ファイルではないので `t_tdms`・`t_bytes`・番号に入れず、`t_results_tdms` に数える
     （注意「T内A@@」。ANAL（サーバー）の「現状」で ANAL へ移す）。
   - 「解析が済んだか・まだ必要か」の判定は `progress.py` だけで行う。アプリごとに別の式を書かない。
     数えていない値（None）は 0 とみなさず「確認不可」「未計数」として残し、異常（超過・ANALなし など）は隠さない。
     判定を変えたら `shared/test_progress.py` と各アプリのテストを実行し、実際の DB で変わる件数を確認する。
   - 1サンプルを書き直すときは `rack_store.refresh_sample()` を使い、全体調査と同じ形で保存する。
   - DB の作成・全体調査は database アプリが行う。ほかのアプリは読み取り（`connect(readonly=True)`）と
     `refresh_sample()` だけにする。
   - サーバー上のファイルを移動するときは `file_moves.execute_moves()` を使い、記録を `journal_dir()` に残す。
     どのアプリから移動してもシグナル解析の「整理の履歴…」から元に戻せる。記録の JSON の形は変えない。
5. **データの置き場所は持ち主のアプリのフォルダ。** DB と移動・名前変更の記録（`database/file_logs/`）は `database/`、転送履歴は `exmanager/logs/` のまま。
   `shared/` にはパスの決め方だけを置く。
   人が決める情報（SEQlist・BC・サンプル台帳）は machinedb（D:\Qstatus）の1か所だけに置き、アプリ内にコピーを持たない。
   サンプル台帳は `sample_plan.update()` で変えた項目だけを書く（全体を書き戻すと別の PC の変更を消す）。

## Ex Infoの閲覧・編集

`ex_info_store.py` はAXNと共通の `PLAN/ex_info.json` を読み書きします。
研究Owner・研究／サンプルの説明のみ編集可能です。AXNと同じ `ex_info.lock`・revision・バックアップを使い、
最新の台帳へ変更項目だけを反映します。同じ項目の競合や同期競合コピーがあれば保存を止めます。
同期前の別PC同時編集を完全に排他する仕組みではありません。
`ex_info_owners.py` は登録場所をDBの保存場所と照合し、OwnerをPlanへ表示します。

## 転送後のフォルダ変更の追跡

`destination_history.load_moves()` は `database/file_logs/` と `machinedb/SEQ/DestinationMoves/` を読みます。読み取り時には移行・書き込みを行いません。
サンプル名変更のJSON履歴はフォルダのstep（identityのサイズがNone）、`file_moves` のJSON履歴は `dir=True` の操作だけを使います。
ファイル単体の整理・名前変更はサンプルの保存先変更とみなしません。
新しい履歴には操作ごとの `moved_at`、サンプル名変更の復元には `restored_at` を残します（既存JSONへの項目追加）。
転送時の `Transfers.jsonl` は書き換えず、転送後の操作だけを順に適用します。元に戻す操作も転送時刻と比較します。

復元時刻がない旧履歴、pending/restoringの操作、旧CSV形式のフォルダ名変更は現在地の根拠が足りないので要確認です。
壊れた履歴は追跡を不確実にし、同名サンプルから完了を推測しません。
復元用の完全な履歴はPCごとに保持します。新しいサンプル名変更・復元と `file_moves` のUNCフォルダ操作では、
追跡に必要なフォルダの対応・時刻・確定状態だけを `machinedb/SEQ/DestinationMoves/` へ共有します（schema_version 1）。
ファイル単体の名前・内容・ハッシュ・identityは共有しません。PCローカルのドライブ文字は別PCで同じ保存先を示せないため対象外です。

共有するJSONは履歴キー・版番号・内容ハッシュごとに別ファイルへ追加します。他PCの記録や以前の版を書き換えません。
読み取り側は履歴ごとの最新版だけを使い、同じ版で内容が競合した場合は要確認とします。
同じ履歴のローカル版があるPCではローカル履歴を優先し、共有版を重ねて再生しません。
共有前にローカル履歴と同じフォルダの `.tracking/` に送信待ちを永続保存します。
共有失敗は警告を出してローカルの操作・復元用履歴を保持し、次の同種操作・復元や台帳出力で再送します。
既存履歴の初回共有や障害復旧後の手動再送は、履歴を持つPCで次を実行します（本番の共有先へ書きます）：

```powershell
& 'C:\Users\ohshi\anaconda3\python.exe' -m shared.destination_history --sync
```

旧CSV形式の履歴の共有は未対応です。記録を残さない手作業の移動も追跡できません。
同期が終わるまでは新しい記録を参照できません。「履歴なし」は移動していない証明ではありません。

## アプリ側での読み込み方

起動スクリプトの先頭で、リポジトリ直下を import パスに入れてから `shared` を使う。

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # リポジトリ直下（shared/ を読むため）
from shared import ui_theme, catalog  # noqa: E402
```

テストはリポジトリ直下の `conftest.py` が同じことをするので、どのフォルダから pytest を実行してもよい。

## 新しいアプリを加えるとき

1. リポジトリ直下にアプリのフォルダを作り、起動スクリプトを置く（Analyzer は `Analyzer/python/gui_app.py`）。
   別リポジトリで開発している場合も、そのフォルダをここに置けば同じように扱える。
2. 他のアプリの機能が必要になったら、そのアプリを import せず、該当処理を `shared/` に移してから使う。
3. `shared/ui_theme.py` にアクセント色を足し、ランチャーのカードと同じタイトル・説明・アイコンでヘッダーを置く。
   Tkinter なら `ui_theme.apply(root, 色)` と `ui_theme.header(...)`、PyQt6 なら `qt_theme.apply(app, 色)` と
   `qt_theme.Header(...)`・`qt_theme.window_icon(...)`（Analyzer の `python/gui_app.py` が例）。
4. `launcher.py` の `APPS` に1行追加する（キー・名前・説明・スクリプト・作業フォルダ・アイコン・色）。
5. テストはアプリのフォルダ内に置き、ルート README のテスト手順にフォルダ名を足す。

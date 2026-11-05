# shared — アプリ間で共有する部品

ランチャーから起動する各アプリ（exmanager / ML / filemanager / Viewer / database / Plotting / Analyzer）は、
**互いを直接 import しない**。共有するものはすべてこのフォルダを通す。
こうしておくと、アプリのフォルダを丸ごと作り直したり、別リポジトリで開発したものと差し替えたりしても、
ここにある関数の形と意味が変わらない限り他のアプリは壊れない。

## 中身と利用アプリ

| ファイル | 内容 | 使うアプリ |
|---|---|---|
| `ui_theme.py` | 共通ダークテーマ（色・フォント・`apply()`・`header()`）、ランチャーのアプリ別アクセント色 | 全アプリ・launcher |
| `qt_theme.py`, `qt_icons/` | `ui_theme` の色・ヘッダー・アイコンを PyQt6 で再現したもの（`apply()`・`Header`・`window_icon()`） | Analyzer |
| `server_browse.py` | 計測サーバー → 共有 → 実験 → サンプルを一覧から選ぶダイアログ | ML, Viewer |
| `folder_layout.py` | 解析サーバー上の標準サンプルフォルダ構成 `create_sample_folders()` | exmanager, filemanager |
| `machinedb.py` | machinedb の場所（`D:\My_Dropbox\ou&t\machinedb`。別の PC では環境変数 `PYVSCODE_MACHINEDB`） | 全アプリ |
| `seq_records.py` | SEQ の装置一覧CSV（`Machine_N.csv`）と転送履歴CSV（LabVIEW版 `Uploader.csv` ＋ Python版） | exmanager（書く）, database（読む） |
| `seq_targets.py` | SEQlist の target CSV（BNAL 解析対象）の名前の読み込み | database, filemanager |
| `bnal_lists.py` | SEQlist（配列）と BC（コンダクタンス値）の読み込み、核酸・アミノ酸の判定（中身で判定） | Analyzer, database |
| `sample_plan.py` | サンプル台帳（試料の種類・BNAL に回すか・使う SEQlist / BC）。machinedb に置き各 PC で共有 | Analyzer（今後 exmanager・database） |
| `catalog.py` | 実験ファイルDB の基本テーブル（sources / files / folders / updates）と接続 `connect()` | database, filemanager, Plotting, Analyzer |
| `rack_store.py` | 解析サーバーのサンプル集計（rack_samples / rack_roots / rack_bnals）、測定番号の規則 `measurement_key()` | database, filemanager, Plotting, Analyzer |
| `file_moves.py` | 記録（ジャーナル）つきのファイル移動・取り消し、記録の保存先 `journal_dir()`、ANAL の一覧 | filemanager, Analyzer |
| `t_stock.py` | Data 退避（T ⇄ T/stocked）の計画 | filemanager, Analyzer |
| `progress.py` | ANAL・BNAL の進捗の状態（未着手・進行中・完了・超過・解析用コピー・確認不可）と注意（欠番・退避・混在・差異・要更新・コピー不足）の判定 | database, filemanager, Analyzer |

## 取り決め（守ること）

1. **アプリから他のアプリを import しない。** 2つ以上のアプリで必要になった処理は `shared/` に移す。
   `shared/` からアプリのフォルダを import することもしない。
2. **`shared/` は画面を持たない**（`ui_theme.py`・`server_browse.py` の汎用部品は例外）。
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
     どのアプリから移動しても filemanager の履歴画面から元に戻せる。記録の JSON の形は変えない。
5. **データの置き場所は持ち主のアプリのフォルダ。** DB は `database/`、転送履歴は `exmanager/logs/` のまま。
   `shared/` にはパスの決め方だけを置く。
   人が決める情報（SEQlist・BC・サンプル台帳）は machinedb（Dropbox）の1か所だけに置き、アプリ内にコピーを持たない。
   サンプル台帳は `sample_plan.update()` で変えた項目だけを書く（全体を書き戻すと別の PC の変更を消す）。

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

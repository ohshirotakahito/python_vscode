# AXN と実験ファイルDB の情報共有：現状・方針・アイデア

AXN（`D:\GitHub\AXN`）と python_vscode（exmanager・実験ファイルDB ほか）のあいだで、
どの情報を誰が書き、誰が読むのかを整理し、最終形を決めていくための文書です。
思い付いたことはまず「5. アイデア箱」に日付つきで入れ、整理できたら「3. 決まったこと」か「4. 未決の論点」へ移します。

- 作成: 2026-11-23
- 更新するとき: 各節の末尾に追記し、決めたことは「6. 決定の記録」に 1 行残す

---

## 1. 用語（両アプリで同じ呼び名にする）

| 呼び名 | 意味 | 例 | 主な置き場所 |
|---|---|---|---|
| **実験名** | 解析サーバーの実験フォルダ ＝ Ex Info の実験（Project） | `Suzuki_20260708` | Ex Info・解析サーバー |
| **サンプル名** | 実験フォルダの下のサンプルフォルダ ＝ Ex Info のサンプル | `Kme2_pH12` | Ex Info・解析サーバー |
| **実験ID** | 測定フォルダ名 ＝ AXN の Experimental ID（G PSET）＝ SQserver の「元の名前」 | `20261110_1055 UNAN#2Pex1n1_Km2epH12` | G PSET・SQserver |
| 装置 | 計測機のコード | `AXN2` | Machine_N.csv |

注意: Ex Info の実験にある `experiment_id` 項目（「正式な実験ID」用・現在すべて空）は、上の **実験ID とは別物**です。呼び名を決める必要があります（4-6）。

---

## 2. 現状の地図（2026-11-23 時点）

### 2-1. 流れ

```
計測（AXN）
  Ex Info で実験・サンプルを登録 ─┐
  G PSET で実験ID を実験・サンプルに結び付け（MeasurementLinks）
        │ 測定フォルダ
        ▼
SQserver（Recent_Data → Archived_Data → Stocked_Data）
        │ exmanager が転送（Uploader CSV・Transfers.jsonl に記録）
        ▼
解析サーバー（Rackstation / QTserver / QDserver / QKserver）  …  実験名\サンプル名\T・ANAL・BNAL@
        │ 実験ファイルDB が走査（開発PC だけ・毎時）
        ▼
実験ファイルDB（開発PC のローカル DB）──毎時公開──▶ D:\Qstatus\database（CSV・閲覧用DB）
                                                       ▲ AXN・ユーザー端末の実験ファイルDB が読む
```

### 2-2. 共有ファイルの一覧（誰が書き、誰が読むか）

`D:\Qstatus` は Synology で全端末に同期されます。

| ファイル | 中身 | 書く | 読む |
|---|---|---|---|
| `machinedb\PLAN\ex_info.json` | 実験・サンプル・保存場所・Owner・説明 | AXN Ex Info、実験ファイルDB（Owner・説明だけ） | AXN、実験ファイルDB、exmanager |
| `machinedb\PLAN\MeasurementLinks.jsonl` | 実験ID → Ex Info の実験・サンプル（計画） | AXN（G PSET） | exmanager、実験ファイルDB |
| `machinedb\PLAN\chip_info.json` | チップ台帳 | AXN、Chip Atlas | AXN、Chip Atlas |
| `machinedb\SamplePlan\sample_plan.json` | サンプル台帳（試料の種類・BNAL 対象・SEQlist・BC） | 実験ファイルDB ① | 実験ファイルDB、Analyzer |
| `machinedb\SEQ\Uploader.csv` | 転送履歴（LabVIEW 版） | LabVIEW（旧） | exmanager、実験ファイルDB |
| `exmanager\logs\Uploader_python.csv` | 転送履歴（Python 版） | exmanager | 実験ファイルDB（**開発PC だけにある**） |
| `machinedb\SEQ\Transfers.jsonl` | 転送ごとの送り先の共有・計画との照合（2026-11-13〜） | exmanager | 実験ファイルDB |
| `database\sq_experiments.csv` | SQserver の測定フォルダ一覧と解析着手 | publish（開発PC・毎時） | AXN |
| `database\analysis_server_all.csv` | 解析サーバーのサンプルごとの T・ANAL 数 | publish | AXN（`ex_info_folders.py`。列名 `実験名`・`サンプル名`） |
| `database\bnal_progress.csv` | BNAL 進捗 | publish | AXN |
| `database\sq_transfers.csv` | 実験ID ごとの転送先と ANAL 進捗 | publish | AXN（`upload_check.py`。列名 `装置`・`元の名前`・`転送先の実験名`…） |
| `database\inventory_view.sqlite3` ＋ `.view.json` | 閲覧用DB（2026-11-23〜） | publish | ユーザー端末の実験ファイルDB |

公開CSVの列名は AXN が名前で読んでいるので、**変えるときは両方を同時に直す**（現在の約束事）。

### 2-3. いま見えている食い違い・穴

- **名前のずれ**: 台帳の `Suzuki_20260708 / Kme2pH12` と、実際の転送先 `Kme2_pH12`。名前だけで照合しているので、ずれると「台帳のみ（DBになし）」になる。
- **計画の結び付けがほぼ使われていない**: MeasurementLinks は 1 件だけ。転送済みの実験ID（約 5,300 件）は、ほぼすべて「計画なし」。
- **未整理の測定フォルダ**: SQserver Recent_Data に、解析サーバーへ転送していない測定フォルダが 116 件ある（実験ID 対応タブの黄色の行）。
- **サンプル台帳が 2 つ**: Ex Info のサンプル（AXN）と sample_plan.json（実験ファイルDB の BNAL 対象・SEQlist・BC）が、名前でしかつながっていない。
- **Python 版の転送履歴が共有されていない**: `Uploader_python.csv` は開発PC のリポジトリ内にあり、ユーザー端末では Transfers.jsonl（2026-11-13 以降）の分しか見えない。
- **AXN の取り込みが開発PC のファイルを直接開く**: AXN Ex Info の取り込みは、既定で `python_vscode\database\inventory.sqlite3` を開く（`ex_info_gui.py`）。閲覧用DB を読む形にできる。
- **Archived_Data・Stocked_Data は未取得**: 古い実験ID が SQserver のどこにあるかはまだ分からない。

### 2-4. 名前の対照表（同じものの呼び名・項目名）

2026-11-23 に AXN（画面の日本語訳 `ui_catalog.py`）、共有ファイルの項目名、Chip Atlas・実験ファイルDB・exmanager の画面を調べた結果。
「共通名（案）」は提案で、まだ決めていない（4-9〜4-14）。

| もの | AXN の画面 | 共有ファイルの項目名 | python_vscode の画面 | 共通名（案） |
|---|---|---|---|---|
| 実験（Project） | プロジェクト／実験（混在） | Ex Info `projects.name` | 実験名（Ex Info タブは旧「研究」） | **実験名** |
| サンプル | 試料／試料名 | Ex Info `samples.name`、G PSET `Sample Name` | サンプル名 | **未決**（4-9） |
| 試料コード | 試料コード | G PSET `Sample Code` | （なし） | 未決 |
| 試料かブランクか | 試料・ブランク | `kind`（Sample / Blank） | 試料・ブランク | 未決（「試料の種類」と衝突、4-12） |
| 試料の種類（核酸・アミノ酸など） | （なし） | sample_plan `kind` | 試料の種類 | 同上 |
| 溶媒 | 溶媒／試料の溶媒 | G PSET `Solution Medium`、Ex Info `medium` | Medium | **溶媒** |
| 実験ID | 実験ID（Experimental ID） | G PSET `Experimental ID`、チップ履歴 `EXID`、Uploader `source_experiment`、Transfers `source`、公開CSV `元の名前` | 実験ID（② は「実験ID（元の名前）」） | **実験ID** |
| 正式な実験・サンプルの番号 | — | Ex Info `experiment_id`・`sample_id`（すべて空） | — | 未決（4-6） |
| 実験番号・計測番号 | 実験番号・計測番号 | 実験ID の中の `ex1` `n1` | — | 実験番号・計測番号 |
| チップ | チップ番号 | G PSET `Device Code`、chip_info `code`、チップ履歴のファイル名 | ChipID／チップID | **未決**（4-10） |
| チップの Wafer・Lot・製造元 | チップの製造元 | G PSET `Device Wafer`・`Device Lot`・`Device Supply` | Device Wafer・Device Lot | 未決 |
| 装置 | 装置 | `AXN1`（Machine DBCode）、`AXN#1`（Machine selection）、`AN#1`（Machine Code・実験IDの中）、`AX1`／`AX#1`（旧装置名・GPSET ファイル名） | 装置（`AXN1`） | **装置コード `AXN1`**（ほかは別名として対応表で持つ、4-11） |
| 実験の責任者 | 所有者・担当者、プロジェクト担当者 | Ex Info `owner_names`、chip_info `owner`（すべて空） | Owner | **未決**（4-13） |
| 計測した人 | 担当者 | G PSET `Operator`（名前）、Uploader `operator_code`（`TO`）、実験IDの先頭（`UN`） | Operator | **未決**（4-13） |
| データの保存場所 | 解析先 | Ex Info `locations.root_path` ＋ `experiment_folder` ＋ `sample_folder` | 共有・グループ・解析ルート（混在） | 未決（4-14） |
| チップの保管場所 | — | chip_info `location` | — | 保管場所（「保存場所」と分ける） |
| 実験日時 | — | G PSET `Experiment Date`、Uploader `experiment_at` | 実験日時 | **実験日時** |

### 2-5. つながりの現状（2026-11-23 の実データで数えた結果）

| つなぎ方 | 結果 | 読み取れること |
|---|---|---|
| 実験ID：転送履歴 ↔ G PSET | 転送済み 5,009 件のうち 4,008 件に G PSET 行あり | 実験ID は全体をつなぐ鍵として使える |
| 実験ID：チップ履歴 `EXID` ↔ G PSET | 16,264 件のうち 13,887 件が一致 | チップ ↔ 実験ID ↔ 実験名・サンプル名 がたどれる |
| 実験ID：G PSET（2024年以降 1,427 件）↔ 転送履歴 | 147 件だけ一致 | **要調査**：転送の記録漏れか、実験IDの書き方の違いか |
| チップ：G PSET `Device Code`（2024年以降 413 種）↔ chip_info | 404 種が登録済み、9 種が未登録（例 `SC91-00000`、`SC78-4301-24`） | ほぼつながっている |
| 人：Ex Info の Owner ↔ 担当者表 `Operator.csv`（18 人） | `Daicel`・`Takahagi`・`Numata`・`Yamazaki` が担当者表にない | Owner と担当者表が別々 |
| 人：G PSET `Operator`（2024年以降） | すべて担当者表にあるが、371 件が `Other` | 誰が測ったか分からない計測がある |
| チップの owner | 3,492 件すべて空 | 未使用の項目 |
| サンプル：sample_plan（1,123）↔ Ex Info（1,479） | 1,109 件が名前で一致 | 2 つの台帳を名前でつないでいる |
| サンプル：G PSET `Sample Name`（2024年以降 1,409 件）↔ Ex Info のサンプル名 | 237 件だけ一致 | 計測時の試料名は自由入力で、台帳とつながっていない |
| Ex Info の名前 ↔ 登録した保存場所のフォルダ名 | すべて一致 | Ex Info の中では名前とフォルダが同じ |
| 計画の結び付け（MeasurementLinks） | 1 件 | G PSET でまだ使われていない |

### 2-6. 同じ情報が複数の場所にあるもの（重複）

| 情報 | 置き場所 | 減らし方の案 |
|---|---|---|
| サンプルの定義 | Ex Info のサンプル、sample_plan.json（キーは「実験名\|サンプル名」の文字列）、G PSET `Sample Name`（自由入力） | Ex Info のサンプルを正にし、ほかは UUID（`sample_key`）で参照（4-2・4-3） |
| 人 | 担当者表 `Operator.csv`（名前＋2 文字コード）、Ex Info `owner_names`、chip_info `owner`、G PSET `Operator`、Uploader `operator_code` | 担当者表を正にし、ほかは担当者表の名前かコードで参照（4-13） |
| 装置の名前 | Machine_N.csv の 4 列（DBCode・selection・Machine Code・PC selection）、Machine_N2.csv、旧 GPSET ファイル名 `AX1` | 装置コードを 1 つに決め、ほかは別名の列として残す（4-11） |
| チップの使用状況 | チップ履歴 `ChipDB\DB\*.csv`、chip_info の `last_used_on`・`run_count` など、G PSET `Device Code` | 履歴を正にし、chip_info の集計値は履歴から作る値として扱う |
| 転送履歴 | Uploader.csv、Uploader_python.csv（開発PCだけ）、Transfers.jsonl | 4-5 |
| チップの別名 | chip_aliases.json、chip_info `aliases` | （整理中の作業。メモリ「Chip ID cleanup」） |

---

## 3. 決まったこと

- 実験・サンプルの定義は Ex Info（`ex_info.json`）。内部キーは UUID で、名前を変えてもキーは変えない（AXN ex_info_design.md）。
- 実験ID（測定フォルダ名）は一意なので、計測の特定に使ってよい（HANDOFF_plan_to_transfer.md）。
- 送り先の決め方は、リンクの `location_key` → リンクの `destination` → サンプルのメインの保存場所。AXN `upload_check.read_plans` と `shared/ex_info_links.load_plans` で同じにしておく。
- サーバーを走査するのは開発PC だけ。ほかの端末（AXN・ユーザー端末の実験ファイルDB）は公開されたファイルを読むだけ（2026-11-23）。
- 画面の呼び名は「実験名・サンプル名・実験ID」（2026-11-23）。
- 呼び名のルールを [naming_rules.md](naming_rules.md) に定めた（2026-11-23）。サンプル（試料はやめる）、計測区分（サンプル／ブランク）、サンプルの種類、チップID、装置コード `AXN1`、責任者（Owner）・測定者（Operator）・転送者、メンバー表・装置表。変えるのは画面だけで、データの項目名・共有CSV の列名は変えない。python_vscode は適用済み、AXN は未適用（4-9〜4-13 は決着）。

---

## 4. 未決の論点（最終形を決めるための問い）

番号は議論用。決まったら「3. 決まったこと」へ移します。

1. **正本はどこか**: 実験・サンプルの名前を直すとき、Ex Info を直して解析サーバーのフォルダ名を合わせるのか、フォルダ名を正として Ex Info を合わせるのか。
2. **サンプル台帳の統合**: sample_plan.json の項目（試料の種類・BNAL 対象・SEQlist・BC）を Ex Info のサンプルに持たせて 1 本にするか、別のままキー（UUID）でつなぐか。
3. **計画の結び付けを必須にするか**: G PSET で実験・サンプルを選ぶのを必須にすれば、名前の照合に頼らずに済む。既存の約 5,300 件をあとから結び付けるか。
4. **未整理の測定フォルダの扱い**: 116 件を誰が、どの画面で、どこへ転送するか（exmanager の「計画どおりにセットへ追加」を使うか）。
5. **転送履歴の置き場所**: `Uploader_python.csv` を machinedb に移すか、Transfers.jsonl だけに一本化するか。
6. **ID の呼び名**: Ex Info の「正式な実験ID／サンプルID」（ユーザーが付ける ID）を何と呼ぶか。「実験ID」は測定フォルダ名で使っている。
7. **AXN が DB を読む形**: AXN が見たい情報（ANAL・BNAL 進捗、転送の状況、未整理の測定フォルダ）を、公開CSV で渡すか、閲覧用DB を直接読ませるか。
8. **書き込みの分担**: Ex Info を実験ファイルDB からどこまで編集してよいか（いまは Owner・説明だけ）。
9. **「サンプル」と「試料」**: AXN は「試料」、python_vscode は「サンプル」。どちらに統一するか。
10. **チップの呼び名**: 「チップ番号」（AXN）と「チップID」（Chip Atlas）。どちらに統一するか。
11. **装置の正式コード**: `AXN1`・`AXN#1`・`AN#1`・`AX1` のどれを正式にし、ほかを別名として扱うか。
12. **「種類」の衝突**: 試料かブランクか（`kind`：Sample / Blank）と、試料の種類（核酸・アミノ酸）が、同じ `kind`・同じ「種類」という語を使っている。呼び名を分ける。
13. **人の呼び名と台帳**: 実験の責任者（Owner）と計測した人（Operator）が、AXN ではどちらも「担当者」と訳されている。呼び名を分け、担当者表 `Operator.csv` を人の正本にするか。
14. **保存場所の呼び名**: 解析先・共有・グループ・解析ルートが混在している。
15. **G PSET の試料名を台帳から選ぶようにするか**: いまは自由入力で、Ex Info と一致するのは 2024 年以降の 1,409 件中 237 件だけ（4-3 と関係）。

---

## 5. アイデア箱（思い付いたら日付つきで追加）

| 日付 | アイデア・気づき | 関係する論点 | 状態 |
|---|---|---|---|
| 2026-11-23 | 研究（実験）・サンプル・実験ID の関係が一目で分かる画面がほしい | — | 実装済み（実験ID 対応タブ） |
| 2026-11-23 | 同じものは同じ名前で呼ばないと分かりにくい | 4-6 | 画面の呼び名は統一済み。ID の呼び名は未決 |
| 2026-11-23 | 実験ID は SQserver 側から見るのか解析サーバー側から見るのか。解析フォルダに整理されていない測定フォルダがある | 4-4 | SQserver 側から照合し、未整理を表示 |
| 2026-11-23 | 測定機からも実験ファイルDB を開きたい | 4-7 | 閲覧専用で実装済み |
| 2026-11-23 | AXN のチップと実験DB などで使う名前を共通にし、重複を減らしたい。連携がうまくいっているか分からない | 4-9〜4-15 | 対照表（2-4）・つながりの数値（2-5）・重複（2-6）を作成。呼び名を 1 つずつ決める |
| 2026-11-23 | 実験ID 対応タブに、G PSET とチップ履歴からチップ・計測した人・試料名を出せそう | 4-3・4-10 | 案（実験ID で G PSET とチップ履歴を引ける） |

---

## 6. 決定の記録

| 日付 | 決めたこと | 理由・経緯 |
|---|---|---|
| 2026-11-23 | ユーザー端末の実験ファイルDB は閲覧専用。開発PC が毎時公開する閲覧用DB を読む | DB 本体は 3.7GB で同期に向かない。走査を 1 台に限る |
| 2026-11-23 | 画面の呼び名を「実験名・サンプル名・実験ID」に統一。公開CSV の列名は変えない | AXN が CSV を列名で読んでいるため |
| 2026-11-23 | サンプル／チップID／装置コード AXN1／責任者・測定者 に統一（naming_rules.md） | 同じものを同じ名前で呼ぶため。AXN では Owner と Operator がどちらも「担当者」だった |

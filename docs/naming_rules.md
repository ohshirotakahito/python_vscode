# 呼び名のルール（AXN・python_vscode 共通）

2026-11-23 決定。AXN（`D:\GitHub\AXN`）と python_vscode（実験ファイルDB・exmanager・Chip Atlas ほか）の**画面に出す呼び名**をそろえる。
経緯・未決の論点は [axn_database_roadmap.md](axn_database_roadmap.md)。

## 守ること

1. **変えるのは画面の表示だけ**。データの項目名・ファイル名・共有CSV の列名・LabVIEW が書く列名（`Operator`・`Device Code`・`EXID`・`Machine selection` など）は変えない。
   共有CSV（`D:\Qstatus\database\*.csv`）の列名を変えるときは、読む側（AXN）と同時に直す。
2. 新しい画面・列・メッセージは、下の表の「使う呼び名」で書く。「使わない呼び名」は新しく使わない。
3. どちらにも当てはまらない新しい言葉が必要になったら、先にこの表に足してから使う。

## 呼び名の表

| もの | 使う呼び名 | 使わない呼び名 | 補足 |
|---|---|---|---|
| 実験（Ex Info の Project・解析サーバーの実験フォルダ） | **実験**・**実験名** | 研究、プロジェクト | |
| サンプル（Ex Info の Sample・サンプルフォルダ） | **サンプル**・**サンプル名** | 試料、試料名 | G PSET の `Sample Name` も「サンプル名」、`Sample Code` は「サンプルコード」 |
| サンプルかブランクか（`kind`：Sample / Blank） | **計測区分**（値は「サンプル」「ブランク」） | 試料・ブランク、区分 | |
| サンプルの中身の種類（核酸・アミノ酸など。サンプル台帳 `kind`） | **サンプルの種類** | 試料の種類 | 計測区分と混ぜない |
| 溶媒（`Medium`・`Solution Medium`） | **溶媒** | Medium、試料の溶媒 | |
| 測定フォルダ名（G PSET の Experimental ID） | **実験ID** | 元の名前、実験 ID（空白入り）、EXID | 共有CSV の列名「元の名前」は残す |
| 実験ID の中の番号（`ex1`・`n1`） | 実験番号・計測番号 | | |
| チップ（`Device Code`・chip_info `code`） | **チップID** | チップ番号、ChipID、Device Code | ウェハー・ロットは「ウェハー」「ロット」 |
| 装置 | **装置**。値は**装置コード `AXN1`** | （値として）`AXN#1`・`AN#1`・`AX1` | ほかの形は別名。対応は `machinedb\SEQ\Machine_N.csv`（DBCode 列が装置コード） |
| 実験・チップの責任者（Ex Info `owner_names`・chip_info `owner`） | **責任者** | Owner、担当者、所有者 | |
| 計測した人（G PSET `Operator`・チップ履歴 `Operator`・実験ID の先頭 2 文字） | **測定者** | Operator、担当者、実験担当者 | |
| 解析サーバーへ転送した人（exmanager の uploader） | **転送者** | アップロード担当者 | |
| 人の一覧（`machinedb\SEQ\Operator.csv`：名前・2 文字コード・所属） | **メンバー表** | 担当者CSV | 責任者・測定者・転送者はここの人から選ぶ（予定） |
| 装置の一覧（`Machine_N.csv`） | **装置表** | 装置CSV | |
| チップの保管場所（chip_info `location`） | **保管場所** | | データの「保存場所」と分ける |
| データの置き場所（Ex Info `locations`） | **保存場所** | | 共有・グループ・解析ルートの整理は未決（ロードマップ 4-14） |

「担当者」は責任者・測定者・転送者のどれにも使わない（意味が決まらないため）。

## 適用状況

| アプリ | 状況 |
|---|---|
| 実験ファイルDB・exmanager・Chip Atlas・shared（python_vscode） | 2026-11-23 に画面の呼び名を変更。Chip Atlas の「チップ台帳」画面（`chipdb/ledger_view.py`）は別作業の変更が残っているため、その作業のあとに直す |
| AXN | **未適用**。下の「AXN でやること」 |

## AXN でやること（AXN の Claude Code への依頼）

1. `axn/ui_catalog.py` の日本語訳を上の表に合わせる。主なもの:
   - `Sample Name`・`Sample name` → サンプル名、`Sample` → サンプル、`Sample Code` → サンプルコード、`Sample / Blank` → 計測区分
   - `Project`・`New project` など → 実験（「プロジェクト」をやめる）
   - `Device Code` → チップID、`Chip` → チップ
   - `Owner`・`Owner (project; …)` → 責任者、`Operator` → 測定者、`Recipe owner` → 計測条件の責任者
   - `Medium`・`Medium (sample)` → 溶媒
2. 装置の表示に `AXN#1` を出している所は、装置コード `AXN1` を出す（値の保存形式は変えない）。
3. 共有CSV・machinedb のファイル形式と列名は変えない。
4. AXN のテスト・版の決まりに従い、AXN のリポジトリでコミットして push する。

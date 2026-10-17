# stat_features_rmc.py

RMCのメタ特徴量について、モデル学習を行わずにヒストグラムと統計CSVを作成します。

## 基本的な実行方法

```powershell
python stat_features_rmc.py LPhe LIle LVal
```

結果は実行ごとに日時付きの別フォルダへ保存されます。

```text
hist_results/20260903_120000_123456_LPhe-LIle-LVal/
```

## 色の指定

色を指定しない場合は、`DEFAULT_COLOR_PALETTE`のパレットからサンプルの指定順に自動配色します。既定値は`tab20`です。

```powershell
python stat_features_rmc.py LPhe LIle LVal --palette tab10
```

> `tab10`と`tab20`の`1`は数字です。`tabl10`（小文字のL）ではありません。

### 比較に使いやすいパレット

| パレット | 向いている用途 | 色数の目安 |
|---|---|---:|
| `tab10` | 少数のサンプル比較 | 10 |
| `tab20` | サンプル数が多い比較 | 20 |
| `Set1` | はっきりした色での比較 | 9 |
| `Set2` | やや淡い色での比較 | 8 |
| `Set3` | 多めのカテゴリ比較 | 12 |
| `Dark2` | 濃く識別しやすい色 | 8 |
| `Paired` | 関連するサンプルをペアで表示 | 12 |
| `Accent` | 強調色を使った比較 | 8 |

連続量向けの`viridis`、`plasma`、`cividis`なども指定できますが、独立したサンプルの比較には`tab10`や`tab20`などのカテゴリ用パレットを推奨します。

利用中のMatplotlibで使える全パレットは、次のコマンドで確認できます。

```powershell
python stat_features_rmc.py --list-palettes
```

### 色を順番で直接指定する

`--colors`の色は、コマンドに記載したサンプルと同じ順番で対応します。

```powershell
python stat_features_rmc.py LPhe LIle LVal `
  --colors royalblue darkorange green
```

16進カラーも利用できます。PowerShellでは`#`を含む値を引用符で囲んでください。

```powershell
python stat_features_rmc.py LPhe LIle LVal `
  --colors "#0072B2" "#D55E00" "#009E73"
```

## 横軸範囲の指定

特徴量ごとに下限と上限を指定できます。

```powershell
python stat_features_rmc.py LPhe LIle `
  --lower-limit relative_signal=0 `
  --upper-limit relative_signal=80 `
  --lower-limit duration=0 `
  --upper-limit duration=150
```

指定可能な特徴量は次の15種類です。

- `absolute_signal`
- `relative_signal`
- `duration`
- `wave_0`～`wave_11`

## distanceによるデータ選択

distanceを指定しなければ、従来どおり全distanceのデータを使用します。

### コード内で設定する方法

`stat_features_rmc.py`の設定欄にある次の3項目を編集します。

```python
# distanceを選択しない（全distanceを使用）
DEFAULT_DISTANCE_VALUES = None
DEFAULT_DISTANCE_MIN = None
DEFAULT_DISTANCE_MAX = None
```

特定のdistanceだけを使用する場合：

```python
DEFAULT_DISTANCE_VALUES = [0.54, 0.57]
DEFAULT_DISTANCE_MIN = None
DEFAULT_DISTANCE_MAX = None
```

範囲で選択する場合：

```python
DEFAULT_DISTANCE_VALUES = None
DEFAULT_DISTANCE_MIN = 0.50
DEFAULT_DISTANCE_MAX = 0.60
```

下限または上限だけを設定することもできます。特定値と範囲を同時に設定した場合は、両方の条件を満たすデータだけが対象になります。

### コマンドラインで設定する方法

```powershell
python stat_features_rmc.py oxytocin vasopressin
```

まず、選択可能なdistanceと各件数を確認できます。

```powershell
python stat_features_rmc.py oxytocin vasopressin --list-distances
```

特定のdistanceだけを使用する場合は`--distance`を指定します。複数の値も一度に選択できます。

```powershell
# distance=0.54だけを使用
python stat_features_rmc.py oxytocin vasopressin --distance 0.54

# distance=0.54または0.57を使用
python stat_features_rmc.py oxytocin vasopressin --distance 0.54 0.57
```

範囲で選択する場合は`--distance-min`と`--distance-max`を使用します。境界値も対象に含まれます。

```powershell
# 0.50以上0.60以下を使用
python stat_features_rmc.py oxytocin vasopressin `
  --distance-min 0.50 `
  --distance-max 0.60
```

片側だけの指定も可能です。

```powershell
# 0.50以上を使用
python stat_features_rmc.py oxytocin vasopressin --distance-min 0.50

# 0.60以下を使用
python stat_features_rmc.py oxytocin vasopressin --distance-max 0.60
```

`--distance`と範囲指定を併用した場合は、両方の条件を満たすデータだけを使用します。distanceを指定した結果、データが0件になった場合は処理を停止して通知します。出力フォルダ名にも選択したdistance条件が記録されます。

## ビン数の指定

既定ではデータから自動決定します。固定する場合は正の整数を指定します。

```powershell
python stat_features_rmc.py LPhe LIle --bins 50
```

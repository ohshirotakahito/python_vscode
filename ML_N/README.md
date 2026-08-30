# ML_N

元TDMSの連続波形からsignalを検出し、実験条件・波形・特徴量を保存する新しいパイプライン。

## Responsibilities

- 元TDMSの読み込み
- 実験条件と測定情報の取得
- 電流値のpA変換
- signal-coreを使ったsignal検出
- ANALファイルとの比較
- イベント・波形・特徴量の保存

## Data flow

元TDMS → TDMS reader → pA波形＋metadata → signal-core → events

## Boundaries

signal検出の汎用計算はsignal-coreへ置く。
TDMS構造、保存先、実験固有情報はML_Nへ置く。
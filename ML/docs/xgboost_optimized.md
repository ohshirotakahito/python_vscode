# XGBoost + tsfresh 精度改善版

`train_xgboost_tsfresh_optimized.py` は既存スクリプトを変更せずに使う独立した学習用スクリプトです。

```powershell
python ML/train_xgboost_tsfresh_optimized.py --check-only
python -u ML/train_xgboost_tsfresh_optimized.py
```

このPCでは `python` の代わりに `C:/Users/ohshi/anaconda3/python.exe` を指定できます。
依存ライブラリは numpy、pandas、scikit-learn、xgboost、tsfresh、parquet入力時はpyarrowです。

## 既定の実験

- ALTNA / GLTNA / CLTNA / TLTNA、distance=0.58。元スクリプトと同じ時間・信号・ベースラインの範囲で絞り込みます。
- `ex_id + sample_name` 単位で開発用と最終テスト用を分割。5分割の1つをテストに使うため、件数比は厳密な20%とは限りません。
- 開発データ内で最大5-foldのグループ交差検証。グループ数が不足するとfold数を減らし、クラス欠落があれば停止します。イベント単位のランダム分割には切り替えません。
- EfficientFCParametersの特徴量を抽出。無限値を欠損にし、学習foldの中央値で補完します。定数列除去と任意のtsfresh特徴選択も学習fold内で実施します。
- 元のメタ特徴量に、波形の差分・区間平均と標準偏差・面積・ピーク位置・64/128/256点の時間正規化波形・振幅特徴を追加。
- 固定6候補（Minimal＋間引き、Minimal＋全件、Minimal＋重み、Efficient＋全件、Efficient＋重み、波形追加＋重み）と、100候補のランダム探索を比較。
- ランダム探索では深さ・学習率・正則化・サンプリング・特徴量セット・クラス重み・特徴選択・absolute_signalの有無を比較。
- 最大3,000ラウンド、early stopping 150。最終学習回数は各foldの `best_iteration + 1` の中央値。
- OOF Macro F1で候補を選び、上位1/2/3候補の確率平均もOOF上で比較。確定した構成だけを最終テストで評価します。

OOFはモデル選択用スコアです。同じfoldでearly stoppingも行うため、独立した汎化性能の推定値としては最終テストを使ってください。
元スクリプトと分割・前処理が異なるため、旧スコアとの直接比較ではなく、新版の固定候補を基準とします。

2026-09-25に確認した対象データでは、ALTNA・CLTNA・TLTNAは各1測定しかありません。
既定のファイル分割では別の日や別装置への汎化を検証できません。
各クラスの独立測定を十分に追加した後は `--group-column ex_id` を指定してください。

## 設定例

```powershell
# 重い特徴量も追加する別実験
python ML/train_xgboost_tsfresh_optimized.py --fc-mode comprehensive
# 探索を拡張
python ML/train_xgboost_tsfresh_optimized.py --trials 300 --rounds 5000 --patience 200
# 別のクラス・距離
python ML/train_xgboost_tsfresh_optimized.py --samples ALTNA GLTNA --distance 0.52
```

異なる実験を同じ最終テストのスコアで選び続けるとテストへの過適合になります。
追加実験の選択は開発CVで行い、必要なら新たな独立測定で最終確認してください。
LightGBMとの統合、複数seedでの再検証、追加の生波形チャネル抽出はこの版には含めていません。
波形の面積はサンプル間隔単位、半値幅特徴は閾値以上の点の割合であり、物理時間単位の幅ではありません。

## 保存先と再利用

結果: `ML/results/rmc/xgboost_optimized/<日時>/`

- `config.json`: 設定、入力ファイルのサイズ・更新日時、ライブラリバージョン
- `splits.csv`: イベントごとの開発/テスト区分とCV fold
- `candidates.json`, `trials.json`, `trials.csv`: 探索設定と候補ごとの途中成績
- `selection.json`: 単体/アンサンブルの選択結果
- `model_*.json`, `pipeline.pkl`: モデル、列順、補完中央値、ラベル対応
- `test_metrics.json`, `confusion_matrix.csv`, `test_predictions.csv`: 最終評価

抽出キャッシュ: `ML/data/features/rmc/_cache_optimized_v1/`。
元のキャッシュとは分離し、入力・設定・tsfreshバージョン・イベントIDに応じて再利用します。
再実行時は完了済みの特徴抽出バッチを再利用しますが、パラメータ探索は最初からです。
計算量は大きく、波形CSVを1クラスずつメモリに読み込みます。バッチを小さくしてもCSV読込自体のメモリは減りません。

モデル再利用時は同じ特徴抽出を行い、各モデルの `preprocessing['columns']` 順に列を揃え、
無限値をNaNへ置換し、保存済み `preprocessing['medians']` で補完します。
各モデルの確率を平均し、保存済みLabelEncoderでクラス名に戻してください。
既存の `predict_mix_xgboost_tsfresh.py` とはモデル形式が異なるため、そのまま読み込めません。
pickleファイルは自身が作成した信頼できるもののみ読み込んでください。

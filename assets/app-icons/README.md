# アプリ別タスクバーアイコン

各アプリの画面ヘッダーと同じSegoe Fluent Iconsの記号・色から生成したICOです。
`shared/app_icons.py` が対応表とWindowsのアプリ識別子を管理します。
Quantum Workbench本体は従来のQアイコンを使用します。

再生成はWindows上で `python tools/build_app_icons.py` を実行します（Pillowが必要）。
SegoeIcons.ttfを直接読み込むため、描画環境による別フォントへの代替を避けます。
配布時には生成済みICOを含めるため、利用側での生成やフォントの追加設定は不要です。

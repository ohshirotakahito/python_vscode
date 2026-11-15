# Quantum Workbenchの開発端末とユーザー端末

## 開発端末（このPC）

リポジトリ直下で実行します。同じPython環境でランチャーを起動してください。

```powershell
python tools/terminal_setup.py --role developer
python launcher.py
```

全アプリを利用できます。修正したアプリの版・更新履歴を記録し、テスト後に必要なソースをコミットして
GitHubの配布ブランチへpushします。更新チェックはpushされた変更だけを検出します。
開発端末の未コミット変更はユーザー端末には配布されません。

## ユーザー端末の初回設定

GitとPython、および使用する各アプリの依存パッケージをインストールした端末で実行します。
シグナル解析の依存パッケージ一覧は `Analyzer/requirements.txt` です。
非公開リポジトリの場合はGitHubへの読み取り認証も設定してください。

```powershell
git clone https://github.com/ohshirotakahito/python_vscode.git
cd python_vscode
python tools/terminal_setup.py --role user --apps exmanager
python launcher.py
```

上の例では実験データ整理だけが利用可能です。設定時に管理者が端末ごとにアプリを選びます。
未設定の端末もユーザー端末扱いで、実験データ整理だけを利用できます。`--role user` だけを指定した場合も同じです。
明示的にアプリを空にするには `--role user --apps` と指定します。設定内容はランチャーの「端末設定」で確認できます。
設定変更後はランチャーを再起動してください。

| アプリID | アプリ |
|---|---|
| chipdb | Chip Atlas・チップDB |
| exmanager | 実験データ整理 |
| database | 実験ファイルDB |
| analyzer | シグナル解析 |
| viewer | TDMSビューア |
| ml | MLパネル |
| plotting | 配列アセンブリ |
| scoring | Scoring |

## GitHubからの更新

1. 作業を保存し、起動中の各アプリを閉じます。
2. ランチャーで「更新チェック」を押し、起動中・手元・GitHub配布元の版を確認します。
3. 「更新する」を押します。fetch済みの配布コミットへfast-forwardします（`pull --ff-only` と同じ更新方式）。
4. 「再起動」を押してランチャーを再起動し、必要なアプリを開きます。

端末設定 `.workbench/terminal.json` はGit管理外で、更新しても保持されます。
更新はリポジトリ全体を取得しますが、表示・起動できるアプリは端末設定の選択だけです。
ブランチが分岐している場合や、ローカルの変更がある場合は更新を停止し、上書きしません。
依存パッケージの自動インストールは行いません。配布元の更新履歴に従って必要に応じて追加してください。

端末設定は運用上の利用制限です。ローカル設定やPythonソースを変更できる人に対する強制的な権限制御ではありません。
設定の変更を管理者に限定する場合は、OSのファイルアクセス権も管理してください。

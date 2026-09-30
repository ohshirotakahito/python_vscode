# -*- coding: utf-8 -*-
"""ML/app/_run_app.py

launch_ui.bat から呼ばれるランチャー。ダブルクリックだけで起動できるよう、
以下の2つの問題を回避してからStreamlitを起動する。

1. 一部のWindows環境では、Streamlit内部が使うtornado（組み込みWebサーバー）が
   起動時にデフォルトSSLコンテキストを作ろうとして、Windowsの証明書ストアの
   読み込みに失敗しクラッシュすることがある
   （`ssl.SSLError: [ASN1: NOT_ENOUGH_DATA] not enough data`）。
   これはHTTPS通信の証明書検証ではなく、tornadoがimport時に一応SSLコンテキストを
   用意しておくだけの処理で発生するため、ローカルでUIを使う分には無視して問題ない。
   そのためstreamlit本体をimportする前に無視するパッチを当てる。

2. Streamlitは初回起動時、コンソール上でメールアドレス入力を求める対話プロンプトを
   出すことがある。ダブルクリック起動ではこのプロンプトに気づけず、
   「起動しない（実際は入力待ちで止まっている）」ように見えてしまうため、
   `--server.headless=true` を指定してこのプロンプトを出さないようにし、
   代わりにこちらから明示的にブラウザを開く。
"""

import ssl

_original_load_default_certs = ssl.SSLContext.load_default_certs


def _safe_load_default_certs(self, purpose=ssl.Purpose.SERVER_AUTH):
    try:
        return _original_load_default_certs(self, purpose)
    except ssl.SSLError:
        pass


ssl.SSLContext.load_default_certs = _safe_load_default_certs

import socket
import sys
import threading
import webbrowser
from pathlib import Path


def _find_free_port(preferred: int = 8501) -> int:
    """preferredポートが空いていればそれを、埋まっていれば別の空きポートを返す。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


if __name__ == "__main__":
    home_path = Path(__file__).resolve().parent / "Home.py"
    port = _find_free_port()
    url = f"http://localhost:{port}"

    sys.argv = [
        "streamlit", "run", str(home_path),
        "--server.headless=true",
        f"--server.port={port}",
    ]

    print(f"MLパネルを起動しています... 準備でき次第ブラウザで {url} を開きます。")
    print("(自動的に開かない場合は、上のURLを手動でブラウザに貼り付けてください)")
    threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    import streamlit.web.cli as stcli

    sys.exit(stcli.main())

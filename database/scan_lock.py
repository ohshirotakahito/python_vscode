"""このPCでサーバー走査を1つずつ行うためのロック。

実験ファイルDBアプリの更新ボタンと、1時間ごとの台帳書き出し（publish.py）が同じロックを使う。
同時に走査すると同じ inventory.sqlite3 に書き込み、ネットワークの負荷も倍になるため。
OS のファイルロックなので、プロセスが落ちてもロックは自動で外れる（古いロックが残らない）。
"""
from datetime import datetime
from pathlib import Path
import msvcrt
import os

LOCK_PATH = Path(__file__).resolve().parent / 'logs' / 'scan.lock'


class ScanLock:
    def __init__(self, owner, path=LOCK_PATH):
        self.owner = owner
        self.path = Path(path)
        self.note = self.path.with_suffix('.owner')
        self.stream = None

    def acquire(self):
        """True when this object now holds the lock; never waits."""
        if self.stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(self.path, 'a+b')
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        try:
            self.note.write_text(f'{self.owner}（PID {os.getpid()}、{datetime.now():%Y-%m-%d %H:%M} 開始）',
                                 encoding='utf-8')
        except OSError:
            pass
        return True

    def release(self):
        stream, self.stream = self.stream, None
        if stream is None:
            return
        try:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            stream.close()

    def holder(self):
        """Who holds the lock, for messages ('' when unknown)."""
        try:
            return self.note.read_text(encoding='utf-8')
        except OSError:
            return ''

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()

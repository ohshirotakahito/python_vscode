"""Cooperative cancellation shared by the GUI worker and rename operations."""
import os
import time


class JobCancelled(Exception):
    pass


_next_check = 0


def check_cancel():
    global _next_check
    path = os.environ.get('FILEOPS_CANCEL_FILE')
    if not path:
        return
    now = time.monotonic()
    if now < _next_check:
        return
    _next_check = now + 0.05
    if os.path.exists(path):
        raise JobCancelled('処理を中止しました。実行済みの変更は履歴から復元できます。')

"""画面更新に失敗してもバックグラウンド処理の通知を受信する。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app_tk"))
from widgets import TaskRunner


class TaskRunnerTests(unittest.TestCase):
    def test_status_updates_while_no_new_progress_arrives(self):
        runner = TaskRunner(Mock())
        listener = Mock()
        runner.add_status_listener(listener)
        runner._panel = Mock()
        runner._started_at = 10
        runner._set_busy(True)
        runner._q.put(('progress', (1, 4, '波形を計算中')))
        with patch('widgets.time.monotonic', return_value=75):
            runner._poll()
            runner._poll()
        self.assertEqual(listener.call_count, 2)
        listener.assert_called_with(True, 65, '波形を計算中', .25)

    def test_large_log_queue_yields_to_ui(self):
        runner = TaskRunner(Mock())
        runner._panel = Mock()
        for _ in range(300):
            runner._q.put(('log', 'message'))
        runner._poll()
        self.assertEqual(runner._q.qsize(), 100)
        self.assertEqual(runner.root.after.call_count, 2)

    def test_poll_continues_after_completion_callback_fails(self):
        root = Mock()
        runner = TaskRunner(root)
        panel = Mock()
        runner._panel = panel
        runner._on_done = Mock(side_effect=ValueError("更新失敗"))
        runner._set_busy(True)
        runner._q.put(("done", "output"))

        with patch("widgets.messagebox.showerror") as showerror:
            runner._poll()
        self.assertFalse(runner.busy)
        showerror.assert_called_once()
        panel.log.assert_called_once()
        self.assertEqual(root.after.call_count, 2)

        # 失敗したコールバックの後にも、次のタスクの完了を受け取れる。
        runner._on_done = Mock()
        listener = Mock()
        runner.add_busy_listener(listener)
        runner._set_busy(True)
        runner._q.put(("done", "next output"))
        runner._poll()
        self.assertFalse(runner.busy)
        runner._on_done.assert_called_once_with("next output")
        listener.assert_called_with(False)
        self.assertEqual(root.after.call_count, 3)


if __name__ == "__main__":
    unittest.main()

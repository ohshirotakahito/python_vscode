"""Existing samples inside the add-destination dialog."""
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk


def sample_names(root, experiment):
    if not experiment:
        return []
    if experiment in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', experiment):
        raise ValueError('実験名に使用できない文字があります。')
    folder = Path(root) / experiment
    try:
        return sorted((p.name for p in folder.iterdir() if p.is_dir()), key=str.casefold)
    except FileNotFoundError:
        # Distinguish a genuinely new experiment from an unavailable share.
        if not Path(root).is_dir():
            raise OSError('保存先に接続できません。')
        return []


class DestinationPreview(ttk.LabelFrame):
    def __init__(self, master, root, experiment, samples):
        super().__init__(master, text='選択した実験の既存サンプル', padding=8)
        self.root, self.experiment, self.samples = root, experiment, samples
        self.names = []
        self.loaded_for = None
        self.generation = 0
        self.pending = None
        self.events = queue.Queue()
        row = ttk.Frame(self)
        row.pack(fill='x')
        self.status = ttk.Label(row, text='実験を選択してください。')
        self.status.pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='一覧を更新', command=self.schedule).pack(side='right')
        body = ttk.Frame(self)
        body.pack(fill='both', expand=True, pady=(6, 0))
        self.listbox = tk.Listbox(body, height=8, exportselection=False)
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.listbox.pack(side='left', fill='both', expand=True)
        self.warning = ttk.Label(self, text='', wraplength=540)
        self.warning.pack(fill='x', pady=(4, 0))
        self.ex_trace = experiment.trace_add('write', self.schedule)
        self.sample_trace = samples.trace_add('write', self.show_overlap)
        self.bind('<Destroy>', self.cleanup)
        self.schedule()
        self.poll_id = self.after(100, self.poll)

    def schedule(self, *_):
        self.generation += 1
        if self.pending is not None:
            self.after_cancel(self.pending)
        self.names, self.loaded_for = [], None
        self.listbox.delete(0, 'end')
        self.warning.configure(text='')
        self.status.configure(text='既存サンプルを確認中…')
        self.pending = self.after(250, self.load)

    def load(self):
        self.pending = None
        generation, experiment = self.generation, self.experiment.get().strip()

        def work():
            try:
                result, error = sample_names(self.root, experiment), None
            except (OSError, ValueError) as exc:
                result, error = [], str(exc)
            self.events.put((generation, experiment, result, error))
        threading.Thread(target=work, daemon=True).start()

    def poll(self):
        try:
            while True:
                generation, experiment, names, error = self.events.get_nowait()
                if generation != self.generation:
                    continue
                self.names = names
                self.loaded_for = experiment if error is None else None
                self.listbox.delete(0, 'end')
                for name in names:
                    self.listbox.insert('end', name)
                self.status.configure(text=(f'取得できません: {error}' if error else
                    f'{experiment}：{len(names)} サンプル' if names else
                    '既存サンプルはありません（新しい実験・サンプルを作成できます）。' if experiment else
                    '実験を選択または入力してください。'))
                self.show_overlap()
        except queue.Empty:
            pass
        self.poll_id = self.after(100, self.poll)

    def show_overlap(self, *_):
        entered = {name.strip().casefold() for name in self.samples.get().split(',') if name.strip()}
        overlap = [name for name in self.names if name.casefold() in entered]
        self.listbox.selection_clear(0, 'end')
        for i, name in enumerate(self.names):
            if name in overlap:
                self.listbox.selection_set(i)
        if overlap:
            self.listbox.see(self.names.index(overlap[0]))
        self.warning.configure(text='入力した名前は既に存在します（既存フォルダを利用）: ' + '、'.join(overlap) if overlap else '')

    def cleanup(self, event):
        if event.widget is self:
            if self.pending is not None:
                self.after_cancel(self.pending)
            self.after_cancel(self.poll_id)
            self.experiment.trace_remove('write', self.ex_trace)
            self.samples.trace_remove('write', self.sample_trace)

"""CSV table editor preserving encoding, headerless rows and uneven columns."""
import csv
import io
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
import uuid
import tkinter as tk
from tkinter import ttk, messagebox

def sort_rows(rows, column, descending=False):
    """Sort whole rows, keeping empty values last and equal values stable."""
    def value(row):
        return row[column].strip() if column < len(row) else ''
    def key(row):
        text = value(row)
        try:
            number = Decimal(text)
            if number.is_finite():
                return (0, number)
        except InvalidOperation:
            pass
        return (1, tuple((0, int(part)) if part.isdigit() else (1, part)
                         for part in re.split(r'(\d+)', text.casefold())))
    populated = [row for row in rows if value(row)]
    empty = [row for row in rows if not value(row)]
    return sorted(populated, key=key, reverse=descending) + empty

def move_selected_rows(rows, selected, direction):
    """Move selected blocks one step, preserving order at the boundaries."""
    selected = set(selected)
    moved = False
    indices = sorted(selected, reverse=direction > 0)
    for index in indices:
        target = index + direction
        if 0 <= target < len(rows) and target not in selected:
            rows[index], rows[target] = rows[target], rows[index]
            selected.remove(index)
            selected.add(target)
            moved = True
    return sorted(selected), moved

def split_header(rows, column_titles, first_row_header=False):
    """Recognize the shared schema without mistaking legacy data for a header."""
    if rows and (first_row_header or (column_titles and tuple(rows[0][:len(column_titles)]) == tuple(column_titles))):
        return rows[0][:], rows[1:]
    return None, rows


def read_document(path):
    original = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            text = original.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('文字コードを判定できません。UTF-8またはCP932のCSVを指定してください。')
    if encoding == 'utf-8-sig' and not original.startswith(b'\xef\xbb\xbf'):
        encoding = 'utf-8'
    return original, encoding, list(csv.reader(io.StringIO(text, newline=''))), '\r\n' if '\r\n' in text else '\n'


def missing_history_rows(existing, source_path, column_titles):
    """Read missing complete history records without changing either document."""
    _, _, source, _ = read_document(source_path)
    _, source = split_header(source, column_titles)
    width = len(column_titles)
    seen = {tuple(row[:width]) for row in existing if len(row) >= width}
    missing = []
    for index, row in enumerate(source, 1):
        if not row or not any(row):
            continue
        if len(row) != width or not row[0]:
            raise ValueError(f'Python版CSVのデータ行{index}が履歴の{width}列形式ではありません。')
        key = tuple(row)
        if key not in seen:
            missing.append(row[:])
            seen.add(key)
    return missing


def save_document(path, original, encoding, rows, newline):
    path = Path(path)
    if path.read_bytes() != original:
        raise ValueError('別の処理でCSVが変更されています。開き直してから編集してください。')
    stream = io.StringIO(newline='')
    csv.writer(stream, lineterminator=newline).writerows(rows)
    content = stream.getvalue().encode(encoding)
    backup = path.with_name(path.name + '.' + uuid.uuid4().hex + '.bak')
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        backup.write_bytes(original)
        temp.write_bytes(content)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return content


class CsvEditor(tk.Toplevel):
    def __init__(self, parent, path, on_saved, column_titles=(), first_row_header=False, history_source=None):
        self.path = Path(path)
        self.history_source = Path(history_source) if history_source is not None else None
        self.history_columns = tuple(column_titles)
        self.original, self.encoding, self.rows, self.newline = read_document(path)
        self.header, self.rows = split_header(self.rows, column_titles, first_row_header)
        if self.header is not None:
            column_titles = tuple(self.header)
        super().__init__(parent)
        self.on_saved = on_saved
        self.dirty = False
        self.title(f'CSV閲覧・編集 — {self.path.name}')
        self.geometry('1100x600')
        self.transient(parent)
        self.grab_set()
        self.protocol('WM_DELETE_WINDOW', self.close)
        header_note = 'CSVの先頭行を列見出しとして表示しています。' if self.header is not None else '先頭行もデータとして表示します。'
        ttk.Label(self, text=f'セルをダブルクリックして編集。{header_note}保存時は同じフォルダに.bakを作成します。').pack(anchor='w', padx=10)
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.column_titles = column_titles
        self.width = max(len(column_titles), max((len(row) for row in self.rows), default=1))
        self.tree = ttk.Treeview(frame, columns=tuple(str(i) for i in range(self.width)), show='tree headings', selectmode='extended')
        self.tree.heading('#0', text='行')
        self.tree.column('#0', width=55, stretch=False)
        for i in range(self.width):
            title = column_titles[i] if i < len(column_titles) else f'列 {i + 1}'
            self.tree.heading(str(i), text=title)
            self.tree.column(str(i), width=150, stretch=False)
        self.tree.grid(row=0, column=0, sticky='nsew')
        for orient, axis, row, col, sticky in [('vertical', 'y', 0, 1, 'ns'), ('horizontal', 'x', 1, 0, 'ew')]:
            bar = ttk.Scrollbar(frame, orient=orient, command=getattr(self.tree, axis + 'view'))
            bar.grid(row=row, column=col, sticky=sticky)
            self.tree.configure(**{axis + 'scrollcommand': bar.set})
        self.tree.bind('<Double-1>', self.edit_cell)
        sorting = ttk.Frame(self, padding=(10, 0))
        sorting.pack(fill='x')
        if self.history_source is not None:
            importing = ttk.Frame(self, padding=(10, 4))
            importing.pack(fill='x')
            ttk.Button(importing, text='Python版の不足履歴を取り込む', command=self.import_history).pack(side='left', padx=4)
            ttk.Label(importing, text='不足履歴を追加し、uploaded_atの降順に並べます。反映には「保存」が必要です。').pack(side='left', padx=8)
        ttk.Label(sorting, text='並べ替える列').pack(side='left', padx=4)
        self.sort_column = ttk.Combobox(sorting, state='readonly', width=35,
            values=[f'{i + 1}: {column_titles[i] if i < len(column_titles) else f"列 {i + 1}"}'
                    for i in range(self.width)])
        self.sort_column.current(0)
        self.sort_column.pack(side='left', padx=4)
        ttk.Button(sorting, text='昇順', command=lambda: self.sort_by_column(False)).pack(side='left', padx=4)
        ttk.Button(sorting, text='降順', command=lambda: self.sort_by_column(True)).pack(side='left', padx=4)
        ttk.Label(sorting, text='全データ行を並べ替えます（空欄は末尾）。').pack(side='left', padx=8)
        actions = ttk.Frame(self, padding=10)
        actions.pack(fill='x')
        for label, command in [('行を追加', self.add_row), ('選択行を編集', self.edit_row), ('選択行を削除', self.delete_rows),
                               ('上へ', lambda: self.move_rows(-1)), ('下へ', lambda: self.move_rows(1)),
                               ('保存', self.save), ('閉じる', self.close)]:
            ttk.Button(actions, text=label, command=command).pack(side='left', padx=4)
        self.status = ttk.Label(actions)
        self.status.pack(side='left', padx=12)
        self.refresh()

    def import_history(self):
        try:
            if self.path.resolve() == self.history_source.resolve() or os.path.samefile(self.path, self.history_source):
                raise ValueError('LabVIEW版CSVとPython版CSVは別のファイルにしてください。')
            missing = missing_history_rows(self.rows, self.history_source, self.history_columns)
            # Report encoding failures before changing the editable document.
            for row in missing:
                for value in row:
                    value.encode(self.encoding)
        except Exception as exc:
            messagebox.showerror('履歴を取り込めません', str(exc), parent=self)
            return
        ordered = sort_rows(self.rows + missing, 0, descending=True)
        if not missing and ordered == self.rows:
            messagebox.showinfo('履歴の取り込み', 'Python版にだけ存在する履歴はありません。', parent=self)
            return
        self.rows = ordered
        self.dirty = True
        self.refresh()
        self.tree.yview_moveto(0)
        messagebox.showinfo('履歴の取り込み', f'{len(missing)}件の不足履歴を追加し、全履歴をuploaded_atの降順に並べました。\n内容を確認して「保存」を押してください。', parent=self)

    def sort_by_column(self, descending):
        selected_rows = {id(self.rows[int(item)]) for item in self.tree.selection()}
        ordered = sort_rows(self.rows, self.sort_column.current(), descending)
        if all(old is new for old, new in zip(self.rows, ordered)):
            return
        self.rows = ordered
        self.dirty = True
        self.refresh()
        self.tree.selection_set(*[str(i) for i, row in enumerate(self.rows) if id(row) in selected_rows])
        self.tree.yview_moveto(0)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.rows):
            self.tree.insert('', 'end', iid=str(i), text=str(i + 1), values=row)
        self.status.configure(text=f'{len(self.rows)} データ行 / {self.encoding}' + (' / 未保存' if self.dirty else ''))

    def edit_row(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showinfo('行を選択', '編集する行を1行選択してください。', parent=self)
            return
        index = int(selected[0])
        original = self.rows[index][:]
        dialog = tk.Toplevel(self)
        dialog.title(f'行を編集 — {index + 1}行 — {self.path.name}')
        dialog.geometry(f'1000x{min(850, max(350, self.winfo_screenheight() - 120))}')
        dialog.minsize(600, 300)
        dialog.transient(self)
        dialog.grab_set()
        ttk.Label(dialog, text='全項目をまとめて編集できます。「反映」後、CSV編集画面の「保存」でファイルに保存します。',
                  wraplength=900).pack(anchor='w', padx=12, pady=10)
        container = ttk.Frame(dialog)
        container.pack(fill='both', expand=True, padx=12)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        fields = ttk.Frame(canvas, padding=4)
        window = canvas.create_window(0, 0, window=fields, anchor='nw')
        fields.columnconfigure(1, weight=1)
        fields.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        editors = []
        for col in range(self.width):
            title = self.column_titles[col] if col < len(self.column_titles) else f'列 {col + 1}'
            ttk.Label(fields, text=f'{col + 1}. {title}').grid(row=col, column=0, sticky='nw', padx=(0, 12), pady=5)
            text = original[col] if col < len(original) else ''
            editor = tk.Text(fields, height=min(3, max(1, text.count('\n') + 1)), width=60,
                             wrap='word', undo=True)
            editor.insert('1.0', text)
            editor.grid(row=col, column=1, sticky='ew', pady=5)
            editors.append(editor)
            def focus_field(event, widget=editor):
                dialog.update_idletasks()
                top, bottom = widget.winfo_y(), widget.winfo_y() + widget.winfo_height()
                view_top = canvas.canvasy(0)
                if top < view_top or bottom > view_top + canvas.winfo_height():
                    canvas.yview_moveto(max(0, top - 10) / max(1, fields.winfo_height()))
            editor.bind('<FocusIn>', focus_field)
            def next_field(event, position=col, step=1):
                editors[(position + step) % len(editors)].focus_set()
                return 'break'
            editor.bind('<Tab>', next_field)
            editor.bind('<Shift-Tab>', lambda event, position=col: next_field(event, position, -1))
        def scroll(event):
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'
        dialog.bind('<MouseWheel>', scroll)
        def cancel():
            dialog.destroy()
            self.grab_set()
        def commit():
            values = [editor.get('1.0', 'end-1c') for editor in editors]
            # Preserve short rows unless a previously absent cell was filled.
            length = max(len(original), max((i + 1 for i, value in enumerate(values) if value), default=0))
            updated = values[:length]
            if updated != original:
                self.rows[index] = updated
                self.dirty = True
                self.refresh()
                self.tree.selection_set(str(index))
                self.tree.focus(str(index))
                self.tree.see(str(index))
            cancel()
        actions = ttk.Frame(dialog, padding=12)
        actions.pack(fill='x')
        ttk.Button(actions, text='反映', command=commit).pack(side='left', padx=4)
        ttk.Button(actions, text='キャンセル', command=cancel).pack(side='left', padx=4)
        dialog.protocol('WM_DELETE_WINDOW', cancel)
        editors[0].focus_set()

    def edit_cell(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or column in ('', '#0'):
            return
        index, col = int(item), int(column[1:]) - 1
        dialog = tk.Toplevel(self)
        title = self.column_titles[col] if col < len(self.column_titles) else f'{col + 1}列'
        dialog.title(f'{index + 1}行 / {title}')
        dialog.transient(self)
        dialog.grab_set()
        value = tk.Text(dialog, width=70, height=5)
        value.pack(padx=10, pady=10)
        value.insert('1.0', self.rows[index][col] if col < len(self.rows[index]) else '')
        def commit():
            updated = value.get('1.0', 'end-1c')
            row = self.rows[index]
            if col >= len(row) or row[col] != updated:
                row.extend([''] * max(0, col + 1 - len(row)))
                row[col] = updated
                self.dirty = True
                self.refresh()
            dialog.destroy()
            self.grab_set()
        def cancel():
            dialog.destroy()
            self.grab_set()
        ttk.Button(dialog, text='反映', command=commit).pack(side='left', padx=10, pady=10)
        ttk.Button(dialog, text='キャンセル', command=cancel).pack(side='left')
        dialog.protocol('WM_DELETE_WINDOW', cancel)
        value.focus_set()

    def add_row(self):
        self.rows.append([''] * self.width)
        self.dirty = True
        self.refresh()
        self.tree.see(str(len(self.rows) - 1))

    def delete_rows(self):
        selected = self.tree.selection()
        if selected and messagebox.askyesno('行を削除', f'{len(selected)}行を削除しますか？', parent=self):
            for i in sorted(map(int, selected), reverse=True):
                del self.rows[i]
            self.dirty = True
            self.refresh()

    def move_rows(self, direction):
        selected, moved = move_selected_rows(self.rows, map(int, self.tree.selection()), direction)
        if not moved:
            return
        self.dirty = True
        self.refresh()
        self.tree.selection_set(*map(str, selected))
        active = str(selected[0] if direction < 0 else selected[-1])
        self.tree.focus(active)
        self.tree.see(active)

    def save(self):
        if not self.dirty:
            return
        try:
            rows = [self.header] + self.rows if self.header is not None else self.rows
            self.original = save_document(self.path, self.original, self.encoding, rows, self.newline)
        except Exception as exc:
            messagebox.showerror('保存できません', str(exc), parent=self)
            return
        self.dirty = False
        self.refresh()
        self.on_saved()
        messagebox.showinfo('保存完了', 'CSVを保存し、元ファイルのバックアップを作成しました。', parent=self)

    def close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel('未保存の変更', '変更を保存して閉じますか？', parent=self)
            if answer is None:
                return
            if answer:
                self.save()
                if self.dirty:
                    return
        self.destroy()

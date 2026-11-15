"""Staged rectangular edits; all operations validate before changing the draft."""
import csv
import io


class Draft:
    def __init__(self, rows):
        self.original = [list(r) for r in rows]
        self.rows = [list(r) for r in rows]
        self.undo_stack = []

    def apply(self, updates):
        updates = dict(updates)
        changes = []
        for (row, column), value in updates.items():
            if row < 0 or row >= len(self.rows) or column < 0 or column >= len(self.rows[row]):
                raise ValueError('貼り付け先が表の範囲を超えています。行数・列数は増やせません。')
            old = self.rows[row][column]
            if value == old:
                continue
            if column == 0 or (row == 0 and column != 6):
                raise ValueError('種別・チップID・見出しは変更できません。先頭行ではGAPだけ編集できます。')
            if any(c in value for c in ',\r\n\x00\v\f\x85\u2028\u2029'):
                raise ValueError('カンマ・改行・制御文字は保存できません。')
            value.encode('cp932')
            if column == 6 and (row == 0 or self.rows[row][0] == 'Experiment'):
                if not value.isascii() or not value.isdigit():
                    raise ValueError('GAP # は0以上の半角整数で指定してください。')
            changes.append((row, column, old, value))
        if changes:
            self.undo_stack.append(changes)
            for row, column, old, value in changes:
                self.rows[row][column] = value

    def paste(self, row, column, text):
        cells = list(csv.reader(io.StringIO(text), delimiter='\t'))
        self.apply([((row+i, column+j), value) for i, line in enumerate(cells) for j, value in enumerate(line)])

    def replace(self, rows, column, old, new, fill=False):
        updates = []
        for row in rows:
            if column >= len(self.rows[row]) or column == 0 or (row == 0 and column != 6):
                continue
            if column == 6 and row != 0 and self.rows[row][0] != 'Experiment':
                continue
            if fill or self.rows[row][column] == old:
                updates.append(((row, column), new))
        self.apply(updates)

    def undo(self):
        if self.undo_stack:
            for row, column, old, value in self.undo_stack.pop():
                self.rows[row][column] = old

    def changes(self):
        return [(i, j, old, self.rows[i][j]) for i, row in enumerate(self.original)
                for j, old in enumerate(row) if old != self.rows[i][j]]

"""画面の呼び名のルール（docs/naming_rules.md）が守られていることの確認。

アプリのソースにある文字列（docstring とコメントは除く）に「使わない呼び名」が入っていないかを調べる。
共有CSV・保存データの列名そのもの（shared/naming.py の SCREEN のキー）と一致する文字列は、データの名前なので許す。
Chip Atlas（chipdb）は移行が済んだら APP_DIRS に加える。
"""
import ast
import unittest
from pathlib import Path

from shared import naming

ROOT = Path(__file__).resolve().parents[1]
APP_DIRS = ('database', 'exmanager', 'Analyzer/python', 'scoring', 'shared')
APP_FILES = ('launcher.py',)
SKIP = {'naming.py'}

# 使わない呼び名 -> 使う呼び名
BANNED = {
    '実験名': 'プロジェクト名',
    '実験フォルダ': 'プロジェクトフォルダ、または実験ID（測定フォルダ）',
    '保存先実験': '保存先プロジェクト',
    '元実験': '実験ID',
    '実験一覧': 'プロジェクト一覧、または実験ID一覧',
    '試料': 'サンプル',
    '担当者': '責任者・測定者・転送者・作成者',
}
DATA_NAMES = set(naming.SCREEN)


def source_files():
    for folder in APP_DIRS:
        for path in sorted((ROOT / folder).rglob('*.py')):
            if '__pycache__' in path.parts or path.name.startswith(('test_', 'validate_')) or path.name in SKIP:
                continue
            yield path
    for name in APP_FILES:
        yield ROOT / name


def docstrings(tree):
    nodes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    found = set()
    for node in nodes:
        body = getattr(node, 'body', [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            found.add(id(body[0].value))
    return found


def violations(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    skip = docstrings(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in skip:
            continue
        if node.value in DATA_NAMES:
            continue
        for word, use in BANNED.items():
            if word in node.value:
                yield f'{path.relative_to(ROOT)}:{node.lineno}: 「{word}」→ {use}: {node.value[:60]!r}'


class NamingTests(unittest.TestCase):
    def test_screen_strings_use_the_shared_names(self):
        found = [v for path in source_files() for v in violations(path)]
        self.assertEqual(found, [], '\n' + '\n'.join(found))

    def test_screen_titles_keep_data_columns(self):
        titles = ('実験名', 'サンプル名', '元の名前')
        self.assertEqual(naming.screen_titles(titles), ('プロジェクト名', 'サンプル名', '実験ID（元の名前）'))
        self.assertEqual(titles[0], '実験名')


if __name__ == '__main__':
    unittest.main()

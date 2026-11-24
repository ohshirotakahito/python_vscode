"""画面の呼び名（docs/naming_rules.md）。

共有CSV・保存データの列名は AXN などが名前で読むので変えない。同じ見出しを画面にも使っている表は、
画面に出すときだけ screen_titles で言い換える。
"""

# 列名（データ） -> 画面の呼び名
SCREEN = {
    '実験名': 'プロジェクト名',
    '転送先の実験名': '転送先のプロジェクト名',
    '元の名前': '実験ID（元の名前）',
    'グループ': '共有',              # 解析サーバーの共有（表示は QDserver/analysis）。SQserver の表では SQ_SCREEN
    '解析ルート': '保存先の共有',    # exmanager の .local/AnalysisRoots.csv の見出し
}

# SQserver の表の「グループ」は Recent_Data などの SQserver 上のフォルダ
SQ_SCREEN = {'グループ': 'SQserver フォルダ'}


def screen_titles(titles, overrides=None):
    """Column titles as shown on screen; titles without a screen name are kept. overrides wins over SCREEN."""
    names = {**SCREEN, **(overrides or {})}
    return tuple(names.get(title, title) for title in titles)

"""AXN リポジトリのスコア関数を、コピーせずにそのまま使う。

`ac.score` / `exsv_score.score` / `gg_review.evaluate` は Qt にも装置にも依存しない。基準を変えたとき
2 つのリポジトリでずれないよう、ここでは AXN の `axn` パッケージを import パスに足して読むだけにする
（引き継ぎ資料 docs/scoring_app_handoff.md「設計上のお願い」）。

AXN の場所: 環境変数 PYVSCODE_AXN、なければ D:\\GitHub\\AXN など（CANDIDATES）。
"""
from dataclasses import dataclass
import hashlib
import importlib
import os
from pathlib import Path
import sys

from shared import REPO_ROOT

CANDIDATES = (r'D:\GitHub\AXN', r'D:\github\AXN', r'C:\GitHub\AXN', str(REPO_ROOT.parent / 'AXN'))


class AxnNotFound(RuntimeError):
    pass


def axn_root():
    """AXN リポジトリのフォルダ（`axn/ac.py` があるもの）。見つからなければ None。"""
    env = os.environ.get('PYVSCODE_AXN')
    if env:
        return Path(env)
    for candidate in CANDIDATES:
        if (Path(candidate) / 'axn' / 'ac.py').is_file():
            return Path(candidate)
    return None


def commit_of(root):
    """`.git` から今のコミットの短い ID（git コマンドなし）。分からなければ ''。"""
    try:
        head = (root / '.git' / 'HEAD').read_text(encoding='utf-8').strip()
        if head.startswith('ref:'):
            ref = head.split(None, 1)[1]
            ref_file = root / '.git' / ref
            if ref_file.is_file():
                head = ref_file.read_text(encoding='utf-8').strip()
            else:
                head = ''
                for line in (root / '.git' / 'packed-refs').read_text(encoding='utf-8').splitlines():
                    if line.endswith(' ' + ref):
                        head = line.split()[0]
                        break
        return head[:7]
    except OSError:
        return ''


@dataclass(frozen=True)
class Modules:
    root: Path
    commit: str
    ac: object
    exsv_score: object
    gg_review: object
    gg_score: object
    data_locations: object = None     # 実験フォルダの探し方（照合用。古い AXN には無い）

    @property
    def criteria(self):
        """スコアの基準の版: AXN のコミットと、基準の定数の指紋（定数が変われば変わる）。"""
        return criteria_text(self)


def _fingerprint(*values):
    return hashlib.sha1(repr(values).encode('utf-8')).hexdigest()[:6]


def criteria_text(mods):
    ac, ex = mods.ac, mods.exsv_score
    ac_fp = _fingerprint(ac.SCORE_G0_WIDE, ac.SCORE_G0_ONE, ac.SCORE_HIGH_A, ac.SCORE_HIGH_S, ac.SCORE_LOW_A,
                         ac.SCORE_LOW_S, ac.SCORE_LAST_PA, ac.CUT_KA1, ac.CUT1_S, ac.POINTS_PER_S)
    exsv_fp = _fingerprint(ex.PARTS)
    return f'axn {mods.commit or "?"}; ac {ac_fp}; exsv {exsv_fp}; gg {mods.gg_review.VERSION}'


_loaded = None


def load(root=None):
    """AXN のスコアモジュールを読み込む（1 度だけ）。root を渡すとその場所を使う。"""
    global _loaded
    if _loaded is not None and root is None:
        return _loaded
    explicit = root is not None
    root = Path(root) if explicit else axn_root()
    if root is None or not (root / 'axn' / 'ac.py').is_file():
        raise AxnNotFound('AXN リポジトリが見つかりません。D:\\GitHub\\AXN に clone するか、'
                          '環境変数 PYVSCODE_AXN でフォルダを指定してください')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        data_locations = importlib.import_module('axn.data_locations')
    except ImportError:
        data_locations = None
    mods = Modules(root, commit_of(root), importlib.import_module('axn.ac'), importlib.import_module('axn.exsv_score'),
                   importlib.import_module('axn.gg_review'), importlib.import_module('axn.gg_score'), data_locations)
    if not explicit:
        _loaded = mods
    return mods

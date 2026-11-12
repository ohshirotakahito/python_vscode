"""machinedb（装置・SEQ 履歴・SEQlist・BC・サンプル台帳）の置き場所。全アプリがここだけを参照する。

共有フォルダ D:\\Qstatus\\machinedb（Synology で各 PC と同期）。2026-11-10 に Dropbox（ou&t）から移した。
LabVIEW も同じ場所を読むので、SEQlist / BC はフォルダ分けせずに置く（核酸・アミノ酸の区別は中身で判定する: shared.bnal_lists）。
Qstatus のない PC だけ、従来の Dropbox を使う。場所を決め打ちしたいときは環境変数 PYVSCODE_MACHINEDB で指定する。
"""
import os
from pathlib import Path

_CANDIDATES = (
    r'D:\Qstatus\machinedb', r'C:\Qstatus\machinedb', r'E:\Qstatus\machinedb',
    r'D:\My_Dropbox\ou&t\machinedb',   # Qstatus 移行前の置き場所
)


def _find_root() -> Path:
    if os.environ.get('PYVSCODE_MACHINEDB'):
        return Path(os.environ['PYVSCODE_MACHINEDB'])
    for candidate in _CANDIDATES:
        if Path(candidate).is_dir():
            return Path(candidate)
    return Path(_CANDIDATES[0])


ROOT = _find_root()
SEQ = ROOT / 'SEQ'                # Machine_N.csv・Uploader.csv（実験データ整理の履歴）
SEQLIST = ROOT / 'SEQlist'        # BNAL の target リスト（核酸・ペプチド）
BC = ROOT / 'BC'                  # 塩基・アミノ酸のコンダクタンス値（相対レベル）
SAMPLE_PLAN = ROOT / 'SamplePlan' / 'sample_plan.json'  # サンプル台帳（shared.sample_plan）

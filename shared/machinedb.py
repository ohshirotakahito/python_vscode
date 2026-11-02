"""machinedb（装置・SEQ 履歴・SEQlist・BC・サンプル台帳）の置き場所。全アプリがここだけを参照する。

元は D:\\My_Dropbox\\ou&t\\machinedb（Dropbox で各 PC と共有）。LabVIEW も移行期間中は同じ場所を読むので、
SEQlist / BC はフォルダ分けせずに置く（核酸・アミノ酸の区別は中身で判定する: shared.bnal_lists）。
別の PC で場所が違うときは環境変数 PYVSCODE_MACHINEDB で指定する。
"""
import os
from pathlib import Path

ROOT = Path(os.environ.get('PYVSCODE_MACHINEDB') or r'D:\My_Dropbox\ou&t\machinedb')
SEQ = ROOT / 'SEQ'                # Machine_N.csv・Uploader.csv（実験データ整理の履歴）
SEQLIST = ROOT / 'SEQlist'        # BNAL の target リスト（核酸・ペプチド）
BC = ROOT / 'BC'                  # 塩基・アミノ酸のコンダクタンス値（相対レベル）
SAMPLE_PLAN = ROOT / 'SamplePlan' / 'sample_plan.json'  # サンプル台帳（shared.sample_plan）

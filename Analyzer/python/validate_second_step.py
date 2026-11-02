"""Run the 2nd step on an A@@ file and compare the written B@@ file with LabVIEW's.

Usage: python validate_second_step.py <A@@...tdms> <LabVIEW B@@...tdms> [OUTDIR]

The settings are taken from the BC Table of the LabVIEW file.  Every table
value is compared as the string LabVIEW wrote; numbers that differ only by one
unit in the last digit (LabVIEW rounding) are reported separately.
"""
import sys
import tempfile

import numpy as np
import pandas as pd
from nptdms import TdmsFile

from second_step import condition_from_bc_table
from second_step_io import process_file

# values that differ by design (time of analysis, version, LabVIEW stale value bug)
EXPECTED = {"BR Table": {"RR MIN Over S# [n]", "Assembly Ratio 2 [%]", "RR Ratio [%]",
                         "BNAL analysis time [xx:xx:xx]", "BNAL Programe version", "BNAL update Date"}}


def lsb(a, b):
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False
    dec = len(b.split(".")[1]) if "." in b else 0
    return abs(x - y) <= 1.01 * 10.0 ** -dec


def main(a_path, b_path, out_dir=None):
    lv = TdmsFile.read(b_path)
    bc = pd.DataFrame({c.name: c[:] for c in lv["BC Table"].channels()})
    out, _ = process_file(a_path, condition_from_bc_table(bc), out_dir or tempfile.mkdtemp())
    py = TdmsFile.read(out)
    print("written", out)
    print("groups equal:", [g.name for g in py.groups()] == [g.name for g in lv.groups()])
    for g in lv.groups():
        pc = {c.name: list(c[:]) for c in py[g.name].channels()}
        lc = {c.name: list(c[:]) for c in g.channels()}
        n = max((len(v) for v in lc.values()), default=0)
        issues = []
        if list(pc) != list(lc):
            issues.append(f"columns differ: {set(pc) ^ set(lc)}")
        for col, b in lc.items():
            a = pc.get(col)
            if a is None or len(a) != len(b):
                issues.append(f"{col!r}: length py={None if a is None else len(a)} LV={len(b)}")
                continue
            bad = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
            if not bad:
                continue
            tag = " (expected)" if col in EXPECTED.get(g.name, ()) else ""
            if all(lsb(a[i], b[i]) for i in bad):
                issues.append(f"{col!r}: {len(bad)} rows differ by 1 LSB")
            else:
                i = bad[0]
                issues.append(f"{col!r}: MISMATCH {len(bad)}/{len(b)} e.g. py={a[i]!r} LV={b[i]!r}{tag}")
        print(f"== {g.name} ({n} rows): " + ("OK" if not issues else ""))
        for s in issues:
            print("   " + s)


if __name__ == "__main__":
    main(*sys.argv[1:])

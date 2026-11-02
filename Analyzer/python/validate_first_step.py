"""Compare first_step.py against a LabVIEW 1st step output TDMS (A@@...tdms)."""
import sys
import numpy as np
from nptdms import TdmsFile

from first_step import first_step

# table -> [(python column, LabVIEW column)]
COLUMNS = {
    "R Table": [
        ("Region BL [pA]", "Region BL [pA]"),
        ("Region STD [pA]", "Region STD [pA]"),
        ("R Signal Count [n]", "R Signal Count [n]"),
        ("R BL Min Mode Peak [pA]", "R BL Min Mode Peak [ms]"),
        ("R H#1 [n]", "R H#1 [n]"),
        ("R H#2 [n]", "R H#2 [n]"),
        ("R H#3 [n]", "R H#3 [n]"),
    ],
    "S Table": [
        ("S Peak Position [s]", "S Peak Position [s]"),
        ("Signal [pA]", "Signal [pA]"),
        ("Region BL [pA]", "Region BL [pA]"),
        ("Region STD [pA]", "Region STD [pA]"),
        ("Threshold [pA]", "Threshold [pA]"),
        ("Signal S [s]", "Signal S [s]"),
        ("Signal E [s]", "Signal E (s)"),
        ("S TL [ms]", "S TL [ms]"),
        ("S DL [s]", "S DL [s]"),
        ("RT BL [pA]", "RT BL [pA]"),
        ("H1 [n]", "H1 [n]"),
        ("H2 [n]", "H2 [n]"),
    ],
    "B Table": [
        ("B Start [s]", "B Start [s]"),
        ("B End [s]", "B End [s]"),
        ("B time length [ms]", "B time length [ms]"),
        ("B BL [pA]", "B BL [pA]"),
        ("B STD [pA]", "B STD [pA]"),
        ("Region BL [pA]", "Region BL [pA]"),
        ("Region STD [pA]", "Region STD [pA]"),
        ("R # [n]", "R # [n]"),
        ("R S_IDX [n]", "R S_IDX [n]"),
        ("R E_IDX [n]", "R E_IDX [n]"),
        ("B Signal Count [n]", "B Signal Count [n]"),
        ("B Signal MAX [pA]", "B Signal MAX [pA]"),
        ("B Signal Average [pA]", "B Signal Average [n]"),
        ("S S_IDX [n]", "S S_IDX [n]"),
        ("S E_IDX [n]", "S E_IDX [n]"),
        ("B H1 [n]", "B H1 [n]"),
        ("B H2 [n]", "B H2 [n]"),
    ],
    "AR Table": [
        ("Burst#Sum", "Burst#Sum"),
        ("Signal#Sum", "Signal#Sum"),
        ("R B_SCount [n] in", "R B_SCount [n] in"),
        ("B Region Time [s]", "B Region Time [s]"),
        ("Signal Freq [/s]", "Signal Freq [/s]"),
        ("Datapoint", "Datapoint"),
        ("Region STD Mode [pA]", "Region STD Mode [pA]"),
        ("Region BL Mode [pA]", "Region BL Mode [pA]"),
        ("Region Count Mode [a]", "Region Count Mode [a]"),
        ("B Duty Cycle [%]", "B Duty Cycle [%]"),
        ("Total Time [s]", "Total Time [s]"),
    ],
}


def main(path):
    t = TdmsFile.read(path)
    tables = first_step(t["Data"]["Ch1"][:])

    for name, cols in COLUMNS.items():
        py = tables[name]
        lv = {c.name: np.asarray(c[:], dtype=object) for c in t[name].channels()}
        n_lv = len(next(iter(lv.values())))
        print(f"== {name}: rows py={len(py)} LV={n_lv}")
        if len(py) != n_lv:
            print("   row count differs; skipping column comparison")
            continue
        for py_col, lv_col in cols:
            d = np.abs(py[py_col].to_numpy(dtype=float) - lv[lv_col].astype(float))
            print(f"   {py_col:24s} max|diff| = {d.max():.5f}")


if __name__ == "__main__":
    main(sys.argv[1])

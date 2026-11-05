"""Analysis condition sets of the server pages (ANAL = 1st step, BNAL = 2nd step), with their dates.

One set is the current one for each kind. A new set is added with the date it starts being used and becomes the
current one; older sets stay and can be chosen again. The built-in default (the LabVIEW values) cannot be
changed. Every change is kept in the history of Analyzer/conditions/conditions.json (tracked by Git).

    {"anal": {"current": name, "sets": [{"name", "since", "values", "note", "operator", "created"}]},
     "bnal": {...},
     "history": [{"time", "kind", "action": "追加" | "現在にする", "name", "operator"}]}

The name of the set used is written with every result (A@@ / B@@ root property, the run records), so the
conditions of a result can be looked up here.
"""
import json
import os
import socket
from datetime import date, datetime
from pathlib import Path

from first_step import DEFAULT_CONDITION as FIRST_DEFAULT
from second_step import DEFAULT_CONDITION as SECOND_DEFAULT

STORE = Path(__file__).resolve().parent.parent / "conditions" / "conditions.json"
KINDS = {"anal": "ANAL（1st Step）", "bnal": "BNAL（2nd Step）"}
DEFAULT_NAMES = {"anal": "既定（LabVIEW 20180816）", "bnal": "既定（LabVIEW 20180801）"}

# (key, label, type, range or choices) of the values a set holds
FIELDS = {
    "anal": [("point", "Filter Point (±)", int, (1, 100000)),
             ("cut_region", "Cut Region", int, (1, 100000)),
             ("bl_selection", "BL Selection", str, ["Div Mode", "All Mode", "Div Mean"]),
             ("std_selection", "STD Selection", str, ["Div STD Mode", "All STD", "Div STD Mean"]),
             ("d_th_xn", "D Th (STD ×)", float, (0, 100)),
             ("u_th_xn", "U Th (STD ×)", float, (0, 100)),
             ("max_d_time_ms", "MAX D time [ms]", float, (0, 1000)),
             ("max_es_time_ms", "MAX E_S time [ms]", float, (0, 1000)),
             ("read_region_point", "Read Region Point", int, (100, 10000000)),
             ("std_smooth_point", "STD smooth Point", int, (0, 10000))],
    "bnal": [("right_read_min", "Right Read MIN", int, (1, 100)),
             ("noise_max", "noise MAX", int, (0, 100)),
             ("filter_p", "Filter P", int, (0, 100))],
}
# BNAL sets also keep the BC (conductance table) file per list kind: values["bc_files"] = {kind: file name}
DEFAULT_VALUES = {"anal": {k: FIRST_DEFAULT[k] for k, *_ in FIELDS["anal"]},
                  "bnal": {**{k: SECOND_DEFAULT[k] for k, *_ in FIELDS["bnal"]}, "bc_files": {}}}


def default_set(kind):
    return {"name": DEFAULT_NAMES[kind], "since": "", "values": dict(DEFAULT_VALUES[kind]),
            "note": "LabVIEW の既定値（変更できません）", "operator": "", "created": "", "builtin": True}


def load(path=None):
    path = Path(path or STORE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    for kind in KINDS:
        data.setdefault(kind, {"current": DEFAULT_NAMES[kind], "sets": []})
    data.setdefault("history", [])
    return data


def save(data, path=None):
    path = Path(path or STORE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def sets(kind, path=None):
    """The default first, then the saved sets oldest first."""
    return [default_set(kind)] + load(path)[kind]["sets"]


def find(kind, name, path=None):
    return next((s for s in sets(kind, path) if s["name"] == name), None)


def current(kind, path=None):
    data = load(path)
    return find(kind, data[kind]["current"], path) or default_set(kind)


def _operator():
    import first_step_io
    return first_step_io.OPERATOR


def _log(data, kind, action, name):
    data["history"].append({"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
                            "action": action, "name": name, "operator": _operator(), "pc": socket.gethostname()})


def add_set(kind, name, values, note="", since=None, path=None):
    """Save a new set (used from `since`, default today) and make it the current one."""
    name = name.strip()
    if not name:
        raise ValueError("名前を入れてください")
    if find(kind, name, path):
        raise ValueError(f"同じ名前の条件セットがあります: {name}")
    data = load(path)
    data[kind]["sets"].append({"name": name, "since": since or date.today().isoformat(), "values": clean(kind, values),
                               "note": note, "operator": _operator(),
                               "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    data[kind]["current"] = name
    _log(data, kind, "追加", name)
    save(data, path)
    return find(kind, name, path)


def set_current(kind, name, path=None):
    if not find(kind, name, path):
        raise ValueError(f"条件セットがありません: {name}")
    data = load(path)
    if data[kind]["current"] != name:
        data[kind]["current"] = name
        _log(data, kind, "現在にする", name)
        save(data, path)


def clean(kind, values):
    """The values of FIELDS (typed, inside their range); BNAL also keeps bc_files."""
    out = {}
    for key, _, typ, spec in FIELDS[kind]:
        v = values.get(key, DEFAULT_VALUES[kind][key])
        if typ is str:
            if v not in spec:
                raise ValueError(f"{key}: {v} は選べません（{', '.join(spec)}）")
        else:
            v = typ(v)
            if not spec[0] <= v <= spec[1]:
                raise ValueError(f"{key}: {v} は範囲外です（{spec[0]}〜{spec[1]}）")
        out[key] = v
    if kind == "bnal":
        out["bc_files"] = {k: str(v) for k, v in (values.get("bc_files") or {}).items() if v}
    return out


def differences(kind, values, base=None):
    """[(key, value, base value)] where values differ from base (default: the built-in default)."""
    base = base or DEFAULT_VALUES[kind]
    return [(k, values.get(k), base.get(k)) for k, *_ in FIELDS[kind] if values.get(k) != base.get(k)]


def describe(kind, s):
    return s["name"] + (f"（{s['since']} から）" if s.get("since") else "")

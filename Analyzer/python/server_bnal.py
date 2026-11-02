"""2nd Step (BNAL) on the analysis servers.

Samples, their ANAL counts and BNAL@<target> counts come from the experiment file database (read-only);
the chosen sample x target pairs are listed on the server right before the run.

    <root>/<experiment>/<sample>/<sample>_10k_Sample/T/ANAL/A@@<time>_<sample>#NNN.tdms
                                                   T/BNAL@<target>/B@@<time>_<sample>#NNN.tdms

B@@ keeps the ANAL '#NNN' number, so a target resumes by analysing the ANAL numbers that have no B@@ yet.
ANAL/stocked (ANAL set aside) is not analysed; it is only reported.
"""
import csv
import os
import re
import shutil
import sqlite3
from pathlib import Path

from first_step import long_path
from server_anal import DEFAULT_DB, anal_key, identity, load_samples, t_dir, progress  # noqa: F401  (re-exported)

from shared import bnal_lists, machinedb, sample_plan  # noqa: E402

# target lists (SEQlist) and conductance tables (BC) are read from machinedb, unsorted as LabVIEW keeps them;
# nucleotide / peptide is told from their contents (shared.bnal_lists)
SEQ_DIR, BC_DIR = machinedb.SEQLIST, machinedb.BC
SEQ_KINDS, AMINO_ONLY = bnal_lists.SEQ_KINDS, bnal_lists.AMINO_ONLY
_decode, read_targets, read_bc = bnal_lists.decode, bnal_lists.read_targets, bnal_lists.read_bc
is_amino, seq_kind, bc_kind = bnal_lists.is_amino, bnal_lists.seq_kind, bnal_lists.bc_kind
VARIANT = re.compile(r"_[NM]_\d+$")  # modified variants such as hsa-miR-3613-5p_M_8 (as the database app)


def parent_target(name):
    return VARIANT.sub("", name)


def base_table(seq, bc):
    """LabVIEW Base Table: the letters of the sample sequence in BC order, then B.
    Returns (bases {letter: level}, stds {letter: std}, missing letters)."""
    levels = {b: (lv, sd) for b, _, lv, sd in bc}
    letters = [b for b, *_ in bc if b != "B" and b in seq]
    missing = sorted(set(seq) - set(letters))
    chosen = letters + ["B"]
    bases = {b: levels.get(b, (0.0, 2.0))[0] for b in chosen}
    stds = {b: levels.get(b, (0.0, 2.0))[1] for b in chosen}
    return bases, stds, missing


# BNAL@<target> の状態（shared.progress.bnal_state と同じ判定。数えていないものを「対象なし」にしない）
BNAL_STATES = dict(complete="完了", partial="進行中", excess="超過", unstarted="未着手")
UNSTARTED, UNCONFIRMED, UNCOUNTED, NO_ANAL = "未着手", "未確認", "未計数", "ANALなし"


def combo_state(r, name, counts):
    """State of one sample x target: counts = {target: BNAL tdms count or None} saved for that share."""
    if name not in counts:
        # the T listing saw more BNAL@ folders than the saved names: the folder may exist (update the DB)
        return UNCONFIRMED if progress.bnal_names_missing(r, len(counts)) else UNSTARTED
    anal = None if r["anal_tdms"] is None else r["anal_tdms"] + (r["anal_stocked_tdms"] or 0)
    _, _, tag = progress.bnal_state(counts[name], anal)
    return BNAL_STATES.get(tag) or (UNCOUNTED if counts[name] is None else NO_ANAL)


def load_bnals(db_path=DEFAULT_DB):
    """{(root identity, experiment, sample): {target: tdms count or None}} from the saved database."""
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='rack_bnals'").fetchone():
            return {}
        out = {}
        for root, exp, sample, target, tdms in db.execute(
                "SELECT root, experiment, sample, target, tdms FROM rack_bnals"):
            out.setdefault((identity(root), exp, sample), {})[target] = tdms
        return out
    finally:
        db.close()


def _tdms(folder):
    try:
        with os.scandir(long_path(folder)) as it:
            return [e for e in it if e.is_file() and e.name.lower().endswith(".tdms")]
    except FileNotFoundError:
        return []


def anal_files(t):
    """{#NNN: newest A@@ path} of T/ANAL (the timestamp in the name decides between generations)."""
    out = {}
    for e in sorted(_tdms(Path(t) / "ANAL"), key=lambda e: e.name):
        if e.name.startswith("A@@"):
            out[anal_key(e.name)] = Path(t) / "ANAL" / e.name
    return out


def pending(t, target):
    """Live listing for one sample x target: dict(todo=[A@@ paths], done, anal, stocked, duplicates)."""
    t = Path(t)
    if not os.path.isdir(long_path(t)):
        raise FileNotFoundError(f"T フォルダが見つかりません: {t}")
    anal_names = [e.name for e in _tdms(t / "ANAL") if e.name.startswith("A@@")]
    anal = anal_files(t)
    done = {anal_key(e.name) for e in _tdms(t / f"BNAL@{target}") if e.name.startswith("B@@")}
    todo = [p for k, p in sorted(anal.items(), key=lambda kv: (isinstance(kv[0], str), str(kv[0]).zfill(6)))
            if k not in done]
    return dict(todo=todo, done=len(done & set(anal)), anal=len(anal), bnal=len(done),
                stocked=len(_tdms(t / "ANAL" / "stocked")), duplicates=len(anal_names) - len(anal))


def move_to_old(t, target):
    """Redo: move the existing B@@ files of BNAL@<target> into BNAL@<target>/old (names kept, never overwritten).
    Returns (moved, skipped)."""
    folder = Path(t) / f"BNAL@{target}"
    old = folder / "old"
    moved = skipped = 0
    for e in _tdms(folder):
        if not e.name.startswith("B@@"):
            continue
        os.makedirs(long_path(old), exist_ok=True)
        for name in (e.name, e.name + "_index"):
            src, dst = folder / name, old / name
            if not os.path.exists(long_path(src)):
                continue
            if os.path.exists(long_path(dst)):
                skipped += 1
                continue
            shutil.move(long_path(src), long_path(dst))
            moved += name == e.name
    return moved, skipped


# ---------------------------------------------------------------------------
# colours by chemical species
# ---------------------------------------------------------------------------
NUCLEIC_COLORS = {"G": "#3b82f6", "A": "#ef4444", "C": "#d946ef", "U": "#2dd4bf", "T": "#22c55e", "B": "#9ca3af"}
MODIFIED_COLORS = ["#f59e0b", "#fb923c", "#facc15", "#f472b6", "#a3e635", "#38bdf8", "#fda4af", "#c4b5fd"]
AMINO_CLASS = {  # 3-letter name -> class
    **dict.fromkeys(["Ala", "Val", "Leu", "Ile", "Met"], "hydrophobic"),
    **dict.fromkeys(["Phe", "Trp", "Tyr"], "aromatic"),
    **dict.fromkeys(["Lys", "Arg", "His"], "basic"),
    **dict.fromkeys(["Asp", "Glu"], "acidic"),
    **dict.fromkeys(["Ser", "Thr", "Asn", "Gln"], "polar"),
    "Cys": "cys", "Gly": "gly", "Pro": "pro",
}
CLASS_COLORS = {"hydrophobic": "#fbbf24", "aromatic": "#a78bfa", "basic": "#60a5fa", "acidic": "#f87171",
                "polar": "#34d399", "cys": "#fde047", "gly": "#cbd5e1", "pro": "#f9a8d4", "modified": "#fb923c"}
CLASS_NAMES = {"hydrophobic": "疎水性", "aromatic": "芳香族", "basic": "塩基性", "acidic": "酸性", "polar": "極性",
               "cys": "Cys", "gly": "Gly", "pro": "Pro", "modified": "修飾"}


def species(bc):
    """{letter: (colour, class label)} for one BC table: nucleobases by base (modified ones in their own
    colours), amino acids by side-chain class (phospho / methyl forms as modified)."""
    out = {"B": (NUCLEIC_COLORS["B"], "Baseline")}
    if is_amino(bc):
        for letter, name, *_ in bc:
            if letter == "B":
                continue
            cls = AMINO_CLASS.get(name, "modified")
            out[letter] = (CLASS_COLORS[cls], CLASS_NAMES[cls])
        return out
    k = 0
    for letter, name, *_ in bc:
        if letter in NUCLEIC_COLORS:
            out[letter] = (NUCLEIC_COLORS[letter], "核酸塩基" if letter != "B" else "Baseline")
        else:
            out[letter] = (MODIFIED_COLORS[k % len(MODIFIED_COLORS)], "修飾塩基")
            k += 1
    return out


# ---------------------------------------------------------------------------
# editing: BC tables and targets
# ---------------------------------------------------------------------------
def write_bc(path, src_path, levels):
    """Copy a BC csv with some relative levels replaced ({letter: level}); other columns are kept."""
    rows = list(csv.reader(_decode(src_path).splitlines()))
    for row in rows:
        if len(row) >= 5 and row[1].strip() in levels:
            row[4] = f"{levels[row[1].strip()]:g}"
    with open(path, "w", newline="", encoding="cp932", errors="replace") as f:
        csv.writer(f, lineterminator="\r\n").writerows(rows)


def write_targets(path, targets, append=False):
    """SEQlist csv rows name, sequence, length (append keeps the existing rows)."""
    mode = "a" if append else "w"
    with open(path, mode, newline="", encoding="cp932", errors="replace") as f:
        csv.writer(f, lineterminator="\r\n").writerows([name, seq, len(seq)] for name, seq in targets)


def variants(name, seq, letter, positions):
    """Modified variants named as the database app expects: <name>_<letter>_<position> (1-based)."""
    out = []
    for p in positions:
        if 1 <= p <= len(seq) and seq[p - 1] != letter:
            out.append((f"{name}_{letter}_{p}", seq[:p - 1] + letter + seq[p:]))
    return out


# ---------------------------------------------------------------------------
# condition history
# ---------------------------------------------------------------------------
HISTORY = Path(__file__).resolve().parent.parent / "logs" / "bnal_history.jsonl"


def add_history(entry, path=None):
    import json
    path = Path(path or HISTORY)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(path=None):
    """Run entries, newest first."""
    import json
    path = Path(path or HISTORY)
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[::-1]


# ---------------------------------------------------------------------------
# list kinds (Nucleotide / Peptide, Base / Amino acid)
# ---------------------------------------------------------------------------
def seq_dir(kind=None, root=None):
    """The SEQlist folder (one folder for every kind, as in machinedb)."""
    return Path(root or SEQ_DIR)


def bc_dir(seq_kind_name=None, root=None):
    return Path(root or BC_DIR)


def seq_files(kind, root=None):
    """SEQlist csv names of one kind ('Nucleotide' | 'Peptide') in the folder."""
    return list(bnal_lists.seq_files(seq_dir(kind, root), kind))


def bc_files(seq_kind_name, root=None):
    """BC csv names used with one SEQlist kind (Base for Nucleotide, Amino acid for Peptide)."""
    return list(bnal_lists.bc_files(bc_dir(seq_kind_name, root), seq_kind_name))


# ---------------------------------------------------------------------------
# samples to BNAL or not: the sample ledger in machinedb (shared.sample_plan), per experiment + sample
# ---------------------------------------------------------------------------
def sample_id(r):
    return sample_plan.key(r["experiment"], r["sample"])


def read_groups(path=None):
    """{sample id: 'target' | 'excluded'}; samples not listed are unclassified."""
    return {k: e["bnal"] for k, e in sample_plan.load(path).items() if e.get("bnal")}


def set_groups(changes, path=None):
    """changes = {sample id: 'target' | 'excluded' | None (unclassified)}; only these samples are written,
    so changes made meanwhile on another PC are kept. Returns the routing after the update."""
    sample_plan.update({k: {"bnal": v} for k, v in changes.items()}, path=path)
    return read_groups(path)

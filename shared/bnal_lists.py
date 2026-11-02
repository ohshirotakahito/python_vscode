"""BNAL の target リスト（SEQlist）とコンダクタンス値の表（BC）の読み込み（Analyzer・実験ファイルDB で共通）。

どちらも machinedb にフォルダ分けせずに置かれている（LabVIEW と同じ）。核酸かアミノ酸かは中身で判定する:
    SEQlist: アミノ酸にしかない文字（D E I K L P Q V）を使う配列が過半数なら Peptide、それ以外は Nucleotide
    BC     : アミノ酸の3文字名（Ala など）が10個以上あれば Amino acid、それ以外は Base
"""
import csv
import re
from pathlib import Path

from shared import machinedb

SEQ_KINDS = {'Nucleotide': 'Base', 'Peptide': 'Amino acid'}  # SEQlist の種類 -> 一緒に使う BC の種類
KIND_LABELS = {'Nucleotide': '核酸', 'Peptide': 'アミノ酸'}
AMINO_ONLY = set('DEIKLPQV')  # letters no nucleobase BC table uses
AMINO_NAMES = {'Ala', 'Val', 'Leu', 'Ile', 'Met', 'Phe', 'Trp', 'Tyr', 'Lys', 'Arg', 'His', 'Asp', 'Glu',
               'Ser', 'Thr', 'Asn', 'Gln', 'Cys', 'Gly', 'Pro'}


def decode(path):
    data = Path(path).read_bytes()
    for enc in ('utf-8-sig', 'cp932'):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode('cp932', errors='replace')


def read_targets(path):
    """SEQlist csv (no header): name, sequence[, length, ...] -> [(name, sequence)] in file order."""
    out, seen = [], set()
    for row in csv.reader(decode(path).splitlines()):
        if len(row) < 2:
            continue
        name, seq = row[0].strip(), re.sub(r'\s', '', row[1]).upper()
        if name and seq and re.fullmatch(r'[A-Z]+', seq) and name not in seen:
            seen.add(name)
            out.append((name, seq))
    return out


def read_bc(path):
    """BC csv: index, letter, name, colour, relative level, STD, ... -> [(letter, name, level, std)]."""
    out = []
    for row in csv.reader(decode(path).splitlines()):
        if len(row) < 5 or not row[1].strip():
            continue
        try:
            level = float(row[4])
        except ValueError:
            continue  # letters without a level are not usable
        try:
            std = float(row[5])
        except (IndexError, ValueError):
            std = 2.0
        out.append((row[1].strip(), row[2].strip(), level, std))
    return out


def is_amino(bc):
    return sum(name in AMINO_NAMES for _, name, *_ in bc) >= 10


def seq_kind(targets):
    """'Peptide' when most sequences use a letter that only amino acids have, else 'Nucleotide'."""
    peptide = sum(bool(set(s) & AMINO_ONLY) for _, s in targets)
    return 'Peptide' if targets and peptide * 2 > len(targets) else 'Nucleotide'


def bc_kind(bc):
    return 'Amino acid' if is_amino(bc) else 'Base'


def _files(folder, kind_of):
    out = {}
    for path in sorted(Path(folder).glob('*.csv'), key=lambda p: p.name.casefold()):
        try:
            out[path.name] = kind_of(path)
        except (OSError, ValueError):
            out[path.name] = None  # unreadable: listed as unknown, never silently dropped
    return out


def seq_files(folder=None, kind=None):
    """{csv name: 'Nucleotide' | 'Peptide' | None} in the SEQlist folder; with kind, only that kind."""
    files = _files(folder or machinedb.SEQLIST, lambda p: seq_kind(read_targets(p)))
    return {n: k for n, k in files.items() if kind is None or k == kind}


def bc_files(folder=None, seq_kind_name=None):
    """{csv name: 'Base' | 'Amino acid' | None} in the BC folder; with a SEQlist kind, only the BC used with it."""
    files = _files(folder or machinedb.BC, lambda p: bc_kind(read_bc(p)))
    want = SEQ_KINDS.get(seq_kind_name)
    return {n: k for n, k in files.items() if want is None or k == want}

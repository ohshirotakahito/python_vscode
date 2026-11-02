"""SEQlist の target CSV（BNAL の解析対象一覧）の読み込み（database と filemanager で共通）。"""
from pathlib import Path

from shared import machinedb

DEFAULT_TARGET_DIR = str(machinedb.SEQLIST)


def read_targets(path):
    """SEQlist CSVs have no header: name,sequence,length. Returns names in file order without duplicates."""
    data = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'cp932'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f'文字コードを判別できません: {path}')
    names = (line.split(',', 1)[0].strip() for line in text.splitlines())
    return list(dict.fromkeys(name for name in names if name))


def list_target_csvs(folder):
    return sorted((p.name for p in Path(folder).glob('*.csv') if p.is_file()), key=str.casefold)

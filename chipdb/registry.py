"""AXN schema-v1 chip master, sharing its revision and chip_info.lock protocol."""
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

from chipdb.summary import summarize

STATES = ('未設定', '作製予定', '保管中', '使用中', '使用終了')
FIELDS = {'state': '状態', 'location': '保管場所', 'user': '使用者', 'origin': '作成元',
          'fabricated_on': '作製日', 'use_started_on': '使用開始日', 'disposed_on': '廃棄日',
          'description': '説明・メモ'}
EXTRA = ('last_used_on', 'last_machine', 'resistance_ohm', 'gap_count', 'run_count')


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def identity(code):
    return normalize_code(code).casefold()


def normalize_code(code):
    return re.sub(r'[_=\u30fc\uff0d\uff1d\u2010-\u2015]', '-', code.strip())


def valid_code(code):
    code = code.strip()
    if not code or code.endswith('.') or any(c in code for c in ',<>:"/\\|?*') or any(ord(c) < 32 for c in code):
        raise ValueError('チップIDに空欄・カンマ・改行・ファイル名に使えない文字は指定できません。')
    code.encode('cp932')
    return code


def metadata(values):
    if set(values) - FIELDS.keys():
        raise ValueError('未対応の管理項目です。')
    if any(not isinstance(v, str) for v in values.values()):
        raise ValueError('管理情報は文字列で指定してください。')
    if values.get('state', '未設定') not in STATES:
        raise ValueError('状態が不正です。')
    for field in ('fabricated_on', 'use_started_on', 'disposed_on'):
        if values.get(field):
            try:
                if date.fromisoformat(values[field]).isoformat() != values[field]:
                    raise ValueError()
            except ValueError:
                raise ValueError(f'{FIELDS[field]}は YYYY-MM-DD の形で指定してください。') from None
    return values


def validate(data):
    if not isinstance(data, dict) or data.get('schema_version') != 1 or type(data.get('revision')) is not int or not isinstance(data.get('chips'), list):
        raise ValueError('対応していないチップ台帳形式です。')
    names, keys = set(), set()
    for chip in data['chips']:
        if not isinstance(chip, dict) or not isinstance(chip.get('code'), str) or not isinstance(chip.get('key'), str):
            raise ValueError('チップ台帳の項目が不正です。')
        code = valid_code(chip['code'])
        if code.casefold() in names or chip['key'] in keys or chip.get('state') not in STATES:
            raise ValueError('チップ台帳に重複または不正な状態があります。')
        names.add(code.casefold())
        keys.add(chip['key'])
    return data


def find(data, code):
    candidates = [chip for chip in data['chips'] if identity(chip['code']) == identity(code)]
    if len(candidates) > 1:
        raise ValueError('表記違いのチップが複数あります。台帳を確認してください。')
    return candidates[0] if candidates else None


def snapshot(path):
    body = path.read_bytes()
    text = body.decode('cp932')
    summary = summarize([line.split(',') for line in text.splitlines()])
    # AXN keeps different key spelling from CT column labels.
    renamed = {('Machine Last' if k == 'Machine' else 'Operator Last' if k == 'Operator'
                else 'M/T LAST [um/V]' if k == 'M/T Last'
                else k.replace(' Last', ' LAST') if k in ('CAL Last', 'FC Last', 'MA Last', 'TA Last') else k): v
               for k, v in summary.items()}
    return dict(path=str(path), sha256=hashlib.sha256(body).hexdigest(), text=text,
                summary=renamed, error='', missing=False, imported_at=stamp())


class Store:
    def __init__(self, db):
        self.db = Path(db)
        self.path = self.db.parent.parent / 'machinedb' / 'PLAN' / 'chip_info.json'

    def load(self):
        try:
            return validate(json.loads(self.path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            return dict(schema_version=1, revision=0, chips=[])

    def save_chip(self, code, values, expected_revision, *, new=False):
        code = valid_code(normalize_code(code) if new else code)
        metadata(values)
        if not self.db.is_dir():
            raise ValueError('先にChipDB/DBがある共有フォルダを選択してください。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix('.lock')
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise OSError('別のチップ台帳保存処理があります。再読み込みしてください。') from None
        try:
            os.close(fd)
            if any(p != self.path for p in self.path.parent.glob('chip_info*.json')):
                raise OSError('チップ台帳の競合ファイルがあります。確認してください。')
            data = self.load()
            if data['revision'] != expected_revision:
                raise ValueError('台帳が外部で更新されました。再読み込みして編集し直してください。')
            chip = find(data, code)
            sources = [p for p in self.db.glob('*.csv') if identity(p.stem) == identity(code)]
            if new and (chip or sources):
                raise ValueError('このチップは既に存在します。管理情報の編集を使ってください。')
            if chip is None:
                chip = dict(key=uuid4().hex, code=code, state='未設定', location='', user='', origin='',
                            fabricated_on='', use_started_on='', disposed_on='', description='',
                            snapshot=None, created_at=stamp())
                data['chips'].append(chip)
            for field in EXTRA:
                chip.setdefault(field, '')
            chip.update(values)
            chip['updated_at'] = stamp()
            if sources:
                if len(sources) != 1 or sources[0].stem != chip['code']:
                    raise ValueError('チップIDと履歴ファイルの表記違いを確認してください。')
                chip['snapshot'] = snapshot(sources[0])
            data['revision'] += 1
            data['updated_at'] = stamp()
            validate(data)
            if self.path.exists():
                folder = self.path.parent / 'backups'
                folder.mkdir(exist_ok=True)
                shutil.copy2(self.path, folder / f'chip_info_{uuid4().hex}.json')
            temporary = self.path.with_name(f'.chip_info_{uuid4().hex}.tmp')
            try:
                temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            return data
        finally:
            lock.unlink(missing_ok=True)

"""計測の計画（AXN の Ex Info・G PSET が書く）と、解析サーバーへの転送の記録。

計測PC（AXN）の General PSET は、測定フォルダ（= Experimental ID。SQserver でも同じ名前）を
Ex Info の実験・サンプルに結び付けて machinedb\\PLAN\\MeasurementLinks.jsonl に 1 行ずつ足す。
送り先（解析ルート\\実験\\サンプル）はその行の destination、なければ Ex Info（PLAN\\ex_info.json）の
そのサンプルのメインの保存場所。AXN の Upload Check（axn/upload_check.py の read_plans）と同じ決め方にしている。
ここでは読むだけで、台帳には書かない（書くのは AXN）。

転送の記録（machinedb\\SEQ\\Transfers.jsonl）は exmanager が転送を終えたときに 1 実験 × 1 送り先で 1 行足す。
17 列の Uploader CSV（seq_records）にはない、解析サーバーの共有・送り先の完全なパス・Ex Info のキーを持つ。
書くのは転送担当の PC だけなので、同期の競合は起きにくい。
"""
import json
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PureWindowsPath
from uuid import uuid4

from shared import machinedb

PLAN_DIR = machinedb.ROOT / 'PLAN'
LINKS = PLAN_DIR / 'MeasurementLinks.jsonl'
LEDGER = PLAN_DIR / 'ex_info.json'
TRANSFERS = machinedb.SEQ / 'Transfers.jsonl'


def path_key(path):
    """Compare roots the way Windows does: case and separators ignored, no trailing separator."""
    return str(path or '').replace('/', '\\').rstrip('\\').casefold()


def split_root(path):
    r"""'\\server\share\Exp\S1' -> ('\\server\share', 'Exp', 'S1'), or None when it is not that shape."""
    parts = [p for p in str(path or '').replace('/', '\\').split('\\') if p]
    if not str(path or '').startswith(('\\\\', '//')) or len(parts) != 4:
        return None
    return '\\\\' + '\\'.join(parts[:2]), parts[2], parts[3]


@dataclass(frozen=True)
class Planned:
    """The plan of one measurement folder (= Experimental ID)."""
    experimental_id: str
    project: str
    sample: str
    kind: str                 # Sample / Blank
    project_key: str = ''
    sample_key: str = ''
    link_key: str = ''
    root: str = ''            # \\server\share; '' when the analysis server is not decided
    experiment_folder: str = ''
    sample_folder: str = ''
    assigned_at: str = ''
    by: str = ''

    @property
    def decided(self):
        return bool(self.root and self.experiment_folder and self.sample_folder)

    @property
    def destination(self):
        r"""'\\server\share\Exp\S1', or '' when not decided."""
        if not self.decided:
            return ''
        return str(PureWindowsPath(self.root) / self.experiment_folder / self.sample_folder)

    @property
    def text(self):
        if self.decided:
            return f'{self.destination} [{self.kind}]'
        return f'{self.project} / {self.sample} [{self.kind}]（保存先未定）'

    def matches(self, root, experiment, sample, kind):
        """Is (root, experiment, sample, kind) where this measurement was planned to go? None when not decided."""
        if not self.decided:
            return None
        return (path_key(root) == path_key(self.root)
                and experiment.casefold() == self.experiment_folder.casefold()
                and sample.casefold() == self.sample_folder.casefold()
                and kind == self.kind)


def main_location(locations):
    """The sample's main location: the one marked is_main, else the only one (as AXN's ex_info.main_location)."""
    mains = [loc for loc in locations if loc.get('is_main', False)]
    if len(mains) == 1:
        return mains[0]
    return locations[0] if len(locations) == 1 else None


def _read_lines(path):
    try:
        text = path.read_text(encoding='utf-8-sig')
    except FileNotFoundError:
        return []
    out = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue          # a line cut by a sync in progress; the next read sees it whole
        if isinstance(record, dict):
            out.append(record)
    return out


def load_plans(links=None, ledger=None):
    """{Experimental ID (measurement folder name): Planned}; the last link of a name wins.

    The Ex Info ledger is only needed for links without their own destination; when it cannot be read,
    those measurements are 'not decided' rather than an error (the transfer still works by hand)."""
    links = LINKS if links is None else links
    ledger = LEDGER if ledger is None else ledger
    records = _read_lines(links)
    if not records:
        return {}
    try:
        data = json.loads(ledger.read_text(encoding='utf-8-sig'))
        locations = data.get('locations', []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        locations = []
    by_key = {loc.get('key'): loc for loc in locations if loc.get('key')}
    out = {}
    for link in records:
        name = link.get('experimental_id')
        if not name:
            continue
        loc = by_key.get(link.get('location_key')) if link.get('location_key') else None
        if loc is None and link.get('destination'):
            parts = split_root(link['destination'])
            loc = dict(zip(('root_path', 'experiment_folder', 'sample_folder'), parts)) if parts else None
        if loc is None and not link.get('destination'):
            loc = main_location([l for l in locations if l.get('sample_key') == link.get('sample_key')])
        loc = loc or {}
        out[name] = Planned(
            experimental_id=name, project=link.get('project', ''), sample=link.get('sample', ''),
            kind=link.get('kind', ''), project_key=link.get('project_key', ''),
            sample_key=link.get('sample_key', ''), link_key=link.get('key', ''),
            root=str(loc.get('root_path', '')).rstrip('\\/'), experiment_folder=loc.get('experiment_folder', ''),
            sample_folder=loc.get('sample_folder', ''), assigned_at=link.get('assigned_at', ''),
            by=link.get('by', ''))
    return out


@dataclass(frozen=True)
class Transfer:
    """One finished transfer: a measurement folder copied to one destination sample."""
    machine: str
    source: str                # measurement folder name = Experimental ID
    selection: str             # Recent_Data / Archived_Data
    root: str                  # \\server\share
    experiment: str
    sample: str
    kind: str                  # Sample / Blank
    uploader: str = ''
    planned: bool | None = None  # went where Ex Info planned; None when there was no decided plan
    project_key: str = ''
    sample_key: str = ''
    link_key: str = ''
    pc_id: str = ''
    pc_name: str = ''
    manifest: str = ''
    transferred_at: str = ''
    key: str = ''

    @property
    def destination(self):
        return str(PureWindowsPath(self.root) / self.experiment / self.sample)


def transfer_record(machine, source, selection, root, experiment, sample, kind, plan=None, **extra):
    """A Transfer of one finished copy, with the Ex Info keys of its plan when there is one."""
    plan_fields = {}
    if plan is not None:
        plan_fields = dict(planned=plan.matches(root, experiment, sample, kind), project_key=plan.project_key,
                           sample_key=plan.sample_key, link_key=plan.link_key)
    return Transfer(machine=machine, source=source, selection=selection, root=str(root).rstrip('\\/'),
                    experiment=experiment, sample=sample, kind=kind, **plan_fields, **extra,
                    transferred_at=datetime.now(timezone.utc).isoformat(), key=uuid4().hex)


def append_transfer(record, path=None):
    """Add one line to Transfers.jsonl (append only: lines written by earlier transfers are never rewritten)."""
    path = TRANSFERS if path is None else path
    line = dict(schema_version=1, **asdict(record), destination=record.destination, by=socket.gethostname())
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(line, ensure_ascii=False) + '\n')


def load_transfers(path=None):
    """Every recorded transfer (oldest first), skipping lines of an unknown schema."""
    path = TRANSFERS if path is None else path
    fields = set(Transfer.__dataclass_fields__)
    return [Transfer(**{k: v for k, v in r.items() if k in fields})
            for r in _read_lines(path) if r.get('schema_version') == 1 and r.get('source') and r.get('root')]

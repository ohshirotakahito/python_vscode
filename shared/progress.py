"""解析の進捗と注意の判定（実験ファイルDB・ファイル管理・Analyzer で共通の規則）。

すべて保存済みの DB の行（rack_samples / rack_bnals）だけから判定し、サーバーにはアクセスしない。
判定の規則を変えるときはここだけを直す（各アプリで別に書かない）。取りこぼしを防ぐため、
数えていないもの（None）を 0 として扱わず、「確認不可」「未計数」として残す。

ANAL（1つの共有の1サンプル）:
    元ファイル = T + T/stocked、解析済み = ANAL + ANAL/stocked
    status: unknown（T 未調査 または T も ANAL も 0）/ copy（T 0 で ANAL あり＝BNAL 用の解析コピー）/
            unstarted / partial / complete / excess（ANAL が元より多い＝要確認）
BNAL（1つの共有の1サンプルの BNAL@<target>）:
    進捗 = BNAL の TDMS 数 / (ANAL + ANAL/stocked)（同じ共有）
    BNAL@ フォルダがあっても TDMS が 0 件なら未着手（LabVIEW の途中停止・エラーでファイルが出なかったもの）
"""
import re

DONE_STATES = ('complete', 'excess')
STATUS_TEXT = dict(unknown='未計数・対象なし', unstarted='未着手', partial='進行中', complete='完了',
                   excess='超過', copy='解析用コピー（Tなし）')

# ANAL の注意。BLOCKING は「先に片付けが必要」、INFO は参考（DB の ANAL進捗タブの凡例と同じ区分）
ANAL_FLAGS = dict(欠番='ANAL の途中に抜けがある（最後の #番号より前が未解析）', 退避='ANAL/stocked に退避中',
                  混在='ANAL に同じ #番号がある（世代混在の疑い）', 差異='共有間で T（元ファイル）数が違う',
                  要更新='再開地点が未取得（Analysis_server で更新）', コピー不足='解析用コピーの ANAL が元より少ない')
ANAL_BLOCKING = ('欠番', '退避', '混在')
ANAL_INFO = ('差異', '要更新', 'コピー不足')


def parse_ranges(text):
    """'#001-#003,#007' -> {1, 2, 3, 7} (inverse of rack_store.number_ranges)."""
    numbers = set()
    for part in (text or '').split(','):
        bounds = [int(n) for n in re.findall(r'\d+', part)]
        if bounds:
            numbers.update(range(bounds[0], bounds[-1] + 1))
    return numbers


def resume_point(row):
    """(resume number, gap numbers) for one share, or (None, None) when the scan predates these columns.

    Missing numbers above the largest ANAL number are the unfinished tail: the run resumes at the first of them.
    Missing numbers below it are gaps that a plain resume would never fill."""
    if not row.get('anal_missing_checked'):
        return None, None
    missing = parse_ranges(row.get('anal_missing'))
    last = row.get('anal_last')
    if last is None:
        return (min(missing) if missing else None), []
    tail = [n for n in missing if n > last]
    return (min(tail) if tail else None), sorted(n for n in missing if n < last)


def anal_share(row):
    """ANAL status of one sample on one share from its rack_samples row."""
    total = None if row.get('t_tdms') is None else row['t_tdms'] + (row.get('t_stocked_tdms') or 0)
    analysed = None if row.get('anal_tdms') is None else row['anal_tdms'] + (row.get('anal_stocked_tdms') or 0)
    resume, gaps = resume_point(row)
    cell = dict(total=total, analysed=analysed, resume=resume, gaps=gaps,
                checked=bool(row.get('anal_missing_checked')), stocked=row.get('anal_stocked_tdms') or 0,
                mixed=row['anal_tdms'] - row['anal_distinct'] if row.get('anal_distinct') is not None and row.get('anal_tdms') else 0)
    if total is None or (total == 0 and not analysed):
        return dict(cell, status='unknown', ratio=None, remaining=None, state='確認不可' if total is None else '対象なし（T 0件）')
    if total == 0:
        # ANAL without T: ANAL copied here to run BNAL. Not an ANAL run, so it never decides the sample's progress.
        return dict(cell, status='copy', ratio=None, remaining=None, coverage=None, state=STATUS_TEXT['copy'])
    if not analysed:
        return dict(cell, status='unstarted', ratio=0.0, remaining=total,
                    state='未着手（ANALなし）' if analysed is None else '未着手（ANAL 0件）')
    ratio = analysed / total
    status = 'excess' if ratio > 1 else 'complete' if ratio == 1 else 'partial'
    return dict(cell, status=status, ratio=ratio, remaining=max(total - analysed, 0),
                state=dict(excess='超過・要確認', complete='完了', partial='進行中')[status])


def share_flags(cell):
    """Flags of one share by itself (欠番・退避・混在・要更新)."""
    flags = []
    if cell['gaps']:
        flags.append('欠番')
    if cell['stocked']:
        flags.append('退避')
    if cell['mixed']:
        flags.append('混在')
    if cell['status'] not in DONE_STATES and cell['status'] != 'unknown' and not cell['checked']:
        flags.append('要更新')
    return flags


def best_share(cells):
    """Most advanced share: highest ratio, then the one with the fewest remaining files. Analysis copies only
    count when the sample has nothing else."""
    cells = [c for c in cells if c['status'] != 'copy'] or cells
    known = [c for c in cells if c['ratio'] is not None]
    if not known:
        return next((c for c in cells if c['status'] == 'excess'), cells[0])
    return max(known, key=lambda c: (min(c['ratio'], 1), c['status'] != 'excess', -(c['remaining'] or 0)))


def assess_sample(cells):
    """(best share, flags) of one sample over every share holding it; sets 'coverage' on its analysis copies.

    Flags: 差異 (T counts differ between shares), 欠番 / 要更新 (of the best share), 退避 / 混在 (any share),
    コピー不足 (an analysis copy holds fewer ANAL than the best share)."""
    best = best_share(cells)
    copies = [c for c in cells if c['status'] == 'copy' and c is not best]
    for c in copies:
        c['coverage'] = c['analysed'] / best['analysed'] if best['analysed'] else None
    flags = []
    if len({c['total'] for c in cells if c['total']}) > 1:
        flags.append('差異')
    if best['gaps']:
        flags.append('欠番')
    if any(c['stocked'] for c in cells):
        flags.append('退避')
    if any(c['mixed'] for c in cells):
        flags.append('混在')
    if best['status'] not in DONE_STATES and best['status'] != 'unknown' and not best['checked']:
        flags.append('要更新')
    if any(c['coverage'] is not None and c['coverage'] < 1 for c in copies):
        flags.append('コピー不足')
    return best, flags


def bnal_state(tdms, anal):
    """(進捗表示, 状態, タグ) for one BNAL@ folder; anal = ANAL + ANAL/stocked on the same share.

    tdms None = the folder exists but its files were not counted yet (never 'nothing to do').
    tdms 0 = the folder exists but holds no file (a LabVIEW run stopped or failed before writing): 未着手.
    A share without ANAL is reported first, since its BNAL@ folder itself is out of place."""
    if tdms is None:
        return '未計数', '着手（TDMS未計数）', 'unknown'
    if not anal:
        return '確認不可', 'ANALなし・要確認', 'unknown'
    if tdms == 0:
        return '0%', '未着手（BNAL@ 0件）', 'unstarted'
    rate = f'{tdms / anal * 100:.1f}%'
    if tdms > anal:
        return rate, '超過・要確認', 'excess'
    if tdms == anal:
        return rate, '完了', 'complete'
    return rate, '進行中', 'partial'


def bnal_names_missing(row, saved_names):
    """True when the share's T listing saw more BNAL@ folders than the names saved for it (scanned before
    names were kept): a target missing from the saved names may exist, so it is 未確認, not 未着手."""
    return (row.get('bnal_folders') or 0) > saved_names

"""ANAL進捗 panel: every saved 10k_Sample against its original TDMS, one column per analysis share.

Everything here reads the saved DB (rack_samples); no server is accessed.
Progress on one share = (ANAL + ANAL/stocked) / (T + T/stocked) on that share. ANAL makes one file per original
file and keeps its '#NNN' number, so the first original number missing from ANAL is where an interrupted run resumes.
A sample's progress is its most advanced share.
"""
from collections import defaultdict
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
try:
    from . import bnal_progress, catalog, rackstation_view
except ImportError:
    import bnal_progress, catalog, rackstation_view
import ui_theme  # noqa: E402  (rackstation_view puts the repository root on sys.path)

DONE_STATES = bnal_progress.DONE_STATES
STATUS_TEXT = dict(bnal_progress.STATUS_TEXT, unknown='未計数・対象なし', copy='解析用コピー（Tなし）')
FLAG_LEGEND = ('⚠（黄の帯）＝先に片付けが必要：欠番＝ANALの途中に抜けがある（最後の#番号より前が未解析）　'
               '退避＝ANAL/stockedに退避中　混在＝ANALに同じ#番号（世代混在の疑い）。'
               '参考：差異＝共有間でT（元ファイル）数が違う　要更新＝再開地点が未取得（Analysis_serverで更新）　'
               'コピー不足＝解析用コピーのANALが元より少ない')
INFO_FLAGS = ('差異', '要更新', 'コピー不足')


def parse_ranges(text):
    """'#001-#003,#007' -> {1, 2, 3, 7} (inverse of rackstation_view.number_ranges)."""
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


def share_cell(row, group):
    """Status of one sample on one share from its rack_samples row."""
    total = None if row.get('t_tdms') is None else row['t_tdms'] + (row.get('t_stocked_tdms') or 0)
    analysed = None if row.get('anal_tdms') is None else row['anal_tdms'] + (row.get('anal_stocked_tdms') or 0)
    resume, gaps = resume_point(row)
    cell = dict(group=group, row=row, total=total, analysed=analysed, resume=resume, gaps=gaps,
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


def _best(cells):
    """Most advanced share: highest ratio, then the one with the fewest remaining files. Analysis copies only
    count when the sample has nothing else."""
    cells = [c for c in cells if c['status'] != 'copy'] or cells
    known = [c for c in cells if c['ratio'] is not None]
    if not known:
        return next((c for c in cells if c['status'] == 'excess'), cells[0])
    return max(known, key=lambda c: (min(c['ratio'], 1), c['status'] != 'excess', -(c['remaining'] or 0)))


def group_order(samples, labels):
    """Registered shares in ANALYSIS_GROUPS order, then any other saved root."""
    registered = [rackstation_view.group_label(server, share) for server, share in rackstation_view.ANALYSIS_GROUPS]
    present = {labels.get(catalog.root_identity(r['root']), r['root']) for r in samples}
    return [g for g in registered if g in present] + sorted(present - set(registered), key=str.casefold)


def build_progress(samples, labels):
    """One entry per (experiment, sample) with a cell per share holding it, plus per-share and overall summaries.

    labels: root identity -> group label (unknown roots fall back to the root itself)."""
    by_sample = defaultdict(dict)
    for r in samples:
        group = labels.get(catalog.root_identity(r['root']), r['root'])
        by_sample[(r['experiment'], r['sample'])][group] = share_cell(r, group)
    entries = []
    for (experiment, sample), cells in by_sample.items():
        best = _best(list(cells.values()))
        copies = [c for c in cells.values() if c['status'] == 'copy' and c is not best]
        for c in copies:
            c['coverage'] = c['analysed'] / best['analysed'] if best['analysed'] else None
        flags = []
        if len({c['total'] for c in cells.values() if c['total']}) > 1:
            flags.append('差異')
        if best['gaps']:
            flags.append('欠番')
        if any(c['stocked'] for c in cells.values()):
            flags.append('退避')
        if any(c['mixed'] for c in cells.values()):
            flags.append('混在')
        if best['status'] not in DONE_STATES and best['status'] != 'unknown' and not best['checked']:
            flags.append('要更新')
        if any(c['coverage'] is not None and c['coverage'] < 1 for c in copies):
            flags.append('コピー不足')
        entries.append(dict(experiment=experiment, sample=sample, cells=cells, best=best, status=best['status'],
                            ratio=best['ratio'], remaining=best['remaining'], total=best['total'],
                            resume=best['resume'], gaps=best['gaps'], flags=tuple(flags)))
    shares = defaultdict(lambda: dict(samples=0, complete=0, partial=0, unstarted=0, unknown=0, copy=0, done=0, goal=0))
    total = dict(samples=0, complete=0, partial=0, unstarted=0, unknown=0, copy=0, done=0, goal=0)
    for entry in entries:
        for cell in entry['cells'].values():
            _count(shares[cell['group']], cell)
        _count(total, entry['best'])
    summary = [dict(group=name, **shares[name]) for name in sorted(shares, key=str.casefold)]
    return entries, summary, dict(group='全体（各サンプルの最も進んだ共有）', **total)


def _count(bucket, cell):
    bucket['samples'] += 1
    bucket['complete' if cell['status'] in DONE_STATES else cell['status']] += 1
    if cell['total']:
        bucket['done'] += min(cell['analysed'] or 0, cell['total'])
        bucket['goal'] += cell['total']


def filter_entries(entries, query='', incomplete_only=False):
    query = query.strip().casefold()
    return [e for e in entries if query in f"{e['experiment']}/{e['sample']}".casefold()
            and not (incomplete_only and e['status'] in DONE_STATES)]


def sort_entries(entries, order='name'):
    """order='work': remaining original files (desc), then nearly done first; otherwise natural name order."""
    name = lambda e: (bnal_progress.natural_key(e['experiment']), bnal_progress.natural_key(e['sample']))  # noqa: E731
    if order == 'work':
        return sorted(entries, key=lambda e: (-(e['remaining'] or 0), -min(e['ratio'] or 0, 1), name(e)))
    return sorted(entries, key=name)


def warnings_for(entries):
    lines = []
    for e in entries:
        prefix = f"{e['experiment']}/{e['sample']}: "
        cells = e['cells'].values()
        if '差異' in e['flags']:
            lines.append(prefix + '共有ごとにT（元ファイル）数が異なります（'
                         + bnal_progress._listing((c['group'], c['total']) for c in cells if c['total']) + '）')
        if e['gaps']:
            lines.append(prefix + f"ANALの途中に未解析の#番号があります（{e['best']['group']}: "
                         f"{rackstation_view.number_ranges(e['gaps'])}）。再開だけでは埋まりません。")
        for key, text in (('stocked', 'ANAL/stockedに退避中のファイルがあります（{}件）。進捗には含めています。'),
                          ('mixed', 'ANALに同じ#番号のファイルがあります（重複 {}件）。世代が混在している可能性があります。')):
            hits = [(c['group'], c[key]) for c in cells if c[key]]
            if hits:
                lines.append(prefix + text.format(bnal_progress._listing(hits)))
    return lines


def number_text(number):
    return f'#{number:03d}' if number is not None else ''


def progress_text(cell):
    if cell['status'] == 'copy':
        return 'コピー' + ('' if cell.get('coverage') is None else f"（元ANALの{cell['coverage'] * 100:.1f}%）")
    return '—' if cell['ratio'] is None else f"{cell['ratio'] * 100:.1f}%"


def resume_text(cell):
    if cell['status'] in DONE_STATES or cell['status'] in ('unknown', 'copy'):
        return '—'
    if not cell['checked']:
        return '要更新'
    return number_text(cell['resume']) or ('欠番のみ' if cell['gaps'] else '—')


DETAIL_COLUMNS = ('experiment', 'sample', 'group', 'total', 'analysed', 'progress', 'state',
                  'mixed', 'stocked', 'duplicate_risk', 'resume', 'gaps', 'missing', 'last_success')
DETAIL_TITLES = ('実験名', 'サンプル名', 'グループ', 'T（直下＋stocked）', 'ANAL（直下＋stocked）', '進捗', '状態',
                 'ANALの#番号重複', 'ANAL/stockedへの退避', '実験重複の可能性',
                 '再開地点', '欠番（途中の未解析）', '未解析の#番号（すべて）', '最終取得 UTC')
SUMMARY_COLUMNS = ('group', 'samples', 'complete', 'partial', 'unstarted', 'unknown', 'copy', 'tdms', 'rate')
SUMMARY_TITLES = ('グループ', 'サンプル数', '完了', '進行中', '未着手', '未計数・対象なし', '解析用コピー', 'ANAL済／T', '進捗')


def detail_values(entry, cell):
    row = cell['row']
    duplicate_known = row.get('anal_distinct') is not None
    duplicate = (f"重複あり（{cell['mixed']:,}件）" if cell['mixed'] else
                 '重複なし' if duplicate_known else '未判定（要更新）')
    stocked = (f"退避中（{cell['stocked']:,}件）" if cell['stocked'] else
               '退避なし' if row.get('anal_stocked_tdms') is not None else '未取得（要更新）')
    duplicate_risk = ('要確認（同じ#番号・世代混在）' if cell['mixed'] else
                      '兆候なし（#番号重複なし）' if duplicate_known else '未判定（要更新）')
    return dict(experiment=entry['experiment'], sample=entry['sample'], group=cell['group'],
                total='—' if cell['total'] is None else cell['total'],
                analysed='—' if cell['analysed'] is None else cell['analysed'],
                progress=progress_text(cell), state=cell['state'],
                resume=resume_text(cell), gaps=rackstation_view.number_ranges(cell['gaps']) if cell['gaps'] else '',
                missing=(row.get('anal_missing') or '') if cell['checked'] else '要更新',
                stocked=stocked, mixed=duplicate, duplicate_risk=duplicate_risk,
                last_success=row.get('last_success') or '—')


def summary_values(row):
    return dict(row, tdms=f"{row['done']:,} / {row['goal']:,} TDMS", rate=bnal_progress.rate_text(row['done'], row['goal']))


MAP_BAR_W = 140


def cell_color(cell):
    if cell is not None and cell['status'] == 'copy':
        return ui_theme.mix(ui_theme.FIELD, ui_theme._ROW_HUES['info'], 0.45)
    return bnal_progress.map_color(cell)
MAP_ORDERS = {'次に解析すべき順': 'work', 'サンプル名順': 'name'}


def create_map_view(frame, jump):
    """Overview canvas: one row per sample, one column per share; jump(experiment, sample) opens the detail rows."""
    legend = ttk.Frame(frame, padding=(8, 4))
    legend.pack(fill='x')
    for status in ('unstarted', 'partial', 'complete', 'excess', 'copy', 'unknown'):
        swatch = tk.Canvas(legend, width=14, height=14, highlightthickness=0, bg=ui_theme.PANEL)
        swatch.create_rectangle(1, 1, 13, 13, fill=cell_color(dict(status=status, ratio=0.6)), outline='')
        swatch.pack(side='left', padx=(8, 2))
        ttk.Label(legend, text=STATUS_TEXT[status] + ('（濃いほど進んでいる）' if status == 'partial' else '')).pack(side='left')
    ttk.Label(legend, text='　空欄＝その共有にサンプルなし', style='Muted.TLabel').pack(side='left')
    order = tk.StringVar(value='次に解析すべき順')
    order_box = ttk.Combobox(legend, textvariable=order, values=list(MAP_ORDERS), state='readonly', width=16)
    order_box.pack(side='right', padx=(4, 0))
    order_box.bind('<<ComboboxSelected>>', lambda _: redraw())
    ttk.Label(legend, text='並び').pack(side='right')
    ttk.Label(frame, text='マス＝共有ごとの進捗（ANAL÷T）。右側は最も進んだ共有の値。「次に解析すべき順」＝残りTが多い順 → 完了に近い順。'
                          '再開地点＝中断したANALを再開する#番号。マスにマウスで詳細、クリックで明細へ移動します。',
              style='Muted.TLabel', padding=(8, 2)).pack(fill='x')
    ttk.Label(frame, text=FLAG_LEGEND, style='Muted.TLabel', padding=(8, 0)).pack(fill='x')
    body = ttk.Frame(frame)
    body.pack(fill='both', expand=True, padx=8, pady=4)
    canvas = tk.Canvas(body, bg=ui_theme.FIELD, highlightthickness=0)
    vertical = ttk.Scrollbar(body, orient='vertical', command=canvas.yview)
    horizontal = ttk.Scrollbar(body, orient='horizontal', command=canvas.xview)
    canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    canvas.grid(row=0, column=0, sticky='nsew')
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal.grid(row=1, column=0, sticky='ew')
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    state = dict(entries=[], groups=[], hover={})
    font = tkfont.Font(family=ui_theme.UI, size=11)
    small = tkfont.Font(family=ui_theme.UI, size=10)

    def redraw():
        canvas.delete('all')
        state['hover'].clear()
        entries = sort_entries(state['entries'], MAP_ORDERS[order.get()])
        if not entries:
            canvas.create_text(16, 16, anchor='nw', fill=ui_theme.MUTED, font=font,
                               text='表示するサンプルがありません（Analysis_serverで更新するか、絞り込みを確認してください）。')
            canvas.configure(scrollregion=(0, 0, 0, 0))
            return
        groups = state['groups']
        label_w = 16 + max(font.measure(f"{e['experiment']} / {e['sample']}") for e in entries) + 12
        cell_w = max(64, max((small.measure(part) for g in groups for part in g.split('/')), default=0) + 14)
        row_h = 26
        header_h = 54
        grid_right = label_w + cell_w * len(groups)
        rate_x = grid_right + 12
        bar_x = rate_x + font.measure('100.0%') + 16
        number_w = max(small.measure(f"{e['remaining'] or 0:,}") for e in entries) + 18
        resume_x = bar_x + MAP_BAR_W + number_w
        flag_x = resume_x + small.measure('欠番のみ') + 24
        canvas.create_text(8, header_h - 6, anchor='sw', text=f'サンプル（{len(entries)}）', fill=ui_theme.MUTED, font=small)
        for index, group in enumerate(groups):
            server, _, share = group.partition('/')
            canvas.create_text(label_w + index * cell_w + cell_w / 2, header_h - 6, anchor='s', justify='center',
                               text=f'{server}\n{share}' if share else group, fill=ui_theme.MUTED, font=small)
        for text, x in (('進捗', rate_x), ('残りT', bar_x), ('再開', resume_x), ('注意', flag_x)):
            canvas.create_text(x, header_h - 6, anchor='sw', text=text, fill=ui_theme.MUTED, font=small)
        most = max((e['remaining'] or 0 for e in entries), default=0) or 1
        warn = ui_theme._ROW_HUES['warning']
        for index, e in enumerate(entries):
            y = header_h + index * row_h
            fixes = [f for f in e['flags'] if f not in INFO_FLAGS]
            notes = [f for f in e['flags'] if f in INFO_FLAGS]
            if fixes:
                canvas.create_rectangle(0, y + 1, 4, y + row_h - 1, fill=warn, outline='')
            item = canvas.create_text(10, y + row_h / 2, anchor='w', text=f"{e['experiment']} / {e['sample']}",
                                      fill=ui_theme.TEXT, font=font)
            state['hover'][item] = (e, None)
            for col, group in enumerate(groups):
                cell = e['cells'].get(group)
                x = label_w + col * cell_w
                item = canvas.create_rectangle(x + 1, y + 1, x + cell_w - 1, y + row_h - 1,
                                               fill=cell_color(cell), outline='')
                state['hover'][item] = (e, group)
                if cell:
                    text = ('コピー' if cell['status'] == 'copy' else
                            '—' if cell['ratio'] is None else f"{min(cell['ratio'], 9.99) * 100:.0f}%")
                    item = canvas.create_text(x + cell_w / 2, y + row_h / 2, text=text, fill=ui_theme.TEXT, font=small)
                    state['hover'][item] = (e, group)
            canvas.create_text(rate_x, y + row_h / 2, anchor='w', fill=ui_theme.TEXT, font=font,
                               text='—' if e['ratio'] is None else f"{e['ratio'] * 100:.1f}%")
            remaining = e['remaining'] or 0
            bar = MAP_BAR_W * remaining / most
            if bar:
                canvas.create_rectangle(bar_x, y + 7, bar_x + bar, y + row_h - 7,
                                        fill=ui_theme.mix(ui_theme.FIELD, ui_theme.TEXT, 0.45), outline='')
            canvas.create_text(bar_x + bar + 6, y + row_h / 2, anchor='w', text=f'{remaining:,}',
                               fill=ui_theme.MUTED, font=small)
            resume = resume_text(e['best'])
            canvas.create_text(resume_x, y + row_h / 2, anchor='w', text=resume, font=small,
                               fill=ui_theme.TEXT if resume.startswith('#') else ui_theme.DIM)
            mark = flag_x
            if fixes:
                item = canvas.create_text(mark, y + row_h / 2, anchor='w', text='⚠ ' + '・'.join(fixes), fill=warn, font=small)
                mark = canvas.bbox(item)[2] + 8
            if notes:
                canvas.create_text(mark, y + row_h / 2, anchor='w', text='・'.join(notes), fill=ui_theme.DIM, font=small)
        width = flag_x + small.measure('⚠ 欠番・退避・混在　差異・要更新') + 16
        canvas.configure(scrollregion=(0, 0, width, header_h + len(entries) * row_h + 12))

    def describe(entry, group):
        best = entry['best']
        head = (f"{entry['experiment']} / {entry['sample']}\n{STATUS_TEXT[entry['status']]}"
                + ('' if entry['ratio'] is None else f"　{entry['ratio'] * 100:.1f}%")
                + f"　残りT {entry['remaining'] or 0:,}　（最も進んだ共有: {best['group']}）")
        if entry['flags']:
            head += '\n⚠ ' + '・'.join(entry['flags'])
        if group is None:
            return head + '\nクリックで明細へ移動'
        cell = entry['cells'].get(group)
        if cell is None:
            return head + f'\n{group}: このサンプルなし'
        text = (f"\n{group}: {cell['state']}　T {'—' if cell['total'] is None else cell['total']}"
                f"　ANAL {'—' if cell['analysed'] is None else cell['analysed']}\n進捗 {progress_text(cell)}"
                f"　再開地点 {resume_text(cell)}")
        if cell['gaps']:
            text += f"\n欠番 {rackstation_view.number_ranges(cell['gaps'])}"
        return head + text

    def found_at(event):
        x, y = canvas.canvasx(event.x), canvas.canvasy(event.y)
        hit = next((state['hover'][i] for i in reversed(canvas.find_overlapping(x, y, x, y)) if i in state['hover']), None)
        return hit, x, y

    def hover(event):
        hit, x, y = found_at(event)
        canvas.delete('tip')
        if hit is None:
            return
        text = canvas.create_text(x + 16, y + 16, anchor='nw', text=describe(*hit), fill=ui_theme.TEXT, font=font, tags='tip')
        _, _, right, bottom = canvas.bbox(text)
        canvas.move(text, min(0, canvas.canvasx(canvas.winfo_width()) - right - 8),
                    min(0, canvas.canvasy(canvas.winfo_height()) - bottom - 8))
        left, top, right, bottom = canvas.bbox(text)
        box = canvas.create_rectangle(left - 6, top - 4, right + 6, bottom + 4, fill=ui_theme.PANEL,
                                      outline=ui_theme.BORDER, tags='tip')
        canvas.tag_raise(text, box)

    def click(event):
        hit, _, _ = found_at(event)
        if hit:
            jump(hit[0]['experiment'], hit[0]['sample'], hit[1])

    canvas.bind('<Motion>', hover)
    canvas.bind('<Leave>', lambda _: canvas.delete('tip'))
    canvas.bind('<Button-1>', click)
    canvas.bind('<MouseWheel>', lambda e: canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units'))
    canvas.bind('<Shift-MouseWheel>', lambda e: canvas.xview_scroll(-1 if e.delta > 0 else 1, 'units'))

    def set_data(entries, groups):
        state['entries'], state['groups'] = entries, groups
        redraw()

    redraw()
    return set_data


def create_panel(parent, window):
    bar = ttk.Frame(window, padding=8)
    bar.pack(fill='x')
    query = tk.StringVar()
    incomplete_only = tk.BooleanVar(value=False)
    ttk.Label(bar, text='絞り込み（実験 / サンプル）').pack(side='left')
    ttk.Entry(bar, textvariable=query, width=30).pack(side='left', padx=6)
    ttk.Checkbutton(bar, text='未完了のみ', variable=incomplete_only, command=lambda: show()).pack(side='left', padx=6)
    status = tk.StringVar(value='Analysis_serverタブで更新した保存済み情報から集計します（サーバーには接続しません）。')
    ttk.Label(window, textvariable=status, padding=(8, 4), wraplength=1300).pack(side='bottom', fill='x')
    views = ttk.Notebook(window)
    views.pack(fill='both', expand=True, padx=8, pady=4)
    map_tab = ttk.Frame(views)
    detail_tab = ttk.Frame(views)
    views.add(map_tab, text='全体マップ')
    views.add(detail_tab, text='明細')
    summary_tree = parent.table(SUMMARY_COLUMNS, SUMMARY_TITLES, 6, parent=detail_tab)
    summary_tree.tag_configure('total', background=ui_theme.row('violet', 0.2))
    warning_frame = ttk.Frame(detail_tab, padding=(8, 2))
    warning_frame.pack(fill='x')
    warning_box = tk.Text(warning_frame, height=4, wrap='word', bg=ui_theme.FIELD, fg=ui_theme.MUTED,
                          relief='flat', font=(ui_theme.UI, 9))
    warning_scroll = ttk.Scrollbar(warning_frame, command=warning_box.yview)
    warning_box.configure(yscrollcommand=warning_scroll.set, state='disabled')
    warning_scroll.pack(side='right', fill='y')
    warning_box.pack(fill='x', expand=True)
    detail_tree = parent.table(DETAIL_COLUMNS, DETAIL_TITLES, 18, True, parent=detail_tab)
    for col in DETAIL_COLUMNS:
        detail_tree.column(col, width=220 if col in ('gaps', 'missing', 'state', 'last_success', 'duplicate_risk')
                           else 180 if col in ('mixed', 'stocked') else 130)
    for tag in ('unstarted', 'partial', 'complete', 'excess'):
        detail_tree.tag_configure(tag, background=ui_theme.row(dict(unstarted='danger', partial='warning',
                                                                     complete='success', excess='violet')[tag]))
    detail_tree.tag_configure('unknown', background=ui_theme.row('neutral', 0.16))
    detail_tree.tag_configure('copy', background=ui_theme.row('info'))
    detail_tree.bind('<Double-1>', lambda event: parent.show_row_details(detail_tree))
    ttk.Label(detail_tab, text='進捗＝同じ共有の (ANAL＋ANAL/stocked) ÷ (T＋T/stocked)。再開地点＝ANALの最後の#番号より後で、'
                               '最初に未解析の元ファイル#番号。欠番＝最後の#番号より前の未解析（再開では埋まらない）。'
                               '実験重複の可能性＝同じ#番号の重複を根拠にした注意表示（重複の確定ではありません）。'
                               '退避中のファイルも進捗に含みます。行は Ctrl / Shift で複数選択でき、上のボタンの対象になります。',
              padding=(8, 2), wraplength=1300).pack(fill='x')
    saved = dict(samples=[], entries=[])
    row_keys = {}

    def jump(experiment, sample, group):
        views.select(detail_tab)
        items = [item for item, key in row_keys.items() if key[:2] == (experiment, sample)
                 and (group is None or key[2] == group)]
        if items:
            detail_tree.selection_set(items)
            detail_tree.see(items[0])
            detail_tree.focus(items[0])

    set_map = create_map_view(map_tab, jump)

    def show(*_):
        entries = filter_entries(saved['entries'], query.get(), incomplete_only.get())
        groups = group_order(saved['samples'], bnal_progress.group_labels(parent.configured))
        set_map(entries, groups)
        detail_tree.delete(*detail_tree.get_children())
        row_keys.clear()
        for entry in sort_entries(entries):
            for group in groups:
                cell = entry['cells'].get(group)
                if cell:
                    values = detail_values(entry, cell)
                    item = detail_tree.insert('', 'end', values=[parent.cell(values, c) for c in DETAIL_COLUMNS],
                                              tags=(cell['status'],))
                    row_keys[item] = (entry['experiment'], entry['sample'], group)
        _, shares, total = build_progress([c['row'] for e in entries for c in e['cells'].values()],
                                          bnal_progress.group_labels(parent.configured))
        summary_tree.delete(*summary_tree.get_children())
        for row in shares:
            summary_tree.insert('', 'end', values=[parent.cell(summary_values(row), c) for c in SUMMARY_COLUMNS])
        summary_tree.insert('', 'end', values=[parent.cell(summary_values(total), c) for c in SUMMARY_COLUMNS],
                            tags=('total',))
        notes = warnings_for(entries)
        warning_box.config(state='normal')
        warning_box.delete('1.0', 'end')
        warning_box.insert('1.0', '\n'.join([f'警告 {len(notes)}件（スクロールで確認できます）'] + notes) if notes else '警告はありません。')
        warning_box.config(state='disabled')
        stale = sum('要更新' in e['flags'] for e in entries)
        status.set(f"{total['samples']}サンプル: 完了 {total['complete']} / 進行中 {total['partial']} / "
                   f"未着手 {total['unstarted']} / 未計数 {total['unknown']}　"
                   f"全体の進捗 {bnal_progress.rate_text(total['done'], total['goal'])}（{total['done']:,} / {total['goal']:,} TDMS）"
                   + (f"　再開地点が未取得 {stale}サンプル（Analysis_serverで更新すると表示されます）" if stale else ''))

    def reload_db():
        try:
            saved['samples'], _ = bnal_progress.load_saved(parent.db_path)
        except Exception as exc:
            status.set(f'DBを読めません: {exc}')
            return
        saved['entries'] = build_progress(saved['samples'], bnal_progress.group_labels(parent.configured))[0]
        show()

    def selected_rows(wanted):
        keys = {row_keys[item] for item in detail_tree.selection() if item in row_keys}
        rows = [cell['row'] for e in saved['entries'] for group, cell in e['cells'].items()
                if (e['experiment'], e['sample'], group) in keys]
        return [r for r in rows if wanted(r)]

    actions = ttk.Frame(bar)
    actions.pack(side='left', padx=(24, 0))
    ttk.Button(actions, text='DBを再読込', command=reload_db).pack(side='left')
    ttk.Button(actions, text='選択行のANAL/stockedをANALへ戻す…', command=lambda: bnal_progress.restore_stocked(
        parent, window, selected_rows(bnal_progress.restore_wanted), reload_db)).pack(side='left', padx=(24, 0))
    ttk.Button(actions, text='選択行のANAL旧世代をANAL/oldへ…', command=lambda: bnal_progress.move_old_generations(
        parent, window, selected_rows(bnal_progress.old_wanted), reload_db)).pack(side='left', padx=6)
    query.trace_add('write', show)
    window.after(0, reload_db)
    return reload_db

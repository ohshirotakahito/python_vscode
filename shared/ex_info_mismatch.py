"""サンプル台帳と AXN の Ex Info の不一致リスト（読むだけ）。

台帳の各サンプル（プロジェクトフォルダ｜サンプルフォルダ）が、Ex Info に登録した保存場所と 1 つに結び付かない理由と、
直し方の案内を出す。結び付いたものは sample_plan.link_ex_info が sample_key を記録するので、このリストから消える。
直すのは人（AXN の Ex Info で登録する、またはフォルダ・台帳を直す）。このモジュールはどこにも書かない。
"""
from shared import ex_info_links, ex_info_owners

NO_SERVER = '解析サーバーにフォルダがない'
NOT_REGISTERED = 'Ex Info にサンプルがない'
OTHER_PROJECT = 'Ex Info では別のプロジェクトにある'
NO_LOCATION = 'Ex Info に保存場所が未登録'
OTHER_LOCATION = 'Ex Info の保存場所が違う'
AMBIGUOUS = '複数の Ex Info サンプルに当たる'
REASONS = (NOT_REGISTERED, OTHER_PROJECT, NO_LOCATION, OTHER_LOCATION, AMBIGUOUS, NO_SERVER)

HINTS = {
    NOT_REGISTERED: 'AXN の Ex Info でプロジェクト・サンプルを追加し、保存場所にこのフォルダを登録する',
    OTHER_PROJECT: 'どちらが正しいか確かめ、Ex Info のサンプルの所属か、解析サーバーのフォルダを直す',
    NO_LOCATION: 'AXN の Ex Info で、このサンプルの保存場所にこのフォルダを登録する',
    OTHER_LOCATION: 'Ex Info の保存場所か解析サーバーのフォルダのどちらかを直し、同じ場所にそろえる',
    AMBIGUOUS: '同じフォルダを登録した Ex Info のサンプルが複数ある。正しい 1 つを残す',
    NO_SERVER: 'フォルダの移動・名前の変更を確かめ、サンプル台帳の項目を移すか消す',
}

COLUMNS = ('reason', 'project', 'sample', 'server', 'ex_info', 'hint')
TITLES = ('理由', 'プロジェクト名', 'サンプル名', '解析サーバーの保存先', 'Ex Info の登録', '直し方')


def _place(loc):
    return '\\'.join(p for p in (loc.get('root_path', '').rstrip('\\'), loc.get('experiment_folder', ''),
                                 loc.get('sample_folder', '')) if p)


def mismatches(plan, samples, data):
    """[{reason, project, sample, server, ex_info, hint}] for ledger entries with no single Ex Info sample.
    Entries that already hold a sample_key are linked and left out."""
    pairs = [tuple(k.split('|', 1)) for k, entry in plan.items() if '|' in k and not entry.get('sample_key')]
    found = ex_info_owners.candidate_keys([dict(experiment=e, sample=s) for e, s in pairs], samples, data)
    projects = {p['key']: p for p in data['projects'] if p.get('key')}
    by_name = {}
    for sample in data['samples']:
        by_name.setdefault(sample.get('name', '').casefold(), []).append(sample)
    locations = {}
    for loc in data['locations']:
        locations.setdefault(loc.get('sample_key'), []).append(loc)
    servers = {}
    for row in samples:
        servers.setdefault((row['experiment'], row['sample']), set()).add(ex_info_links.path_key(row['root']))
    result = []
    for (project, sample), keys in zip(pairs, found):
        if len(keys) == 1:
            continue
        on_server = sorted(servers.get((project, sample), ()))
        same = [s for s in by_name.get(sample.casefold(), [])
                if projects.get(s.get('project_key'), {}).get('name', '').casefold() == project.casefold()]
        elsewhere = [s for s in by_name.get(sample.casefold(), []) if s not in same]
        registered = [_place(loc) for s in same for loc in locations.get(s['key'], [])]
        if len(keys) > 1:
            reason = AMBIGUOUS
        elif not on_server:
            reason = NO_SERVER
        elif not same:
            reason = OTHER_PROJECT if elsewhere else NOT_REGISTERED
        elif not registered:
            reason = NO_LOCATION
        else:
            reason = OTHER_LOCATION
        if not registered and elsewhere:
            registered = [f"{projects.get(s.get('project_key'), {}).get('name', '?')} / {s['name']}" for s in elsewhere]
        result.append(dict(reason=reason, project=project, sample=sample,
                           server=' ; '.join(f'{root}\\{project}\\{sample}' for root in on_server) or '（なし）',
                           ex_info=' ; '.join(dict.fromkeys(registered)) or '（なし）', hint=HINTS[reason]))
    result.sort(key=lambda r: (REASONS.index(r['reason']), r['project'].casefold(), r['sample'].casefold()))
    return result

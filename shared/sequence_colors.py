"""アミノ酸の色と、BNAL の target が核酸かペプチドかの判定（配列アセンブリ・シグナル解析で共通）。

色は性質のグループごとに色相をそろえ、グループ内は濃淡で見分ける。AMINO_COLORS は白背景（図）用、
AMINO_TEXT_COLORS は暗い背景（画面の文字・波形）用に明るくしたもの。
"""
from shared.bnal_lists import AMINO_ONLY

AMINO_GROUPS = (
    ('疎水性', {'A': '#4f7fd6', 'V': '#3a68c0', 'L': '#2c56a8', 'I': '#22468e', 'M': '#6c86d8',
              'F': '#5468b8', 'W': '#7462c4'}),
    ('正電荷', {'K': '#d84a3a', 'R': '#b8322a', 'H': '#e57c55'}),
    ('負電荷', {'D': '#c23c9a', 'E': '#9c2c86'}),
    ('極性', {'S': '#3daa5c', 'T': '#2b8a48', 'N': '#62bf7d', 'Q': '#22773d', 'Y': '#1ea3a3'}),
    ('特殊', {'C': '#d9b200', 'G': '#f08c2a', 'P': '#a68a3e'}),
)
AMINO_COLORS = {aa: c for _, colors in AMINO_GROUPS for aa, c in colors.items()}
AMINO_GROUP_OF = {aa: name for name, colors in AMINO_GROUPS for aa in colors}
AMINO_ORDER = [aa for _, colors in AMINO_GROUPS for aa in colors]


def lighter(color, amount=0.35):
    """'#rrggbb' を白に amount だけ近づける"""
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(v + (255 - v) * amount):02x}' for v in rgb)


AMINO_TEXT_COLORS = {aa: lighter(c) for aa, c in AMINO_COLORS.items()}

# BNAL（サーバー）で選んだ target リストの分類。B@@ のファイルのプロパティに書く
LIST_KIND_PROPERTY = 'BNAL List Kind'
LIST_KINDS = ('Nucleotide', 'Peptide')


def list_kind(recorded, sequence=''):
    """'Nucleotide' / 'Peptide'。B@@ の記録を優先し、無ければ（古い B@@）アミノ酸だけの文字があればペプチド"""
    if recorded in LIST_KINDS:
        return recorded
    return 'Peptide' if set(str(sequence)) & AMINO_ONLY else 'Nucleotide'

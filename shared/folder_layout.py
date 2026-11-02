"""解析サーバー上の標準サンプルフォルダ構成（exmanager と filemanager で共通）。"""
import os


def create_sample_folders(root, ex, sample):
    """Create the standard sample tree under an arbitrary analysis root."""
    base = os.path.join(root, ex, sample)
    folders = [base, os.path.join(base, 'raw data')]
    for rate in ('10k', '100k'):
        for kind in ('Sample', 'Blank'):
            folder = os.path.join(base, f'{sample}_{rate}_{kind}')
            folders.append(folder)
            if rate == '10k':
                folders.extend(os.path.join(folder, *parts) for parts in
                               [('T',), ('stocked',), ('T', 'ANAL'), ('T', 'stocked')])
    for folder in folders:
        os.makedirs(folder, exist_ok=True)
    return folders

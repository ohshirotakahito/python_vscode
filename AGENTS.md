# Application releases

When changing a launcher application or the launcher itself, update its entry in
`app_versions.json` using `tools/app_version.py --bump` and add concrete Japanese release notes.
Small fixes use `patch` (0.1.0 -> 0.1.1), medium feature changes use `minor`
(0.1.1 -> 0.2.0), and major changes use `major` (0.2.0 -> 1.0.0).
Increment once per completed change, not once per edited file. A minor increment
resets patch to zero; a major increment resets minor and patch to zero.
Shared changes must increment every affected application, including `workbench`
when its behavior changes. Keep unrelated apps' versions unchanged.
Run `python -m unittest shared.test_app_release shared.test_app_update` after release/update changes.
Terminal selection is PC-local in ignored `.workbench/terminal.json`; never commit or distribute it.
New PCs default to the user role with `exmanager`, `viewer`, `chipdb`, and `database` enabled, as requested by the user; on user terminals `database` is read-only and opens the snapshot that `database/publish.py` writes to `D:\Qstatus\database`. Legacy standard profiles (exmanager-only and exmanager/viewer before version 3, exmanager/viewer/chipdb before version 4) also get the current default; preserve custom selections and explicit version-4 choices. This development PC is explicitly configured locally.
After terminal or deployment changes also run `shared.test_terminal_profile` and `shared.test_update_checkout`.

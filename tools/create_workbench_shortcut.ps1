param([string]$Destination = (Join-Path (Split-Path $PSScriptRoot -Parent) 'Quantum Workbench.lnk'))
$projectRoot = Split-Path $PSScriptRoot -Parent
$shortcutShell = New-Object -ComObject WScript.Shell
$shortcut = $shortcutShell.CreateShortcut([System.IO.Path]::GetFullPath($Destination))
$shortcut.TargetPath = Join-Path $projectRoot 'launch_apps.bat'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = (Join-Path $projectRoot 'assets\quantum-workbench.ico') + ',0'
$shortcut.Description = 'Quantum Workbench'
$shortcut.Save()
Write-Output "Created: $Destination"

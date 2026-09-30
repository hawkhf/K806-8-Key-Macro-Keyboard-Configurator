[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectDirectory

python -m pip install --upgrade ".[build]"
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "X806-Configurator" `
    "x806_configurator_mvp.py"

Write-Host "Built dist\X806-Configurator.exe"

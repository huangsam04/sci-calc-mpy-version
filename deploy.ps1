[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Port,
    [switch]$Reset
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkspaceRoot = Split-Path -Parent $ProjectRoot
$Python = Join-Path $WorkspaceRoot ".venv\python.exe"
$Source = Join-Path $ProjectRoot "source"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing project Python: $Python"
}

function Invoke-MpRemote {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $Python -m mpremote connect $Port @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "mpremote failed: $($Arguments -join ' ')"
    }
}

Write-Host "Installing internal launch and recovery files on $Port..."
Invoke-MpRemote exec "import os`ntry: os.mkdir('/display')`nexcept OSError: pass"
Invoke-MpRemote fs cp (Join-Path $Source "boot.py") :/boot.py
Invoke-MpRemote fs cp (Join-Path $Source "internal_main.py") :/main.py
Invoke-MpRemote fs cp (Join-Path $Source "recovery.py") :/recovery.py
Invoke-MpRemote fs cp (Join-Path $Source "display\ssd1322.py") :/display/ssd1322.py
Invoke-MpRemote fs cp (Join-Path $Source "display\mono_palette.py") :/display/mono_palette.py

$MountSd = @"
import os, vfs
from machine import SDCard
try:
    os.stat('/sd')
except OSError:
    vfs.mount(SDCard(slot=2, width=1, sck=18, mosi=23, miso=19, cs=4, freq=10000000), '/sd')
"@
Invoke-MpRemote exec $MountSd

$Directories = @("anim", "calc", "display", "fonts", "functions", "input", "screens", "ui", "utils")
$CreateDirectories = "import os`n" + (($Directories | ForEach-Object {
    "try: os.mkdir('/sd/$_')`nexcept OSError: pass"
}) -join "`n")
Invoke-MpRemote exec $CreateDirectories

Write-Host "Uploading SD application..."
$Excluded = @("boot.py", "internal_main.py", "recovery.py")
Get-ChildItem -LiteralPath $Source -Recurse -File | ForEach-Object {
    $Relative = $_.FullName.Substring($Source.Length + 1).Replace("\", "/")
    $AllowedExtension = $_.Extension -in @(".py", ".json", ".c")
    $IsCache = $Relative.Split("/") -contains "__pycache__"
    if ($Excluded -notcontains $Relative -and $AllowedExtension -and -not $IsCache) {
        Invoke-MpRemote fs cp $_.FullName (":/sd/" + $Relative)
    }
}

Write-Host "Verifying entry points..."
Invoke-MpRemote fs sha256sum :/boot.py :/main.py :/sd/main.py :/sd/calc/parser.py

if ($Reset) {
    Write-Host "Resetting device..."
    Invoke-MpRemote reset
}

Write-Host "SCI-CALC deployment complete."

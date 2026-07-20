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
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        & $Python -m mpremote connect $Port @Arguments
        if ($LASTEXITCODE -eq 0) {
            return
        }
        if ($Attempt -lt 3) {
            Write-Warning "mpremote attempt $Attempt failed; reconnecting..."
            Start-Sleep -Milliseconds 500
        }
    }
    throw "mpremote failed after 3 attempts: $($Arguments -join ' ')"
}

function Wait-SdMount {
    $LastProbeOutput = ""
    for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
        $LastProbeOutput = (& $Python -m mpremote connect $Port exec `
            "import os`nos.listdir('/sd')`nprint('SD_READY')" 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0 -and $LastProbeOutput -match "SD_READY") {
            Write-Host "SD card mounted."
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "SD card was not mounted by /boot.py after reset. Check that the FAT32 card is inserted and review the serial boot log. Last probe: $LastProbeOutput"
}

Write-Host "Installing internal launch and recovery files on $Port..."
Invoke-MpRemote exec "import os`ntry: os.mkdir('/display')`nexcept OSError: pass"
Invoke-MpRemote fs cp (Join-Path $Source "boot.py") :/boot.py
Invoke-MpRemote fs cp (Join-Path $Source "sdcard.py") :/sdcard.py
Invoke-MpRemote fs cp (Join-Path $Source "internal_main.py") :/main.py
Invoke-MpRemote fs cp (Join-Path $Source "recovery.py") :/recovery.py
Invoke-MpRemote fs cp (Join-Path $Source "display\ssd1322.py") :/display/ssd1322.py
Invoke-MpRemote fs cp (Join-Path $Source "display\mono_palette.py") :/display/mono_palette.py

# The old application may still own SPI1.  A hardware reset releases it and
# lets the newly-installed /boot.py become the sole owner of SD initialisation.
Write-Host "Restarting into the new internal launcher..."
Invoke-MpRemote reset
Start-Sleep -Milliseconds 800
Wait-SdMount

$Directories = @("anim", "calc", "display", "fonts", "functions", "input", "screens", "ui", "utils")
$CreateDirectories = "import os`n" + (($Directories | ForEach-Object {
    "try: os.mkdir('/sd/$_')`nexcept OSError: pass"
}) -join "`n")
Invoke-MpRemote exec $CreateDirectories

Write-Host "Uploading SD application..."
$Excluded = @("boot.py", "internal_main.py", "recovery.py", "sdcard.py")
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

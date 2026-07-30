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
$MpyCross = Join-Path $WorkspaceRoot "micropython\mpy-cross\build\mpy-cross.exe"
$BuildRoot = Join-Path $ProjectRoot ".mpy-build"
$FontBuild = Join-Path $BuildRoot "fonts"
$MpyBuild = Join-Path $BuildRoot "deploy-mpy"
$ProbeSource = Join-Path $ProjectRoot "tools\mpy_abi_probe.py"
$ProbeMpy = Join-Path $BuildRoot "sci_calc_mpy_probe.mpy"
$RuntimeAssets = @()

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

function Test-RemotePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Probe = "import os`ntry:`n os.stat('$Path')`n print('FILE_EXISTS')`nexcept OSError:`n print('FILE_MISSING')"
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        $Output = (& $Python -m mpremote connect $Port exec $Probe 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0) {
            return $Output -match "FILE_EXISTS"
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Unable to inspect remote path: $Path"
}

function Remove-RemotePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    Invoke-MpRemote exec "import os`ntry:`n os.remove('$Path')`nexcept OSError:`n pass"
}

function Copy-RuntimeAsset {
    param(
        [Parameter(Mandatory = $true)][string]$LocalPath,
        [Parameter(Mandatory = $true)][string]$RemotePath
    )
    Invoke-MpRemote fs cp $LocalPath (":" + $RemotePath)
    $script:RuntimeAssets += [PSCustomObject]@{
        Local = $LocalPath
        Remote = $RemotePath
    }
}

function Get-RemoteSha256Map {
    param([Parameter(Mandatory = $true)][string[]]$RemotePaths)
    $Arguments = @("fs", "sha256sum") + @($RemotePaths | ForEach-Object {
        ":" + $_
    })
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        $Output = (& $Python -m mpremote connect $Port @Arguments 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0) {
            $Hashes = @{}
            $CurrentPath = $null
            foreach ($Line in ($Output -split "`r?`n")) {
                if ($Line -match "^sha256sum :(.+)$") {
                    $CurrentPath = $Matches[1]
                } elseif ($CurrentPath -and $Line -match "^(?i:[0-9a-f]{64})$") {
                    $Hashes[$CurrentPath] = $Line.ToLowerInvariant()
                    $CurrentPath = $null
                }
            }
            foreach ($RemotePath in $RemotePaths) {
                if (-not $Hashes.ContainsKey($RemotePath)) {
                    throw "Unable to parse device SHA-256 for ${RemotePath}: $Output"
                }
            }
            return ,$Hashes
        }
        if ($Attempt -lt 3) {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Unable to calculate device SHA-256 values"
}

function Confirm-RuntimeAssets {
    $RemotePaths = @($RuntimeAssets | ForEach-Object { $_.Remote })
    $DeviceHashes = Get-RemoteSha256Map -RemotePaths $RemotePaths
    foreach ($Asset in $RuntimeAssets) {
        $HostHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Asset.Local).Hash.ToLowerInvariant()
        $DeviceHash = $DeviceHashes[$Asset.Remote]
        if ($HostHash -ne $DeviceHash) {
            throw "SHA-256 mismatch for $($Asset.Remote): host=$HostHash device=$DeviceHash"
        }
    }
    Write-Host "Verified SHA-256 for $($RuntimeAssets.Count) runtime assets."
}

function Build-FontAssets {
    & $Python (Join-Path $ProjectRoot "tools\build_fonts.py") `
        --source-dir (Join-Path $Source "fonts") `
        --output-dir $FontBuild
    if ($LASTEXITCODE -ne 0) {
        throw "compact font asset generation failed"
    }
}

function Test-MpyCompatibility {
    if (-not (Test-Path -LiteralPath $MpyCross)) {
        Write-Warning "mpy-cross is unavailable; deploying source files."
        return $false
    }
    $Version = (& $MpyCross --version 2>&1 | Out-String)
    if ($Version -notmatch "mpy v6\.3") {
        Write-Warning "mpy-cross does not emit the required mpy v6.3 format: $Version"
        return $false
    }

    New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
    # Keep compiler diagnostics out of the function pipeline so its caller
    # receives only the compatibility Boolean.
    & $MpyCross -march=xtensawin -o $ProbeMpy $ProbeSource 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Unable to build the native mpy ABI probe; deploying source files."
        return $false
    }

    $ProbeRemote = "/sci_calc_mpy_probe.mpy"
    try {
        Invoke-MpRemote fs cp $ProbeMpy (":" + $ProbeRemote) | Out-Null
        $Probe = "import sys`nif '/' not in sys.path: sys.path.insert(0, '/')`nimport sci_calc_mpy_probe`nassert sci_calc_mpy_probe.PROBE_VALUE == 42`nprint('MPY_ABI_OK')"
        Invoke-MpRemote exec $Probe | Out-Null
        Write-Host "Device accepted the native mpy ABI probe."
        return $true
    } catch {
        Write-Warning "Device rejected the native mpy ABI probe; deploying source files. $($_.Exception.Message)"
        return $false
    } finally {
        try {
            Remove-RemotePath -Path $ProbeRemote | Out-Null
        } catch {
            Write-Warning "Unable to remove temporary mpy ABI probe: $($_.Exception.Message)"
        }
    }
}

function Build-MpyAssets {
    $Excluded = @("boot.py", "internal_main.py", "recovery.py", "sdcard.py", "launch.py")
    Get-ChildItem -LiteralPath $Source -Recurse -Filter "*.py" | ForEach-Object {
        $Relative = $_.FullName.Substring($Source.Length + 1).Replace("\", "/")
        if ($Excluded -contains $Relative -or $Relative.Split("/") -contains "__pycache__") {
            return
        }
        $MpyRelative = [System.IO.Path]::ChangeExtension($Relative, ".mpy").Replace("\", "/")
        $Output = Join-Path $MpyBuild $MpyRelative.Replace("/", "\")
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
        & $MpyCross -march=xtensawin -X no-source-lines -s $Relative `
            -o $Output $_.FullName
        if ($LASTEXITCODE -ne 0) {
            throw "mpy-cross failed: $Relative"
        }
    }
}

Build-FontAssets

Write-Host "Installing internal launch and recovery files on $Port..."
Invoke-MpRemote exec "import os`ntry: os.mkdir('/display')`nexcept OSError: pass"
Copy-RuntimeAsset (Join-Path $Source "boot.py") "/boot.py"
Copy-RuntimeAsset (Join-Path $Source "sdcard.py") "/sdcard.py"
Copy-RuntimeAsset (Join-Path $Source "internal_main.py") "/main.py"
Copy-RuntimeAsset (Join-Path $Source "recovery.py") "/recovery.py"
Copy-RuntimeAsset (Join-Path $Source "display\ssd1322.py") "/display/ssd1322.py"
Copy-RuntimeAsset (Join-Path $Source "display\mono_palette.py") "/display/mono_palette.py"

# The old application may still own the shared SPI2 host. A reset releases it
# and lets the newly-installed /boot.py become the sole owner of SD setup.
Write-Host "Restarting into the new internal launcher..."
Invoke-MpRemote reset
Start-Sleep -Milliseconds 800
Wait-SdMount

$UseMpy = [bool](Test-MpyCompatibility)
if ($UseMpy) {
    Build-MpyAssets
}

$Directories = @("calc", "display", "fonts", "functions", "input", "screens", "ui", "utils")
$CreateDirectories = "import os`n" + (($Directories | ForEach-Object {
    "try: os.mkdir('/sd/$_')`nexcept OSError: pass"
}) -join "`n")
Invoke-MpRemote exec $CreateDirectories

$ObsoleteRuntimeFiles = @(
    "/sd/ui/lazy_screen.py",
    "/sd/ui/lazy_screen.mpy",
    "/sd/ui/residency.py",
    "/sd/ui/residency.mpy",
    "/sd/anim/__init__.py",
    "/sd/anim/__init__.mpy",
    "/sd/anim/engine.py",
    "/sd/anim/engine.mpy"
)
foreach ($RemotePath in $ObsoleteRuntimeFiles) {
    Remove-RemotePath -Path $RemotePath
}

Write-Host ("Uploading SD application as " + $(if ($UseMpy) { ".mpy" } else { ".py" }) + " files...")
Copy-RuntimeAsset (Join-Path $Source "launch.py") "/sd/launch.py"

$ExcludedSourceFiles = @("boot.py", "internal_main.py", "recovery.py", "sdcard.py", "launch.py", "settings.json", "vars.json")
Get-ChildItem -LiteralPath $Source -Recurse -File | ForEach-Object {
    $Relative = $_.FullName.Substring($Source.Length + 1).Replace("\", "/")
    if ($ExcludedSourceFiles -contains $Relative -or $Relative.Split("/") -contains "__pycache__") {
        return
    }
    if ($Relative.StartsWith("fonts/")) {
        return
    }
    if ($_.Extension -ne ".py") {
        return
    }

    $IsPlugin = $Relative.StartsWith("functions/")
    if ($UseMpy -and -not $IsPlugin) {
        $MpyRelative = [System.IO.Path]::ChangeExtension($Relative, ".mpy").Replace("\", "/")
        $LocalPath = Join-Path $MpyBuild $MpyRelative.Replace("/", "\")
        if (-not (Test-Path -LiteralPath $LocalPath)) {
            throw "Missing compiled runtime module: $MpyRelative"
        }
        Remove-RemotePath -Path ("/sd/" + $Relative)
        Copy-RuntimeAsset $LocalPath ("/sd/" + $MpyRelative)
    } else {
        if (-not $IsPlugin) {
            $MpyRelative = [System.IO.Path]::ChangeExtension($Relative, ".mpy").Replace("\", "/")
            Remove-RemotePath -Path ("/sd/" + $MpyRelative)
        }
        Copy-RuntimeAsset $_.FullName ("/sd/" + $Relative)
    }
}

foreach ($Name in @("Bally7x9", "Neato5x7", "FixedFont5x8")) {
    $FontAsset = Join-Path $FontBuild ($Name + ".xglcd")
    if (-not (Test-Path -LiteralPath $FontAsset)) {
        throw "Missing generated font asset: $FontAsset"
    }
    Copy-RuntimeAsset $FontAsset ("/sd/fonts/" + $Name + ".xglcd")
    Remove-RemotePath -Path ("/sd/fonts/" + $Name + ".c")
}

$Preserved = @("settings.json", "vars.json")
foreach ($Name in $Preserved) {
    $RemotePath = "/sd/$Name"
    if (Test-RemotePath -Path $RemotePath) {
        Write-Host "Preserving existing $RemotePath"
    } else {
        Write-Host "Initializing $RemotePath"
        Copy-RuntimeAsset (Join-Path $Source $Name) $RemotePath
    }
}

if ($UseMpy) {
    Invoke-MpRemote exec "import sys`nsys.path.insert(0, '/sd')`nimport main`nprint('MPY_APP_IMPORT_OK')"
}

Confirm-RuntimeAssets

if ($Reset) {
    Write-Host "Resetting device..."
    Invoke-MpRemote reset
}

Write-Host "SCI-CALC deployment complete."

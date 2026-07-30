from pathlib import Path


def test_deploy_preserves_runtime_settings_and_variables():
    script = (Path(__file__).parents[1] / "deploy.ps1").read_text(encoding="utf-8")

    assert '$Preserved = @("settings.json", "vars.json")' in script
    assert "Test-RemotePath -Path $RemotePath" in script
    assert "Preserving existing $RemotePath" in script


def test_deploy_hashes_freshly_initialized_settings_and_variables():
    script = (Path(__file__).parents[1] / "deploy.ps1").read_text(encoding="utf-8")

    assert "Initializing $RemotePath" in script
    assert "Copy-RuntimeAsset (Join-Path $Source $Name) $RemotePath" in script


def test_deploy_uses_mpy_only_after_an_on_device_abi_probe():
    script = (Path(__file__).parents[1] / "deploy.ps1").read_text(encoding="utf-8")

    assert '$WorkRoot = Join-Path $ProjectRoot ".work"' in script
    assert '$BuildRoot = Join-Path $WorkRoot "mpy"' in script
    assert "Test-MpyCompatibility" in script
    assert "sci_calc_mpy_probe.mpy" in script
    assert "$UseMpy" in script
    assert "[bool](Test-MpyCompatibility)" in script
    assert "-march=xtensawin" in script
    assert "-X no-source-lines" in script
    assert "-s $Relative" in script


def test_deploy_verifies_hashes_for_every_runtime_asset_and_compact_fonts():
    script = (Path(__file__).parents[1] / "deploy.ps1").read_text(encoding="utf-8")

    assert "tools\\build_fonts.py" in script
    assert ".xglcd" in script
    assert "Get-FileHash" in script
    assert '"fs", "sha256sum"' in script
    assert "Get-RemoteSha256Map" in script
    assert "$RuntimeAssets" in script

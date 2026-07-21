from pathlib import Path


def test_deploy_preserves_runtime_settings_and_variables():
    script = (Path(__file__).parents[1] / "deploy.ps1").read_text(encoding="utf-8")

    assert '$Preserved = @("settings.json", "vars.json")' in script
    assert "Test-RemotePath -Path $RemotePath" in script
    assert "Preserving existing $RemotePath" in script

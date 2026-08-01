"""Build and flash the single SCI-CALC product firmware."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
WORK_ROOT = PROJECT_ROOT / ".work"
PROCESS_TEMP_ROOT = WORK_ROOT / "temp"
PYTHON_CACHE_ROOT = WORK_ROOT / "pycache"
PROCESS_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
PYTHON_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
for _name in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_name] = str(PROCESS_TEMP_ROOT)
os.environ["PYTHONPYCACHEPREFIX"] = str(PYTHON_CACHE_ROOT)
sys.pycache_prefix = str(PYTHON_CACHE_ROOT)


def _powershell_executable():
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        raise RuntimeError("PowerShell is required to deploy SCI-CALC")
    return executable


def _invoke(script, arguments, project_root, runner, powershell):
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    command.extend(arguments)
    result = runner(command, cwd=project_root, check=False)
    if result.returncode != 0:
        raise RuntimeError(script.name + " failed with exit code "
                           + str(result.returncode))


def run(port, *, build=True, baud=921600, project_root=PROJECT_ROOT,
        runner=subprocess.run, powershell=None, emit=print):
    """Optionally rebuild, then flash only the product application image."""
    if not isinstance(port, str) or not port.strip():
        raise ValueError("port must be a non-empty string")
    if baud not in (460800, 921600):
        raise ValueError("unsupported flash baud")

    project_root = Path(project_root)
    tools_root = project_root / "tools"
    image = project_root / ".work" / "firmware" / "product" / "micropython.bin"
    powershell = powershell or _powershell_executable()

    if build:
        _invoke(
            tools_root / "build_firmware.ps1", (), project_root, runner,
            powershell)
    if not image.is_file() or image.stat().st_size <= 0:
        raise RuntimeError("Missing SCI-CALC product firmware: " + str(image))

    emit("RELEASE_FIRMWARE_READY " + str(image))
    _invoke(
        tools_root / "flash_firmware.ps1",
        ("-Port", port, "-Baud", str(baud)),
        project_root, runner, powershell)
    emit("RELEASE_APPLIED port=" + port + " image=" + str(image))
    return image


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Deploy SCI-CALC as one self-contained firmware image.")
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, choices=(460800, 921600),
                        default=921600)
    parser.add_argument(
        "--skip-build", action="store_true",
        help="flash the existing .work firmware image without rebuilding")
    args = parser.parse_args(argv)
    run(args.port, build=not args.skip_build, baud=args.baud)


if __name__ == "__main__":
    main()

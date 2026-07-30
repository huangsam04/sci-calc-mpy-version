# Fast in-place deployment with transactional first-provision support.
import argparse
import os
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
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "source"))

from tools import release_build, release_device_mpremote
from tools.release_apply import apply_release
from tools.release_plan import validate_release_plan
from tools.release_protocol import SelectionTicket, SlotRef

_MPY_CROSS = (
    PROJECT_ROOT.parent / "micropython" / "mpy-cross" / "build"
    / "mpy-cross.exe")


def _mpy_cross_compiler(executable):
    def compile_module(source_path, output_path):
        source_path = Path(source_path)
        try:
            source_name = source_path.relative_to(
                PROJECT_ROOT / "source").as_posix()
        except ValueError:
            source_name = source_path.name
        result = subprocess.run(
            (str(executable), "-march=xtensawin", "-X", "no-source-lines",
             "-s", source_name, "-o", str(output_path), str(source_path)),
            capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                "mpy-cross failed for " + str(source_path) + ": "
                + result.stderr.decode(errors="replace"))
    return compile_module


def apply_fast_release(plan, adapter):
    """Update one trusted confirmed slot and reset into the new release."""
    validate_release_plan(plan)
    ticket = adapter.run_session(lambda session: session.sync_confirmed(plan))
    if (not isinstance(ticket, SelectionTicket)
            or not isinstance(ticket.slot_ref, SlotRef)):
        raise ValueError("release adapter returned an invalid selection ticket")
    if (ticket.slot_ref.release_id != plan.release_id
            or ticket.slot_ref.manifest_sha256 != plan.manifest_sha256):
        raise ValueError("selection ticket release identity mismatch")

    return plan.release_id


def run(port, mode, compiler, device_factory, project_root=PROJECT_ROOT,
        adapter_factory=None, boot_wait_s=5.0, emit=print,
        transactional=False):
    plans = release_build.prepare_release_plans(project_root, compiler)
    plan = plans.source if mode == "source" else plans.mpy
    emit("RELEASE_ADMISSION_PLAN " + plan.release_id)
    emit("RELEASE_ADMISSION_MANIFEST_SHA256 " + plan.manifest_sha256)

    if adapter_factory is None:
        adapter = release_device_mpremote.MpremoteReleaseAdapter(
            device_factory, boot_wait_s=boot_wait_s)
    else:
        adapter = adapter_factory(device_factory)
    deploy = apply_release if transactional else apply_fast_release
    release_id = deploy(plan, adapter)
    emit("RELEASE_APPLIED " + release_id)
    return release_id


def main():
    parser = argparse.ArgumentParser(
        description="Deploy SCI-CALC to a device.")
    parser.add_argument("--port", required=True)
    parser.add_argument("--mode", choices=("source", "mpy"), required=True)
    parser.add_argument("--mpy-cross", default=str(_MPY_CROSS))
    parser.add_argument("--boot-wait", type=float, default=5.0)
    parser.add_argument(
        "--transactional", action="store_true",
        help="first-provision or repair through the A/B deployment path")
    args = parser.parse_args()

    def device_factory():
        return release_device_mpremote.MpremoteDevice(args.port)

    run(
        args.port,
        args.mode,
        _mpy_cross_compiler(args.mpy_cross),
        device_factory,
        boot_wait_s=args.boot_wait,
        transactional=args.transactional,
    )


if __name__ == "__main__":
    main()

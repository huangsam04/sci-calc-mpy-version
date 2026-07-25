# Transactional release deployment entry point.
# Phase 1 runs the trusted adoption (idempotent), phase 2 applies the
# release through the A/B transaction. Every device contact is wrapped in
# reset/close semantics and any failure path still attempts a final reset.
import argparse
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "source"))

from tools import release_adoption, release_build, release_device_mpremote
from tools.release_apply import apply_release

_MPY_CROSS = (
    PROJECT_ROOT.parent / "micropython" / "mpy-cross" / "build"
    / "mpy-cross.exe")


def _mpy_cross_compiler(executable):
    def compile_module(source_path, output_path):
        result = subprocess.run(
            (str(executable), "-march=xtensawin",
             "-o", str(output_path), str(source_path)),
            capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                "mpy-cross failed for " + str(source_path) + ": "
                + result.stderr.decode(errors="replace"))
    return compile_module


def run(port, mode, compiler, device_factory, project_root=PROJECT_ROOT,
        baseline_hashes=None, adapter_factory=None, boot_wait_s=10.0,
        emit=print):
    plans = release_build.prepare_release_plans(project_root, compiler)
    plan = plans.source if mode == "source" else plans.mpy
    emit("RELEASE_PLAN " + plan.release_id)
    emit("RELEASE_MANIFEST_SHA256 " + plan.manifest_sha256)

    phase = "adoption"
    try:
        device = device_factory()
        device.connect()
        try:
            changed = release_adoption.adopt_device(
                device, plan, baseline_hashes=baseline_hashes)
            emit("RELEASE_ADOPTION "
                 + ("applied" if changed else "already-current"))
        finally:
            try:
                device.reset()
            except Exception:
                pass
            time.sleep(boot_wait_s)
            device.close()

        phase = "release"
        if adapter_factory is None:
            adapter = release_device_mpremote.MpremoteReleaseAdapter(
                device_factory)
        else:
            adapter = adapter_factory(device_factory)
        release_id = apply_release(plan, adapter)
        emit("RELEASE_APPLIED " + release_id)
        return release_id
    except BaseException:
        try:
            recovery_device = device_factory()
            recovery_device.connect()
            try:
                recovery_device.reset()
            finally:
                recovery_device.close()
        except Exception:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(
        description="Deploy SCI-CALC through the transactional A/B release.")
    parser.add_argument("--port", required=True)
    parser.add_argument("--mode", choices=("source", "mpy"), required=True)
    parser.add_argument("--mpy-cross", default=str(_MPY_CROSS))
    parser.add_argument("--boot-wait", type=float, default=10.0)
    args = parser.parse_args()

    def device_factory():
        return release_device_mpremote.MpremoteDevice(args.port)

    run(
        args.port,
        args.mode,
        _mpy_cross_compiler(args.mpy_cross),
        device_factory,
        boot_wait_s=args.boot_wait,
    )


if __name__ == "__main__":
    main()

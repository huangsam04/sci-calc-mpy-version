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


def run(port, mode, compiler, device_factory, project_root=PROJECT_ROOT,
        baseline_hashes=None, adapter_factory=None, boot_wait_s=10.0,
        emit=print):
    plans = release_build.prepare_release_plans(project_root, compiler)
    plan = plans.source if mode == "source" else plans.mpy
    admission = release_adoption.prepare_adoption(
        plan, baseline_hashes=baseline_hashes)
    # RELEASE_ADMISSION_* lines are host-side admission evidence computed
    # before any device contact. Observed device evidence is emitted only
    # afterwards, as RELEASE_ADOPTION_RECEIPT and RELEASE_APPLIED; the two
    # namespaces are deliberately distinct and must stay that way.
    emit("RELEASE_ADMISSION_PLAN " + plan.release_id)
    emit("RELEASE_ADMISSION_MANIFEST_SHA256 " + plan.manifest_sha256)
    emit("RELEASE_ADMISSION_BOOTSTRAP_SHA256 " + admission.bootstrap_sha256)
    emit("RELEASE_ADMISSION_BASELINE_SHA256 " + admission.baseline_sha256)

    recovery_contact_allowed = False
    try:
        device = device_factory()
        # A factory failure has not yielded a transport that this deployment
        # may recover. In particular, do not turn a failed construction into
        # an unsolicited second attempt to contact the device.
        recovery_contact_allowed = True
        connected = False
        primary_error = None
        try:
            device.connect()
            connected = True
            receipt = release_adoption.adopt_prepared_device(
                device, admission)
            emit("RELEASE_ADOPTION_RECEIPT " + receipt.bootstrap_sha256
                 + " " + receipt.baseline_sha256
                 + (" applied" if receipt.changed else " already-current"))
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_error = None
            try:
                if connected:
                    try:
                        device.reset()
                    except BaseException as error:
                        cleanup_error = error
                    try:
                        time.sleep(boot_wait_s)
                    except BaseException as error:
                        if cleanup_error is None:
                            cleanup_error = error
            finally:
                try:
                    device.close()
                except BaseException as error:
                    if cleanup_error is None:
                        cleanup_error = error
            if primary_error is None and cleanup_error is not None:
                raise cleanup_error

        if adapter_factory is None:
            adapter = release_device_mpremote.MpremoteReleaseAdapter(
                device_factory)
        else:
            adapter = adapter_factory(device_factory)
        release_id = apply_release(plan, adapter)
        emit("RELEASE_APPLIED " + release_id)
        return release_id
    except BaseException:
        # The recovery reset must never be the first device contact: a
        # host-side failure, including device construction, leaves the
        # hardware untouched. Recovery itself is best effort and must not
        # hide the error that triggered it.
        if recovery_contact_allowed:
            try:
                recovery_device = device_factory()
                try:
                    recovery_device.connect()
                    recovery_device.reset()
                finally:
                    recovery_device.close()
            except BaseException:
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

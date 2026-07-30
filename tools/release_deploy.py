# Fast in-place deployment with an explicit transactional A/B fallback.
import argparse
import os
import subprocess
import sys
import time
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

from tools import release_adoption, release_build, release_device_mpremote
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
        baseline_hashes=None, adapter_factory=None, boot_wait_s=5.0,
        emit=print, adopt=True, transactional=True):
    if adopt and not transactional:
        raise ValueError("--adopt requires --transactional")
    plans = release_build.prepare_release_plans(project_root, compiler)
    plan = plans.source if mode == "source" else plans.mpy
    admission = None
    if adopt:
        admission = release_adoption.prepare_adoption(
            plan, baseline_hashes=baseline_hashes)
    # RELEASE_ADMISSION_* lines are host-side admission evidence computed
    # before any device contact. Observed device evidence is emitted only
    # afterwards, as RELEASE_ADOPTION_RECEIPT and RELEASE_APPLIED; the two
    # namespaces are deliberately distinct and must stay that way.
    emit("RELEASE_ADMISSION_PLAN " + plan.release_id)
    emit("RELEASE_ADMISSION_MANIFEST_SHA256 " + plan.manifest_sha256)
    if adopt:
        emit("RELEASE_ADMISSION_BOOTSTRAP_SHA256 "
             + admission.bootstrap_sha256)
        emit("RELEASE_ADMISSION_BASELINE_SHA256 "
             + admission.baseline_sha256)
    else:
        emit("RELEASE_ADOPTION_SKIPPED already-provisioned")

    recovery_contact_allowed = False
    try:
        if adopt:
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
                     + (" applied" if receipt.changed
                        else " already-current"))
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
                device_factory, boot_wait_s=boot_wait_s)
        else:
            adapter = adapter_factory(device_factory)
        deploy = apply_release if transactional else apply_fast_release
        release_id = deploy(plan, adapter)
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
        description="Deploy SCI-CALC to an already-provisioned device.")
    parser.add_argument("--port", required=True)
    parser.add_argument("--mode", choices=("source", "mpy"), required=True)
    parser.add_argument("--mpy-cross", default=str(_MPY_CROSS))
    parser.add_argument("--boot-wait", type=float, default=5.0)
    parser.add_argument(
        "--transactional", action="store_true",
        help="use the full A/B deployment instead of in-place incremental sync")
    parser.add_argument(
        "--adopt", action="store_true",
        help="install or repair the internal bootstrap before deployment")
    args = parser.parse_args()
    if args.adopt and not args.transactional:
        parser.error("--adopt requires --transactional")

    def device_factory():
        return release_device_mpremote.MpremoteDevice(args.port)

    run(
        args.port,
        args.mode,
        _mpy_cross_compiler(args.mpy_cross),
        device_factory,
        boot_wait_s=args.boot_wait,
        adopt=args.adopt,
        transactional=args.transactional,
    )


if __name__ == "__main__":
    main()

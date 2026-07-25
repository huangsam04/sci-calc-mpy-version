"""Apply one immutable release through reset-separated A/B slot sessions."""

from tools.release_plan import validate_release_plan
from tools.release_protocol import (
    ColdBootObservation,
    PhaseFailure,
    ReleaseFailure,
    ReleaseSmokeResult,
    SelectionTicket,
    SelectorRecord,
    SlotImage,
    SlotRef,
)


def _validate_smoke_result(plan, result):
    if not isinstance(result, ReleaseSmokeResult):
        raise ValueError("invalid trial smoke result")
    if result.release_id != plan.release_id:
        raise ValueError("trial smoke release identity mismatch")
    if result.app_version != plan.app_version:
        raise ValueError("trial smoke application version mismatch")
    if result.mode != plan.mode:
        raise ValueError("trial smoke build mode mismatch")
    if result.abi_tag != plan.abi_tag:
        raise ValueError("trial smoke ABI mismatch")
    if result.resident_runtime is not True:
        raise ValueError("trial smoke resident runtime is not ready")
    if result.root_visible is not True:
        raise ValueError("trial smoke root visible contract failed")
    buffers = result.buffers
    if not isinstance(buffers, tuple) or len(buffers) != 1:
        raise ValueError("trial smoke framebuffer contract failed")
    main_buffer = buffers[0]
    if (not isinstance(main_buffer, tuple)
            or len(main_buffer) != 3
            or main_buffer[0] != "main"
            or main_buffer[1] != 8192
            or not isinstance(main_buffer[2], int)
            or isinstance(main_buffer[2], bool)
            or main_buffer[2] <= 0):
        raise ValueError("trial smoke framebuffer contract failed")


def _validate_boot_observation(plan, ticket, observation, trial):
    if not isinstance(observation, ColdBootObservation):
        raise ValueError("invalid cold boot observation")
    if (not isinstance(observation.boot_id, int)
            or isinstance(observation.boot_id, bool)
            or observation.boot_id <= 0
            or not isinstance(observation.selector_generation, int)
            or isinstance(observation.selector_generation, bool)
            or observation.selector_generation < ticket.selector_generation):
        raise ValueError("invalid cold boot observation identity")
    if observation.selected != ticket.slot_ref:
        raise ValueError("cold boot selected the wrong release slot")
    if trial:
        if (observation.selection_generation
                != ticket.selector_generation
                or observation.selector_generation
                <= ticket.selector_generation):
            raise ValueError("stale trial cold boot observation")
    elif observation.selection_generation is not None:
        raise ValueError("confirmed boot reused a trial observation")
    _validate_smoke_result(plan, observation.smoke)


def _notify(observer, event, release_id):
    if observer is not None:
        observer(event, release_id)


def _stage_candidate(plan, adapter):
    def operation(session):
        phase = "resume_confirmed"
        try:
            ticket = session.resume_confirmed(plan)
            if ticket is not None:
                return ticket
            phase = "resume_trial"
            ticket = session.resume_trial(plan)
            if ticket is not None:
                return ticket
            phase = "resume_cleanup"
            session.resume_cleanup()
            phase = "bootstrap"
            session.validate_bootstrap(plan)
            phase = "stage"
            session.stage(plan)
            phase = "verify"
            session.verify(plan)
            phase = "activate_trial"
            try:
                return session.select_trial(plan)
            except BaseException as selection_error:
                try:
                    ticket = session.reconcile_trial_selection(plan)
                except BaseException as reconcile_error:
                    raise ReleaseFailure(
                        phase,
                        selection_error,
                        (
                            PhaseFailure(
                                "reconcile_trial_selection",
                                reconcile_error,
                            ),
                        ),
                    ) from selection_error
                if ticket is not None:
                    return ticket
                raise
        except BaseException as primary:
            if isinstance(primary, ReleaseFailure):
                raise
            secondary = ()
            try:
                session.abort_staging(plan.release_id)
            except BaseException as abort_error:
                secondary = (
                    PhaseFailure("abort_staging", abort_error),)
            raise ReleaseFailure(
                phase, primary, secondary) from primary

    return adapter.run_session(operation)


def _confirm_candidate(plan, ticket, adapter, observer):
    def operation(session):
        phase = "smoke_trial"
        confirmed = False
        try:
            observation = session.read_boot_observation(ticket, trial=True)
            _validate_boot_observation(
                plan, ticket, observation, trial=True)
            phase = "observer_before_promote"
            _notify(observer, "before_promote", plan.release_id)
            phase = "promote"
            try:
                session.confirm_trial(ticket)
            except BaseException as confirm_error:
                try:
                    confirmed = session.is_release_confirmed(ticket)
                except BaseException as reconcile_error:
                    raise ReleaseFailure(
                        phase,
                        confirm_error,
                        (
                            PhaseFailure(
                                "reconcile_confirm",
                                reconcile_error,
                            ),
                        ),
                    ) from confirm_error
                raise
            try:
                confirmed = session.is_release_confirmed(ticket)
            except BaseException as readback_error:
                raise ReleaseFailure(
                    phase, readback_error) from readback_error
            if not confirmed:
                raise ValueError(
                    "trial promotion did not commit the selected release")
            phase = "observer_after_promote"
            _notify(observer, "after_promote", plan.release_id)
        except BaseException as primary:
            if isinstance(primary, ReleaseFailure):
                raise
            secondary = ()
            if not confirmed:
                try:
                    session.reject_trial(ticket)
                except BaseException as reject_error:
                    secondary = (
                        PhaseFailure("rollback_trial", reject_error),)
            raise ReleaseFailure(
                phase, primary, secondary) from primary

    adapter.run_session(operation)


def _finalize_release(plan, ticket, adapter):
    def operation(session):
        phase = "smoke_confirmed"
        try:
            observation = session.read_boot_observation(
                ticket, trial=False)
            _validate_boot_observation(
                plan, ticket, observation, trial=False)
            phase = "cleanup"
            session.finalize_release(ticket, plan)
        except BaseException as primary:
            secondary = ()
            if phase == "smoke_confirmed":
                try:
                    session.rollback_confirmation(ticket)
                except BaseException as rollback_error:
                    secondary = (
                        PhaseFailure(
                            "rollback_confirmation",
                            rollback_error,
                        ),
                    )
            raise ReleaseFailure(
                phase, primary, secondary) from primary

    adapter.run_session(operation)


def apply_release(plan, adapter, observer=None):
    """Atomically install *plan* through cold-booted A/B slot selection."""
    validate_release_plan(plan)
    ticket = _stage_candidate(plan, adapter)
    if not isinstance(ticket, SelectionTicket):
        raise ValueError("release adapter returned an invalid selection ticket")
    if not isinstance(ticket.slot_ref, SlotRef):
        raise ValueError("release adapter returned an invalid selection ticket")
    if (ticket.slot_ref.release_id != plan.release_id
            or ticket.slot_ref.manifest_sha256 != plan.manifest_sha256):
        raise ValueError("selection ticket release identity mismatch")
    if not ticket.already_confirmed:
        _confirm_candidate(plan, ticket, adapter, observer)
    _finalize_release(plan, ticket, adapter)
    return plan.release_id


__all__ = (
    "ColdBootObservation",
    "PhaseFailure",
    "ReleaseFailure",
    "ReleaseSmokeResult",
    "SelectionTicket",
    "SelectorRecord",
    "SlotImage",
    "SlotRef",
    "apply_release",
)

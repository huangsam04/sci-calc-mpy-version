"""Neutral release-selector records shared by release adapters."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class PhaseFailure:
    phase: str
    error: BaseException


class ReleaseFailure(Exception):
    """One primary release error plus ordered recovery/reset errors."""

    def __init__(self, phase, primary, secondary=()):
        self.phase = phase
        self.primary = primary
        self.secondary = tuple(secondary)
        super().__init__(
            "release failed during " + phase + ": " + str(primary))


def run_guarded_session(operation, reset, close):
    """Run operation, then exactly one reset and one close, in order.

    Recovery/reset/close failures are merged into the operation's
    ReleaseFailure as ordered secondaries; a clean operation with a failed
    reset or close raises its own ReleaseFailure instead.
    """
    result = None
    operation_error = None
    reset_error = None
    close_error = None
    try:
        try:
            result = operation()
        except BaseException as error:
            operation_error = error
        finally:
            try:
                reset()
            except BaseException as error:
                reset_error = error
    finally:
        try:
            close()
        except BaseException as error:
            close_error = error

    if operation_error is not None:
        if isinstance(operation_error, ReleaseFailure):
            secondary = list(operation_error.secondary)
            if reset_error is not None:
                secondary.append(PhaseFailure("reset", reset_error))
            if close_error is not None:
                secondary.append(PhaseFailure("close", close_error))
            if len(secondary) != len(operation_error.secondary):
                raise ReleaseFailure(
                    operation_error.phase,
                    operation_error.primary,
                    secondary,
                ) from operation_error
        raise operation_error

    if reset_error is not None:
        secondary = ()
        if close_error is not None:
            secondary = (PhaseFailure("close", close_error),)
        raise ReleaseFailure(
            "reset", reset_error, secondary) from reset_error
    if close_error is not None:
        raise ReleaseFailure("close", close_error) from close_error
    return result


@dataclass(frozen=True, slots=True)
class SlotRef:
    name: str
    release_id: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class SelectorRecord:
    generation: int
    confirmed: Optional[SlotRef]
    trial: Optional[SlotRef]
    trial_generation: Optional[int]
    trial_consumed: bool
    retired: tuple
    confirmation_pending: bool = False


@dataclass(frozen=True, slots=True)
class SelectionTicket:
    selector_generation: int
    slot_ref: SlotRef
    already_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ReleaseSmokeResult:
    release_id: str
    app_version: str
    mode: str
    abi_tag: str
    resident_runtime: bool
    root_visible: bool
    buffers: tuple


@dataclass(frozen=True, slots=True)
class ColdBootObservation:
    selector_generation: int
    selection_generation: Optional[int]
    boot_id: int
    selected: Optional[SlotRef]
    smoke: Optional[ReleaseSmokeResult]


@dataclass(frozen=True, slots=True)
class SlotImage:
    slot_ref: SlotRef
    manifest_bytes: bytes
    assets: tuple

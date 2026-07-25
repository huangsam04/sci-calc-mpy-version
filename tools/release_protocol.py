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

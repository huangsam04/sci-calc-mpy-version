# Fresh full-build Module for immutable release plans.
# Builds fonts and compiled modules from scratch, then derives and
# validates both the source and the mpy release plan. The compiler is an
# injected callable so host tests can drive the Module with a fake.
from dataclasses import dataclass
from pathlib import Path
import shutil

from tools import build_fonts
from tools.release_plan import (
    MPY_MODE,
    SOURCE_MODE,
    is_compiled_in_mpy,
    plan_release,
    snapshot_release_tree,
    validate_release_plan,
)

_WORK_DIR_NAME = ".work"
_BUILD_DIR_NAME = "mpy"


@dataclass(frozen=True, slots=True)
class ReleasePlans:
    source: object
    mpy: object


def prepare_release_plans(project_root, compiler):
    """Build every asset and return validated (source, mpy) plans."""
    project_root = Path(project_root)
    source_root = project_root / "source"
    build_root = project_root / _WORK_DIR_NAME / _BUILD_DIR_NAME
    if build_root.exists():
        shutil.rmtree(build_root)
    build_root.mkdir(parents=True)

    build_fonts.build_all(source_root / "fonts", build_root / "fonts")

    snapshot = snapshot_release_tree(source_root, build_root)
    for path, _content in snapshot.source_files:
        if not is_compiled_in_mpy(path):
            continue
        output = build_root / (path[:-3] + ".mpy")
        output.parent.mkdir(parents=True, exist_ok=True)
        compiler(source_root / path, output)
        if not output.exists() or output.stat().st_size == 0:
            raise ValueError(
                "compiler produced no compiled output for: " + path)

    snapshot = snapshot_release_tree(source_root, build_root)
    source_plan = plan_release(snapshot, mode=SOURCE_MODE)
    validate_release_plan(source_plan)
    mpy_plan = plan_release(snapshot, mode=MPY_MODE)
    validate_release_plan(mpy_plan)
    return ReleasePlans(source=source_plan, mpy=mpy_plan)


__all__ = ("ReleasePlans", "prepare_release_plans")

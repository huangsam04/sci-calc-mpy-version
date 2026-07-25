"""Apply one immutable release plan through a session-owning adapter."""

from tools.release_plan import cleanup_candidates


def apply_release(plan, adapter, observer=None):
    """Promote *plan* while preserving paths the prior release did not own."""
    del observer  # Progress events are outside the first tracer bullet.

    def operation(session):
        previous_manifest, previous_manifest_sha256 = (
            session.read_confirmed_manifest()
        )
        cleanup = cleanup_candidates(
            previous_manifest,
            previous_manifest_sha256,
            plan,
        )
        session.stage(plan)
        session.activate(plan.release_id, cleanup)
        return plan.release_id

    return adapter.run_session(operation)

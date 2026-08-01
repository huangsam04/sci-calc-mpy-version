from pathlib import Path

import pytest

from runtime_handle import (
    ApplicationBinding, get_resident_runtime, set_resident_runtime)
import runtime_handle as runtime_handle_module
from runtime_materialize import (
    RuntimeHandle, get_resident_runtime as materialize_runtime)


SOURCE = Path(__file__).parents[1] / "source"


class _PageOwnerNav:
    __slots__ = ("root", "stack", "memory")

    def __init__(self, root=None):
        self.root = object() if root is None else root
        self.stack = [self.root]
        self.memory = object()


def _binding(nav=None):
    nav = _PageOwnerNav() if nav is None else nav
    return (
        ApplicationBinding(nav, nav.root, object(), object(), object()),
        nav)


def test_application_binding_keeps_only_page_owner_and_shared_state():
    nav = _PageOwnerNav()
    registry = object()
    settings = object()
    persistence = object()
    binding = ApplicationBinding(
        nav, nav.root, registry, settings, persistence)

    assert binding._nav is nav
    assert binding.root is nav.root
    assert binding.registry is registry
    assert binding.settings is settings
    assert binding.persistence is persistence
    assert binding.require_page_owner(nav.root) is binding
    assert not hasattr(binding, "screens")
    assert not hasattr(binding, "calculator")
    assert not hasattr(binding, "__dict__")
    with pytest.raises(AttributeError):
        binding._nav = _PageOwnerNav()


def test_binding_does_not_expose_acceptance_page_lifecycle():
    binding, _nav = _binding()

    assert not hasattr(binding, "open_page_scenario_transaction")
    assert not hasattr(binding, "acquire_page")
    assert not hasattr(binding, "release_page")


def test_application_binding_requires_exact_resident_state_and_root():
    nav = _PageOwnerNav()
    with pytest.raises(ValueError, match="resident state"):
        ApplicationBinding(nav, nav.root, None, object(), object())

    binding = ApplicationBinding(
        nav, nav.root, object(), object(), object())
    with pytest.raises(RuntimeError, match="page owner"):
        binding.require_page_owner(object())
    nav.stack[0] = object()
    with pytest.raises(RuntimeError, match="page owner"):
        binding.require_page_owner()


def test_resident_binding_is_registered_without_materializing_a_handle():
    binding, _nav = _binding()
    previous = get_resident_runtime()
    try:
        runtime_handle_module._resident_runtime = binding
        assert get_resident_runtime() is binding
    finally:
        runtime_handle_module._resident_runtime = previous


def test_acceptance_materializes_the_registered_page_owner_once():
    binding, nav = _binding()
    previous = get_resident_runtime()
    try:
        runtime_handle_module._resident_runtime = binding

        runtime = materialize_runtime()

        assert isinstance(runtime, RuntimeHandle)
        assert runtime.nav is nav
        assert runtime.root is nav.root
        assert runtime.targets == ()
        assert runtime.application_binding is binding
        assert runtime.scenario_adapter is not None
        assert runtime.optional_buffer_target is nav.memory
        assert runtime.optional_buffer_size == 104
        assert materialize_runtime() is runtime
        assert get_resident_runtime() is runtime
    finally:
        runtime_handle_module._resident_runtime = previous


def test_runtime_handle_fails_closed_for_missing_or_foreign_binding():
    binding, nav = _binding()
    missing = RuntimeHandle(nav, nav.root, (), mode="resident")
    foreign_nav = RuntimeHandle(
        _PageOwnerNav(nav.root), nav.root, (), mode="resident",
        application_binding=binding)
    foreign_root = RuntimeHandle(
        nav, object(), (), mode="resident", application_binding=binding)
    bound = RuntimeHandle(
        nav, nav.root, (), mode="resident", application_binding=binding)

    with pytest.raises(RuntimeError, match="application binding"):
        missing.require_application_binding()
    with pytest.raises(RuntimeError, match="foreign"):
        foreign_nav.require_application_binding()
    with pytest.raises(RuntimeError, match="foreign"):
        foreign_root.require_application_binding()
    assert bound.require_application_binding() is binding


def test_main_constructs_binding_without_a_resident_page_tuple():
    main_source = (SOURCE / "application.py").read_text(encoding="utf-8")
    materializer_source = (
        SOURCE / "runtime_materialize.py").read_text(encoding="utf-8")

    configured = main_source.index("nav.configure_pages(")
    binding = main_source.index("application_binding = ApplicationBinding(")
    runtime = main_source.index(
        "runtime = application_binding if run_loop else RuntimeHandle(")
    published = main_source.index("set_resident_runtime(runtime)", runtime)

    assert configured < binding < runtime < published
    assert "resident_screens" not in main_source
    assert "nav.register_screens" not in main_source
    assert "_managed" not in main_source
    binding_block = main_source[binding:runtime]
    assert "nav, main_menu, registry, settings, persistence" in binding_block
    assert "binding.require_page_owner()" in materializer_source
    assert "nav, binding.root, ()" in materializer_source
    normal_publish = main_source.index(
        '__import__("runtime_handle")._resident_runtime = runtime', runtime)
    assert runtime < normal_publish

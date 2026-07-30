from pathlib import Path

import pytest

from runtime_application_controller import (
    build_resident_application_scenario_adapter)
from runtime_handle import (
    ApplicationBinding, get_resident_runtime, set_resident_runtime)
import runtime_handle as runtime_handle_module
from runtime_materialize import (
    RuntimeHandle, get_resident_runtime as materialize_runtime)
from runtime_trusted_construction import (
    prepare_trusted_resident_scenario_adapter)


SOURCE = Path(__file__).parents[1] / "source"


class _PageTransactionNav:
    __slots__ = ("calls", "received_screens", "transaction")

    def __init__(self):
        self.calls = 0
        self.received_screens = None
        self.transaction = object()

    def open_page_scenario_transaction(self, screens):
        self.calls += 1
        self.received_screens = screens
        return self.transaction


def _canonical_screens():
    root = object()
    calculator = object()
    plot = object()
    function_panel = object()
    stopwatch = object()
    settings_screen = object()
    about = object()
    letters = object()
    function_picker = object()
    variables = object()
    return (
        root, calculator, plot, function_panel, stopwatch, settings_screen,
        about, letters, function_picker, variables)


def test_application_binding_keeps_existing_resident_identities_without_copy():
    screens = _canonical_screens()
    registry = object()
    settings = object()
    persistence = object()

    binding = ApplicationBinding(screens, registry, settings, persistence)

    assert binding.screens is screens
    assert binding.registry is registry
    assert binding.settings is settings
    assert binding.persistence is persistence
    assert binding.root is screens[0]
    assert binding.calculator is screens[1]
    assert binding.plot is screens[2]
    assert binding.function_panel is screens[3]
    assert binding.stopwatch is screens[4]
    assert binding.settings_screen is screens[5]
    assert binding.about is screens[6]
    assert binding.letters is screens[7]
    assert binding.function_picker is screens[8]
    assert binding.variables is screens[9]
    assert binding.require_canonical_screens() is binding
    assert binding.require_canonical_screens(screens[0]) is binding
    assert not hasattr(binding, "__dict__")
    with pytest.raises(AttributeError):
        binding.screens = ()
    with pytest.raises(AttributeError):
        binding.calculator = object()
    with pytest.raises(AttributeError):
        binding.unexpected = object()


def test_binding_opens_page_transaction_only_through_its_owned_nav():
    screens = _canonical_screens()
    nav = _PageTransactionNav()
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    runtime = RuntimeHandle(
        nav, screens[0], (), mode="resident", application_binding=binding)

    transaction = (
        runtime.require_application_binding().open_page_scenario_transaction())

    assert transaction is nav.transaction
    assert nav.calls == 1
    assert nav.received_screens is screens
    assert not hasattr(binding, "nav")
    with pytest.raises(AttributeError):
        binding._nav = _PageTransactionNav()


def test_runtime_packs_the_bound_plot_allowance_without_extra_instance_slots():
    screens = _canonical_screens()
    binding = ApplicationBinding(
        screens, object(), object(), object())

    runtime = RuntimeHandle(
        object(), screens[0], screens[1:6], mode="in_memory",
        optional_buffer_size=104, application_binding=binding)

    assert "optional_buffers" not in RuntimeHandle.__slots__
    assert "optional_buffer_target" not in RuntimeHandle.__slots__
    assert runtime.optional_buffer_target is screens[2]
    assert runtime.optional_buffer_size == 104


def test_application_binding_requires_an_existing_screen_tuple_and_state():
    registry = object()
    settings = object()
    persistence = object()

    with pytest.raises(TypeError, match="existing tuple"):
        ApplicationBinding([], registry, settings, persistence)
    with pytest.raises(ValueError, match="resident state"):
        ApplicationBinding((), None, settings, persistence)

    host_binding = ApplicationBinding(
        (object(),), registry, settings, persistence)
    with pytest.raises(RuntimeError, match="Canonical resident screens"):
        host_binding.require_canonical_screens()

    unavailable = ApplicationBinding(
        _canonical_screens(), registry, settings, persistence)
    with pytest.raises(RuntimeError, match="page transaction"):
        unavailable.open_page_scenario_transaction()


def test_resident_binding_is_registered_without_materializing_a_handle():
    screens = _canonical_screens()
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=object())
    previous = get_resident_runtime()
    try:
        runtime_handle_module._resident_runtime = binding

        assert get_resident_runtime() is binding
    finally:
        runtime_handle_module._resident_runtime = previous


def test_acceptance_materializes_the_registered_binding_once():
    screens = _canonical_screens()
    nav = object()
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    previous = get_resident_runtime()
    try:
        runtime_handle_module._resident_runtime = binding

        runtime = materialize_runtime()

        assert isinstance(runtime, RuntimeHandle)
        assert runtime.nav is nav
        assert runtime.root is screens[0]
        assert runtime.targets is screens
        assert runtime.application_binding is binding
        assert runtime.optional_buffer_target is screens[2]
        assert runtime.optional_buffer_size == 104
        assert materialize_runtime() is runtime
        assert get_resident_runtime() is runtime
    finally:
        runtime_handle_module._resident_runtime = previous


def test_application_binding_rejects_wrong_or_duplicate_canonical_topology():
    screens = _canonical_screens()
    registry = object()
    settings = object()
    persistence = object()
    wrong_root = ApplicationBinding(
        _canonical_screens(), registry, settings, persistence)
    duplicate = ApplicationBinding(
        (
            screens[0], screens[1], screens[1], screens[3], screens[4],
            screens[5], screens[6], screens[7], screens[8], screens[9],
        ),
        registry,
        settings,
        persistence,
    )

    with pytest.raises(RuntimeError, match="Canonical resident screens"):
        wrong_root.require_canonical_screens(screens[0])
    with pytest.raises(RuntimeError, match="Canonical resident screens"):
        duplicate.require_canonical_screens(screens[0])


def test_runtime_handle_exposes_optional_binding_and_fails_closed_when_missing():
    runtime = RuntimeHandle(object(), object(), (), mode="resident")
    screens = _canonical_screens()
    nav = _PageTransactionNav()
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    bound = RuntimeHandle(
        nav, screens[0], (), mode="resident", application_binding=binding)

    assert runtime.application_binding is None
    with pytest.raises(RuntimeError, match="application binding"):
        runtime.require_application_binding()
    assert bound.application_binding is binding
    assert bound.require_application_binding() is binding
    with pytest.raises(AttributeError):
        bound.application_binding = ApplicationBinding(
            _canonical_screens(), object(), object(), object())
    with pytest.raises(AttributeError):
        runtime.application_binding = binding


def test_runtime_handle_seals_the_exact_resident_application_adapter():
    screens = _canonical_screens()
    nav = _PageTransactionNav()
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    construction = prepare_trusted_resident_scenario_adapter(binding)
    adapter = construction.adapter
    runtime = RuntimeHandle(
        nav,
        screens[0],
        (),
        mode="resident",
        scenario_adapter=adapter,
        application_binding=binding,
    )
    construction.seal_runtime(runtime)

    assert runtime.require_resident_application_adapter() is adapter
    with pytest.raises(AttributeError):
        runtime.scenario_adapter = object()
    with pytest.raises(AttributeError, match="controller"):
        adapter._controller = object()

    other_binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    foreign = RuntimeHandle(
        nav,
        screens[0],
        (),
        mode="resident",
        scenario_adapter=adapter,
        application_binding=other_binding,
    )
    with pytest.raises(RuntimeError, match="adapter"):
        foreign.require_resident_application_adapter()

    with pytest.raises(AttributeError):
        object.__setattr__(runtime, "scenario_adapter", object())
    object.__setattr__(
        runtime, "_runtime_state", (object(), binding, None, 0))
    with pytest.raises(RuntimeError, match="adapter"):
        runtime.require_resident_application_adapter()

    compatibility_runtime = RuntimeHandle(
        nav,
        screens[0],
        (),
        mode="resident",
        scenario_adapter=build_resident_application_scenario_adapter(binding),
        application_binding=binding,
    )
    with pytest.raises(RuntimeError, match="adapter"):
        compatibility_runtime.require_resident_application_adapter()


def test_runtime_handle_rejects_bindings_for_a_foreign_nav_or_root():
    screens = _canonical_screens()
    nav = _PageTransactionNav()
    binding = ApplicationBinding(
        screens, object(), object(), object(), nav=nav)
    foreign_nav_runtime = RuntimeHandle(
        _PageTransactionNav(),
        screens[0],
        (),
        mode="resident",
        application_binding=binding,
    )
    foreign_root_runtime = RuntimeHandle(
        nav,
        object(),
        (),
        mode="resident",
        application_binding=binding,
    )

    with pytest.raises(RuntimeError, match="foreign"):
        foreign_nav_runtime.require_application_binding()
    with pytest.raises(RuntimeError, match="foreign"):
        foreign_root_runtime.require_application_binding()


def test_resident_runtime_rejects_a_canonical_binding_without_its_nav():
    screens = _canonical_screens()
    runtime = RuntimeHandle(
        _PageTransactionNav(),
        screens[0],
        (),
        mode="resident",
        application_binding=ApplicationBinding(
            screens, object(), object(), object()),
    )

    with pytest.raises(RuntimeError, match="foreign"):
        runtime.require_application_binding()


def test_main_constructs_binding_after_resident_screens_and_before_publication():
    main_source = (SOURCE / "main.py").read_text(encoding="utf-8")
    materializer_source = (
        SOURCE / "runtime_materialize.py").read_text(encoding="utf-8")

    resident_screens = main_source.index("resident_screens = (")
    registered = main_source.index("nav.register_screens(resident_screens)")
    binding = main_source.index("application_binding = ApplicationBinding(")
    runtime = main_source.index(
        "runtime = application_binding if run_loop else RuntimeHandle(")
    published = main_source.index("set_resident_runtime(runtime)", runtime)

    assert resident_screens < registered < binding < runtime < published
    binding_block = main_source[binding:runtime]
    assert "resident_screens, registry, settings, persistence" in binding_block
    assert "nav=nav" in binding_block
    assert "_managed" not in binding_block
    topology = main_source[resident_screens:binding]
    assert "main_menu, calc_screen, plot_screen, func_panel, stopwatch" in topology
    assert "settings_screen, about, letter_panel, func_picker, var_panel" in topology
    assert "prepare_trusted_resident_scenario_adapter" not in binding_block
    assert "build_resident_application_scenario_adapter" not in binding_block
    runtime_block = main_source[runtime:published]
    assert "application_binding=application_binding" in runtime_block
    assert "scenario_adapter=None" in runtime_block
    assert "optional_buffer_size=104" in materializer_source
    assert "application_binding=binding" in materializer_source
    normal_publish = main_source.index(
        '__import__("runtime_handle")._resident_runtime = runtime',
        runtime)
    assert runtime < normal_publish

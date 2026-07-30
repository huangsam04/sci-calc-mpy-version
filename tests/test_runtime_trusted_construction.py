import pytest

from runtime_application_controller import _ResidentApplicationScenarioController
from runtime_handle import ApplicationBinding
from runtime_materialize import RuntimeHandle
from runtime_scenarios import ResidentApplicationScenarioAdapter
from runtime_trusted_construction import (
    TrustedResidentScenarioConstruction,
    prepare_trusted_resident_scenario_adapter,
    require_trusted_resident_application_adapter)


class _Nav:
    __slots__ = ("root", "stack", "memory")

    def __init__(self):
        self.root = object()
        self.stack = [self.root]
        self.memory = object()

    def open_page_scenario_transaction(self):
        raise AssertionError("construction must not open a page transaction")


def _binding():
    nav = _Nav()
    binding = ApplicationBinding(
        nav, nav.root, object(), object(), object())
    return binding, nav, nav.root


def _runtime(binding, nav, root, adapter):
    return RuntimeHandle(
        nav,
        root,
        (),
        mode="resident",
        application_binding=binding,
        scenario_adapter=adapter,
    )


def test_trusted_construction_authenticates_and_releases_prepublication_refs():
    binding, nav, root = _binding()
    construction = prepare_trusted_resident_scenario_adapter(binding)
    runtime = _runtime(binding, nav, root, construction.adapter)

    assert isinstance(construction, TrustedResidentScenarioConstruction)
    with pytest.raises(RuntimeError, match="not sealed"):
        require_trusted_resident_application_adapter(
            binding, construction.adapter)
    assert construction.seal_runtime(runtime) is runtime
    assert (require_trusted_resident_application_adapter(
        binding, runtime.scenario_adapter) is runtime.scenario_adapter)
    assert construction._binding is None
    assert construction._adapter is None
    assert construction._controller is None
    assert not hasattr(construction, "__dict__")
    with pytest.raises(RuntimeError, match="already sealed"):
        _ = construction.adapter
    with pytest.raises(RuntimeError, match="already sealed"):
        construction.seal_runtime(runtime)
    with pytest.raises(AttributeError, match="immutable"):
        construction._adapter = object()


def test_trusted_construction_rejects_a_foreign_binding_before_publication():
    binding, nav, root = _binding()
    construction = prepare_trusted_resident_scenario_adapter(binding)
    foreign_binding, foreign_nav, foreign_root = _binding()
    foreign_runtime = _runtime(
        foreign_binding, foreign_nav, foreign_root, construction.adapter)

    with pytest.raises(RuntimeError, match="binding is foreign"):
        construction.seal_runtime(foreign_runtime)

    runtime = _runtime(binding, nav, root, construction.adapter)
    assert construction.seal_runtime(runtime) is runtime


def test_trusted_construction_rejects_an_equally_bound_but_foreign_adapter():
    binding, nav, root = _binding()
    construction = prepare_trusted_resident_scenario_adapter(binding)
    foreign_construction = prepare_trusted_resident_scenario_adapter(binding)
    runtime = _runtime(binding, nav, root, foreign_construction.adapter)

    with pytest.raises(RuntimeError, match="adapter is foreign"):
        construction.seal_runtime(runtime)


def test_trusted_verifier_rejects_a_plain_adapter_on_the_same_binding():
    binding, _nav, _root = _binding()
    plain_adapter = ResidentApplicationScenarioAdapter(
        _ResidentApplicationScenarioController(binding))

    with pytest.raises(RuntimeError, match="Trusted resident adapter"):
        require_trusted_resident_application_adapter(binding, plain_adapter)


def test_prepublication_proof_rejects_a_replaced_controller():
    binding, nav, root = _binding()
    construction = prepare_trusted_resident_scenario_adapter(binding)
    object.__setattr__(
        construction.adapter,
        "_controller",
        _ResidentApplicationScenarioController(binding),
    )
    runtime = _runtime(binding, nav, root, construction.adapter)

    with pytest.raises(RuntimeError, match="controller is foreign"):
        construction.seal_runtime(runtime)


def test_trusted_construction_rejects_runtime_with_foreign_nav_or_root():
    binding, nav, root = _binding()
    foreign_nav = _Nav()
    construction = prepare_trusted_resident_scenario_adapter(binding)
    wrong_nav_runtime = _runtime(
        binding, foreign_nav, root, construction.adapter)

    with pytest.raises(RuntimeError, match="foreign"):
        construction.seal_runtime(wrong_nav_runtime)

    wrong_root_runtime = _runtime(
        binding, nav, object(), construction.adapter)
    with pytest.raises(RuntimeError, match="foreign"):
        construction.seal_runtime(wrong_root_runtime)


def test_trusted_construction_rejects_an_unowned_root():
    nav = _Nav()
    binding = ApplicationBinding(
        nav, object(), object(), object(), object())

    with pytest.raises(RuntimeError, match="page owner"):
        prepare_trusted_resident_scenario_adapter(binding)

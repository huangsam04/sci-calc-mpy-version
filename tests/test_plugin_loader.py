import pytest
from pathlib import Path

import main
from calc import functions as functions_module
from calc import loader, plugin_reload
from calc.functions import EvalContext, FunctionRegistry, build_registry
from calc.limits import (MAX_DISCOVERED_PLUGIN_FILES, MAX_ENABLED_FUNCTIONS,
                         MAX_ENABLED_PLUGINS, MAX_FUNCTION_NAME_LENGTH,
                         MAX_PLUGIN_DEPENDENCY_DEPTH, MAX_PLUGIN_FUNCTIONS,
                         MAX_PLUGIN_SOURCE_BYTES)
from calc.loader import load_function_files
from calc.plugin_reload import FunctionEnvironment
from calc.parser import evaluate


SOURCE = Path(__file__).parents[1] / "source"


def test_normal_loader_keeps_the_staged_reload_transaction_cold():
    source = (SOURCE / "calc" / "loader.py").read_text(encoding="utf-8")

    assert "class PluginReloadTransaction" not in source
    assert "calc.plugin_reload" not in source


def test_plugin_registers_functions_and_broken_file_is_isolated(tmp_path):
    (tmp_path / "good.py").write_text(
        "def twice(value, context):\n"
        "    return value * 2\n"
        "def register(registry):\n"
        "    registry.prefix('twice', twice)\n",
        encoding="utf-8",
    )
    (tmp_path / "broken.py").write_text(
        "def broken(value, context): return value\n"
        "def register(registry):\n"
        "    registry.prefix('half_registered', broken)\n"
        "    raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    registry = build_registry()

    report = load_function_files(registry, func_dir=str(tmp_path))

    assert [item[0] for item in report.loaded] == ["good"]
    assert report.errors[0][0] == "broken"
    assert "half_registered" not in registry
    assert evaluate("twice(6)", EvalContext({}, registry)) == 12


def test_canonical_bundled_plugins_skip_runtime_source_compilation(
        tmp_path, monkeypatch):
    for name in ("basic", "solve", "trig"):
        (tmp_path / (name + ".py")).write_text(
            "raise AssertionError('source shim executed')\n", encoding="utf-8")
    monkeypatch.setattr(loader, "_default_func_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        loader, "_execute_plugin",
        lambda *_args: pytest.fail("bundled source was compiled"))
    registry = build_registry()

    report = load_function_files(
        registry, ["basic", "solve", "trig"], in_place=True)

    assert report.errors == []
    assert [item[0] for item in report.loaded] == ["basic", "solve", "trig"]
    assert "%" in registry
    assert "solve" in registry
    assert "sind" in registry


def test_firmware_registrations_skip_untrusted_definition_validation(
        monkeypatch):
    monkeypatch.setattr(
        FunctionRegistry, "_add",
        lambda *_args: pytest.fail("firmware definition was revalidated"))
    registry = build_registry()
    from calc.bundled_plugins import register_bundled

    for name in ("basic", "solve", "trig"):
        assert register_bundled(name, registry) is True

    assert "+" in registry
    assert "%" in registry
    assert "solve" in registry
    assert "sind" in registry


def test_registry_rejects_ambiguous_names_and_plugin_conflicts():
    registry = FunctionRegistry()
    callback = lambda value, context: value

    with pytest.raises(ValueError, match="Invalid identifier"):
        registry.prefix("bad+", callback)
    with pytest.raises(ValueError, match="require an identifier"):
        registry.prefix("!", callback)

    registry.prefix("same", callback)
    with pytest.raises(ValueError, match="already registered"):
        registry.prefix("same", callback)
    staging = FunctionRegistry()
    staging.prefix("same", callback)
    with pytest.raises(ValueError, match="already registered"):
        registry.merge(staging)


def test_registry_merge_keeps_live_state_unchanged_when_allocation_fails(
        monkeypatch):
    live = FunctionRegistry()
    live.prefix("stable", lambda value, context: value)
    staging = FunctionRegistry()
    staging.prefix("incoming", lambda value, context: value)
    original_defs = live._defs
    original_revision = live.revision
    original_symbols = live._symbolic_names

    def exhaust_heap(value):
        raise MemoryError("injected")

    monkeypatch.setattr(functions_module, "dict", exhaust_heap, raising=False)

    with pytest.raises(MemoryError, match="injected"):
        live.merge(staging)

    assert live._defs is original_defs
    assert tuple(live.keys()) == ("stable",)
    assert live.revision == original_revision
    assert live._symbolic_names is original_symbols


def test_registry_merge_preflights_revision_before_committing_live_tables():
    live = FunctionRegistry()
    live.prefix("stable", lambda value, context: value)
    staging = FunctionRegistry()
    staging.prefix("incoming", lambda value, context: value)
    original_defs = live._defs

    class ExhaustedRevision:
        def __add__(self, value):
            raise MemoryError("injected revision")

    original_revision = ExhaustedRevision()
    live._revision = original_revision

    with pytest.raises(MemoryError, match="injected revision"):
        live.merge(staging)

    assert live._defs is original_defs
    assert "stable" in live
    assert "incoming" not in live
    assert live._revision is original_revision


def test_loader_propagates_plugin_memory_error_without_mutating_live_registry(
        tmp_path):
    (tmp_path / "oom.py").write_text(
        "def temporary(value, context): return value\n"
        "def register(registry):\n"
        "    registry.prefix('temporary', temporary)\n"
        "    raise MemoryError('injected')\n",
        encoding="utf-8",
    )
    registry = build_registry(["basic"])
    original_defs = registry._defs
    original_exports = registry._plugin_exports
    original_revision = registry.revision

    with pytest.raises(MemoryError, match="injected"):
        load_function_files(registry, ["oom"], str(tmp_path))

    assert registry._defs is original_defs
    assert registry._plugin_exports is original_exports
    assert "temporary" not in registry
    assert registry.revision == original_revision


def test_plugin_commit_keeps_defs_exports_and_revision_atomic_on_oom(
        monkeypatch):
    live = FunctionRegistry()
    live.prefix("stable", lambda value, context: value)
    staging = FunctionRegistry()
    staging.prefix("incoming", lambda value, context: value)
    exports = {"incoming_helper": lambda value: value}
    original_defs = live._defs
    original_exports = live._plugin_exports
    original_dependencies = live._dependency_exports
    original_revision = live.revision
    real_dict = dict
    calls = []

    def copy_until_exports(value):
        calls.append(value)
        if len(calls) == 3:
            raise MemoryError("injected exports")
        return real_dict(value)

    monkeypatch.setattr(
        functions_module, "dict", copy_until_exports, raising=False)

    with pytest.raises(MemoryError, match="injected exports"):
        live.commit_plugin("incoming", staging, exports)

    assert live._defs is original_defs
    assert live._plugin_exports is original_exports
    assert live._dependency_exports is original_dependencies
    assert tuple(live.keys()) == ("stable",)
    assert live.revision == original_revision


def test_plugin_commit_preflights_revision_before_committing_live_tables():
    live = FunctionRegistry()
    live.prefix("stable", lambda value, context: value)
    staging = FunctionRegistry()
    staging.prefix("incoming", lambda value, context: value)
    original_defs = live._defs
    original_exports = live._plugin_exports

    class ExhaustedRevision:
        def __add__(self, value):
            raise MemoryError("injected revision")

    original_revision = ExhaustedRevision()
    live._revision = original_revision

    with pytest.raises(MemoryError, match="injected revision"):
        live.commit_plugin("incoming", staging, {})

    assert live._defs is original_defs
    assert live._plugin_exports is original_exports
    assert "stable" in live
    assert "incoming" not in live
    assert live._revision is original_revision


def test_loader_rolls_back_a_committed_plugin_when_report_metadata_ooms(
        tmp_path, monkeypatch):
    (tmp_path / "ok.py").write_text(
        "def loaded(value, context): return value\n"
        "EXPORTS = {'helper': loaded}\n"
        "def register(registry):\n"
        "    registry.prefix('loaded', loaded)\n",
        encoding="utf-8",
    )

    class ExhaustingList(list):
        def append(self, value):
            raise MemoryError("injected report metadata")

    class Report:
        def __init__(self):
            self.loaded = ExhaustingList()
            self.errors = []
            self.auto_enabled = []
            self.dependencies = {}
            self.functions = {}

    monkeypatch.setattr(loader, "LoadReport", Report)
    registry = build_registry(["basic"])
    original_defs = registry._defs
    original_exports = registry._plugin_exports
    original_revision = registry.revision

    with pytest.raises(MemoryError, match="injected report metadata"):
        load_function_files(registry, ["ok"], str(tmp_path))

    assert registry._defs is original_defs
    assert registry._plugin_exports is original_exports
    assert "loaded" not in registry
    assert registry.revision == original_revision


def test_registry_hot_reload_replaces_in_place():
    live = FunctionRegistry()
    live.prefix("old", lambda value, context: value)
    replacement = FunctionRegistry()
    replacement.prefix("new", lambda value, context: value + 1)

    live.replace(replacement)

    assert "old" not in live
    assert evaluate("new(2)", EvalContext({}, live)) == 3


def test_registry_hot_reload_keeps_plugin_errors_for_ui():
    live = FunctionRegistry()
    replacement = FunctionRegistry()
    replacement.plugin_errors = [("broken", "boom")]

    live.replace(replacement)

    assert live.plugin_errors == [("broken", "boom")]


def test_registry_replace_transfers_prebuilt_state_without_allocating(
        monkeypatch):
    live = FunctionRegistry()
    live.prefix("old", lambda value, context: value)
    replacement = FunctionRegistry()
    replacement.prefix("new", lambda value, context: value + 1)
    replacement.plugin_errors = [("broken", "boom")]
    replacement.plugin_functions = {"new_pack": ["new"]}
    replacement.plugin_dependencies = {"new_pack": ()}
    replacement.plugin_files = [("new_pack", "new_pack.py")]
    replacement._plugin_exports = {"new_pack": {"helper": object()}}

    def exhaust_heap(*args, **kwargs):
        raise MemoryError("replace must not copy staged state")

    monkeypatch.setattr(functions_module, "dict", exhaust_heap, raising=False)
    monkeypatch.setattr(functions_module, "list", exhaust_heap, raising=False)

    assert live.replace(replacement) is live

    assert live._defs is replacement._defs
    assert live.plugin_errors is replacement.plugin_errors
    assert live.plugin_functions is replacement.plugin_functions
    assert live.plugin_dependencies is replacement.plugin_dependencies
    assert live.plugin_files is replacement.plugin_files
    assert live._plugin_exports is replacement._plugin_exports
    assert live.get("new")[-1](2, None) == 3


def test_reload_rebuilds_the_identity_stable_registry_in_place(
        monkeypatch):
    live = build_registry(["basic"])
    live.prefix("old_plugin", lambda value, context: value)
    live.angle_mode = 1
    observations = []

    class Report:
        errors = []
        functions = {"fresh": ["fresh_plugin"]}
        dependencies = {"fresh": ()}
        files = [("fresh", "fresh.py")]

    def load_replacement(registry, enabled_files, in_place=False):
        assert in_place is True
        observations.append((registry is live, "old_plugin" in live,
                             "old_plugin" in registry, list(enabled_files)))
        registry.prefix("fresh_plugin", lambda value, context: value)
        return Report()

    monkeypatch.setattr(loader, "load_function_files", load_replacement)

    assert main._reload_functions(
        {"enabled_functions": ["basic", "plugin:fresh"]}, live) is live

    assert observations == [(True, False, False, ["fresh"])]
    assert "old_plugin" not in live
    assert "fresh_plugin" in live
    assert live.angle_mode == 1


def test_reload_of_only_bundled_plugins_does_not_import_source_loader(
        monkeypatch):
    live = build_registry(["basic"])
    known_files = [
        ("basic", "basic.py"), ("solve", "solve.py"),
        ("trig", "trig.py")]
    live.plugin_files = known_files
    monkeypatch.setattr(
        loader, "load_function_files",
        lambda *_args, **_kwargs: pytest.fail("source loader was used"))
    monkeypatch.setattr(
        loader, "list_function_files",
        lambda *_args, **_kwargs: pytest.fail("source catalog was rescanned"))

    result = main._reload_functions(
        {"enabled_functions": [
            "basic", "plugin:basic", "plugin:solve", "plugin:trig"]},
        live)

    assert result is live
    assert live.plugin_files is known_files
    assert tuple(live._plugin_exports) == ("basic", "solve", "trig")
    assert "+" in live
    assert "%" in live
    assert "solve" in live
    assert "sind" in live


def test_reload_oom_leaves_the_identity_stable_builtin_registry_usable(
        monkeypatch):
    live = build_registry(["basic"])
    live.prefix("old_plugin", lambda value, context: value)
    live.angle_mode = 1
    original_defs = live._defs
    original_exports = live._plugin_exports
    original_revision = live.revision

    def exhaust_heap(registry, enabled_files, in_place=False):
        assert in_place is True
        raise MemoryError("injected reload")

    monkeypatch.setattr(loader, "load_function_files", exhaust_heap)

    with pytest.raises(MemoryError, match="injected reload"):
        main._reload_functions(
            {"enabled_functions": ["basic", "plugin:fresh"]}, live)

    assert live._defs is original_defs
    assert live._plugin_exports is original_exports
    assert "old_plugin" not in live
    assert "+" in live
    assert live.angle_mode == 1
    assert live.revision > original_revision


def test_reload_error_falls_back_to_builtins_without_partial_plugins(
        monkeypatch):
    live = build_registry(["basic"])
    live.prefix("old_plugin", lambda value, context: value)
    live.plugin_functions = {"old": ["old_plugin"]}
    live.plugin_dependencies = {"old": ()}
    original_defs = live._defs
    original_exports = live._plugin_exports
    original_functions = live.plugin_functions
    original_dependencies = live.plugin_dependencies
    original_revision = live.revision

    class Report:
        errors = [("broken", "source limit")]
        functions = {}
        dependencies = {}
        files = [("new", "new.py")]

    def staged_failure(registry, enabled_files, in_place=False):
        assert in_place is True
        registry.prefix("new_plugin", lambda value, context: value)
        return Report()

    monkeypatch.setattr(loader, "load_function_files", staged_failure)

    assert main._reload_functions(
        {"enabled_functions": ["basic", "plugin:new"]}, live) is None

    assert live._defs is original_defs
    assert live._plugin_exports is original_exports
    assert live.plugin_functions == {}
    assert live.plugin_dependencies == {}
    assert live.plugin_files == [("new", "new.py")]
    assert "old_plugin" not in live
    assert "new_plugin" not in live
    assert "+" in live
    assert live.revision > original_revision
    assert live.plugin_errors == [("broken", "source limit")]


def test_loader_rejects_oversized_addon_before_executing_source(tmp_path):
    (tmp_path / "large.py").write_text(
        "x" * (MAX_PLUGIN_SOURCE_BYTES + 1), encoding="utf-8")
    registry = build_registry(["basic"])

    report = load_function_files(registry, ["large"], str(tmp_path))

    assert "large" not in registry
    assert dict(report.errors)["large"].endswith("size limit")


def test_plugin_reader_allocates_for_the_measured_source_not_the_limit(
        monkeypatch, tmp_path):
    source = "def register(registry):\n    pass\n"
    reads = []
    path = tmp_path / "measured.py"
    path.write_text(source, encoding="utf-8")
    real_open = open

    class SourceFile:
        def __init__(self, target, mode):
            self.inner = real_open(target, mode)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.inner.close()

        def read(self, amount):
            reads.append(amount)
            return self.inner.read(amount)

    monkeypatch.setattr(
        loader, "open", lambda target, mode: SourceFile(target, mode),
        raising=False)

    namespace = loader._execute_plugin(str(path), "measured")

    assert callable(namespace["register"])
    assert reads == [path.stat().st_size, 1]
    assert MAX_PLUGIN_SOURCE_BYTES + 1 not in reads


def test_device_execfile_streams_bounded_plugin_without_source_read(
        monkeypatch, tmp_path):
    source = "def register(registry):\n    pass\n"
    path = tmp_path / "streamed.py"
    path.write_text(source, encoding="utf-8")
    calls = []

    def stream_execute(target, namespace):
        calls.append((target, namespace["__name__"]))
        exec(source, namespace)

    monkeypatch.setattr(loader, "_STREAM_EXECUTE", stream_execute)
    monkeypatch.setattr(
        loader, "open",
        lambda *_args: pytest.fail("streaming path must not read a source blob"),
        raising=False)

    namespace = loader._execute_plugin(str(path), "streamed")

    assert callable(namespace["register"])
    assert calls == [(str(path), "streamed")]


def test_loader_executes_largest_independent_source_before_smaller_ones(
        monkeypatch, tmp_path):
    (tmp_path / "small.py").write_text(
        "def register(registry):\n    pass\n", encoding="utf-8")
    (tmp_path / "large.py").write_text(
        "#" * 512 + "\ndef register(registry):\n    pass\n", encoding="utf-8")
    executed = []
    real_execute = loader._execute_plugin

    def record(path, module_name):
        executed.append(Path(path).name)
        return real_execute(path, module_name)

    monkeypatch.setattr(loader, "_execute_plugin", record)
    report = load_function_files(
        build_registry(["basic"]), ["small", "large"], str(tmp_path))

    assert report.errors == []
    assert executed == ["large.py", "small.py"]


def test_loader_rejects_plugin_function_registration_past_the_quota(tmp_path):
    registrations = "".join(
        "    registry.prefix('f" + str(index) + "', callback)\n"
        for index in range(MAX_PLUGIN_FUNCTIONS + 1))
    (tmp_path / "crowded.py").write_text(
        "def callback(value, context): return value\n"
        "def register(registry):\n" + registrations,
        encoding="utf-8")
    registry = build_registry(["basic"])

    report = load_function_files(registry, ["crowded"], str(tmp_path))

    assert "f0" not in registry
    assert "limit" in dict(report.errors)["crowded"].lower()


def test_loader_rejects_dependency_chains_past_the_quota(tmp_path):
    names = ["p" + str(index)
             for index in range(MAX_PLUGIN_DEPENDENCY_DEPTH + 1)]
    for index, name in enumerate(names):
        dependency = ("DEPENDENCIES = ('" + names[index + 1] + "',)\n"
                      if index + 1 < len(names) else "")
        (tmp_path / (name + ".py")).write_text(
            dependency + "def register(registry): pass\n", encoding="utf-8")
    registry = build_registry(["basic"])

    report = load_function_files(registry, [names[0]], str(tmp_path))

    assert report.loaded == []
    assert "depth limit" in dict(report.errors)[names[-1]].lower()


def test_loader_rejects_enabled_addons_past_the_selection_quota(tmp_path):
    names = ["p" + str(index) for index in range(MAX_ENABLED_PLUGINS + 1)]
    for name in names:
        (tmp_path / (name + ".py")).write_text(
            "def register(registry): pass\n", encoding="utf-8")
    registry = build_registry(["basic"])

    report = load_function_files(registry, names, str(tmp_path))

    assert len(report.loaded) == MAX_ENABLED_PLUGINS
    assert "limit" in dict(report.errors)[names[-1]].lower()


def _oversized_plugin_snapshot():
    for index in range(MAX_DISCOVERED_PLUGIN_FILES + 1):
        yield ("p" + str(index), "p" + str(index) + ".py")
    raise AssertionError("snapshot reader exceeded its bounded entry cap")


def test_function_environment_rejects_oversized_supplied_snapshot(tmp_path):
    snapshot = _oversized_plugin_snapshot()

    environment = FunctionEnvironment(str(tmp_path), files=snapshot)

    assert dict(environment.report.errors)["Add-ons"] == "Too many add-on files"
    assert environment.report.files == []
    assert environment.namespaces == {}
    assert environment._paths == {}


def test_cold_environment_copies_snapshot_before_caller_mutates_it(
        tmp_path):
    (tmp_path / "owned.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('owned_fn', lambda value, context: value)\n",
        encoding="utf-8")
    snapshot = [("owned", "owned.py")]
    environment = FunctionEnvironment(
        str(tmp_path), files=snapshot, selected_files=[])
    snapshot[0] = ("changed", "changed.py")

    assert environment.report.files is not snapshot
    assert environment.report.files == [("owned", "owned.py")]
    environment.cancel()


def test_reload_rejects_an_oversized_external_selection_before_mutating_live(
        monkeypatch):
    live = build_registry(["basic"])
    live.prefix("old_plugin", lambda value, context: value)
    original_defs = live._defs
    selections = ["plugin:p" + str(index)
                  for index in range(MAX_ENABLED_PLUGINS + 1)]
    monkeypatch.setattr(
        loader, "load_function_files",
        lambda registry, enabled: pytest.fail("oversized selection was loaded"))

    with pytest.raises(ValueError, match="limit"):
        main._reload_functions({"enabled_functions": selections}, live)

    assert live._defs is original_defs
    assert "old_plugin" in live


def test_plugin_reload_uses_supplied_selection_instead_of_settings(tmp_path):
    (tmp_path / "chosen.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('chosen_fn', lambda value, context: value)\n",
        encoding="utf-8",
    )
    settings = {"enabled_functions": ["basic", "plugin:missing"]}
    selection = ["basic", "plugin:chosen"]

    class Checkpoint:
        def __init__(self, checkpoint_selection):
            self.selection = checkpoint_selection
            self.report = None

        def commit(self, report):
            self.report = report
            return True

        def restore(self):
            return True

        def release(self):
            self.selection = None
            self.report = None
            return True

    class Panel:
        def __init__(self):
            self.checkpoint = None

        def open_plugin_reload_checkpoint(self, received_settings,
                                          selection=None):
            assert received_settings is settings
            self.checkpoint = Checkpoint(selection)
            return self.checkpoint

    panel = Panel()
    registry = build_registry(["basic"])
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, settings=settings, func_dir=str(tmp_path),
        selection=selection)

    while not transaction.complete:
        transaction.step()

    assert transaction.succeeded is True
    assert transaction.report.errors == []
    assert [item[0] for item in transaction.report.loaded] == ["chosen"]
    assert panel.checkpoint.selection == ("basic", "plugin:chosen")
    assert panel.checkpoint.selection is not selection
    assert "chosen_fn" in registry
    assert settings["enabled_functions"] == ["basic", "plugin:missing"]
    assert transaction.close() is True


def test_plugin_reload_supplied_missing_selection_rejects_without_live_mutation(
        tmp_path):
    (tmp_path / "settings_only.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('settings_only_fn', lambda value, context: value)\n",
        encoding="utf-8",
    )
    settings = {"enabled_functions": ["basic", "plugin:settings_only"]}
    selection = ["basic", "plugin:missing"]

    class Checkpoint:
        def __init__(self, checkpoint_selection):
            self.selection = checkpoint_selection
            self.restore_calls = 0

        def commit(self, _report):
            raise AssertionError("missing selection must not commit")

        def restore(self):
            self.restore_calls += 1
            return True

        def release(self):
            self.selection = None
            return True

    class Panel:
        def __init__(self):
            self.checkpoint = None

        def open_plugin_reload_checkpoint(self, _settings, selection=None):
            self.checkpoint = Checkpoint(selection)
            return self.checkpoint

    panel = Panel()
    registry = build_registry(["basic"])
    registry.prefix("kept", lambda value, context: value)
    original_defs = registry._defs
    original_revision = registry.revision
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, settings=settings, func_dir=str(tmp_path),
        selection=selection)

    while not transaction.complete:
        transaction.step()

    assert transaction.succeeded is False
    assert dict(transaction.report.errors)["missing"] == (
        "Dependency file was not found")
    assert panel.checkpoint.selection == ("basic", "plugin:missing")
    assert registry._defs is original_defs
    assert registry.revision == original_revision
    assert "kept" in registry
    assert "settings_only_fn" not in registry
    assert settings["enabled_functions"] == ["basic", "plugin:settings_only"]
    assert transaction.close() is True
    assert panel.checkpoint.restore_calls == 2


@pytest.mark.parametrize(
    "selection", (["basic", "plugin:kept"], ("basic", "plugin:kept")))
def test_plugin_reload_copies_external_selection_before_caller_mutation(
        tmp_path, selection):
    (tmp_path / "kept.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('kept_fn', lambda value, context: value)\n",
        encoding="utf-8",
    )

    class Checkpoint:
        def __init__(self, checkpoint_selection):
            self.selection = checkpoint_selection

        def commit(self, _report):
            return True

        def restore(self):
            return True

        def release(self):
            self.selection = None
            return True

    class Panel:
        def __init__(self):
            self.checkpoint = None

        def open_plugin_reload_checkpoint(self, _settings, selection=None):
            self.checkpoint = Checkpoint(selection)
            return self.checkpoint

    panel = Panel()
    registry = build_registry(["basic"])
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, settings={}, func_dir=str(tmp_path),
        selection=selection)
    if isinstance(selection, list):
        selection[1] = "plugin:missing"

    while not transaction.complete:
        transaction.step()

    assert transaction.succeeded is True
    assert panel.checkpoint.selection == ("basic", "plugin:kept")
    assert panel.checkpoint.selection is not selection
    assert "kept_fn" in registry
    assert transaction.close() is True


def test_plugin_reload_rejects_oversized_selection_before_opening_checkpoint():
    registry = build_registry(["basic"])
    registry.prefix("kept", lambda value, context: value)
    original_defs = registry._defs
    original_revision = registry.revision

    class Panel:
        def open_plugin_reload_checkpoint(self, _settings, selection=None):
            raise AssertionError("oversized selection opened a checkpoint")

    with pytest.raises(ValueError, match="limit"):
        plugin_reload.open_plugin_reload_transaction(
            registry, Panel(), settings={},
            selection=["basic"] * (MAX_ENABLED_FUNCTIONS + 1))

    assert registry._defs is original_defs
    assert registry.revision == original_revision
    assert "kept" in registry


@pytest.mark.parametrize(
    ("selection", "message"),
    (
        (["not-a-function-selection"], "Unknown function selection"),
        (["plugin:p" + str(index)
          for index in range(MAX_ENABLED_PLUGINS + 1)],
         "Enabled add-on limit reached"),
        (["plugin:" + "x" * (MAX_FUNCTION_NAME_LENGTH + 1)],
         "Plugin name is invalid or too long"),
    ),
    ids=("unknown-token", "nine-unique-plugin-tokens", "overlong-plugin-token"),
)
def test_plugin_reload_rejects_invalid_external_selection_before_checkpoint(
        selection, message):
    registry = build_registry(["basic"])
    registry.prefix("kept", lambda value, context: value)
    original_defs = registry._defs
    original_exports = registry._plugin_exports
    original_revision = registry.revision

    class Panel:
        def open_plugin_reload_checkpoint(self, _settings, selection=None):
            raise AssertionError("invalid selection opened a checkpoint")

    with pytest.raises(ValueError, match=message):
        plugin_reload.open_plugin_reload_transaction(
            registry, Panel(), settings={}, selection=selection)

    assert registry._defs is original_defs
    assert registry._plugin_exports is original_exports
    assert registry.revision == original_revision
    assert "kept" in registry


def test_package_initializers_are_not_listed_as_plugins(tmp_path):
    from calc.loader import list_function_files

    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "visible.py").write_text("def register(registry): pass\n", encoding="utf-8")

    assert list_function_files(str(tmp_path)) == [("visible", "visible.py")]


def test_cold_environment_executes_each_dependency_once_and_releases_metadata(
        tmp_path):
    import builtins

    marker = "_scicalc_function_environment_hits"
    setattr(builtins, marker, {})
    try:
        (tmp_path / "base.py").write_text(
            "import builtins\n"
            "hits = builtins._scicalc_function_environment_hits\n"
            "hits['base'] = hits.get('base', 0) + 1\n"
            "BIAS = 4\n"
            "def base_value(value):\n"
            "    return value + BIAS\n"
            "EXPORTS = {'base_value': base_value}\n"
            "WELCOME = 'base ready'\n"
            "def register(registry):\n"
            "    registry.prefix('base_fn', lambda value, context: base_value(value))\n",
            encoding="utf-8")
        (tmp_path / "dependent.py").write_text(
            "import builtins\n"
            "hits = builtins._scicalc_function_environment_hits\n"
            "hits['dependent'] = hits.get('dependent', 0) + 1\n"
            "DEPENDENCIES = ('base',)\n"
            "def dependent(value, context):\n"
            "    return context.plugin('base')['base_value'](value) + 1\n"
            "def register(registry):\n"
            "    registry.prefix('dependent', dependent)\n",
            encoding="utf-8")

        environment = FunctionEnvironment(str(tmp_path))
        while not environment.complete:
            environment.step()

        assert getattr(builtins, marker) == {"base": 1, "dependent": 1}
        assert environment.report.files == [
            ("base", "base.py"), ("dependent", "dependent.py")]
        assert environment.report.functions == {
            "base": ["base_fn"], "dependent": ["dependent"]}
        assert environment.report.dependencies == {
            "base": (), "dependent": ("base",)}
        assert environment.report.exports == {"base": ("base_value",), "dependent": ()}

        assert getattr(builtins, marker) == {"base": 1, "dependent": 1}
        environment.release_loaded_metadata(
            (("base", 1, ""), ("dependent", 1, "")))
        for namespace in environment.namespaces.values():
            assert "register" not in namespace
            assert "DEPENDENCIES" not in namespace
            assert "REQUIRES" not in namespace
            assert "EXPORTS" not in namespace
            assert "WELCOME" not in namespace
        environment.cancel()
    finally:
        delattr(builtins, marker)


def test_selected_function_environment_keeps_only_the_enabled_closure(
        tmp_path):
    import builtins

    marker = "_scicalc_selected_environment_hits"
    setattr(builtins, marker, {})
    try:
        for name, dependency in (
                ("base", ""),
                ("active", "DEPENDENCIES = ('base',)\n"),
                ("disabled", "")):
            (tmp_path / (name + ".py")).write_text(
                "import builtins\n"
                "hits = builtins._scicalc_selected_environment_hits\n"
                "hits['" + name + "'] = hits.get('" + name + "', 0) + 1\n"
                + dependency
                + "def callback(value, context): return value\n"
                + "def register(registry): registry.prefix('" + name
                + "_fn', callback)\n",
                encoding="utf-8")

        environment = FunctionEnvironment(
            str(tmp_path), selected_files=["active"])
        while not environment.complete:
            environment.step()

        assert getattr(builtins, marker) == {"active": 1, "base": 1}
        assert "disabled" not in environment.namespaces
        assert environment.report.functions == {
            "base": ["base_fn"], "active": ["active_fn"]}
        assert ("disabled", "disabled.py") in environment.report.files
        environment.cancel()
    finally:
        delattr(builtins, marker)

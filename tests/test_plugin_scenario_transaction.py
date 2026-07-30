import pytest

from calc import loader, plugin_reload
from calc.functions import build_registry
from screens.function_panel import FunctionPanel


def _panel_with_queued_selection(monkeypatch, selection):
    monkeypatch.setattr(loader, "list_function_files", lambda: [])
    previous = ["basic"]
    settings = {"enabled_functions": previous}
    panel = FunctionPanel(None, settings=settings)
    panel._state[0][3] = previous
    settings["enabled_functions"] = selection
    return panel, settings, previous


def _registry_state(registry):
    return registry._plugin_reload_state()


def _assert_registry_state(registry, state):
    assert registry._defs is state[0]
    assert registry._function_limit == state[1]
    assert registry._revision == state[2]
    assert registry.angle_mode == state[3]
    assert registry.plugin_errors is state[4]
    assert registry.plugin_functions is state[5]
    assert registry.plugin_dependencies is state[6]
    assert registry._plugin_exports is state[7]
    assert registry._dependency_exports is state[8]
    assert registry._symbolic_names is state[9]
    assert registry.plugin_files is state[10]


def _finish(transaction):
    while not transaction.complete:
        transaction.step()


def test_staged_plugin_reload_commits_once_and_releases_metadata(
        tmp_path, monkeypatch):
    (tmp_path / "base.py").write_text(
        "EXPORTS = {'base_value': 7}\n"
        "def register(registry):\n"
        "    registry.prefix('base_fn', lambda value, context: value)\n",
        encoding="utf-8")
    (tmp_path / "dependent.py").write_text(
        "DEPENDENCIES = ('base',)\n"
        "def register(registry):\n"
        "    registry.prefix('dependent_fn', lambda value, context: value)\n",
        encoding="utf-8")
    selection = ["basic", "plugin:dependent"]
    panel, settings, _ = _panel_with_queued_selection(monkeypatch, selection)
    registry = build_registry(["basic"])
    source_steps = []
    real_execute = loader._execute_plugin

    def count_source(path, module_name):
        source_steps.append(path)
        return real_execute(path, module_name)

    monkeypatch.setattr(plugin_reload, "_execute_plugin", count_source)
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))

    while not transaction.complete:
        before = len(source_steps)
        transaction.step()
        assert len(source_steps) - before <= 1

    assert transaction.succeeded is True
    report = transaction.report
    assert report.loaded[0][0] == "base"
    assert report.loaded[1][0] == "dependent"
    assert source_steps.count(str(tmp_path / "base.py")) == 1
    assert source_steps.count(str(tmp_path / "dependent.py")) == 1
    assert "base_fn" in registry
    assert "dependent_fn" in registry
    assert settings["enabled_functions"] is selection
    assert panel._state[0][3] is None
    assert panel._state[2][0]["dependent"] == ("base",)

    namespace = transaction._environment.namespaces["dependent"]
    assert "register" in namespace
    assert transaction.close() is True
    assert "register" not in namespace
    assert transaction.report is None
    assert transaction._report is None
    assert registry.plugin_dependencies is report.dependencies
    assert transaction.close() is True


def test_fixture_selection_never_publishes_or_retains_user_panel_state(
        tmp_path, monkeypatch):
    (tmp_path / "fixture.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('fixture_fn', lambda value, context: value)\n",
        encoding="utf-8")
    monkeypatch.setattr(loader, "list_function_files", lambda: [])
    enabled = ["basic"]
    settings = {"enabled_functions": enabled}
    panel = FunctionPanel(None, settings=settings)
    registry = build_registry(["basic"])
    state = _registry_state(registry)
    panel_files = panel._state[2][1]
    panel_dependencies = panel._state[2][0]
    selection = ["basic", "plugin:fixture"]

    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, settings=settings, func_dir=str(tmp_path),
        selection=selection)
    _finish(transaction)

    assert transaction.succeeded is True
    assert "fixture_fn" in registry
    assert settings["enabled_functions"] is enabled
    assert panel._state[0][3] is None
    assert panel._state[2][1] is panel_files
    assert panel._state[2][0] is panel_dependencies
    assert transaction.cancel() is True
    _assert_registry_state(registry, state)
    assert settings["enabled_functions"] is enabled
    assert panel._state[2][1] is panel_files
    assert panel._state[2][0] is panel_dependencies


def test_plugin_reload_memory_error_restores_complete_registry_and_panel(
        tmp_path, monkeypatch):
    (tmp_path / "oom.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('oom_fn', lambda value, context: value)\n",
        encoding="utf-8")
    class FailOnceSettings(dict):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.error = None

        def __setitem__(self, key, value):
            if self.error is not None and key == "enabled_functions":
                error = self.error
                self.error = None
                raise error
            return super().__setitem__(key, value)

    monkeypatch.setattr(loader, "list_function_files", lambda: [])
    selection = ["basic", "plugin:oom"]
    previous = ["basic"]
    settings = FailOnceSettings({"enabled_functions": previous})
    panel = FunctionPanel(None, settings=settings)
    panel._state[0][3] = previous
    settings["enabled_functions"] = selection
    registry = build_registry(["basic"])
    registry.plugin_errors = [("old", "kept")]
    registry.plugin_functions = {"old": ["old_fn"]}
    registry.plugin_dependencies = {"old": ()}
    registry._plugin_exports = {"old": {"value": 1}}
    registry._dependency_exports = {"old": {"value": 1}}
    registry._symbolic_names = ("+",)
    state = _registry_state(registry)
    original_error = MemoryError("injected late panel commit OOM")

    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))
    while transaction._phase != transaction._LIVE_COMMIT:
        transaction.step()
    settings.error = original_error

    with pytest.raises(MemoryError) as caught:
        transaction.step()

    assert caught.value is original_error
    assert transaction.complete is True
    assert transaction.succeeded is False
    _assert_registry_state(registry, state)
    assert settings["enabled_functions"] is previous
    assert panel._state[0][3] is None
    assert transaction.close() is True
    assert transaction.report is None


def test_plugin_reload_preserves_ordinary_commit_error_when_rollback_ooms(
        tmp_path):
    (tmp_path / "kept.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('kept_fn', lambda value, context: value)\n",
        encoding="utf-8")
    primary_error = RuntimeError("injected panel commit failure")
    cleanup_error = MemoryError("injected rollback OOM")

    class Checkpoint:
        def __init__(self):
            self.selection = ["basic", "plugin:kept"]
            self.restore_calls = 0
            self.release_calls = 0

        def commit(self, _report):
            raise primary_error

        def restore(self):
            self.restore_calls += 1
            if self.restore_calls == 1:
                raise cleanup_error
            return True

        def release(self):
            self.release_calls += 1
            return True

    class Panel:
        def __init__(self):
            self.checkpoint = Checkpoint()

        def open_plugin_reload_checkpoint(self, _settings):
            return self.checkpoint

    panel = Panel()
    registry = build_registry(["basic"])
    state = _registry_state(registry)
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))
    while transaction._phase != transaction._LIVE_COMMIT:
        transaction.step()

    with pytest.raises(RuntimeError) as caught:
        transaction.step()

    assert caught.value is primary_error
    assert transaction.complete is True
    assert transaction.succeeded is False
    assert transaction._closed is False
    assert panel.checkpoint.restore_calls == 1
    _assert_registry_state(registry, state)

    assert transaction.close() is True
    assert panel.checkpoint.restore_calls == 2
    assert panel.checkpoint.release_calls == 1


def test_plugin_reload_preserves_primary_oom_when_rollback_also_ooms(
        tmp_path):
    (tmp_path / "kept.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('kept_fn', lambda value, context: value)\n",
        encoding="utf-8")
    primary_error = MemoryError("injected panel commit OOM")
    cleanup_error = MemoryError("injected rollback OOM")

    class Checkpoint:
        def __init__(self):
            self.selection = ["basic", "plugin:kept"]
            self.restore_calls = 0

        def commit(self, _report):
            raise primary_error

        def restore(self):
            self.restore_calls += 1
            if self.restore_calls == 1:
                raise cleanup_error
            return True

        def release(self):
            return True

    class Panel:
        def __init__(self):
            self.checkpoint = Checkpoint()

        def open_plugin_reload_checkpoint(self, _settings):
            return self.checkpoint

    panel = Panel()
    registry = build_registry(["basic"])
    state = _registry_state(registry)
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))
    while transaction._phase != transaction._LIVE_COMMIT:
        transaction.step()

    with pytest.raises(MemoryError) as caught:
        transaction.step()

    assert caught.value is primary_error
    assert transaction.complete is True
    assert transaction.succeeded is False
    assert transaction._closed is False
    assert panel.checkpoint.restore_calls == 1
    _assert_registry_state(registry, state)

    assert transaction.close() is True
    assert panel.checkpoint.restore_calls == 2


def test_plugin_reload_ordinary_registration_failure_rolls_back_without_commit(
        tmp_path, monkeypatch):
    (tmp_path / "broken.py").write_text(
        "def register(registry):\n"
        "    raise RuntimeError('broken register')\n",
        encoding="utf-8")
    selection = ["basic", "plugin:broken"]
    panel, settings, previous = _panel_with_queued_selection(
        monkeypatch, selection)
    registry = build_registry(["basic"])
    state = _registry_state(registry)
    transaction = plugin_reload.PluginReloadTransaction(
        registry, panel, func_dir=str(tmp_path))

    _finish(transaction)

    assert transaction.succeeded is False
    assert dict(transaction.report.errors)["broken"] == "broken register"
    _assert_registry_state(registry, state)
    assert settings["enabled_functions"] is previous
    assert panel._state[0][3] is None
    assert transaction.close() is True
    assert transaction.report is None


def test_plugin_reload_cancel_restores_selection_after_source_work(
        tmp_path, monkeypatch):
    (tmp_path / "cancelled.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('cancelled_fn', lambda value, context: value)\n",
        encoding="utf-8")
    selection = ["basic", "plugin:cancelled"]
    panel, settings, previous = _panel_with_queued_selection(
        monkeypatch, selection)
    registry = build_registry(["basic"])
    state = _registry_state(registry)
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))

    while transaction._phase != transaction._DEPENDENCY:
        transaction.step()

    assert transaction.cancel() is True
    _assert_registry_state(registry, state)
    assert settings["enabled_functions"] is previous
    assert panel._state[0][3] is None
    assert transaction.close() is True
    assert transaction.report is None


def test_plugin_reload_cancel_rollback_oom_marks_commit_rejected_for_retry(
        tmp_path):
    (tmp_path / "cancelled.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('cancelled_fn', lambda value, context: value)\n",
        encoding="utf-8")
    cleanup_error = MemoryError("injected cancel rollback OOM")

    class Checkpoint:
        def __init__(self):
            self.selection = ["basic", "plugin:cancelled"]
            self.restore_calls = 0

        def commit(self, _report):
            return True

        def restore(self):
            self.restore_calls += 1
            if self.restore_calls == 1:
                raise cleanup_error
            return True

        def release(self):
            return True

    class Panel:
        def __init__(self):
            self.checkpoint = Checkpoint()

        def open_plugin_reload_checkpoint(self, _settings):
            return self.checkpoint

    panel = Panel()
    registry = build_registry(["basic"])
    state = _registry_state(registry)
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))
    _finish(transaction)

    assert transaction.succeeded is True
    with pytest.raises(MemoryError) as caught:
        transaction.cancel()

    assert caught.value is cleanup_error
    assert transaction.complete is True
    assert transaction.succeeded is False
    assert transaction._closed is False
    assert panel.checkpoint.restore_calls == 1
    _assert_registry_state(registry, state)

    assert transaction.close() is True
    assert panel.checkpoint.restore_calls == 2


def test_plugin_reload_close_fault_retains_report_until_retry_succeeds(
        tmp_path):
    (tmp_path / "kept.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('kept_fn', lambda value, context: value)\n",
        encoding="utf-8")
    cleanup_error = RuntimeError("injected checkpoint cleanup fault")

    class Checkpoint:
        def __init__(self):
            self.selection = ["basic", "plugin:kept"]
            self.report = None
            self.release_calls = 0

        def restore(self):
            return None

        def commit(self, report):
            self.report = report

        def release(self):
            self.release_calls += 1
            if self.release_calls == 1:
                raise cleanup_error
            self.report = None

    class Panel:
        def __init__(self):
            self.checkpoint = Checkpoint()

        def open_plugin_reload_checkpoint(self, _settings):
            return self.checkpoint

    panel = Panel()
    registry = build_registry(["basic"])
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))
    _finish(transaction)
    report = transaction.report
    environment = transaction._environment

    with pytest.raises(RuntimeError) as caught:
        transaction.close()

    assert caught.value is cleanup_error
    assert transaction.report is report
    assert transaction._environment is environment
    assert transaction._closed is False
    assert panel.checkpoint.report is report

    assert transaction.close() is True
    assert transaction.report is None
    assert transaction._environment is None
    assert transaction._report is None
    assert panel.checkpoint.report is None
    assert panel.checkpoint.release_calls == 2


def test_plugin_reload_metadata_release_oom_retries_without_losing_commit(
        tmp_path, monkeypatch):
    (tmp_path / "kept.py").write_text(
        "def register(registry):\n"
        "    registry.prefix('kept_fn', lambda value, context: value)\n",
        encoding="utf-8")
    selection = ["basic", "plugin:kept"]
    panel, _settings, _previous = _panel_with_queued_selection(
        monkeypatch, selection)
    registry = build_registry(["basic"])
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))
    _finish(transaction)
    report = transaction.report
    environment = transaction._environment
    namespace = environment.namespaces["kept"]
    committed_defs = registry._defs
    cleanup_error = MemoryError("injected metadata release OOM")
    real_release = plugin_reload.FunctionEnvironment.release_loaded_metadata
    calls = []

    def fail_once(environment, loaded):
        calls.append(loaded)
        if len(calls) == 1:
            raise cleanup_error
        return real_release(environment, loaded)

    monkeypatch.setattr(
        plugin_reload.FunctionEnvironment, "release_loaded_metadata", fail_once)

    with pytest.raises(MemoryError) as caught:
        transaction.close()

    assert caught.value is cleanup_error
    assert transaction.report is report
    assert transaction._environment is environment
    assert transaction._closed is False
    assert registry._defs is committed_defs
    assert "kept_fn" in registry
    assert "register" in namespace

    assert transaction.close() is True
    assert len(calls) == 2
    assert transaction.report is None
    assert "register" not in namespace


def _write_dependency_chain(tmp_path, length):
    for index in range(length):
        next_index = index + 1
        source = ""
        if next_index < length:
            source += "DEPENDENCIES = ('chain" + str(next_index) + "',)\n"
        source += (
            "def register(registry):\n"
            "    registry.prefix('chain" + str(index)
            + "_fn', lambda value, context: value)\n")
        (tmp_path / ("chain" + str(index) + ".py")).write_text(
            source, encoding="utf-8")


def test_staged_plugin_reload_accepts_a_dependency_chain_at_the_depth_limit(
        tmp_path, monkeypatch):
    _write_dependency_chain(tmp_path, loader.MAX_PLUGIN_DEPENDENCY_DEPTH)
    selection = ["basic", "plugin:chain0"]
    panel, _settings, _previous = _panel_with_queued_selection(
        monkeypatch, selection)
    registry = build_registry(["basic"])
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))

    _finish(transaction)

    assert transaction.succeeded is True
    assert [item[0] for item in transaction.report.loaded] == [
        "chain" + str(index)
        for index in range(loader.MAX_PLUGIN_DEPENDENCY_DEPTH - 1, -1, -1)
    ]
    assert transaction.close() is True


def test_staged_plugin_reload_rejects_a_dependency_chain_past_the_limit(
        tmp_path, monkeypatch):
    _write_dependency_chain(tmp_path, loader.MAX_PLUGIN_DEPENDENCY_DEPTH + 1)
    selection = ["basic", "plugin:chain0"]
    panel, settings, previous = _panel_with_queued_selection(
        monkeypatch, selection)
    registry = build_registry(["basic"])
    state = _registry_state(registry)
    transaction = plugin_reload.open_plugin_reload_transaction(
        registry, panel, func_dir=str(tmp_path))

    _finish(transaction)

    assert transaction.succeeded is False
    assert dict(transaction.report.errors)[
        "chain" + str(loader.MAX_PLUGIN_DEPENDENCY_DEPTH)
    ] == (
        "Dependency depth limit reached")
    _assert_registry_state(registry, state)
    assert settings["enabled_functions"] is previous
    assert panel._state[0][3] is None
    assert transaction.close() is True

import gc
import sys


_heap_min = -1


def _sample():
    global _heap_min
    free = gc.mem_free()
    if _heap_min < 0 or free < _heap_min:
        _heap_min = free
    return free


def _enter(calculator, expression, error=False):
    box = calculator.input_box
    box.set_str(expression, immediate=True)
    calculator._enter()
    popup = calculator._state[1]
    if error:
        if calculator.mode != 2 or not popup.active:
            raise RuntimeError(1)
        popup.dismiss()
        calculator.mode = 0
        box.clear_str()
    elif calculator.mode != 0 or popup.active:
        raise RuntimeError(2)
    _sample()


def _exercise_round(state):
    screens, registry, settings, _persistence, nav = state
    root = screens[0]
    calculator = screens[1]
    plot = screens[2]
    panel = screens[3]
    stopwatch = screens[4]
    context = calculator.context
    variables = context.variables
    history = calculator._state[0]
    laps = stopwatch._clock[2][3]
    if (nav.current is not root or len(nav.stack) != 1 or history
            or calculator.input_box.get_str() or calculator.mode != 0
            or stopwatch._clock[1] or stopwatch._clock[2][0] or laps):
        raise RuntimeError(3)

    dirty = context.dirty
    variable_count = len(variables)
    if variable_count > 16:
        raise RuntimeError(4)
    added = 0
    stopwatch_revision = stopwatch._clock[3][3]
    try:
        while len(variables) < 16:
            prefix = "_a" + str(added)
            name = prefix + "_" * (24 - len(prefix))
            if name in variables:
                raise RuntimeError(5)
            _enter(calculator, name + "=" + str(added + 1))
            added += 1
        history[:] = ()

        index = 0
        while index < 20:
            prefix = str(index)
            width = 46 if index == 19 else 38
            _enter(calculator, prefix + "0" * (width - len(prefix)))
            index += 1
        if len(history) != 20 or len(variables) != 16:
            raise RuntimeError(6)

        if not stopwatch._start():
            raise RuntimeError(7)
        _sample()
        index = 0
        while index < 20:
            if not stopwatch._lap():
                raise RuntimeError(8)
            _sample()
            index += 1
        stopwatch._pause()
        if len(laps) != 20:
            raise RuntimeError(9)

        _enter(calculator, "1/0", error=True)
        if len(history) != 20:
            raise RuntimeError(10)
        gc.collect()
        _sample()

        plot.expr = "x*x"
        plot._state[2][2] = True
        plot._state[2][3] = True
        flags = 1
        steps = 0
        while flags & 1:
            flags = plot._settle_curve_step(propagate_memory=True)
            _sample()
            if flags & 4:
                gc.collect()
                _sample()
            steps += 1
            if steps > 256:
                raise RuntimeError(11)
        if plot._state[2][0] is None or plot._state[1][3] == 2:
            raise RuntimeError(12)
        plot._discard_curve_runtime(release_workspace=True)
        plot.expr = ""
        gc.collect()
        _sample()

        main_module = sys.modules["main"]
        result = main_module._reload_functions_after_reclaim(
            nav, nav.current, settings, registry)
        _sample()
        if result is not registry or registry.plugin_errors:
            raise RuntimeError(13)
        panel.set_plugin_catalog(registry.plugin_dependencies)
        panel.set_load_errors(registry.plugin_errors)
        gc.collect()
        _sample()
        if "calc.loader" in sys.modules:
            raise RuntimeError(14)

        index = 1
        while index < len(screens):
            nav.go_to(screens[index])
            nav.present_current()
            _sample()
            nav.go_back()
            nav.present_current()
            _sample()
            index += 1
    finally:
        calculator._state[1].dismiss()
        calculator.mode = 0
        calculator.input_box.clear_str()
        history[:] = ()
        index = 0
        while index < added:
            prefix = "_a" + str(index)
            name = prefix + "_" * (24 - len(prefix))
            variables.pop(name, None)
            index += 1
        context.dirty = dirty
        laps[:] = ()
        stopwatch._clock[1] = False
        stopwatch._clock[2][0] = False
        stopwatch._clock[2][1] = 0
        stopwatch._clock[2][2] = 0
        stopwatch._clock[3][0] = 0
        stopwatch._clock[3][1] = 0
        stopwatch._clock[3][2] = 1
        stopwatch._clock[3][3] = stopwatch_revision
        plot._discard_curve_runtime(release_workspace=True)
        plot.expr = ""
        nav.reset(root)
        gc.collect()
    if len(variables) != variable_count:
        raise RuntimeError(15)
    return gc.mem_free()


def run(runtime=None, emit=print):
    global _heap_min
    _heap_min = -1
    if runtime is None:
        from runtime_handle import get_resident_runtime
        runtime = get_resident_runtime()
    state = runtime._binding_state
    display = state[4].renderer.display
    framebuffer = display.gs4_buf
    framebuffer_id = id(framebuffer)
    workspace = state[4].memory.get_plot_workspace()
    workspace_id = id(workspace)
    display.sleep()
    gc.collect()
    heap_before = _sample()
    first_end = -1
    heap_end = heap_before
    stable_min = heap_before
    try:
        emit(
            "APPLICATION_BEGIN rounds=5 history=20 history_chars=768 "
            "variables=16 laps=20 plugins=3")
        round_index = 0
        while round_index < 5:
            heap_end = _exercise_round(state)
            _sample()
            if heap_end < stable_min:
                stable_min = heap_end
            if first_end < 0:
                first_end = heap_end
            emit(
                "APPLICATION_ROUND round=" + str(round_index + 1)
                + " heap_end=" + str(heap_end)
                + " heap_min=" + str(_heap_min))
            round_index += 1

        heap_delta = heap_end - first_end
        emit(
            "APPLICATION_END rounds=5 memory_errors=0 errors=0 heap_min="
            + str(_heap_min) + " stable_min=" + str(stable_min)
            + " heap_delta=" + str(heap_delta)
            + " transient_gate=observed_only stable_required=4096 framebuffer_bytes="
            + str(len(framebuffer)))
        if (len(framebuffer) != 8192 or id(framebuffer) != framebuffer_id
                or len(workspace) != 104
                or state[4].memory.get_plot_workspace() is not workspace
                or id(workspace) != workspace_id):
            raise RuntimeError("Framebuffer contract changed")
        if stable_min < 4096:
            raise RuntimeError("Application stable operation reserve is too small")
        if heap_delta < -512:
            raise RuntimeError("heap drift")
    except MemoryError:
        emit("APPLICATION_RESULT FAIL memory_errors=1 errors=0")
        raise
    except BaseException:
        emit("APPLICATION_RESULT FAIL memory_errors=0 errors=1")
        raise
    finally:
        display.sleep()
    emit("APPLICATION_RESULT PASS memory_errors=0 errors=0")
    return (_heap_min, heap_end - first_end, 0, 0)


if __name__ == "__main__":
    run()

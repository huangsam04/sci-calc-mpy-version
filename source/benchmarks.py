"""Read-only synthetic navigation benchmark for the SCI-CALC UI."""
import gc
import time

from performance import metrics as _metrics


def _heap_free():
    free = getattr(gc, "mem_free", None)
    return free() if free is not None else -1


def _collect(metrics):
    started = time.ticks_us()
    gc.collect()
    elapsed = time.ticks_diff(time.ticks_us(), started)
    metrics.record_gc(elapsed)
    return elapsed


def _drive_transition(nav, metrics, frame_pace_ms, record=True):
    while nav.is_transitioning():
        started = time.ticks_us()
        nav.draw_transition(time.ticks_ms())
        if record:
            metrics.record_frame(time.ticks_diff(time.ticks_us(), started))
        if nav.is_transitioning() and frame_pace_ms:
            time.sleep_ms(frame_pace_ms)


def _emit_report(report, emit):
    phases = ",".join(
        name + ":" + str(elapsed)
        for name, elapsed in report["boot_phases_ms"])
    emit("BENCH boot_phases_ms=" + phases)
    emit("BENCH nav_event_p95_us="
         + str(report["input_to_present_us"]["p95_us"])
         + " nav_event_max_us="
         + str(report["input_to_present_us"]["max_us"]))
    emit("BENCH frame_p95_us=" + str(report["frame_us"]["p95_us"])
         + " frame_max_us=" + str(report["frame_us"]["max_us"]))
    emit("BENCH gc_p95_us=" + str(report["gc_us"]["p95_us"])
         + " gc_max_us=" + str(report["gc_us"]["max_us"]))
    emit("BENCH heap_before=" + str(report["heap_before"])
         + " heap_after=" + str(report["heap_after"])
         + " heap_delta=" + str(report["heap_delta"]))


def _build_runtime(metrics):
    """Build the normal UI without entering its infinite keyboard loop."""
    metrics.start_boot()
    from main import _init_display, _reload_functions, Nav
    metrics.mark_boot("main_module")
    display = _init_display()
    metrics.mark_boot("display")

    from input.keyboard import Keyboard
    Keyboard()
    metrics.mark_boot("keyboard")

    from display.xglcd_font import XglcdFont
    font_main = XglcdFont("/sd/fonts/Bally7x9.xglcd", 7, 9)
    font_small = XglcdFont("/sd/fonts/Neato5x7.xglcd", 5, 7)
    metrics.mark_boot("fonts")

    from utils.storage import DeferredStorage, load_settings, load_vars
    settings = load_settings()
    persistence = DeferredStorage()
    metrics.mark_boot("settings")
    vars_dict = load_vars()
    metrics.mark_boot("variables")
    registry = _reload_functions(settings)
    registry.angle_mode = settings.get("angle_mode", 0)
    metrics.mark_boot("functions")

    from screens.about import AboutScreen
    from screens.calculator import CalculatorScreen
    from screens.function_panel import FunctionPanel
    from screens.main_menu import MainMenu
    from screens.plot import PlotScreen
    from screens.settings import SettingsScreen
    from screens.stopwatch import StopwatchScreen
    from version import VERSION
    metrics.mark_boot("screen_imports")

    about = AboutScreen(font_main, VERSION)
    calc_screen = CalculatorScreen(
        font_main, font_small, registry, vars_dict,
        display_digits=settings.get("display_digits", 4))
    settings_screen = SettingsScreen(
        font_main, display, settings, about,
        request_save=persistence.request_settings,
        on_display_digits_change=calc_screen.set_display_digits)
    func_panel = FunctionPanel(
        font_main, request_settings=persistence.request_settings,
        settings=settings)
    func_panel.set_load_errors(registry.plugin_errors)
    stopwatch = StopwatchScreen(font_main)
    plot_screen = PlotScreen(font_main, font_small, registry)
    main_menu = MainMenu(font_main)
    main_menu.add_screen("Calculator", calc_screen)
    main_menu.add_screen("Plot", plot_screen)
    main_menu.add_screen("Function Panel", func_panel)
    main_menu.add_screen("Stopwatch", stopwatch)
    main_menu.add_screen("Settings", settings_screen)
    nav = Nav(display, font_small, registry)
    nav.boot(main_menu)
    metrics.bind_runtime(
        nav, main_menu,
        (calc_screen, plot_screen, func_panel, stopwatch, settings_screen))
    metrics.mark_boot("ui_ready")


def run(cycles=50, frame_pace_ms=16, gc_runs=3, emit=print,
        metrics=_metrics, build_runtime=None):
    """Measure synthetic repeated navigation without changing user state."""
    runtime = metrics.runtime()
    if runtime is None:
        (build_runtime or _build_runtime)(metrics)
        runtime = metrics.runtime()
    if runtime is None:
        raise RuntimeError("Benchmark runtime builder did not bind navigation")
    nav, root, targets = runtime
    if not targets:
        raise RuntimeError("Benchmark runner has no navigation targets")

    if nav.current is not root:
        nav.reset(root)

    # Load each target and populate its bounded caches before sampling heap
    # stability. This keeps one-time font/module allocations out of the result.
    warmup_transitions = 0
    for target in targets:
        nav.go_to(target)
        _drive_transition(nav, metrics, frame_pace_ms, record=False)
        nav.go_back()
        _drive_transition(nav, metrics, frame_pace_ms, record=False)
        warmup_transitions += 2

    metrics.reset_run()

    _collect(metrics)
    heap_before = _heap_free()
    for _ in range(max(1, gc_runs) - 1):
        _collect(metrics)

    for index in range(max(0, cycles)):
        target = targets[index % len(targets)]
        metrics.record_input()
        nav.go_to(target)
        _drive_transition(nav, metrics, frame_pace_ms)
        nav.go_back()
        _drive_transition(nav, metrics, frame_pace_ms)

    _collect(metrics)
    heap_after = _heap_free()
    report = metrics.snapshot()
    report["navigation_cycles"] = max(0, cycles)
    report["warmup_transitions"] = warmup_transitions
    report["heap_before"] = heap_before
    report["heap_after"] = heap_after
    report["heap_delta"] = (heap_after - heap_before
                            if heap_before >= 0 and heap_after >= 0 else -1)
    if emit is not None:
        _emit_report(report, emit)
    return report

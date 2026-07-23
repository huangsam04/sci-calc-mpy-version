# SCI-CALC MicroPython 技术说明

本文是 `mp_version` 1.2.1 的实现说明和维护入口。它以源码当前行为为准，使用伪代码解释
从 ESP32 上电到应用、输入、计算、显示、持久化、部署与诊断的完整逻辑；其中已合并
2026-07-23 的性能调优记录。除 C 字体文件和 PNG 截图这类纯数据资产外，所有运行时和
主机侧可执行逻辑均在本文中覆盖。

## 1. 系统边界与文件布局

目标硬件为 ESP32-WROOM-32E、SSD1322 256x64 4 位灰阶 OLED、5x6 矩阵键盘和 FAT32
SD 卡。应用使用 MicroPython；内部 Flash 只放可恢复的启动链，绝大多数应用位于 SD 卡。

```text
内部 Flash                                      SD 卡 /sd
-----------                                      ---------
boot.py       挂载 SD                            launch.py -> main
sdcard.py     SPI block-device 驱动               main.py 或 main.mpy
main.py       内部启动器 / 恢复分支               anim/ calc/ display/ input/ screens/ ui/ utils/
recovery.py   SD 应用损坏时的最小界面             functions/*.py（动态插件源码）
display/      恢复界面所需驱动                    fonts/*.xglcd（构建期紧凑字体）
                                                     settings.json / vars.json（可变状态）
```

源码目录的职责如下：

| 区域 | 责任 |
| --- | --- |
| `source/main.py` | 应用构造、事件循环、导航状态机、崩溃恢复。 |
| `source/calc/` | 高精度十进制数、函数注册表、依赖感知插件加载、Pratt 表达式解析与求值。 |
| `source/screens/` | 各业务页面的状态、绘制和按键处理。 |
| `source/ui/`、`source/anim/` | 通用控件、帧合成、页面/控件动画和状态栏。 |
| `source/display/` | SSD1322 帧缓冲/SPI 驱动、单色调色板、X-GLCD 字体读取。 |
| `source/input/keyboard.py` | 键盘矩阵扫描、去抖、边沿事件与长按。 |
| `source/utils/` | 崩溃可恢复 JSON 存储和 OLED 空闲休眠。 |
| `source/boot.py`、`internal_main.py`、`recovery.py`、`sdcard.py` | 内部 Flash 启动和 SD 失败恢复。 |
| `source/diagnostics.py`、`benchmarks.py`、`performance.py` | 串口自检、可重复的导航基准和有界统计。 |
| `tools/`、`deploy.ps1`、`check.ps1` | 主机端字体构建、ABI 验证、部署与持续检查。 |

`__init__.py` 仅标记包，不承载业务逻辑。`source/fonts/*.c` 是 96 个 ASCII 字形的源数据，
`image*.png` 是静态图片；它们不包含运行期控制流程。

## 2. 硬件资源与启动链

### 2.1 引脚、总线和显示坐标

| 用途 | 资源 |
| --- | --- |
| OLED SPI2 | SCK GPIO18、MOSI GPIO23、CS GPIO5、DC GPIO16、RST GPIO17，10 MHz。 |
| SD SPI2 | SCK GPIO18、MOSI GPIO23、MISO GPIO19、CS GPIO4，10 MHz。 |
| 电池电压 | GPIO36 ADC，11 dB 衰减，按 `raw / 4095 * 3.3 * 2` 估算。 |
| 键盘行 | GPIO33、32、35、34、39（输入）。 |
| 键盘列 | GPIO13、12、14、27、26、25（输出）。 |
| 页面内容区 | x=0..209；状态栏 x=213..254；屏幕是 256x64。 |

SD 卡和 OLED 有意共享 SPI2。CS4 与 CS5 将两个从设备的事务隔离，因此不要替换为独占
SPI host 的 `machine.SDCard(slot=2)`。

### 2.2 MicroPython 启动与恢复

MicroPython 的启动顺序是 `_boot.py -> /boot.py -> /main.py`。内部 `boot.py` 仅负责建立
SD 文件系统并把 `/sd` 放到 `sys.path`，失败时把原因输出到串口，随后仍让内部启动器继续。

```text
on boot.py:
    try:
        spi2 = SPI(2, pins=18/23/19, 10 MHz)
        card = SDCard(spi2, CS=4)
        mount(card, "/sd")
        prepend_once(sys.path, "/sd")
    except error:
        print("SD mount failed", error)

on internal /main.py:
    try:
        path = "/sd/launch.py" if it exists else "/sd/main.py"  # 兼容旧版
        execfile(path)                                            # 不重复 import main
    except app_error:
        print("SCI-CALC recovery", app_error)
        remove_all(sys.path, "/sd")                              # 损坏模块不再遮蔽内部模块
        prepend_once(sys.path, "/")
        unload("recovery", "display", "display.ssd1322", "display.mono_palette")
        print_exception(app_error)
        show_recovery(app_error)
```

恢复界面只初始化 OLED 所需的 SPI、CS、DC、RST，显示“CHECK SD CARD”、截断至 28 字符的
错误信息和“Fix card, then RESET”。这样即使 SD 上的主应用、字体或 Python 模块损坏，设备
仍有可见的故障出口。

`launch.py` 的职责只有 `import main; main.main()`，所以部署 `.mpy` 时解释器会优先加载
`main.mpy`，回退部署时加载 `main.py`。

### 2.3 主应用的分阶段引导

`main._init_display()` 最先构造 SSD1322，这让用户先看到启动页，再承受后续导入与 SD I/O。
进度条不是虚假的长动画：每个同步阶段只提交一帧，标题灰度逐步增加，完成阶段才推进条宽。

```text
main():
    metrics.start_boot()
    display = init_display(SPI2, OLED pins)
    mark("display"); boot_progress(1/8, "Loading keyboard")

    kb = required_import(Keyboard)                    # 失败：显示错误后终止，应用不可操作
    mark("keyboard")

    font_main  = try_load("/sd/fonts/Bally7x9.xglcd") # 失败：使用内置 8x8 字体
    font_small = try_load("/sd/fonts/Neato5x7.xglcd") # 失败：允许为 None
    mark("fonts")

    settings = try_load_settings_or_defaults()
    display.set_brightness(settings.brightness)
    mark("settings")
    variables = try_load_vars_or_empty_dict()
    mark("variables")

    registry = try_reload_functions(settings)
               or builtin_registry(all four groups)
    registry.angle_mode = settings.angle_mode
    mark("functions")

    import all screens
    construct DeferredStorage, pages, overlays, main menu
    mark("screen_imports")
    boot_progress(8/8); sleep(40 ms)
    enter_main_loop(...)
```

每个非关键阶段的异常都会调用 `_boot_fail()`：画出阶段、错误摘要，等待两秒后使用默认设置、
空变量、内置函数或内置字体继续。键盘和页面模块属于关键依赖，失败后不能安全运行，因此会
向上抛出异常。

## 3. 输入：矩阵扫描、去抖和按键语义

### 3.1 5x6 矩阵扫描

键盘每至少 15 ms 扫描一次。扫描某一列时将该列拉高、等 10 us、读取所有行，再拉低该列。
每个键持有独立状态，状态为 `NOT_PRESSED`、`RISING_EDGE`、`PRESSED` 或 `FALLING_EDGE`。

```text
Keyboard.scan():
    now = ticks_ms()
    if last_scan exists and now - last_scan < 15 ms: return
    last_scan = now
    for each column:
        column.high(); sleep_us(10)
        for each row:
            key[row][column].update(read_row(row), now)
        column.low()

Key.update(high, now):
    if high and not pressed:
        since_release = now - end_press, if any
        if first press or since_release > 50 ms:
            accept_rising_edge()
        else if since_release >= 15 ms:
            accept only after two consecutive high samples
        # 小于 15 ms：视为释放抖动，忽略
    else if high:
        state = PRESSED; status_time = now - start_press
    else if pressed:
        pressed = false; state = FALLING_EDGE; end_press = now
    else:
        state = NOT_PRESSED
        reset click counter after 100 ms of release
```

`pop_key_event()` 按行列顺序取第一个尚未消费的上升沿，并在同一时刻采集 Shift `(4, 0)`
是否按下，返回 `(row, col, shift_held)`。因此同一物理按下只会被业务层处理一次，也不会在
稍后读取 Shift 造成标签错配。`consume_long_press()` 对每次按住只返回一次；松开才重新武装。
`discard_pending_events()` 在休眠/唤醒或恢复流程中清掉未消费上升沿。

键位字符串由普通和 Shift 两套映射决定：Shift+`/`、`*` 为括号，Shift+`8/2/4/6` 是方向键，
Shift+`^` 为 `sqrt`，Shift+`RPN` 为 `rpn`，Shift+`Tab` 为 `stab`。页面以标签或原始
行列判断操作，后者用于区分物理 4/6 列跳转、快捷键和字母面板。

## 4. UI 生命周期、导航和帧调度

### 4.1 UIElement 与动画所有权

所有页面/控件继承 `UIElement`，约定 `activate()`、`deactivate()`、`draw(display)`、
`update(keyboard, event)` 和 `animation_children()`。基类不做业务工作，只定义生命周期边界。

动画引擎用 `(id(target), attribute)` 为键保存 `Animation`，同一属性新动画覆盖旧动画；每帧
按延迟、时长和 easing 写回对象属性，结束时精确吸附到终值。支持线性、三次进/出/平滑、
quint 出、quad 出、弹跳；项目常用 `OUT_QUAD`。临时退出目标留在 `_tmp_targets`，直到它没有
活动动画。离开页面时，`cancel_animations(root)` 深度遍历 `animation_children()`，只取消该页面
拥有的动画，避免错误清除其他页面动画。

```text
animate_all():
    for each live animation:
        if before delay: keep it
        else:
            t = clamp((now - start) / duration, 0..1)
            target.attr = int(start + (end - start) * easing(t))
            if t == 1: set exact end; remove it

cancel_animations(root):
    owned_ids = DFS(root + animation_children)
    remove animations and temporary targets whose target id is in owned_ids
```

共享时序：页面转场 190 ms，面板滑入 130 ms，菜单光标 100 ms，文本光标 70 ms；活跃帧目标
16 ms、空闲帧目标 66 ms，活动/空闲循环睡眠分别为 1/10 ms。

### 4.2 `Nav` 栈和输入锁

`Nav` 维护页面栈，并把页面状态生命周期委托给 `PageResidency` 的 `leave/prepare/settle` 三个
接口。离页时旧像素继续留在 OLED 显示 RAM，只捕获一条有界逻辑状态，派生缓存和 Plot
workspace 随即释放；JSON 编码与写盘都推迟到无动画的安静循环。目标页只激活默认空布局，
真实状态和重缓存必须等转场结束后恢复。

```text
Nav.filter_event(keyboard, event):
    if transition is running: return None
    if input_locked:
        if any key remains physically pressed: return None
        input_locked = false
    return event

Nav.draw_transition(now):
    if no transition: return false
    t = clamp((now - started) / 190 ms, 0..1)
    if reveal strip is available:
        renderer.present_transition(ease_out_quad(t), forward)
    else:
        fade OLED current; present default target only at the dark midpoint
    if t == 1: transition = None        # 当前仍是目标页的默认空布局
    remember SPI present elapsed time
    return true
```

转场完成后的安静循环调用 `settle_current()`，每次最多执行一项 SWAP 写入、读取或页面重建；
返回的位标志决定是否重绘和是否还有后续工作。锁定机制保证触发 `ENT`/`ESC` 的按键释放前不会
落入新页面。`reset(root)` 用于崩溃恢复：清空
所有动画和转场，将栈替换为根页并锁住输入，避免保留损坏页面状态。

### 4.3 Renderer、状态栏和低内存页面揭示

`Renderer` 不再为页面保存两张 6.7 KiB GS4 图层。旧页已经存在于 SSD1322 自带显示 RAM；
新页默认布局只需画入应用原有的 8 KiB 主帧缓冲。转场额外内存是4个控制器列 x 2字节/列 x
64行，即固定512字节条带，并保留7 KiB启动安全线。旧页释放后仍无法取得条带时，导航改用
SSD1322 master-current 淡出/淡入；中点电流为零时才提交默认目标页，因此不存在可见硬切。

```text
Renderer.present(screen):
    display.clear_buffers(black)
    screen.draw(display)                         # 仅画内容区
    outgoing_screen = screen
    sidebar.draw(display)                        # 先清除 x >= 210，再重画 BAT/DEG|RAD
    timed display.present()

Renderer.capture_transition(outgoing, incoming):
    ensure 512-byte transition strip
    keep outgoing pixels untouched in SSD1322 RAM
    render allocation-bounded incoming default layout into the framebuffer

Renderer.present_transition(progress, forward):
    newly_revealed = eased controller columns - already_revealed
    Viper copy only those packed GS4 rows into the 512-byte strip
    display.present_region(direction, strip)      # OLED 其余 RAM 保持旧页
```

SSD1322 一个控制器列包含 4 个 GS4 像素。Viper 按连续字节复制新增区域，`present_region()` 只为
该窗口设置列/行地址并发送数据；整段 190 ms 动画合计约写一屏内容，而不是每帧写一屏。侧栏不在
揭示窗口内，仍每 500 ms 读取一次 ADC 并显示电压与注册表的 `RAD`/`DEG`。

### 4.4 主循环、渲染门控和崩溃恢复

主循环是唯一调用 `pop_key_event()` 的位置。这确保页面不会争抢输入，并允许在一个循环内先
处理导航再决定是否显示，让输入帧成为第一张转场帧。

```text
forever:
    try:
        kb.scan()
        event = kb.pop_key_event()
        now = ticks_ms()

        state = display_power.update(now, kb.any_pressed())
        if state != AWAKE:
            kb.discard_pending_events()
            if state == WOKE: force next render
            sleep(25 ms); continue

        every 100 loops: collect GC (and measure it when diagnostics enabled)
        animate_all(); cleanup_finished_temporary_targets()
        event = nav.filter_event(kb, event)

        if event:
            optionally print INPUT trace
            handle global shortcuts before page:
                Shift+RPN on calculator/plot -> letter panel
                ANG -> toggle registry.angle_mode, queue settings write
        if not transitioning and (event or DEL/ESC physically held):
            result = current_page.update(kb, event)

        route result:
            BACK -> nav.go_back()
            page object -> nav.go_to(page object)
            FUNC_PICKER/LETTER/VAR done -> nav.go_back()
            FUNC_PANEL_DONE -> reload registry in place, show errors or go back
            FUNC_PANEL_CANCEL -> go back

        active = transition or any_animation
        render if input changed, or active/dirty/stopwatch requires deadline,
                  or 500 ms keepalive elapsed
        if render: draw transition OR canonical current page, clear dirty

        if EvalContext became dirty: snapshot and queue vars write
        if idle (no animation/input/result): flush at most one deferred write
        sleep(1 ms if active else 10 ms)
    except error:
        wake/reset power state; draw minimal crash page; print exception
        clear font caches; GC
        wait any key, then wait all keys released
        nav.reset(main_menu)
```

`_needs_render()` 绕过空闲帧限速以立即显示输入结果；活跃状态依 16 ms 目标更新，静止页依
66 ms 限制，但无变化时至少每 500 ms 保活。诊断模式每 5 秒输出平均渲染/传输耗时、GC 前后
空闲堆和活动动画数。

## 5. 持久化、空闲休眠和运行期服务

### 5.1 JSON 状态与原子提交

默认设置：`angle_mode=0`、`cursor_mode=1`、四个内置函数组均开启、诊断关闭、休眠 180 秒、
亮度 100%、`display_digits=4`。读取时合并默认值，丢弃旧版本字段，校验函数列表、角度、
亮度、显示位数和休眠时间；休眠范围为 0..86400 秒，亮度范围为 10..100%，显示位数范围为
1..12。

主文件损坏时先读 `.bak`；两个候选都不可用才返回默认对象。写入按如下顺序完成：

```text
atomic_write(path, data):
    write JSON to path.tmp; flush
    if path exists and is valid JSON object:
        delete old path.bak
        rename path -> path.bak
    else if path exists but invalid:
        rename path -> path.bad             # 不得覆盖已知良好的备份
    rename path.tmp -> path
    if os.sync exists: os.sync()
    return true
on any failure:
    delete path.tmp
    if valid primary was moved and new primary absent: restore .bak -> primary
    retain in-memory cache; return false
```

`configure_storage()` 仅供宿主测试改写目录。`save_settings()` 和 `save_vars()` 在写之前更新
内存缓存，写失败也不会丢失当前用户状态。

`DeferredStorage` 让按键逻辑不做 SD I/O，也不再在请求时深复制整棵 JSON 数据；它只保留最新
对象引用并合并同类请求，编码和写入都发生在无动画的空闲阶段。失败时保留请求，约2秒后重试，
并调用回调更新 UI 的“Not saved - check SD”。

`SessionSwap` 在 `/sd/.sci-calc/swap` 为每页保存独立、最大4 KiB的会话记录，外层包含魔数、
版本、UTF-8长度和校验值，并通过 `.tmp/.bak` 原子替换。启动时删除旧会话；文件缺失、损坏或
SD不可用时，`PageResidency` 只作废当前页记录、显示错误并保留长期 settings/variables。

### 5.2 OLED 休眠

这是 OLED 控制器休眠，不是 ESP32 深度睡眠，因为矩阵键盘无法可靠地从后者唤醒。`DisplayPower`
维护最后活动时间，任意物理按键都刷新它。

```text
DisplayPower.update(now, any_pressed):
    if sleeping:
        if no key: return SLEEPING
        display.wake(); sleeping=false; wake_locked=true; last_activity=now
        return WOKE
    if wake_locked:
        if key still held: return LOCKED
        wake_locked=false; last_activity=now; return AWAKE
    if any key: last_activity=now; return AWAKE
    if timeout > 0 and now-last_activity >= timeout:
        display.sleep(); sleeping=true; return SLEEPING
    return AWAKE
```

唤醒键被隔离至释放后，不能意外触发页面操作；秒表使用真实 ticks 计算，所以显示休眠期间仍按
实际时间前进。

## 6. 计算引擎

### 6.1 可替换的函数注册表

`FunctionRegistry` 的内部定义是紧凑元组：
`(name, precedence, kind, min_args, associativity, callback)`。`kind` 为 `infix`、`prefix`、
`postfix` 或 `list`。注册时拒绝空名、空白/语法字符、混合字母数字的符号运算符、非法结合性、
非回调、非法最小参数和重复名。`symbolic_names()` 把运算符按长度倒序，确保 tokenizer 对
多字符符号优先最长匹配。

```text
registry._add(name, callback, kind, precedence, assoc, min_args):
    validate name grammar and registration metadata
    reject duplicate
    defs[name] = compact tuple
    revision += 1

registry.replace(staged):
    mutate existing registry object with staged definitions
    preserve live references held by calculator/plot/plugins
    copy angle mode and plugin errors; revision += 1

registry.merge(staged):
    first reject all conflicts; update defs atomically at registry level
    revision += 1 iff definitions were added
```

`EvalContext` 共享变量字典和实时注册表。它在构造和 `set_var` 时把普通 int/float 正规化为
高精度 `Number`，并通过 `context.numeric` 暴露同一套科学函数。`set_var` 仅在值变化时置
`dirty`；`delete_var` 同理。主循环消费 dirty 位后才安排变量持久化。角度模式不在 context
自己缓存，而是始终转发 `context.registry.angle_mode`，使重载后的注册表仍然是单一事实来源。

内置组及优先级：

| 组 | 注册内容 |
| --- | --- |
| `basic` | `+ -` 20，`* /` 30，`^` 40 且右结合，赋值 `=` 10 且右结合。 |
| `trig` | `sin cos tan asin acos atan sec csc cot`，随 RAD/DEG 转换。 |
| `math` | `sqrt ln exp log abs`。 |
| `list` | `max(...)`、`min(...)`，至少一个参数。 |

除零会在除法中明确抛错；幂和科学函数均基于 `calc.number.Number`，不经过 `math.pow` 的
浮点溢出路径。`pi`、`e` 是求值期的 30 位保留常量。用户变量和函数名区分大小写，不支持
隐式乘法。

`Number` 使用标准化的 `(signed_coefficient, decimal_exponent)` 表示
`coefficient * 10^exponent`。每一步四则、幂和科学函数计算保留最多 30 位有效数字，指数单独
保存，因此 `10^100000` 只产生指数 100000 而不会分配十万位十进制字符串或得到 `inf`。
超越函数通过高精度级数/范围约化计算；无法可靠约化的超大角度和指数抛出可见错误。显示层将
值圆整为 `x.xxxx*10^x`，与计算精度分离。

### 6.2 词法、Pratt 解析和 AST

`tokenize(expr, registry)` 忽略空白，识别十进制/科学记数法 `Number` 字面量、标识符、单/双引号字符串、
`() , ;` 和当前注册表允许的符号。多小数点、未闭合字符串、未知字符会带源位置的 `ParseError`。

`_Compiler` 是最大深度 30 的 Pratt parser。它把表达式编译为元组 AST，而不是使用 Python
`eval`：

```text
compile(expr):
    if tokens empty: return literal(None)
    statements = [parse_expr(min_precedence=0)]
    while next token is ';': consume it; parse another statement if present
    reject any leftover token
    return only statement or sequence(statements)

parse_expr(min_precedence, depth):
    left = parse_prefix(depth)
    while current is registered infix/postfix and precedence >= min_precedence:
        op = consume
        if postfix: left = postfix(op, left); continue
        next_min = precedence + 1 for left associativity else precedence
        right = parse_expr(next_min, depth+1)
        require variable AST at left side of '='
        left = infix(op, left, right)
    return left

parse_prefix(depth):
    number/string -> literal
    '(' expr ')' -> inner AST
    unary '+'/'-' -> unary(parse_expr(40))
    name registered as list -> parse comma-separated parenthesized call
    name registered as prefix -> parse '(expr)' or following expression at prefix precedence
    otherwise -> variable(name)
```

把一元正负的递归阈值设为 40，使 `-2^2` 解析成 `-(2^2)`，同时 `2^-2` 可以合法地解析为
`2 ^ (-2)`。列表函数必须有括号，按 `min_args` 校验；赋值的左侧必须是变量；`;` 从左到右
逐句求值并返回最后一句的值。

```text
evaluate(node, context):
    literal -> value
    sequence -> evaluate each, return final
    variable -> variables[name], else pi/e, else ParseError
    unary -> grammar unary callback(child value)
    lookup live definition; error if function was disabled after compile
    prefix/postfix -> callback(child value, context)
    list -> evaluate every child to args; callback(args, context)
    infix '=' -> callback(left.variable_name, evaluate(right), context)
    other infix -> callback(evaluate(left), evaluate(right), context)
    numeric callback result -> normalize to Number; reject nan/inf
    wrap unexpected runtime errors as ParseError(source position)
```

### 6.3 SD 插件隔离、热重载和附带插件

插件为 `/sd/functions/*.py` 中不以 `_` 开头的文件，每个必须定义 `register(registry)`。可选的
`DEPENDENCIES = ("other", ...)`（兼容旧名 `REQUIRES`）声明其他 Add-in；可选 `EXPORTS` 字典
显式提供可复用的函数或常量。加载器先在全新的 staging registry 中 `exec(compile(source))`，
仅在 `register` 成功且没有名称冲突时合并到 live registry。单个插件的语法、执行、依赖或注册
错误会记录到 `LoadReport.errors` 和串口，不能破坏其他插件。

```text
load_function_files(live, enabled_names):
    for requested add-on:
        execute source in an isolated namespace once
        read DEPENDENCIES and recursively load every dependency first
        reject missing dependency or cycle without registering the dependent
        staging = empty FunctionRegistry(angle_mode=live.angle_mode)
        staging receives only declared dependency EXPORTS
        require callable namespace.register; namespace.register(staging)
        validate EXPORTS, live.merge(staging), then retain plugin exports
        report.loaded += (file name, definition count, WELCOME text)
        report.auto_enabled += dependencies absent from enabled_names
```

Add-in 可在注册期使用 `registry.plugin(name)`，在回调运行期使用 `context.plugin(name)`；注册期
只注入已声明并成功装载的 `EXPORTS`，Add-in 应在两个阶段都遵循其显式依赖。`describe_function_files()`
也在隔离 registry 中运行插件，以生成面板摘要；`describe_plugin_dependencies()` 单独读取依赖元数据。
`_reload_functions(settings, existing_registry)` 从保存的内置组和 `plugin:` 名称建立 staged registry；
若传入旧 registry 则原地 `replace`，保持所有页面指针有效并保留原角度模式。

随附插件：

| 文件 | 注册逻辑 |
| --- | --- |
| `basic.py` | 左结合、优先级 30 的 `%`；右操作数为零时报错。 |
| `trig.py` | 使用 `context.numeric` 注册 `sinh/cosh/tanh`、固定按度计算的 `sind/cosd/tand`，以及无参数列表函数 `PI()`。 |
| `solve.py` | `solve("expr", "var", guess)`：只编译一次，复制父变量字典，以高精度中央差分导数运行最多 60 次牛顿迭代；拒绝极小导数和不收敛。它不改写持久变量。 |

## 7. 页面与控件状态机

### 7.1 通用控件和错误弹窗

`Menu` 保存项目、选择下标和视图偏移；上下键移动并保持选择行可见，`ENT` 返回 `ENTER`，
`ESC` 返回 `BACK`。光标独立动画到目标行，标签在加入菜单时只截断一次。`InputBox` 保存文本、
插入点和可配置行数的视图；它按比例字体的实际像素宽度切分文本，并用同一测量结果定位光标。
紧凑覆盖层保留单行、42 字符默认值；计算器传入最多双行和 96 字符上限，短公式仍只占一行。`DEL` 删除插入点之前字符，
按住超过 750 ms 后每 100 ms 重复；函数键自动插入如 `sin(`，快捷键则交还页面。

`ErrorPopup` 将内部错误映射到可行动文字（除零、未知变量、定义域、溢出、括号、参数不足、
函数关闭、求解不收敛等），显示表达式和源位置 `^`，任意按键或 10 秒到期后关闭。到期检查放在
`draw()` 和相关 `update()` 中，因此即使没有新输入也会消失。

### 7.2 主菜单与计算器

主菜单有 Calculator、Plot、Function Panel、Stopwatch、Settings 五项；返回根页面保留原选择。

计算器有三种模式：输入、历史导航、错误弹窗。成功求值将 `(expr, result)` 插到最多 20 条历史
的头部，清空输入；每个 `Number` 统一格式化为 `x.xxxx*10^x`。显示位数来自
`settings.display_digits`，只作用于渲染和从历史插入的表达式文本，不改变历史中保留的数值对象。

计算页将高度优先留给编辑：顶部 `InputBox` 默认是 12 px 的单行表达式区，首行放不下时扩展为
22 px 的双行区。单行时下面显示四条 9 px 高的历史记录；展开后显示三条，页脚始终保留状态和长度
计数。`InputBox` 的容量为 96 个字符；它不再把“屏幕能显示多少字”当作“允许输入多少字”。控件按
字体实际像素宽度切分整条表达式，缓存每个视觉行的 `(start, end)` 范围，并让 `view_offset` 始终
指向包含光标的一到两行窗口。这样比例字体、长函数名和光标位置使用同一套宽度计算，历史行中的公式
与结果也会分别裁切，避免互相覆盖。

```text
Calculator.update:
    if popup mode: any event dismisses it
    if ESC held >= 1 s: return BACK
    if input mode:
        action = input_box.update(...)
        ENT + Shift -> insert '='
        ENT -> evaluate stripped input and prepend history / show error popup
        Tab -> enter history if nonempty
        Shift+Tab -> return VARIABLE_PANEL
        ESC -> clear current input, or BACK when already empty
        RPN (not Shift) -> return FUNC_PICKER
        held DEL mutation -> return REDRAW
    if history mode:
        suppress same physical key within 180 ms
        8/2 moves selection; ENT appends selected result to input
        physical 4/6 appends selected original expression
        Tab or ESC exits history (ESC has 500 ms guard)
        Shift+Tab opens variables
```

`context.dirty` 最终由主循环转为异步 `vars.json` 写入。保存失败后的 5 秒内，页脚覆盖显示错误。

### 7.3 函数面板、函数选择器、字母面板和变量面板

函数面板构造时（启动进度仍可见）读取插件文件列表、注册摘要和依赖元数据；普通 `activate()`
只重建菜单，不会执行 SD 插件源码。它列出四个内置组以及 `Add-on:` 项，使用不同 ID（如
`basic` 与 `plugin:basic`），防止同名冲突。若已启用的 Add-in 缺少已知依赖，激活时递归加入
依赖闭包、标为待保存，并在页脚显示 `Auto on: name`；用户关闭一个依赖时，已启用的依赖方也会
关闭。`ENT` 翻转当前会话选择，离开时把 `enabled_functions` 排入异步设置保存，主循环随后原地
重载 registry。加载失败插件会自动聚焦，用户可关闭它。

`Shift+ENT` 是唯一主动重扫路径：重新执行隔离摘要、保持可用的原选择并夹住光标。这样运行中
更换 SD 内容可见，同时不会将任意插件代码带回普通页面转场关键路径。

函数选择器在激活时对 live registry 名称排序，使用四行两列（每页八项）。上下移动一项，
物理 4/6 在列间跳四项，`ENT` 插入选择：prefix/list 插入 `name(`，其他插入符号或名称；`ESC`
也退出而不插入。相同按键 150 ms 冷却。

字母面板是 Shift+RPN 覆盖层，原始键位映射 A-Z、`"` 和 `;`；自身 Shift 键切换大小写，
`Bk` 编辑暂存文本，`OK` 才把暂存文本插入目标 `InputBox`，`ESC` 取消。它限制暂存长度不超过
目标输入的剩余字符数，并对同键 100 ms 去重。

变量面板在激活时对变量名排序，采用与函数选择器相同的两列分页。`ENT` 插入变量名，`DEL`
调用 `EvalContext.delete_var()` 并重建列表，`ESC` 返回；同键 150 ms 冷却，物理 4/6 切列。

### 7.4 绘图

Plot 页持有独立输入框、x 范围 `[-10,10]`、y 范围、仅包含 `x` 的临时变量字典和临时
`EvalContext`，因此绘图不写入计算器变量。查看模式下：8/2 按 0.5/2 缩放 y，Shift+8/2 缩放 x，
物理 4/6 向左/右平移当前 x 范围 25%，ENT 打开编辑，RPN 预填 `x`。编辑框从顶部 14 px 滑入；
ENT 提交，ESC 恢复编辑前表达式，Shift+Tab 重置 x 范围。

编译缓存最多四项 LRU，键为表达式，注册表 `revision` 变化则清空，因此启用/禁用函数后不会
继续使用过期语法树。

```text
render_curve(auto_scale=true):
    program = LRU.get(expr) or compile_expression(expr, live registry)
    if auto_scale, for every second horizontal sample pixel:
        x = x_min + normalized_pixel * (x_max - x_min)
        y = float(evaluate_program(program, temporary x context))
        accept only finite-ish |y| < 1e6; retain at most 24 robust samples
    if no valid sample: clear curve; show popup; return
    if auto_scale:
        full_range = min..max
        central_range = trimmed bounded robust samples
        if full_range > 4 * robust_range: use central range  # 抑制极点
        add 10% padding (at least 0.5; constant curve gets 1)
    reuse or allocate MONO_HMSB curve buffer
    evaluate every second x sample again without retaining a float array:
        map y to pixel only if inside viewport
        plot point; connect to predecessor only if vertical jump <= 3/4 height
        otherwise break polyline to avoid渐近线伪竖线
```

绘制时先画边框、可见的 x/y 轴、原点十字，再以调色板把单色曲线透明 blit 到 GS4 帧缓冲。
曲线缓存只在表达式/范围变化时重算，页面的每一帧只绘制缓存。

### 7.5 秒表、设置与关于页

秒表状态包括运行、暂停、开始 ticks、已累计毫秒、最多 99 条（最新在前）圈次、视图选择和
下一个圈号。`ENT` 在开始/暂停/继续间切换；运行时 `DEL` 记录圈，非运行时 `DEL` 完全复位。
计时一律由 `ticks_diff(now, start_time)` 求得，故页面不刷新或 OLED 睡眠不会停表。列表上下滚动，
相同标签 200 ms 冷却。

设置页四行：固定版本号、打开 About、亮度和 `Display digits`。亮度始终夹到 10..100，步长 10；
物理 4/6 增减，ENT 循环递增至 100 后回 10。显示位数夹到 1..12，步长 1，立即通知
CalculatorScreen 重绘格式。两项可写设置都会异步保存并在底栏显示保存状态。About 显示版本、
ESP32、SSD1322 与键盘硬件说明，ESC 返回。

## 8. 显示、字体和 SD block device

### 8.1 SSD1322 显示驱动

`Display` 构造 256x64、GS4_HMSB 的 8192 字节帧缓冲，复用 1/2 字节命令缓冲并建立 mono-to-GS4
调色板。复位后发送数据手册的命令锁、时钟、1/64 multiplex、行列重映射、供电、灰阶、预充电、
对比度等初始化序列，最后全帧提交。

```text
present():
    set_column_address(0+28, width/4-1+28)  # SSD1322 segment offset / nibble addressing
    set_row_address(0, height-1)
    write WRITE_RAM command
    SPI write entire GS4 buffer

set_brightness(percent):
    percent = clamp(percent, 10, 100)
    current = clamp(round(percent * 15 / 100), 1, 15)
    write MASTER_CURRENT_CONTROL(current)
```

常用 API 是带越界保护的像素、水平/垂直线、Bresenham 线、多线、矩形、圆/椭圆、规则多边形及
填充版本，GS4/mono/raw 位图加载与旋转，精灵、缓存字体文本和内置 8x8 文本。圆采用中点算法，
椭圆采用两个区域的增量算法，填充多边形按扫描线累积每行 x 范围。它们是通用驱动能力；当前
主要 UI 使用 FrameBuffer 的线、矩形和字体 blit。

`sleep()`/`wake()` 仅发送 `AE`/`AF`。`write_cmd()` 对 0、1、2 参数复用固定 bytearray，避免
全帧动画前后创建小对象。`MonoPalette` 用两个像素编码背景/前景灰度，供 `FrameBuffer.blit`
把单色字形和曲线映射到 GS4。

### 8.2 紧凑字体

`tools/build_fonts.py` 从 C 源逐行提取 96 个 ASCII 字形，校验每个字形正好为
`((height-1)//8+1)*width+1` 字节，然后写入：

```text
"XGF1" + [width, height, start_character=32, count=96] + raw glyph bytes
```

`XglcdFont` 读取时优先验证该二进制头；为兼容旧资产，头不匹配时也能以 bytes 方式解析 C 源，
不依赖 UTF-8 注释。字形和整串文本共用上限 256 的缓存；字符串常走一次预渲染/一次 blit，实时
秒表/输入用 `raw=True` 逐字绘制，避免让缓存被不断变化字符串占满。分配失败时清缓存、GC、重试，
仍失败则返回 0 宽占位，不让 UI 崩溃。

### 8.3 SDCard block-device

`sdcard.py` 是 vendored 的 MicroPython SPI SD 驱动，提供 `readblocks`、`writeblocks` 和 `ioctl`
给 VFS。初始化逻辑是低速 100 kHz 发送空时钟，CMD0 进入 idle，CMD8 判断 v2 或非法命令的 v1，
循环 CMD55/ACMD41 直到 ready，CMD9 读取 CSD 计算扇区数，CMD16 设 512 字节块，最后切回目标波特率。

```text
SDCard.cmd(cmd, arg):
    CS low; build [0x40|cmd, arg 4 bytes, crc7|1]; SPI write
    poll R1 response at most 100 reads
    optionally read final response bytes
    CS high and one dummy byte on release

readblocks(n):
    CMD17 for one block or CMD18 for multi-block
    wait data token 0xFE; transfer 512 bytes + discard CRC
    multi-block ends with CMD12

writeblocks(n):
    CMD24 one block / CMD25 multi-block
    send data token, payload, dummy CRC
    require data response low 5 bits == 0x05
    wait nonzero busy byte up to 500 ms; always release CS in finally
    multi-block ends with stop token 0xFD
```

写入被拒或忙超时会抛出可见 `OSError`，且 `finally` 始终释放总线。

## 9. 诊断、基准、部署与主机检查

### 9.1 串口诊断

`diagnostics.DiagnosticSession` 从真实 settings 和启用插件建立轻量 registry/context，但用虚拟
键盘和目标页重放确定性命令，不写变量或设置。支持 `STATUS`、`PANEL`、`KEY row col shift`、
`BACK`、`EVAL expression`；每条输出机器可读 `TRACE`。`run()` 再检查主菜单包含 Calculator、
函数面板 ID 不重复、内置/插件标签不含歧义，打印 `SELFTEST PASS/FAIL failures=n`。

```powershell
..\.venv\python.exe -m mpremote connect COM5 exec `
  "import sys; sys.path.insert(0,'/sd'); import diagnostics; diagnostics.run()"
..\.venv\python.exe -m mpremote connect COM5 reset
```

`mpremote exec` 会进入 raw REPL 并中断正常应用，故 `reset` 是命令的一部分。将 settings 的
`diagnostics` 设为 true 后，正常应用还输出逐键 `INPUT`、页面结果 `ACTION` 和每五秒 `PERF`。

### 9.2 性能基准与统计

`benchmarks.run()` 在没有既有 runtime 时以与 `main` 一致的构造顺序创建独立 UI，但不进入无限
键盘循环。它先对每个目标页往返预热，随后 GC、记录堆、循环选择页面 `go_to/go_back`，驱动转场
至结束，最后再次 GC 并打印启动阶段、导航首帧、帧、GC 与堆变化。该流程不会写用户设置/变量。

`PerformanceMetrics` 的输入到呈现和 GC 样本最多保留 128 个；帧样本不同，使用 128 个 0.5 ms
桶覆盖到 63.5 ms，所有帧都被计数。超慢帧进最后桶；若 p95 落在该桶则报告精确最大值，避免
把量化统计低报为更快。

```text
record_frame(elapsed):
    bucket = min(elapsed // 500 us, 127)
    histogram[bucket] += 1
    frame_count += 1; frame_max = max(frame_max, elapsed)
    if input timestamp pending: append input-to-present sample; clear timestamp

frame_p95():
    find first bucket whose cumulative count >= ceil(0.95 * frame_count)
    return bucket upper bound conservatively
    except last bucket -> return exact frame_max
```

### 9.3 部署

`deploy.ps1 -Port COM5 [-Reset]` 使用工作区 `.venv\python.exe` 的 `mpremote`，每个远程命令最多
重试三次。它先上传内部启动/恢复资产，重置以释放旧程序占用的 SPI2，等待 `/sd` 可列出。

```text
deploy(port):
    build compact fonts
    upload internal boot, sdcard, internal main, recovery, minimal display
    reset and wait for SD mount
    use_mpy = compiler exists AND emits mpy v6.3 AND device imports native ABI probe
    if use_mpy: compile eligible source files with -march=xtensawin
    create SD package directories
    upload launch.py
    upload core as .mpy when accepted, otherwise .py
    always upload functions/*.py as source for runtime plugin loading
    upload compact fonts; remove obsolete device font C files
    preserve existing /sd/settings.json and /sd/vars.json; initialize only if absent
    when .mpy: import main once to prove app import
    compare SHA-256 of every newly uploaded runtime asset, host vs device
    reset only when -Reset was supplied
```

ABI 探针是带 `@micropython.viper` 的 `_identity(41)+1 == 42` 小模块。它能排除“mpy-cross 版本看似
正确但设备 ABI/原生 emitter 不兼容”的风险；探针失败不会中断部署，而是安全回退 `.py`。可变用户
状态被保留也不重复校验，因为它们可能在设备上已改变。

`check.ps1` 强制使用仓库 MicroPython `v1.29.0-preview` 的 `mpy-cross`，依序生成字体、运行 pytest、
CPython `compileall`、对所有源码使用 `-march=xtensawin` 编译 `.mpy`。它在兼容性或语法错误时立刻
失败。

## 10. 已合并的性能调优记录（2026-07-23）

本节取代原性能调优文档，保留其背景、测量定义、结论、数据、复现方法和后续边界。

### 10.1 背景、测量边界和初始定位

问题表现为从主菜单进入子页时，左右滑动开始前有明显停顿和掉帧。审查范围是 `v1.1.3...HEAD`
中的字体加载、部署格式、页面转场、函数面板、绘图缓存、延迟持久化及实机基准。

“导航首帧”定义为合成测试从 `Nav.go_to()` 调用到第一次 `Nav.draw_transition()` 完成呈现的耗时。
它包括目标页激活和转场层捕获，不包括解释器上电、物理键盘扫描和主循环分派，所以它是固件内部
导航路径指标，不能称作完整硬件冷启动时间。

初始 COM5 合成测量：

| 指标 | 初始结果 | 定位结论 |
| --- | ---: | --- |
| 导航首帧 p95 | 498.059 ms | 主要卡顿发生在开始滑动前的同步工作。 |
| 转场帧 p95 | 21.788 ms | 连续帧合成本身不是首要瓶颈。 |
| FunctionPanel 捕获 | 494.061 ms | 进入面板时重新扫描并执行插件预览代码占主导。 |

解决后，插件文件列表和描述移到启动期构造。预加载后 FunctionPanel 捕获约 100.554 ms；最终热态
逐页测量中它为 84.332 ms，其他页面约 13.942 至 31.213 ms。

### 10.2 实现决策

1. `Renderer.present()` 记住 OLED RAM 当前对应的页面。导航时旧页留在面板，新页画到已有主缓冲；
   不再分配 outgoing/incoming 双层，也不在每个动画帧重绘页面。
2. 512 字节条带由 `xtensawin` Viper 从主缓冲复制新增列，再用 SSD1322 地址窗口增量写入。整段动画
   合计约一屏 SPI 数据；7 KiB 门禁、捕获异常回退和串行内存页仍保证压力下安全直切。
3. FunctionPanel 在构造期加载插件目录/描述，`Shift+ENT` 才显式重扫。这保留运行中换卡的能力，
   同时不让任意插件源码回到普通转场路径。
4. 字体从设备运行期解析 C 源改为构建期 `.xglcd`；部署以 ABI probe 决定 `.mpy` 或 `.py`，每个
   上传资产都验证 SHA-256。
5. Plot 以四项 LRU 复用已编译表达式，并以注册表 revision 失效；平移缩放不重复编译。
6. `DeferredStorage` 将 JSON 写出按键关键路径，仍保留 `.tmp -> primary`、`.bak` 和损坏主文件
   的恢复规则。

审查还发现两项必须收口的问题：启动期插件缓存会过期，故添加显式重扫；原始 128 条帧样本会在
50 次导航中淘汰早期慢帧，故改为固定内存直方图并完整计入所有帧。

### 10.3 最终 COM5 验证

2026-07-23，在已部署 COM5 上对最终 512 字节条带版本做 200 次页面往返，并另做每页 10 次
往返的细分监控：

| 指标 | 结果 |
| --- | ---: |
| 聚合导航首帧 p95 / 最大值 | 275.849 ms / 275.849 ms |
| 聚合帧 p95 / 最大值（含直切） | 148.466 ms / 148.466 ms |
| 动画帧平均 / 最大值 | 5.8 ms / 12.304 ms |
| 直切帧平均 / 最大值 | 78.750–123.640 ms / 166.024 ms |
| GC p95 / 最大值 | 22.913 ms / 22.913 ms |
| 200 次往返 GC 后可用堆变化 | -256 字节 |
| 细分监控最低空闲堆 / 内存错误 | 4,640 字节 / 0 |
| 部署运行时资产 SHA-256 | 57 / 57 一致 |
| 运行时诊断 | `SELFTEST PASS failures=0` |

上表是引入页面级 SWAP 前的2026-07-23实机基线；其中148.466 ms来自当时 FunctionPanel 等
内存密集页面的直切，不代表当前实现。当前版本已用默认页揭示和低内存淡入淡出替代所有正常
导航直切，部署后应重新运行逐页监控更新本表。此前动画路径最慢帧仍低于16.7 ms，设备接受
ABI探针并能导入 `main.mpy`；基准与诊断后执行过设备复位，恢复正常应用。

复现命令：

```powershell
.\check.ps1
.\deploy.ps1 -Port COM5 -Reset
..\.venv\python.exe -m mpremote connect COM5 exec `
  "import sys; sys.path.insert(0,'/sd'); import benchmarks; benchmarks.run(cycles=200)"
..\.venv\python.exe -m mpremote connect COM5 run tools\device_runtime_monitor.py
..\.venv\python.exe -m mpremote connect COM5 exec `
  "import sys; sys.path.insert(0,'/sd'); import diagnostics; diagnostics.run()"
..\.venv\python.exe -m mpremote connect COM5 reset
```

### 10.4 仍然成立的性能边界

- 页面揭示帧已进入 16.7 ms 预算；后续优化应看逐页 animated/direct 拆分，不能用包含直切页的
  聚合最高桶判断动画速度。
- FunctionPanel 热态捕获仍高于其他页，因为它绘制多行较长的插件标签；插件源码执行已移出普通
  导航路径。
- 该基准是只读合成导航。真实物理按键端到端时延还包含扫描和主循环，应以串口诊断或额外仪表
  单独测量。

## 11. 验证范围与维护准则

测试按行为域覆盖：启动共享 SPI2、SD 读写拒绝/超时、存储备份与失败重试、键盘边沿和长按、
计算优先级/赋值/插件、页面导航/动画/侧栏、绘图缓存与渐近线、函数面板预加载和重扫、亮度、
字体资产、部署 ABI/SHA-256、诊断和性能直方图。`pytest.ini` 将 `source` 加入导入路径。

修改时应保持以下不变量：

1. 主循环是唯一事件消费者；页面返回结果而不是自行操纵 `Nav`。
2. 转场开始后至触发键释放前不允许业务输入；转场结束帧必须是目标页 canonical 默认空布局，
   实时数据只能在其后的安静循环中渐进加入。
3. 插件必须隔离加载，函数重载必须原地替换 live registry；插件执行不可落入普通转场路径。
4. 写设置/变量必须使用原子提交和空闲期 `DeferredStorage`；失败不得清空内存状态。
5. OLED 与 SD 使用同一 SPI2 但不同 CS；部署前复位释放旧 SPI 状态。
6. 性能数据必须区分内部合成导航与真实端到端输入，不得将导航首帧冒充为冷启动。

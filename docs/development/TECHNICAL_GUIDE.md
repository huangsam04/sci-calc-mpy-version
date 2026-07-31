# SCI-CALC MicroPython 技术说明

本文是 `mp_version` 1.6.1 的实现说明和维护入口。它以源码当前行为为准，使用伪代码解释
从 ESP32 上电到应用、输入、计算、显示、持久化、部署与诊断的完整逻辑。设备使用仓库
MicroPython 1.29.0-preview 的自定义 frozen 固件；动态 Add-ons 和用户数据仍留在 SD 卡。

## 1. 系统边界与文件布局

目标硬件为 ESP32-WROOM-32E、SSD1322 256x64 4 位灰阶 OLED、5x6 矩阵键盘和 FAT32
SD 卡。内部 Flash 保存可恢复启动链和稳定 frozen 核心；SD 卡保存槽身份、动态运行边界、
Add-ons、设置、变量及其他用户文件。

```text
内部 Flash                                      SD 卡 /sd
-----------                                      ---------
boot.py       挂载 SD                            settings.json / vars.json（可变状态）
sdcard.py     SPI block-device 驱动               .slots/A 或 B/
main.py       槽选择与恢复入口                      release.manifest / .sci-calc-owner
bootenv.py / bootsel.py / bootlog.py               launch.py -> main.mpy
bootsupervisor.py                                  fonts/*.xglcd
recovery.py / display/                            Add-ons 和其他未知用户文件
.frozen（稳定应用核心及 approot/performance/
runtime_handle/version）
                                                .staging/（未激活候选）
```

源码目录的职责如下：

| 区域 | 责任 |
| --- | --- |
| `source/main.py` | 应用构造、事件循环、导航状态机、崩溃恢复。 |
| `source/calc/` | 高精度十进制数、函数注册表、依赖感知插件加载、Pratt 表达式解析与求值。 |
| `source/screens/` | 各业务页面的状态、绘制和按键处理。 |
| `source/ui/` | 通用控件、固定行损伤、菜单短距离动效、帧调度和状态栏。 |
| `source/display/` | SSD1322 帧缓冲/SPI 驱动、单色调色板、X-GLCD 字体读取。 |
| `source/input/keyboard.py` | 键盘矩阵扫描、去抖、边沿事件与长按。 |
| `source/utils/` | 崩溃可恢复 JSON 存储和 OLED 空闲休眠。 |
| `source/boot.py`、`internal_main.py`、`bootenv.py`、`bootsel.py`、`bootlog.py`、`bootsupervisor.py`、`recovery.py`、`sdcard.py` | 内部 Flash 的挂载、A/B 选择、启动日志和失败恢复。 |
| `source/diagnostics.py`、`benchmarks.py`、`performance.py` | 串口自检、可重复的导航基准和有界统计。 |
| `tools/`、`check.ps1` | 主机端字体构建、发布清单、A/B 部署、设备验收与持续检查。 |

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

MicroPython 的启动顺序是 `_boot.py -> /boot.py -> /main.py`。内部 `boot.py` 只建立 SD 文件系统，
不会把 `/sd` 放到全局 `sys.path`；这样根目录中的旧应用文件不能遮蔽受信任的内部启动模块。

```text
on boot.py:
    try:
        spi2 = SPI(2, pins=18/23/19, 10 MHz)
        card = SDCard(spi2, CS=4)
        mount(card, "/sd")
    except error:
        print("SD mount failed", error)

on internal /main.py:
    try:
        sys.path = ["/lib", "/"]
        selector = read_redundant_selector_records()
        choose unconsumed trial, otherwise confirmed A/B slot
        write boot evidence; consume a one-shot trial before execution
        require selected slot release.manifest
        sys.path = [".frozen", selected_slot, "/lib"]
        purge cached application modules
        release boot supervisor modules; gc.collect()
        execfile(selected_slot + "/launch.py")
    except app_error:
        sys.path = ["/lib", "/"]
        purge cached slot modules; gc.collect()
        show_recovery(app_error)
```

恢复界面只初始化 OLED 所需的 SPI、CS、DC、RST，显示“CHECK SD CARD”、截断至 28 字符的
错误信息和“Fix card, then RESET”。这样即使 SD 上的主应用、字体或 Python 模块损坏，设备
在堆足够时仍有可见的故障出口。若页面启动已经耗尽到无法再申请恢复界面的 8192 B framebuffer，
`main` 会先发送 OLED 硬件休眠命令再原样上抛 `MemoryError`；此时串口错误是权威出口，避免启动画面
长时间停留造成烧屏风险。

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

    font_main = None; font_small = None             # 常驻路径固定使用内置 8x8 字体
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

    preload frozen screen namespaces; collect boot-only imports
    construct DeferredStorage, Nav and main menu only
    configure Nav with shared state and fixed page ids
    mark("screen_imports")
    bind resident runtime; mark("ui_ready")
    submit first main-menu frame
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

### 4.1 UIElement、FrameScheduler 与固定标量动效

`UIElement` 为普通页面/控件提供默认生命周期；Calculator 和 Function Panel 等紧凑页面直接实现
同一 `activate()`、`deactivate()`、`draw(display)` 和 `update(keyboard, event)` 协议而不继承它。
`FrameScheduler` 记录输入、普通脏帧、秒表连续帧、侧栏轮询和安静
工作期限；普通脏帧间隔为 66 ms，循环固定休眠 4 ms。

`Menu` 在现有 `_state` 中使用起点、目标、开始时间和活动标量，按 96 ms 整数 ease-out 推进；
输入首帧先移动 2 px，滚屏直接吸附，反向输入从当前像素重新定向。`Nav` 的三个固定标量保存
页面方向、开始 ticks 和已呈现位移，按 210 ms 整数 quadratic ease-out 推进。没有 `MotionMenu`、
通用插值框架、亮度淡变或像素合成动画；按键和逐帧路径不调用 `gc.collect()` 或 `gc.mem_free()`。
`InputBox` 用三个打包标量完成 96 ms 光标/历史选择运动，并在 Calculator 安静期按 500 ms 相位
闪烁；Function Picker、Variable Panel 与 Stopwatch 圈速的活动选择/翻页再复用两个打包标量。
Stopwatch 活动路径约 48 B，最坏的 Calculator + Picker 路径包含 Menu、Nav 和 Display 后为
15 个动画标量、约 60 B。

### 4.2 `Nav` 栈、同步页面切换与输入恢复

`Nav` 独占页面栈、固定 `page_id` 和可重建资源生命周期；普通调用方只使用 `open(page_id)`、
`back()` 与 `current`。启动只构造根菜单，进入页面时才创建实例；离页会分离需要保留的紧凑状态、
清除页面引用，并把 GC 安排到安静回收点。真实输入完成旧页停用、释放和新页激活后，进入子级
向左滑动，返回上级向右滑动；程序化导航保持同步。新输入中断、异常、复位和休眠均结束裁剪状态，
重画当前逻辑页并吸附正确终态。

崩溃恢复中的 `reset(root)` 会释放可重建资源、清空导航栈到根页并锁住输入，直到所有物理按键
释放。页面激活或回滚发生 `MemoryError` 时保留原异常优先级，不用视觉效果延迟恢复。

### 4.3 Renderer、DamageMap 与单 framebuffer

`Display` 只定义一个 256 x 64 x 4-bit 的 8192 B framebuffer；页面不拥有条带、页面副本或
像素合成缓冲。`Renderer` 内部复用容量为 2 的 `DamageMap`，并按当前页面身份缓存
class-level 绘制 hook，避免秒表和菜单热路径反复创建 bound method。

```text
Renderer.present(screen):
    damage.clear()
    screen.collect_present_damage(display.height, damage)
    if no damage: return false
    if full or sidebar dirty:
        clear content/full buffer; screen.draw(display); display.present()
    else:
        screen.draw_present_rows(display)
        display.present_rows(damage.ranges)
    mark screen presented; return true
```

`DamageMap.add()` 原地合并相交行带；超过两个独立行带时提升为全帧，不扩容。SSD1322 在启动时
预建有限的菜单跨度视图，供滑动高亮只重画旧行、新行和实际经过行；当前 4 行菜单从清空 9568
像素、重画 4 个标签，降到清空受影响行并只重画相交标签。
`Display.present_rows()` 对其他不认识的合法行区仍直接退化为全帧传输，避免逐帧切片分配。

页面滑动只移动 `CONTENT_W=210` 的 GS4 字节，右侧 Sidebar 不动。`Display` 的 x 平移、左裁剪和
右裁剪三个标量把目标页限制到新暴露条带；`Renderer.present_slide()` 每帧原地移动旧像素、绘制
条带并使用既有全帧提交。全系统仍只有一个 8192 B framebuffer，新增像素缓冲为 0 B；Menu 4、
Nav 3、Display 3、InputBox 3 和当前自绘列表 2 个动效标量合计约 60 B。

侧栏每 5 s 才在安静调度槽检查刷新；输入导致 DEG/RAD 改变时只标记侧栏脏并复用缓存电池样本，
ADC 读取不进入输入帧。电压文本和固定标签使用预分配字节缓冲或紧凑字体直绘。

### 4.4 主循环、渲染门控和崩溃恢复

主循环通过 `_drain_input_batch()` 每轮最多处理 3 个已捕获边沿，并且只有 `Nav.poll_event()`
调用 `pop_key_event()`。先处理输入，再执行页面安静期 `settle_step()`，最后由 `FrameScheduler` 决定是否存在
真实像素提交；无损伤返回不会计作帧。

```text
forever:
    try:
        kb.scan()
        now = ticks_ms()
        update OLED sleep state; sleeping path scans every 25 ms
        drain up to 3 edges through _handle_event(); keep the rest queued
        if no edge, process one supported held-key update
        scheduler.note_input(now) for any physical activity
        scheduler.request_render() only for visible state changes

        quiet = no input/hold/pending/pressed key
        if sidebar deadline and quiet: poll cached chrome
        choose 50 ms stopwatch or 66 ms ordinary deadline
        if scheduler.should_present(...):
            if nav.present_current(now): mark physical present
            else: clear phantom render request
            kb.scan() immediately after OLED transfer

        settle_current() only while quiet
        after 750 ms quiet: perform one reload/scan/reclaim/deferred write step
        every 256 frames in that quiet seam: sample heap and optionally collect
        sleep(4 ms)
    except MemoryError:
        cancel pending plug-in work; release rebuildable resources; reset root
    except ordinary error:
        reset OLED power state; show crash page
        wait acknowledgement and complete release; reset root
```

输入造成的可见变化绕过普通 66 ms 限速。侧栏轮询、GC、插件执行和持久化都要求安静期；
有输入、按键仍按住或页面仍需稳定时不会进入这些分支。诊断模式每 5 秒输出平均渲染/传输耗时和空闲堆。
Calculator 输入帧先提交 12 行编辑区，右下角 `n/96` 计数在紧随其后的 quiet settle 帧提交 10 行；
这复用 presented-mode 标量中的两个状态位，不增加 render 表槽或堆对象，并把输入提交稳定保持在
20 ms 内。

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
对象引用并合并同类请求，编码和写入都发生在无输入的空闲阶段。失败时保留请求，约2秒后重试，
并调用回调更新 UI 的“Not saved - check SD”。

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

插件为活动槽 `functions/*.py` 中不以 `_` 开头的文件，每个必须定义 `register(registry)`。可选的
`DEPENDENCIES = ("other", ...)` 声明其他 Add-in；可选 `EXPORTS` 字典
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
只注入已声明并成功装载的 `EXPORTS`，Add-in 应在两个阶段都遵循其显式依赖。函数面板直接复用启动时已构建的函数和依赖快照，不再为历史调用方重新执行 Add-in。
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
`ESC` 返回 `BACK`。光标直接吸附到目标行，标签在加入菜单时只截断一次。`InputBox` 保存文本、
插入点和可配置行数的视图；它按比例字体的实际像素宽度切分文本，并用同一测量结果定位光标。
紧凑覆盖层保留单行、42 字符默认值；计算器传入最多双行和 96 字符上限，短公式仍只占一行。`DEL` 删除插入点之前字符，
按住超过 750 ms 后每 100 ms 重复；函数键自动插入如 `sin(`，快捷键则交还页面。

`ErrorPopup` 将内部错误映射到可行动文字（除零、未知变量、定义域、溢出、括号、参数不足、
函数关闭、求解不收敛等），显示表达式和源位置 `^`，任意按键或 10 秒到期后关闭。到期检查放在
`draw()` 和相关 `update()` 中，因此即使没有新输入也会消失。

`InputBox` 的现有 Cursor 还承担 Calculator 历史高亮；两者使用同一组三个打包标量做 96 ms
quadratic ease-out。输入态光标每 500 ms 闪烁，历史态则保持可见；离页、窗口变化或新目标无法
安全插值时直接吸附终态。

### 7.2 主菜单与计算器

主菜单有 Calculator、Plot、Function Panel、Stopwatch、Settings 五项；返回根页面保留原选择。
菜单项只保存固定 `page_id`，`Nav.open(page_id)` 在首次和再次进入时按需导入、构造并激活页面；
`Nav.back()` 停用离页对象、保存必要的紧凑状态并清除页面引用。普通产品接口只依赖 `open()`、
`back()` 和 `current`，不保留页面注册表或验收专用租约。Calculator 和 Function Panel 是紧凑页面
对象而非 `UIElement` 子类，因此入口不依赖继承判断，也不会误走全局快捷键分支。

计算器有三种模式：输入、历史导航、错误弹窗。成功求值将 `(expr, result)` 插到最多 20 条历史
的头部，清空输入；每个 `Number` 统一格式化为 `x.xxxx*10^x`。显示位数来自
`settings.display_digits`，只作用于渲染和从历史插入的表达式文本，不改变历史中保留的数值对象。

计算页将高度优先留给编辑：顶部 `InputBox` 默认是 12 px 的单行表达式区，首行放不下时扩展为
22 px 的双行区。最新历史使用 18 px：表达式在上行左对齐，结果在下行按完整 202 px 文字宽度
右对齐；单行输入时其下显示两条 9 px 紧凑旧历史，双行输入时显示一条。页脚始终保留状态和长度
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

函数面板首次打开时读取已由启动注册表保留的插件文件列表、注册摘要和依赖元数据；普通 `activate()`
只重建菜单，不会执行 SD 插件源码。它列出四个内置组以及 `Add-on:` 项，使用不同 ID（如
`basic` 与 `plugin:basic`），防止同名冲突。若已启用的 Add-in 缺少已知依赖，激活时递归加入
依赖闭包、标为待保存，并在页脚显示 `Auto on: name`；用户关闭一个依赖时，已启用的依赖方也会
关闭。`ENT` 翻转当前会话选择，离开时把 `enabled_functions` 排入异步设置保存，主循环随后原地
重载 registry。重载开始前 Function Panel 已显示固定 `Loading add-ons` 进度条，完成后才返回根页；
动态 `execfile()` 保持不可切分，但其最长真机 step 有明确可见反馈。加载失败插件会自动聚焦，用户可关闭它。
四个内置标签使用固定摘要 `Basic (+ - * / ...)`、`Trig (sin cos tan)`、
`Science (sqrt ln exp)` 和 `Lists (max min ...)`；动态 Add-on 标签保持原样。

`Shift+ENT` 是唯一主动重扫路径：重新执行隔离摘要、保持可用的原选择并夹住光标。这样运行中
更换活动槽内容可见，同时不会将任意插件代码带回普通页面切换关键路径。

函数选择器在激活时对 live registry 名称排序，使用四行两列（每页八项）。上下移动一项，
物理 4/6 在列间跳四项，`ENT` 插入选择：prefix/list 插入 `name(`，其他插入符号或名称；`ESC`
也退出而不插入。相同按键 150 ms 冷却。同页四向选择使用 96 ms quadratic ease-out；跨页使用
160 ms 水平 ease-out，并复用 Display 的内容区平移/裁剪。固定缓存保存旧页和新页各八个可见标签
及页脚页码，动画帧只消费缓存且不会创建标签、切片或像素副本。

字母面板是 Shift+RPN 覆盖层，原始键位映射 A-Z、`"` 和 `;`；自身 Shift 键切换大小写，
`Bk` 编辑暂存文本，`OK` 才把暂存文本插入目标 `InputBox`，`ESC` 取消。它限制暂存长度不超过
目标输入的剩余字符数，并对同键 100 ms 去重。

变量面板在激活时对变量名排序，采用与函数选择器相同的两列分页。`ENT` 插入变量名，`DEL`
调用 `EvalContext.delete_var()` 并重建列表，`ESC` 返回；同键 150 ms 冷却，物理 4/6 切列。
同页选择使用 96 ms ease-out；当前八个格式化标签和短页脚使用固定缓存，在变量变化或换页时重建，
逐帧路径不再格式化值。

### 7.4 绘图

Plot 页持有独立输入框、x 范围 `[-10,10]`、y 范围、仅包含 `x` 的临时变量字典和临时
`EvalContext`，因此绘图不写入计算器变量。查看模式下：8/2 按 0.5/2 缩放 y，Shift+8/2 缩放 x，
物理 4/6 向左/右平移当前 x 范围 25%，ENT 打开编辑，RPN 预填 `x`。编辑框固定显示在顶部 14 px；
ENT 提交，ESC 恢复编辑前表达式，Shift+Tab 重置 x 范围。

Plot 只保留当前表达式的一份已编译程序及其注册表 `revision`；表达式或 revision 变化时先释放旧
引用，再在安静调度槽编译，因此不会保留多项 AST。启动期预建的 `_CurveJob` 原地重置，进度复用
曲线揭示宽度标量的负值编码，不创建进度对象或像素缓冲。

```text
settle_curve(auto_scale=true):
    program = current program or compile_expression(expr, live registry)
    if auto_scale, for every second horizontal sample pixel:
        x = x_min + normalized_pixel * (x_max - x_min)
        y = float(evaluate_program(program, temporary x context))
        accept only finite-ish |y| < 1e6; retain at most 12 robust samples
    if no valid sample: clear curve; show popup; return
    if auto_scale:
        full_range = min..max
        central_range = trimmed bounded robust samples
        if full_range > 4 * robust_range: use central range  # 抑制极点
        add 10% padding (at least 0.5; constant curve gets 1)
    clear the fixed 104-byte Plot workspace in a bounded slice
    evaluate every second x sample again without retaining a float array:
        store one y byte per sampled column, or 255 for an invalid point
    update Plotting progress only when an integer bar column advances
    at completion, submit the target curve once
```

提交表达式的首帧先画 `Plotting` 和空进度条。后续切片把自动缩放采样、工作区清零和栅格采样的
已完成量映射到条内宽度，只通过 `DamageMap` 更新进度条所在 7 行；完成时才把 y 样本直接重建到
唯一 GS4 framebuffer，并重画边框、坐标轴和原点十字。新输入、离页、异常或 OOM 会取消工作并
恢复普通终态，按键路径不调用 GC。

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

`sleep()`/`wake()` 仅发送 `AE`/`AF`。`write_cmd()` 对 0、1、2 参数复用固定 bytearray；
亮度设置通过同一 1 字节参数缓冲发送 master-current 命令。`present_rows()` 复用启动时预建的
热区视图。`MonoPalette` 用两个像素编码背景/前景灰度，供
`FrameBuffer.blit` 把单色字形和曲线映射到 GS4。

### 8.2 紧凑字体

`tools/build_fonts.py` 从 C 源逐行提取 96 个 ASCII 字形，校验每个字形正好为
`((height-1)//8+1)*width+1` 字节，然后写入：

```text
"XGF1" + [width, height, start_character=32, count=96] + raw glyph bytes
```

`XglcdFont` 读取时优先验证该二进制头；为兼容旧资产，头不匹配时也能以 bytes 方式解析 C 源，
不依赖 UTF-8 注释。字形和整串文本共用上限 256 的缓存；字符串常走一次预渲染/一次 blit，实时
秒表/输入用 `raw=True` 逐字绘制，避免让缓存被不断变化字符串占满。分配失败时清缓存、GC、重试，
仍失败则返回 0 宽占位，不让 UI 崩溃。固定页壳、菜单、底栏与状态侧栏绕过该缓存，直接从紧凑
字体字节写入主帧缓冲。当前受限设备的正式启动不会构造 `XglcdFont`，所有常驻页面接收 `None`
字体并走内置 8x8 路径；字体解析器和资产只保留为兼容能力，不占 resident graph。

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
..\.venv\python.exe -m mpremote connect PORTNAME exec `
  "import diagnostics; diagnostics.run()"
..\.venv\python.exe -m mpremote connect PORTNAME reset
```

`mpremote exec` 会进入 raw REPL 并中断正常应用，故 `reset` 是命令的一部分。将 settings 的
`diagnostics` 设为 true 后，正常应用还输出逐键 `INPUT`、页面结果 `ACTION` 和每五秒 `PERF`。

### 9.2 性能基准与统计

`benchmarks.run()` 在没有既有 runtime 时以与 `main` 一致的构造顺序创建独立 UI，但不进入无限
键盘循环。它先对每个目标页往返预热，随后 GC、记录堆、循环选择页面 `go_to/go_back`，完成同步
切换与呈现，最后再次 GC 并打印启动阶段、导航首帧、帧、GC 与堆变化。该流程不会写用户设置/变量。

`PerformanceMetrics` 的输入到呈现和 GC 样本各使用 16 个固定循环槽；帧样本不同，使用 128 个 0.5 ms
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

正式入口是：

```powershell
..\.venv\python.exe .\tools\release_deploy.py --port PORTNAME --mode mpy
```

它先在主机生成 source/MPY 两份确定性发布计划，选择指定模式，并在任何设备接触前校验清单、
字体输出、编译产物、路径边界和每项 SHA-256。默认路径面向已 provision 的开发板，在稳定的
confirmed 槽中做一次单会话增量同步：

```text
release_deploy(port, mode):
    prepare and validate immutable release plan
    require one stable confirmed selector and trusted manifest/owner
    upload only new or SHA-changed managed files into that slot
    remove only obsolete files owned by the prior manifest
    commit manifest, owner and selector last; reset once and return
    seed /sd/settings.json and /sd/vars.json only when absent
```

默认路径不创建备用槽，也不在复位后重新连接执行 resident smoke。`/sd/settings.json`、
`/sd/vars.json`、`/sd/Add-ons` 和槽内未知文件不覆盖、不删除；新 managed 路径与未知文件同名时
会在任何写入前拒绝。需要完整 A/B、逐项校验和冷启动
resident smoke 时增加 `--transactional`；同一选项也负责首次安装或修复 bootstrap。MPY 模式固定
使用仓库 `v1.29.0-preview` 的
`mpy-cross -march=xtensawin -X no-source-lines`。COM5 实测从原完整流程 `374 s`、精简后的
完整 A/B `65.890 s`，降至默认单会话增量 `17.352 s`；当前候选含编译与同步为 `31.355 s`。

`check.ps1` 强制使用仓库 MicroPython `v1.29.0-preview` 的 `mpy-cross`，依序生成字体、运行 pytest、
CPython `compileall`、对所有源码使用 `-march=xtensawin` 编译 `.mpy`。它在兼容性或语法错误时立刻
失败。

## 10. 当前内存与动效结论

### 10.1 保留的内存与逻辑优化

当前实现只保留真机测量能证明收益的改动：

1. 显示始终只有一个 8192 B GS4 framebuffer；Plot 按需借用一个 104 B 工作区，并复用页面内
   `_CurveJob`，绘图切片不再反复申请临时工作对象。
2. `Nav` 在打开页面时按需构造实例，离页时通过 `detach_state()` 保存必要的紧凑用户状态，并经
   `release_memory()` 释放可重建资源；FunctionPicker 的名称表原位重建，不复制排序列表。
3. 函数重载复用 live registry，固定 bundled 插件不再导入通用源码 loader；loader 临时模块在
   冷操作后移除，插件清理使用 `popitem()`，不创建键列表副本。
4. Calculator、Function Picker、Variable Panel 和 Stopwatch 在激活、输入或换页边界重建固定大小的可见标签、
   历史和页脚缓存；动画帧只读取这些缓存，不再格式化或截断字符串。
5. 验收侧缓存同一 application matrix 内不变的 framebuffer 身份快照；该缓存不进入普通 release。
   没有增加像素缓冲、通用动画层、`LazyScreen`、SWAP 或第二 framebuffer。

1.6.1 的最终 `check.ps1` 为 `1142 passed in 23.25s`；CPython compileall 和
MicroPython 1.29 全源 mpy-cross 均通过。

### 10.2 COM5 严格门禁（1.6.1）

当前 MPY release 为 `9cebfe0e3d96b147e394ebf36ee8c0a2c2bdbba4edae17db847beb6ec43b569e`，
manifest SHA-256 为 `49ed5486a49b6d0d2fc41bd1009cfd4d6f0c8dcc6b0acf8bb22b830864c8d9ae`。
正式 frozen 镜像为 `1832560 B`，SHA-256 为 `ea9f46c2f1037ffe5ba02c215dca99c7ece1acf061dd931b896dc87795c544ed`；
增量构建用时 `29.126 s`，只写 factory 分区 `0x10000` 并校验用时 `24.635 s`。本轮不创建
`v1.6.1` tag 或 GitHub Release 附件。统一入口仍为：

```powershell
.\tools\run_device_acceptance.ps1 -Port PORTNAME
```

下列行来自同一 1.6.1 候选的最终五阶段统一验收；定向交互 profile 另行覆盖本轮新增路径：

| 检查 | 真机结果 |
| --- | --- |
| 启动与固定缓冲 | resident/root ready；同一个 framebuffer 8192 B；Plot 工作区 104 B；MPY/Viper ABI 通过 |
| 模块来源 | `main=/sd/.slots/B/main.mpy`；`performance`、`runtime_handle`、`version` 为 frozen；`approot` 在根页阶段尚未加载 |
| 应用矩阵 | 35 场景/五轮；最低空闲堆 10832 B，高于 8 KiB 2640 B；漂移 +1552 B；`MemoryError=0`、普通错误 0；加载条覆盖的插件重载 142.466 ms |
| 页面 tracer | 预热后五轮；最低空闲堆 54080 B；漂移 -80 B；普通最大 step 24.066 ms |
| 交互与动画 | 完整输入 `12345`；输入提交最大 18.916 ms；80 个动画帧最大 17.959 ms；菜单/前进/返回净分配均 0 B；交互阶段堆漂移 -16 B |
| v1.6.1 定向交互 | Calculator 历史/光标/闪烁、RPN 往返、Picker 四向/翻页、Variable Panel 与 Stopwatch 均通过；71 帧最大 19.561 ms，最大 step 23.183 ms，Variable Panel 最大帧 14.151 ms，Stopwatch 4 帧最大 19.872 ms，动画分配 0 B，漂移 -368 B |
| 固定帧 | Stopwatch 16 帧全部提交，净分配 0 B |
| 结果 | `ACCEPTANCE_COMPLETE`；最低堆高于 8 KiB，普通 step/动画帧严格 `<40 ms`，输入严格 `<20 ms` |
| 收尾 | 临时 support/stage 载荷已删除；SSD1322 已发送硬件休眠命令 |

普通交互数字从已捕获边沿开始并包含页面更新和 OLED 提交；页面导航的提交在建立动画状态时结束，
第一帧 OLED 提交计入动画时延和分配。矩阵扫描与去抖合同单独报告，不能把它描述为物理按键闭合
到像素的完整端到端时延。

### 10.3 已测热点与淘汰候选

固定宽度历史表达式格式化曾把一次性微基准峰值从 `4352 B` 降到 `1472 B`，但应用矩阵最低堆仍为
`9904 B`，因此实现、专属测试和生成 MPY 均已删除。缓存验收 `buffer_snapshot()` 的不变结果后，
历史五轮通过，完成场景数从 5 增至 10，最低堆升至 10352 B；当时的下一热点稳定落在真实
`error_lifecycle` 安静回收。

自动 GC 阈值 `4096 B` 和 `20000 B` 都把最低堆提高到 `15200 B`，但最大 step 分别为
`41.810 ms` 和 `40.875 ms`，均未严格小于 40 ms；两项实验均已删除。没有继续扩张到计算核心，也没有
为比较保留第二套实现。

v1.6.1 最终 Stopwatch 候选的统一验收两次在 100 次连续错误压力中把自动 GC 合并进随机的
`ErrorPopup` show step，得到 `40.096--40.262 ms`。复原真实常驻差额的定向红灯稳定为
`42.263/44.764 ms`；验收专用 controller 改在每次 dismiss 状态证明之后回收，使同一红灯连续降到
`28.785/29.027 ms`，最终完整矩阵通过。该 controller 不进入普通 release，产品按键和逐帧路径
仍不调用 `gc.collect()` 或 `gc.mem_free()`。

随后冻结 `performance`、`runtime_handle`、`version` 和 `approot`，把正式候选最低堆提高到
`15920 B`；冻结同名 `main.py` 会抢在内部 supervisor 前执行，故该单项已删除并保留 slot
`main.mpy`。Calculator flat 历史和直接 Pratt 求值候选在 `error_lifecycle` 仍同时违反 12 KiB 和
严格 `<40 ms` 门槛，已连同专属测试删除；
Stopwatch 压缩无法影响更早的 `calculator_history` 门禁，按 YAGNI 未实现。

### 10.4 动效门禁

菜单、页面横向滑动、InputBox 光标和当前自绘列表合计使用最多 15 个标量槽（约 60 B）和 0 B 新像素
缓冲。Calculator、Function Picker、Variable Panel、Stopwatch 和固定 footer 的可见文本都在激活、输入或换页
边界预先装入固定缓存，逐帧只读取缓存。Renderer 在
动画启动前解析目标页 hooks，每一帧都把目标页严格裁剪到当前新暴露条带，包含首个 2 px 边缘。
1.6.1 COM5 最终 `heap_min=10832 B`，高于 8192 B 硬底线 2640 B；定向 71 帧、统一交互 80 帧
和 Stopwatch 16 帧的净分配均为 0 B。8 KiB 是启用底线，
12 KiB 仍是优化目标；不得为追求余量删除已经满足错误、漂移和时延门槛的动画。

## 11. 验证范围与维护准则

测试按行为域覆盖：启动共享 SPI2、SD 读写拒绝/超时、存储备份与失败重试、键盘边沿和长按、
计算优先级/赋值/插件、页面导航/行损伤/侧栏、绘图缓存与渐近线、函数面板预加载和重扫、亮度、
字体资产、部署 ABI/SHA-256、诊断和性能直方图。`pytest.ini` 将 `source` 加入导入路径。

修改时应保持以下不变量：

1. 主循环是唯一事件消费者；页面返回结果而不是自行操纵 `Nav`。
2. `Nav.poll_event()` 是唯一边沿出口；同步页面切换完成后才处理下一事件。触发键长按不得重复导航，
   崩溃重置后必须等所有物理键释放再解锁输入。
3. 插件必须隔离加载，函数重载必须原地替换 live registry；插件执行不可落入普通页面切换路径。
4. 写设置/变量必须使用原子提交和空闲期 `DeferredStorage`；失败不得清空内存状态。
5. OLED 与 SD 使用同一 SPI2 但不同 CS；部署前复位释放旧 SPI 状态。
6. 性能数据必须区分内部合成导航与真实端到端输入，不得将导航首帧冒充为冷启动。

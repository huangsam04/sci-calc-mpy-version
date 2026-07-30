# SCI-CALC MicroPython Edition

SCI-CALC 的 MicroPython 固件，目标硬件为 ESP32-WROOM-32E、SSD1322 256×64 灰阶 OLED、5×6 矩阵键盘和 FAT32 SD 卡。

当前应用版本为 **1.4.0**。源码编译基线是本仓库 `micropython/` 中的 **MicroPython 1.29.0-preview**，并已在设备的 **MicroPython 1.28.0 (2026-04-06)** 上完成冷启动验证。项目不修改 MicroPython 核心。

只想日常使用计算器时，请先看 [简明使用说明](USER_GUIDE.md)。本文保留部署、插件和维护细节。

## 目录与启动方式

设备采用“内部启动监督器 + SD A/B 应用槽”布局：

```text
内部 Flash
├── boot.py             # 挂载 SD，随后立即退出
├── sdcard.py           # 官方 Python SPI SD 驱动
├── main.py             # 读取选择记录并启动活动槽
├── bootenv.py / bootsel.py / bootlog.py / bootsupervisor.py
├── recovery.py         # SD/应用损坏时的恢复界面
└── display/            # 恢复界面所需的最小显示驱动

SD 卡 /sd
├── settings.json
├── vars.json
├── .slots/A/ 或 B/
│   ├── release.manifest / .sci-calc-owner
│   ├── launch.py / main.mpy
│   ├── calc/ display/ input/ screens/ ui/ utils/
│   ├── fonts/*.xglcd   # 随发布保留；当前常驻 UI 使用内置 8x8 字体
│   └── functions/      # 可开关的发布内插件
└── .staging/           # 发布中使用，成功后原子改名为候选槽
```

MicroPython 按 `_boot.py → /boot.py → /main.py` 启动。无 SD 卡、挂载失败或
选择记录没有可启动槽、活动槽清单缺失或槽内 `launch.py` 执行失败时，内部恢复界面会显示错误；
串口同时保留完整错误信息。启动监督器只把选中的槽根目录放在应用 `sys.path` 首位，启动后释放
自身模块，避免与常驻页面争用堆。

## 刷入解释器

参考 [官方文档[(https://micropython.org/download/ESP32_GENERIC/) 。

欲确认 `PORTNAME（COM几）`；运行以下命令确认设备：

```powershell
..\.venv\python.exe -m mpremote devs
```

## 正式部署应用

在 `mp_version` 目录执行：

```powershell
..\.venv\python.exe .\tools\release_deploy.py --port PORTNAME --mode mpy
```

该入口会：

1. 生成字体和确定性的 source/MPY 发布清单，并在接触设备前验证全部摘要；
2. 核对稳定 confirmed 槽的 selector、manifest 和 owner 标记；
3. 只上传 SHA-256 变化或新增的 managed 文件，只删除旧 manifest 明确拥有而新版本移除的文件；
4. 最后提交 manifest、owner 和 selector，复位后立即返回。

默认快速模式不创建备用槽，也不再次连接运行 resident smoke。它不会覆盖 `/sd/settings.json`、
`/sd/vars.json`、`/sd/Add-ons` 或槽内未知文件；中断时也不会把新 manifest 身份提前写入
selector。若新 managed 路径与未知文件同名，快速模式会在写入前拒绝。已 provision 的设备需要
完整 A/B 和冷启动校验时使用 `--transactional`。首次安装或
修复 bootstrap 使用：

```powershell
..\.venv\python.exe .\tools\release_deploy.py --port PORTNAME --mode mpy --transactional
```

同版本 COM5 实测：原完整流程 `374 s`，精简后的完整 A/B 为 `65.890 s`，默认单会话
增量为 `17.352 s`。当前设备只发布 `mpy`，`functions/*.py` 始终保留源码。

SD 卡和 OLED 共用 GPIO18/23 上的 SPI2，通过 CS4/CS5 分隔事务。内部
`sdcard.py` 使用官方 Python block-device 驱动；不要替换成独占 SPI host 的
`machine.SDCard(slot=2)`。

## 操作说明

### 主菜单

- `8 / 2`：上、下选择
- `ENT`：进入
- `ESC`：主菜单中无操作

### 计算器

- `ENT`：计算；`Shift+ENT` 输入赋值符号 `=`
- `Tab`：进入或退出历史记录
- `Shift+Tab`：变量表
- `RPN`：函数选择器
- `Shift+RPN`：字母面板
- `ANG`：切换 DEG/RAD 并保存
- `ESC`：先清空输入；空输入时返回。长按一秒直接返回

输入框默认只有一行，首行放不下时自动扩展为两行，最多保存 96 个字符。公式会按屏幕实际宽度自动换行，并且始终让光标所在内容保持可见；
右侧 `^` / `v` 表示上方或下方还有内容，右下角的 `n/96` 显示当前长度。按 `Shift+4` / `Shift+6`
可向左 / 向右移动光标，适合检查和修改长公式。

历史模式中，`8/2` 选择记录，`ENT` 插入结果，物理 `4/6` 插入原表达式。

支持示例：

```text
2 + 3 * 4             -> 14
2^3^2                 -> 512
-2^2                  -> -4
2^-2                  -> 0.25
x=y=2; x+y            -> 4
max(3, 5, 4)          -> 5
```

函数名和变量名区分大小写，不支持隐式乘法。

### 错误提示与日常使用

计算和绘图错误会显示简短原因与下一步操作，而不是直接暴露解析器异常。例如除零会
提示检查分母，未知变量会提示先赋值，括号错误会标出表达式中的位置。弹窗可按任意键
关闭，也会在 10 秒后自动消失。

日常使用时，历史记录适合复用上一轮计算，变量适合保存经常使用的中间量，绘图与
`solve()` 适合快速观察函数和估算方程根。变量写入 SD 失败时仍保留在当前内存中，
屏幕会显示 `Not saved - check SD`，并每两秒自动重试；此时不要立即断电，应检查 SD 卡
后等待提示消失或重新执行一次赋值。

本机定位为离线科学计算与快速函数分析工具。它不会自动补全隐式乘法（请写 `2*x`，
不要写 `2x`）。计算核心保留 30 位有效数字而非无限精度，也不应替代需要单位追踪或可审计
过程的专业计算软件。

### 高精度结果

数字字面量和变量由有限高精度十进制数执行，内部保存“有效数字 + 十进制指数”，不会把
`10^100000` 一类有限结果变为 `inf`。计算器、历史记录和变量面板统一显示为
`x.xxxx*10^x`；例如 `10^1000` 显示为 `1.0000*10^1000`。零显示为
`0.0000*10^0`。

设置页第四项 `Display digits` 控制尾数小数点后的位数，范围为 1--12，默认 4。它只改变
显示和从历史插入公式时的文本，不会降低已经保存的计算结果精度。极大的三角函数自变量或
超出实现可可靠约化范围的超越函数会给出错误提示，而不会返回 `inf` 或 `nan`。

### 绘图

- 查看模式：`8/2` 缩放 Y，Shift+`8/2` 缩放 X，物理 `4/6` 平移
- `ENT`：打开表达式编辑
- `RPN`：快速输入 `x`
- 编辑模式下 `ENT` 绘图、`ESC` 取消、`Shift+Tab` 重置 X 范围

表达式只编译一次；自动缩放和曲线绘制各自按 2 像素步长采样，避免保留整屏浮点数组。
自动缩放通常保留完整采样极值；当少量
渐近线附近的采样值远离中央 90% 数据时，改用稳健范围，避免正常曲线被压扁。
非有限值、视窗外分支和大幅跳变不会被连接成贯穿屏幕的伪曲线。
提交后会立即显示 `Plotting` 和真实进度条；自动缩放、工作区清零和曲线采样按有界切片推进，
中途按键会取消旧任务并进入对应操作。进度条直接画入唯一 framebuffer，未增加像素缓冲。

### 其他页面

- 函数面板：`ENT` 开关函数组或插件；有改动时 `ESC` 保存并返回，未改动时直接返回
- 变量表：`ENT` 插入变量，`DEL` 删除，物理 `4/6` 切换列
- 秒表：`ENT` 开始/暂停/继续，运行时 `DEL` 计圈，停止时 `DEL` 复位
- 字母面板：`Sh` 切换大小写，`OK` 写入，`ESC` 取消，`Bk` 退格
- 设置：第一项显示固件版本，第二项进入关于页面；第三项亮度使用物理 `4/6` 或
  左右方向键调节，范围为 10%–100%，每次调整 10%，立即应用并在空闲时保存。第四项
  `Display digits` 用相同按键调整科学记数法尾数位数。`ENT` 可循环各档位。

### 自动休眠

默认连续 3 分钟没有按键后向 SSD1322 发送硬件睡眠命令。休眠时仅保留低频矩阵键盘
扫描；任意键可唤醒屏幕，且唤醒键必须释放后才会重新参与页面操作。可在
`settings.json` 中设置 `sleep_timeout_s`，例如 `60` 表示一分钟，`0` 表示关闭自动休眠。
最大值为 `86400`（24 小时）。
矩阵键盘当前不能可靠唤醒 ESP32 深度睡眠，因此这里关闭的是 OLED 并让 CPU 大部分时间
处于等待状态，而不是丢失运行状态的深度睡眠；正在运行的秒表会在唤醒后按实际时间继续。

## 插件接口

插件位于活动槽的 `functions/*.py`，实现 `register(registry)`。

```python
WELCOME = "Statistics loaded"

def average(args, context):
    return sum(args) / len(args)

def modulo(left, right, context):
    return left % right

def register(registry):
    registry.list_function("avg", average, min_args=1)
    registry.infix("%", modulo, precedence=30, associativity="left")
```

注册方法：

- `registry.infix(name, callback, precedence, associativity)`
- `registry.prefix(name, callback, precedence=50)`
- `registry.postfix(name, callback, precedence=60)`
- `registry.list_function(name, callback, min_args=0, precedence=50)`

回调的最后一个参数是 `EvalContext`。读取变量使用 `context.variables`；需要持久化
变量时必须调用 `context.set_var(name, value)` 或 `context.delete_var(name)`。
`context.registry` 始终指向当前实时注册表，因此求解器和元函数不会持有过期表。
数字参数是高精度 `Number`；普通的 `+`、`-`、`*`、`/`、`**` 和 `abs()` 会保持该类型。
需要科学函数时可使用 `context.numeric`（例如 `context.numeric.sqrt(value)`），不要将结果
转成 Python `float`。

单个插件先在隔离注册表中完成加载和校验，成功后才合并。损坏插件只会记录串口
错误，不影响其他插件或内置函数。

### Add-in 依赖与导出

Add-in 可在模块顶层声明 `DEPENDENCIES`。装载器会递归、按顺序
先装载依赖；缺失、循环或依赖失败会让依赖方保持未注册。依赖不必预先在设置中勾选，装载器
会自动补齐，函数面板随后将其勾选并显示 `Auto on: 名称`。

依赖之间只通过显式 `EXPORTS` 共享函数，不依赖加载顺序或模块全局变量：

```python
# base.py
def double_value(value):
    return value * 2

EXPORTS = {"double_value": double_value}

def register(registry):
    pass

# dependent.py
DEPENDENCIES = ("base",)

def apply(value, context):
    return context.plugin("base")["double_value"](value) + 1

def register(registry):
    # register() 阶段也可访问已声明依赖。
    assert callable(registry.plugin("base")["double_value"])
    registry.prefix("apply", apply)
```

`context.plugin(name)` 用于表达式执行期间，`registry.plugin(name)` 用于 `register()` 期间；
注册期只会看到已声明且成功装载的 Add-in 导出。回调应继续通过其显式 `DEPENDENCIES` 使用
运行期导出，而不依赖其他已加载 Add-in 的偶然存在。`EXPORTS` 必须是以字符串为键的字典。

设置文件使用 `plugin:文件名` 标识插件，例如 `plugin:solve`，因此 `basic.py` 与
内置 `basic` 函数组不会发生名称冲突。插件默认关闭，可在函数面板中启用。
如在开发调试时替换了活动槽中的插件，在函数面板按 `Sh+ENT` 可显式重新扫描；普通进入
面板不会执行插件源码，以免影响页面转场响应。

### 自带函数组

函数面板上方四项是始终随固件提供的内置函数组，默认全部启用：

- `Arithmetic`：`+`、`-`、`*`、`/`、`^` 和赋值 `=`。幂为右结合，赋值支持
  `x=y=2`；不提供隐式乘法，请使用 `2*x`。
- `Trigonometry`：`sin`、`cos`、`tan`、`asin`、`acos`、`atan`、`sec`、`csc`、
  `cot`。这些函数跟随右侧状态栏的 DEG/RAD 模式。
- `Scientific`：`sqrt`、自然对数 `ln`、指数 `exp`、常用对数 `log` 和绝对值
  `abs`。
- `List tools`：`max(...)` 与 `min(...)`，接受一个或多个逗号分隔参数，例如
  `max(3,5,4)`。

### 随附 Add-on

函数面板中以 `Add-on:` 开头的项目来自活动槽的 `functions` 目录，默认关闭，可按 `ENT` 启用：

- `Add-on: basic`（`basic.py`）：增加左结合的 `%` 取模运算符，例如 `17%5 -> 2`；
  对零取模会显示错误。
- `Add-on: trig`（`trig.py`）：增加双曲函数 `sinh`、`cosh`、`tanh`，以及始终按
  角度计算的 `sind`、`cosd`、`tand`；`PI()` 返回圆周率，不受 DEG/RAD 状态影响。
- `Add-on: solve`（`solve.py`）：使用牛顿法求方程近似根，例如
  `solve("x^2-4", "x", 1) -> 2`。第三个参数是初始猜测；不收敛或导数过小时应换一个
  初始值。表达式只编译一次，求解过程不会修改计算器的持久变量。

内置函数组和 Add-on 使用不同设置 ID，例如 `basic` 与 `plugin:basic`，因此不会互相
覆盖。若 Add-on 损坏，函数面板会定位到该项目并显示错误原因，可按 `ENT` 将其关闭。

## 架构

```text
Keyboard.scan
  -> 主循环唯一一次 pop_key_event
  -> 全局快捷键或 Screen.update(keyboard, event)

compile_expression
  -> Pratt parser / tuple AST
  -> evaluate_program(program, EvalContext)
  -> FunctionRegistry callback
```

显示路径只定义 SSD1322 的一个 8192 B GS4 framebuffer。`FrameScheduler` 统一安排输入帧、
秒表帧和安静期工作；`DamageMap` 复用两个固定行区间，`Renderer` 只重画并上传明确受损的完整行，
未知区间安全退化为一次全帧提交，不创建逐帧 `memoryview` 或像素缓冲。按键和逐帧路径不调用
`gc.collect()` 或 `gc.mem_free()`。

当前代码没有 `MotionMenu`，普通 `Menu` 会把高亮直接吸附到目标行；`Nav` 也没有 SSD1322
master-current 页面淡变状态。COM5 已稳定启动 resident runtime，但多次冷启动后的干净堆样本为
10,752–11,200 B，仍低于启用动效所需的 12 KiB。因此两项动效均已删除；普通界面继续使用吸附式
菜单和同步页面切换，不增加像素缓冲，也不恢复惰性页面、SWAP 或双 framebuffer。

设置与变量采用 `文件.tmp → 文件` 提交，并保留上一份 `.bak`。主文件损坏时优先
读取备份；按键处理只更新内存并把写入排入空闲主循环，写入失败不会清除当前内存状态，
UI 会显示保存失败并每两秒重试。

## 主机测试与兼容检查

项目测试依赖固定在 `requirements-dev.txt`。在 `mp_version` 目录运行：

```powershell
.\check.ps1
```

它会依次运行：

- 从 C 字体源生成紧凑 `.xglcd` 资产；
- pytest 行为测试；
- CPython 语法编译；
- 仓库 MicroPython 1.29.0-preview 的 mpy-cross 逐文件编译。

`check.ps1` 会核对编译器版本，拒绝使用不匹配的 mpy-cross。首次运行前，按前文
命令从仓库中的 `micropython/mpy-cross` 构建；Windows 可使用 GCC/Make 便携工具链。

## 串口诊断与操作回放

`diagnostics.py` 可以在设备的真实 MicroPython、设置和插件目录上回放按键与表达式，
输出机器可读的 `TRACE` 和最终 `SELFTEST PASS/FAIL`：

```powershell
..\.venv\python.exe -m mpremote connect PORTNAME exec `
  "import diagnostics; diagnostics.run()"
..\.venv\python.exe -m mpremote connect PORTNAME reset
```

自定义命令支持 `STATUS`、`PANEL`、`KEY 行 列 Shift`、`BACK` 和 `EVAL 表达式`。
例如 `diagnostics.run(['KEY 3 1 0', 'KEY 3 3 0', 'BACK', 'EVAL 2+3*4'])` 会回放
“主页向下、确认进入、返回、计算表达式”。诊断是只读的，不保存变量或设置。

将 `settings.json` 的 `diagnostics` 设为 `true` 后，正常实机操作还会逐键输出
`INPUT page/row/col/shift/key` 和 `ACTION page/result`，并保留每五秒性能统计。

## 实机性能基准

`benchmarks.py` 在原始 REPL 中构建一套只读 UI 环境，避免依赖被中断的应用主循环，并
报告应用启动分段、合成导航事件到首帧呈现、帧 p95/最大值、GC 暂停以及预热后的导航堆变化。
它不包含解释器上电、物理按键扫描或正常事件分派的时间：

完整的方案、实机数据、代码审查收口与复现步骤见
[技术说明](TECHNICAL_GUIDE.md)。

```powershell
..\.venv\python.exe -m mpremote connect PORTNAME exec `
  "import benchmarks; benchmarks.run(cycles=5)"
..\.venv\python.exe -m mpremote connect PORTNAME reset
```

当前主机检查为 `1177 passed`，并通过 CPython 语法检查和 MicroPython 1.29
`mpy-cross -march=xtensawin` 全源编译。最终五轮主机驻留导航为 0 次 `MemoryError`、8192 B
framebuffer 峰值、16,000 us 最大阻塞步和 5,625 B 稳态 traced peak；这些数据用于比较逻辑
工作量，不替代真机堆或 SPI 时延。

真机验收只使用现有统一入口：

```powershell
.\tools\run_device_acceptance.ps1 -Port PORTNAME
```

该脚本是唯一正式验收入口，依次执行 resident 启动缓冲探针、最大用户状态应用矩阵、五轮运行时
目标导航、五轮捕获边沿到屏幕提交的交互探针，以及 16 帧秒表局部刷新分配探针；每阶段后都复位
设备并让 OLED 休眠。1.4.0 的最终 COM5 验收报告：单一 framebuffer 为 8192 B、固定 Plot 工作区
为 104 B；20 条历史（共 768 字符）、16 个变量、20 个秒表圈和 3 个插件连续五轮无错误，稳态
最低空闲堆 5760 B、观测到的瞬态最低值 400 B；125 个 runtime step 最大 30.165 ms、最低空闲堆
4272 B；OLED 唤醒时捕获边沿到可见提交最大 19.226 ms、堆漂移 0；16 个秒表帧的堆增量全部为 0。

## 实机回归清单

1. 有卡、无卡、损坏活动槽 `main.mpy` 各启动一次，确认恢复界面和串口错误。
2. 计算、赋值、重启，确认变量持久化；开关插件后再次进入函数选择器。
3. 快速输入、长按 DEL/ESC、Shift+RPN、Shift+Tab，确认没有重复事件。
4. 运行 `tools/run_device_acceptance.ps1` 完成统一验收；必须看到
   `ACCEPTANCE_COMPLETE PORTNAME stages=5 animation=removed_heap_below_12k`。1.4.0 已在 COM5 得到该结果。
5. 运行秒表 30 分钟，并检查绘图、缩放、求解和错误弹窗。

诊断模式每五秒输出平均渲染耗时、present 耗时和空闲堆。统一验收目标仍是输入到可见像素小于
20 ms、单个 step 不超过 32 ms、framebuffer 始终只有一个 8192 B 对象、逐帧堆增量为 0、五轮内
无 `MemoryError` 且堆不持续下降。无动效运行按最大受支持用户状态验证，稳态门槛为 4 KiB；12 KiB
仍是启用动效的独立硬门槛，未达到时必须删除动效。交互探针测量的是已捕获边沿到页面更新及提交，
并报告扫描/去抖合同值；它不能单独证明物理按键扫描到像素的完整端到端时延。

# SCI-CALC MicroPython Edition

SCI-CALC 的 MicroPython 固件，目标硬件为 ESP32-WROOM-32E、SSD1322
256×64 灰阶 OLED、5×6 矩阵键盘和 FAT32 SD 卡。

当前应用版本为 **1.1.0**。源码编译基线是本仓库 `micropython/` 中的
**MicroPython 1.29.0-preview**，并已在设备的 **MicroPython 1.28.0
(2026-04-06)** 上完成冷启动验证。项目不修改 MicroPython 核心。

## 目录与启动方式

设备采用“内部启动器 + SD 应用”布局：

```text
内部 Flash
├── boot.py             # 挂载 SD，随后立即退出
├── sdcard.py           # 官方 Python SPI SD 驱动
├── main.py             # execfile('/sd/main.py')
├── recovery.py         # SD/应用损坏时的恢复界面
└── display/            # 恢复界面所需的最小显示驱动

SD 卡 /sd
├── main.py
├── settings.json
├── vars.json
├── anim/ calc/ display/ input/ screens/ ui/ utils/
├── fonts/
└── functions/          # 可开关的 Python 插件
```

MicroPython 按 `_boot.py → /boot.py → /main.py` 启动。无 SD 卡、挂载失败或
`/sd/main.py` 无法执行时，内部恢复界面会显示错误；串口同时保留完整错误信息。

## 构建和刷入解释器

ESP32 port 需要 ESP-IDF 5。准确依赖和命令以
[`micropython/ports/esp32/README.md`](../micropython/ports/esp32/README.md)
为准。构建默认 ESP32_GENERIC 固件：

```bash
cd micropython
make -C mpy-cross
cd ports/esp32
make submodules
make BOARD=ESP32_GENERIC
```

生成文件位于 `micropython/ports/esp32/build-ESP32_GENERIC/firmware.bin`。
首次刷写前擦除 Flash，然后按实际串口写入：

```powershell
..\.venv\python.exe -m esptool --chip esp32 --port COM4 erase_flash
..\.venv\python.exe -m esptool --chip esp32 --port COM4 write_flash 0x1000 `
  ..\micropython\ports\esp32\build-ESP32_GENERIC\firmware.bin
```

不要照搬 `COM4`；先运行以下命令确认设备：

```powershell
..\.venv\python.exe -m mpremote devs
```

## 一键部署应用

在 `mp_version` 目录执行：

```powershell
.\deploy.ps1 -Port COM4 -Reset
```

脚本会：

1. 安装内部启动和恢复文件；
2. 执行一次必要的硬复位，释放旧应用占用的 SPI 状态；
3. 等待新 `/boot.py` 挂载 SD 卡并创建目录；
4. 上传完整应用到 `/sd`；
5. 对关键入口执行 SHA-256 校验；
6. 仅在传入 `-Reset` 时执行部署完成后的再次复位。

脚本可重复执行。中途硬复位用于切换启动器，不能省略；它可避免旧固件的 SPI
对象导致 `ESP_ERR_INVALID_STATE`。部署不会自动擦除 SD 上不属于当前源码的文件；
`settings.json` 和 `vars.json` 仅在设备上不存在时初始化，重复部署会保留用户设置与变量。

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
不要写 `2x`），也不应替代需要单位追踪、任意精度或可审计过程的专业计算软件。

### 绘图

- 查看模式：`8/2` 缩放 Y，Shift+`8/2` 缩放 X，物理 `4/6` 平移
- `ENT`：打开表达式编辑
- `RPN`：快速输入 `x`
- 编辑模式下 `ENT` 绘图、`ESC` 取消、`Shift+Tab` 重置 X 范围

表达式只编译一次，再用于全部采样点。非有限值和大幅跳变不会被连接成贯穿
屏幕的伪曲线。

### 其他页面

- 函数面板：`ENT` 开关函数组或插件，`ESC` 保存并返回
- 变量表：`ENT` 插入变量，`DEL` 删除，物理 `4/6` 切换列
- 秒表：`ENT` 开始/暂停/继续，运行时 `DEL` 计圈，停止时 `DEL` 复位
- 字母面板：`Sh` 切换大小写，`OK` 写入，`ESC` 取消，`Bk` 退格

### 自动休眠

默认连续 3 分钟没有按键后向 SSD1322 发送硬件睡眠命令。休眠时仅保留低频矩阵键盘
扫描；任意键可唤醒屏幕，且唤醒键必须释放后才会重新参与页面操作。可在
`settings.json` 中设置 `sleep_timeout_s`，例如 `60` 表示一分钟，`0` 表示关闭自动休眠。
最大值为 `86400`（24 小时）。
矩阵键盘当前不能可靠唤醒 ESP32 深度睡眠，因此这里关闭的是 OLED 并让 CPU 大部分时间
处于等待状态，而不是丢失运行状态的深度睡眠；正在运行的秒表会在唤醒后按实际时间继续。

## 插件接口

插件位于 `/sd/functions/*.py`，实现 `register(registry)`。旧版 `flist()` 六元组
接口已移除。

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

单个插件先在隔离注册表中完成加载和校验，成功后才合并。损坏插件只会记录串口
错误，不影响其他插件或内置函数。

设置文件使用 `plugin:文件名` 标识插件，例如 `plugin:solve`，因此 `basic.py` 与
内置 `basic` 函数组不会发生名称冲突。插件默认关闭，可在函数面板中启用。

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

函数面板中以 `Add-on:` 开头的项目来自 `/sd/functions`，默认关闭，可按 `ENT` 启用：

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

页面转场由主循环以最高约 30 FPS 驱动，不再阻塞键盘扫描。页面、转场和固定状态栏
统一经过 `ui.renderer.Renderer` 合成并且每帧只提交一次；状态栏在合成末尾先完整清除
专属区域再重绘，因此滑动页面不会污染电压边框。转场和控件运动使用统一的非线性
ease-out 曲线，并在结束时立即重绘实时页面，不等待 500ms 保活刷新。静止页面只在
输入、状态栏更新或保活周期时刷新；普通页面上限约 15 FPS。转场结束后必须等触发键
释放才重新接收业务输入。

设置与变量采用 `文件.tmp → 文件` 提交，并保留上一份 `.bak`。主文件损坏时优先
读取备份；写入失败不会清除当前内存状态，UI 会显示保存失败。

## 主机测试与兼容检查

项目测试依赖固定在 `requirements-dev.txt`。在 `mp_version` 目录运行：

```powershell
.\check.ps1
```

它会依次运行：

- pytest 行为测试；
- CPython 语法编译；
- 仓库 MicroPython 1.29.0-preview 的 mpy-cross 逐文件编译。

`check.ps1` 会核对编译器版本，拒绝使用不匹配的 mpy-cross。首次运行前，按前文
命令从仓库中的 `micropython/mpy-cross` 构建；Windows 可使用 GCC/Make 便携工具链。

## 串口诊断与操作回放

`diagnostics.py` 可以在设备的真实 MicroPython、设置和插件目录上回放按键与表达式，
输出机器可读的 `TRACE` 和最终 `SELFTEST PASS/FAIL`：

```powershell
..\.venv\python.exe -m mpremote connect COM5 exec `
  "import sys; sys.path.insert(0,'/sd'); import diagnostics; diagnostics.run()"
..\.venv\python.exe -m mpremote connect COM5 reset
```

自定义命令支持 `STATUS`、`PANEL`、`KEY 行 列 Shift`、`BACK` 和 `EVAL 表达式`。
例如 `diagnostics.run(['KEY 3 1 0', 'KEY 3 3 0', 'BACK', 'EVAL 2+3*4'])` 会回放
“主页向下、确认进入、返回、计算表达式”。诊断是只读的，不保存变量或设置。

将 `settings.json` 的 `diagnostics` 设为 `true` 后，正常实机操作还会逐键输出
`INPUT page/row/col/shift/key` 和 `ACTION page/result`，并保留每五秒性能统计。

## 实机回归清单

1. 有卡、无卡、损坏 `/sd/main.py` 各启动一次，确认恢复界面和串口错误。
2. 计算、赋值、重启，确认变量持久化；开关插件后再次进入函数选择器。
3. 快速输入、长按 DEL/ESC、Shift+RPN、Shift+Tab，确认没有重复事件。
4. 连续往返页面 50 次；启用 `settings.json` 的 `diagnostics` 后检查串口堆内存。
5. 运行秒表 30 分钟，并检查绘图、缩放、求解和错误弹窗。

诊断模式每五秒输出平均渲染耗时、present 耗时、空闲堆和活动动画数量。执行
`gc.collect()` 后，50 次页面往返的空闲堆下降应不超过 4 KiB。

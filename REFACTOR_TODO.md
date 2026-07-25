# SCI-CALC 性能重构续跑清单

更新时间：2026-07-25

> **历史归档（COM5 / 应用 1.3.0）**
> 本文件保存上一轮重构当时的命令和现场证据，不是当前执行清单。
> 其中“5 轮”沿用当时旧工具口径，后续审计已证明不等于完整场景矩阵重复 5 轮。
> 不得无人值守复跑下方 COM5、热更新或部署命令。当前设备、门槛、未完成项和
> 安全微动效合同一律以 `OPTIMIZATION_TODO.md` 为准；恢复工作时仍须先读两份文件。

## 不可变验收条件

- 所有压力与真机验收固定为 5 轮。
- 真机诊断、`resume exec`、`resume run` 结束后必须执行 COM5 reset。
- 启动必须同时满足：
  - `BOOT_RUNTIME_READY True`
  - `BOOT_ROOT_VISIBLE True`
- 单次阻塞步骤不超过 32,000 us。
- 最低空闲堆不低于 8 KiB。
- 不允许出现 `MemoryError`。
- 不恢复页面 SWAP、旧全页转场动画、LazyScreen 或 residency 架构；
  后续仅允许 `OPTIMIZATION_TODO.md` 预算内、可降级、零像素缓冲的微动效。

## 已完成

- [x] 删除页面 SWAP、过渡缓冲、页面动画、`anim` 包、LazyScreen 和 residency。
- [x] 页面改为启动时一次构造、运行期常驻。
- [x] 键盘改为位图状态和定长事件队列，扫描/去抖为 8 ms。
- [x] 主循环单次最多处理 5 个按键边沿。
- [x] Plot 改为可中断的 4 ms 空闲切片，并使用低分配浮点绘图求值器。
- [x] 辅助页面改用内置 8x8 字体。
- [x] Plot 退出释放工作区，GC 延迟到静默期。
- [x] 根菜单首帧在 `run_loop=False` 返回前强制提交。
- [x] 修复真机诊断后未 reset 导致设备停在 8/8 的流程漏洞。
- [x] 页面切换只清空 210 px 内容区；侧栏只在周期、角度切换、唤醒或完整失效时重画。
- [x] 主机完整检查：146 tests passed，MicroPython `.mpy` 编译通过。
- [x] 热更新后的 COM5 启动探针：
  - `BOOT_RUNTIME_READY True`
  - `BOOT_ROOT_VISIBLE True`

## 最近真机结果

命令：

```powershell
..\.venv\python.exe -m mpremote connect COM5 resume run tools\device_runtime_monitor.py
```

结果（5 轮）：

```text
Calculator forward=23789 us back=12749 us
Plot       forward=19953 us back=14376 us settle_max=34268 us
Functions  forward=31092 us back=14709 us
Stopwatch  forward=14492 us back=14872 us
Settings   forward=18380 us back=14708 us
heap_min=11216 bytes
heap_delta=-320 bytes
MemoryError=0
```

此前唯一失败：Plot 某个 settle 步骤为 34,268 us，超过 32 ms 门槛 2,268 us。

分项探针（5 轮）已确认：

```text
首次编译/准备最高约 12.7 ms
显式 GC 最高约 26.7 ms
OLED present 最高约 25.8 ms
```

根因：最后一个曲线采样切片与全帧 OLED present 在同一次 settle
调用中连续执行，合计达到 32.6–34.8 ms。

## 当前进行中

- [x] 分别测量 Plot 首次编译、GC、最终 present，定位 34.268 ms 步骤。
- [x] 将最后一个采样切片和 OLED present 拆到相邻的两个空闲循环。
- [x] 添加“最终采样与 redraw 必须分步”的回归测试。
- [x] 启动进度页在 `Loading ...` 下显示简短的实际操作文本，例如 `(import screens.*)`。
- [x] 删除 Plot 每个切片的 `gc.mem_free()` 堆遍历，改为固定工作量后请求 GC。
- [x] 重新运行 `.\check.ps1`：150 tests passed。
- [x] 编译并热更新 `main.mpy`、`screens/plot.mpy`、`ui/renderer.mpy`。
- [x] 5 轮完整验收首次通过：`blocking_max=29395 us`、`MemoryError=0`。
- [x] 将 Plot GC 间隔由 8 调为 6；8 的 `heap_min=8768 B` 仅比硬门槛高 576 B，余量不足。
- [x] 最终 5 轮运行时验收通过：
  - `blocking_max=29696 us`
  - `heap_min=10400 B`
  - `heap_delta=-304 B`
  - `MemoryError=0`
- [x] 最终 5 轮输入验收通过：
  - 菜单帧 `7046–11241 us`
  - 连续 `12345` 批量输入帧 `17190 us`
  - `heap_free=14480 B`
- [x] 最终启动探针通过：
  - `BOOT_RUNTIME_READY True`
  - `BOOT_ROOT_VISIBLE True`
- [x] 输入验收改为复用真实常驻 runtime，不再错误地创建第二个 8 KiB framebuffer。
- [x] 删除临时 Plot 分项探针。
- [x] 执行完整 `deploy.ps1 -Port COM5 -Reset`。
- [x] 55 个运行时资产全部通过 SHA-256 校验。
- [x] 部署后启动探针再次通过并 reset。
- [x] 删除 6 个旧 pytest 临时目录。

## 当前状态

本轮重构和验收已全部完成；设备处于正常复位后的应用运行状态。

## 发布

- [x] 应用版本从 `1.2.1` 升级为 `1.3.0`。
- [x] 版本引用一致性检查通过。
- [x] 完整检查通过：150 tests passed，MicroPython `.mpy` 编译通过。
- [x] 创建 Git commit。

## 修复后验收顺序

1. 编译并热更新受影响的 `.mpy`。
2. reset，等待约 10 秒。
3. 运行启动探针并确认两个布尔值都为 `True`。
4. reset。
5. 运行 `tools\device_runtime_monitor.py`，固定 5 轮。
6. reset。
7. 运行 `tools\device_interaction_acceptance.py`，固定 5 轮。
8. reset。
9. 执行 `.\deploy.ps1 -Port COM5 -Reset`。
10. 确认部署脚本的全部运行时资产 SHA-256 校验通过。
11. 最终再执行一次启动探针并 reset。

# 两项动效分支

冻结和页面生命周期完成前不得编写动效。它们现已完成；活动合同以文末“8 KiB 动画优先续行”为准。COM5 最大用户状态、所有支持页面和连续五轮操作必须达到最低空闲堆 `8192 B`、`MemoryError=0` 且堆无持续下降；12 KiB 只作为优化目标，不再是启用条件。

## 共同合同

门禁结论（2026-07-31，release `f8a6badf6054926605642a5bac6725b57e2ce876c6221a0c7b8381152687b9f9`）：初测最低空闲堆 `9888--9904 B`，低于 `12288 B`；`MemoryError=0`、普通错误 `0`、最大同步 step `22.865 ms`，固定像素/工作缓冲峰值 `8296 B`（8192 B framebuffer + 104 B Plot workspace）。补充最低堆 capability/phase/round/step 后，只处理两个连续热点：固定宽度表达式构造把一次性微基准峰值从 `4352 B` 降到 `1472 B`，但矩阵最低值仍为 `9904 B`，故实现及专属测试已删除；缓存验收 `buffer_snapshot()` 的不变结果后，历史五轮通过、完成场景数从 `5` 增至 `10`，最低堆升至 `10352 B`，随后真实 `error_lifecycle` 仍低于门槛且回收 step 为 `37.657 ms`。MicroPython 自动 GC 阈值 `4096 B` 与 `20000 B` 都把最低堆抬到 `15200 B`，但最大 step 分别为 `41.810 ms` 与 `40.875 ms`，均超过 `32 ms`，实验代码已删除。最终保留的 snapshot 缓存不进入普通 release，相关回归 `94 passed`；所有设备操作后均清理临时载荷并确认 OLED 休眠。

因此按本分支降级规则不启用菜单高亮滑动或页面硬件亮度淡变，不创建任何动画状态或像素缓冲，也不继续扩张到计算核心。应用版本保持 `1.4.0`，现有即时高亮、Plot 加载进度和普通页面切换不变。

用户于 2026-07-31 明确把活动同步 step/动画帧门槛调整为严格小于 `40 ms`。正式四模块 frozen 候选先在 `calculator_history` 测得 `heap_min=15920 B`、`blocking_max_us=37724`、`MemoryError=0`、普通错误 0，因此重新打开下列两项固定范围动画；上述 32 ms 禁用结论仅作为历史保留。扩展矩阵随后在 `error_lifecycle` 暴露 12 KiB 失败，故本节记录候选及其删除结果。Plot 的长计算继续使用现有真实 `Plotting` 进度条。

- [x] 始终一个 8192 B GS4 framebuffer，新增像素缓冲 0 B；复用 `FrameScheduler`、`DamageMap`、`Renderer` 和 SSD1322 固定命令缓冲。
- [x] 候选只增加 7 个固定标量槽（Menu 4 + Nav 3，ESP32 上约 28 B），不超过 64 B；按键和逐帧路径不调用 `gc.collect()` / `gc.mem_free()`。候选删除后正式代码动画状态为 0 B。
- [x] 主机候选覆盖输入首帧、严格 `<40 ms` 单步和 140 ms 页面过渡；COM5 因 12 KiB 前置门禁失败未进入动画计时阶段，不将未执行阶段宣称为通过。
- [x] 新输入、低内存、异常、离页、休眠和恢复的正确终态已通过主机候选测试；候选随后完整删除，正式代码不存在需恢复的过渡亮度或动画中间态。

## 菜单高亮滑动

- [x] 在现有 `Menu` 固定状态中加入起点、目标、开始时间和活动标量，不新增动画类或通用插值框架；新增 4 个固定标量槽。
- [x] 使用 96 ms 整数 ease-out 推进高亮，输入首帧立即移动 2 px；每帧只重绘旧行、新行和实际经过行，滚屏直接吸附，方向反转从当前像素重定向且不留下残影。
- [x] 主机聚焦与导航/增量提交/Settings/Function Panel 交叉集通过，覆盖单步、连续方向键、反向、新输入中断、滚屏边缘和 DamageMap 行范围。
- [x] COM5 在动画阶段前以 `heap_min=10736 B` 拒绝候选；未声称逐帧或输入时延通过，并已按规则删除菜单动画实现及专属测试。

## 页面硬件亮度淡出/淡入

- [x] `Nav` 复用 3 个固定标量保存淡出、暗点提交、淡入三个阶段，通过 SSD1322 master-current 固定命令缓冲改变亮度，不读取或合成 framebuffer 像素。
- [x] 真实输入触发后立即降低一个可见亮度级，70 ms 暗点只提交一次目标画面，再用 70 ms 淡入用户亮度；程序化验收导航保持同步页面生命周期。
- [x] 主机覆盖 140 ms 总时长、前进/返回、暗点单次提交、新输入吸附、复位及 `MemoryError` 亮度恢复；相关导航、驱动、菜单、增量提交、Settings 和 Function Panel 交叉集通过。
- [x] COM5 在动画阶段前未达到最低 12 KiB；约 28 B 的两项状态远小于 1552 B 缺口，单独保留菜单也不能通过前置门禁，故页面淡变和菜单滑动一并删除。

## 降级规则与完成条件

- [x] 真机重新验证最大用户状态门禁；在连续五轮 Calculator history/error 路径稳定复现低堆和超时，`MemoryError=0`，失败后恢复根页、清理载荷并休眠 OLED。
- [x] 页面淡入/淡出和菜单滑动均未启用；新增动画状态 `0 B`、新增像素缓冲 `0 B`，未降低堆、时延、OOM 或数据保护门槛。
- [x] 只诊断已测 `error_lifecycle` 热点：循环内结构符号字典候选把主机 `.` tokenizer 微测从 `1024/1711/687 B` 降至 `808/1447/639 B`（保留/峰值/瞬态），但等价 COM5 单 capability 探针从既有 `14752 B/60.456 ms` 退化至 `10208 B/61.472 ms`；已删除候选、专属测试和临时探针，不运行完整 matrix，也不降低门槛。
- [x] 被否决候选清理后重建并仅刷写正式 frozen 应用分区（`1818192 B`，SHA-256 `6fc4215f03575c692e3d0e69cd6dc8b12fc4a45434ad6e3f5cca84ff28b71acb`）；设备 acceptance support/stage 已删除并让 OLED 硬件休眠，阶段 `check.ps1` 为 `1093 passed in 26.53s`、总耗时 `31.8s`。
- [x] 同一 `error_lifecycle` 的错误格式化边界显示，把 `ParseError` 对象或其既有 `args[0]` 字符串传给当前 `ErrorPopup`，20 次均稳定分配 `2560 B`，五轮节省均为 `0 B`；不修改调用点，探针结束后 OLED 已休眠。
- [x] 已测共同规范化分配：固定分支微探针把短错误 20 次分配从 `960 B` 降至 `320 B`，五轮均节省 `640 B`；但真实 COM5 `error_lifecycle` 仍只有 `10496 B` 空闲堆且最大 step `61.235 ms`，`MemoryError=0`、普通错误 0，未接近联合门禁，故删除快路径和专属测试且不运行完整 matrix。
- [x] 第二个候选删除后再次回刷正式 frozen 镜像 `6fc4215f03575c692e3d0e69cd6dc8b12fc4a45434ad6e3f5cca84ff28b71acb`，清理 23 个 support 文件及 stage/hotspot，OLED 已休眠；阶段 `check.ps1` 为 `1093 passed in 25.92s`、总耗时 `31.3s`。
- [x] 本轮三个已测边界到此收口：ErrorPopup 绘制已复用单 framebuffer、宽度内文本和 packed direct-text 路径；剩余垃圾来自通用 tokenizer/AST/异常求值链，继续处理需要重写解析模型或固定 scratch 系统，违反本轮简单优先、1--3 个热点和不扩展方案空间的约束，因此保持两项动效禁用。

## 严格小于 40 ms 的重新启用门禁

- [x] 用户新授权已记录；正式无动画候选 `15920 B / 37.724 ms` 通过 12 KiB 和严格 `<40 ms` 进入门槛，Plot 已有真实进度条，无需为该 38 ms 场景增加加载状态。
- [x] 首个两项动画真机候选构建为 `1822976 B`（SHA-256 `6b316bec184468d6d65631dde96812861a36c82db7662341402e0479c09138e5`），COM5 application matrix 在继续越过 `calculator_history` 后稳定暴露既有 `error_lifecycle`：`heap_min=10736 B`、`blocking_max_us=38263`、`MemoryError=0`、普通错误 0、`failure_mask=8`。动画阶段未运行，临时载荷已删除且 OLED 已休眠；距 12 KiB 仍缺 `1552 B`，而全部动画状态仅约 28 B，故不能靠删一项动画满足全场景进入门槛，按降级规则删除两项产品动画而不降低门槛。
- [x] 菜单高亮候选通过主机的行损伤、输入中断和时序覆盖；COM5 前置堆门禁失败，因此实现及专属测试已删除，未把未执行的动画阶段记作通过。
- [x] 页面淡变候选通过主机的暗点单次提交、140 ms 总时长和亮度恢复覆盖；COM5 前置堆门禁失败，因此实现及专属测试已删除。
- [x] 统一验收按合同在 `error_lifecycle` 以 `failure_mask=8` 停止，没有完成五轮或逐帧阶段；临时载荷已删除、OLED 已休眠，正式无动画固件已恢复。

## 12 KiB 续行：Calculator 直接求值

本节由 2026-07-31 用户明确授权，只有补充 frozen 常驻模块后完整 matrix 仍未达到联合门禁时才执行。公开接口保持 `evaluate(expr, context)`；Calculator 的一次性表达式改用惰性 token cursor 和直接 Pratt 求值，不同时保留完整 token 列表与 tuple AST。`compile_expression()` / `evaluate_program()` 及 Plot 的可复用编译结果保持现有行为，不建立第二套通用计算引擎。

- [x] 四模块 frozen 候选后的完整 matrix 已将最低堆抬至 `15920 B`，但最大阻塞点变为 `calculator_history` 的 `37.761 ms`；该场景只输入 38/46 字符的 `0e+...` 纯数字。
- [x] 公开接口回归 `test_bare_numeric_literal_skips_the_allocating_general_parser` 通过，并以禁止 `_Compiler` 和 `Number.parse` 的方式证明这些零值表达式由 `_parse_bare_number()` 直接返回，不构造 token 列表或 tuple AST。因此惰性 cursor/直接 Pratt 对当前最大 step 没有可达调用路径，候选在写代码前即按主动剪枝规则否决。
- [x] 未创建直接求值实现、兼容层、专属探针或测试；也不为一个不能改善 `calculator_history` 最大 step 的候选重复运行 COM5。现有 `evaluate()`、`compile_expression()`、`evaluate_program()` 和 Plot 编译结果保持不变。
- [x] 联合门禁仍为 `15920 B / 37.761 ms`，故进入直接命中该场景的无损 Calculator 历史压缩；Stopwatch 圈速仍须等待下一次真机数据。
- [x] 固定 40 槽 Calculator 历史消除首个 `calculator_history` 阻塞后，matrix 继续到 `error_lifecycle`，新热点为 `10496 B / 61.728 ms`；因此曾以共享惰性 token cursor 和私有直接 Pratt evaluator 处理一次性 `evaluate()`，Plot 的 `compile_expression()` / `evaluate_program()` 保持原接口。禁止 `_Compiler`、20 类 ErrorPopup 位置、深度限制、插件/高精度/Plot 交叉集均通过。
- [x] 组合固件 `1824272 B`、SHA-256 `6fd20260aed99a5419ecdade329b48aea42583e6f6639410deb64616d7da822a`；COM5 `error_lifecycle` 为 `heap_min=11136 B`、`blocking_max_us=56665`、`MemoryError=0`、普通错误 0。相对固定历史单独候选仅改善 `+640 B / -5.063 ms`，仍同时违反 12 KiB/32 ms，故直接求值、两种 flat 历史实现和全部专属测试均已删除，原 parser、tuple 历史和场景租约已恢复；聚焦回归 `158 passed`。

完成后回到 [PLAN](../PLAN.md) 勾选“两项动效”，再读取[验证分支](verification.md)。

## 8 KiB 动画优先续行

本节覆盖前述基于 12 KiB 的删除结论，但保留全部历史测量。用户最新明确以动画为高优先级，将最低空闲堆硬门槛调整为 `8192 B`，并要求继续尽可能提高余量。首个两项动画候选的 `10736 B / 38.263 ms / MemoryError=0 / errors=0` 在新门槛下有 `2544 B` 堆余量，因此必须继续执行此前被提前跳过的五轮、交互和逐帧阶段。

- [x] 无动画 1.4.0 发布基线已标记为本地 annotated tag `v1.4.0`（`7e4e807`），不 push。
- [x] 将应用矩阵最低堆合同从 12 KiB 改为 8 KiB，保留严格 `<40 ms`、输入 `<20 ms`、零错误和堆稳定合同；`8192 B` 接受、`8191 B` 拒绝的边界测试及 runtime 聚焦回归 `52 passed`。设备交互阶段不在逐帧路径读取空闲堆，继续由应用矩阵门禁加交互零错误/漂移合同共同覆盖。
- [x] 恢复菜单 96 ms 整数 ease-out：只增加 4 个标量槽，只更新旧行、新行和经过行；滚屏吸附，反向从当前像素重新定向；菜单/导航/显示及使用方聚焦回归 `95 passed`。
- [x] 恢复 SSD1322 70 ms 淡出 + 70 ms 淡入：只增加 3 个标量槽，暗点只提交目标页一次；新输入、异常、复位和休眠恢复用户亮度并吸附终态；现有五轮 tracer 已恢复动画逐帧耗时及非零堆变化硬失败，工具/编排聚焦回归 `20 + 11 passed`。
- [x] 主机候选保持一个 8192 B framebuffer、0 B 新像素缓冲、动画状态为菜单 4 + Nav 3 个固定标量、逐帧非零堆变化硬失败；阶段 `check.ps1` 为 `1105 passed`，全部 CPython 与 MicroPython 兼容编译通过。
- [x] 首两次 COM5 应用矩阵均达到 8 KiB 门槛（`10128 B`、`10208 B`，零 MemoryError/普通错误），但 `plugin_reload` 稳定为 `140.323--141.198 ms`。定向诊断证明动态源码 `execfile()` 的 source phase 为 `134--140 ms`、文件复核为 `41--49 ms`，无法继续切片且不得冻结用户 Add-ons；按用户授权在 Function Panel 留页显示固定 `Loading add-ons` 条后再执行原后台 reload。普通 step/动画帧仍严格 `<40 ms`；仅该可见反馈覆盖的 step 严格 `<160 ms`，`159999 us` 接受、`160000 us` 拒绝的边界测试已通过。
- [x] 使用现有 `tools/run_device_acceptance.ps1 -Port COM5` 完整执行五阶段验收：应用矩阵 35 场景/五轮，`heap_min=10576 B`、`heap_delta=+1536 B`、`MemoryError=0`、普通错误 0、固定缓冲峰值 `8296 B`；加载条覆盖的 `plugin_reload=140261 us`。预热后的页面 tracer 为 `heap_min=55392 B`、漂移 `-80 B`、普通最大 step `24817 us`；交互五轮完整输入 `12345`，输入提交最大 `19011 us`，85 个动画帧最大 `17071 us` 且三相净分配均为 `0 B`；Stopwatch 16 帧同样为 `0 B`。五阶段通过后删除载荷并硬件休眠 OLED。
- [x] 动画首次完整通过后复核最新两个实测最低点 `error_lifecycle` 与 `plot_pipeline`；连续候选在 `10224--10736 B` 间波动且无持续下降。首屏缓存前移和删除返回页无效 Sidebar 重绘用于满足逐帧零分配，未宣称稳定净堆收益；已否决 parser/GC 候选不恢复。最终 `10576 B` 相对 8 KiB 有 `2384 B` 余量，没有新的简单热点可在不伤害时延/行为时稳定提高，因此按 1--3 个热点边界收口。
- [x] 完整验收未触发降级：最低堆高于 8 KiB、错误为 0、堆无持续下降且全部时延通过；保留菜单滑动和页面淡变。

## v1.6.0 横向页面滑动

本节替代 1.5.0 的页面硬件亮度淡出/淡入，不改变已通过的菜单高亮滑动。固定状态栏是跨页面持续存在的设备状态，不参与位移；`CONTENT_W=210` 的页面内容区按导航方向移动。进入任意子级时，当前内容向左离开且目标内容从右侧进入；返回时当前内容向右离开且父级内容从左侧进入。轨迹使用 140 ms 的整数 cubic ease-out，并量化为 GS4 每字节两个像素的偶数位移。

- [x] 在测试中先锁定前进负方向、返回正方向、首帧可见移动、严格单调的非线性位移、140 ms 吸附终态，以及新输入中断后目标页完整可见。
- [x] `Display` 只增加平移 x、裁剪左边界和裁剪右边界 3 个固定标量；用现有 GS4 framebuffer 原地移动内容区，并让像素、线、矩形、内置文字和 packed direct-text 只写入目标页当前暴露条带。Plot 曲线不再绕过 Display 绘图边界。
- [x] `Renderer` 复用当前目标页的固定 class-level draw hooks：每帧先移动已经呈现的旧页/目标页像素，再在同一 framebuffer 的暴露条带绘制目标页，最后做一次既有全帧提交；不保留离页对象、页面副本或像素 scratch。
- [x] `Nav` 的 3 个固定标量改为方向、开始 ticks 和已呈现位移；删除 `PAGE_FADE_MS`、`set_transition_current()` 和所有亮度过渡分支。程序化导航仍同步；真实输入才启动滑动。
- [x] 新输入、异常、低内存、休眠、复位和连续导航必须结束裁剪状态并吸附当前逻辑页；任何帧超时、分配、低于 8 KiB 或残影都必须先简化帧数/轨迹，不能恢复双 framebuffer 或降低门槛。
- [x] 最终 `check.ps1` 为 `1125 passed`；COM5 五阶段完成 35 场景/五轮，`heap_min=10528 B`、普通最大 step `26939 us`、输入 `16608 us`、56 个动画帧最大 `20030 us` 且三相净分配均为 `0 B`，错误 0；support/stage 已删除并硬件休眠 OLED。

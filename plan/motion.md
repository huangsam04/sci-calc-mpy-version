# 两项动效分支

冻结和页面生命周期完成前不得编写动效。先在 COM5 最大用户状态、所有支持页面和连续五轮操作中测量；只有全过程最低空闲堆不小于 `12288 B`、`MemoryError=0` 且堆无持续下降，才启用动效。

## 共同合同

门禁结论（2026-07-31，release `f8a6badf6054926605642a5bac6725b57e2ce876c6221a0c7b8381152687b9f9`）：初测最低空闲堆 `9888--9904 B`，低于 `12288 B`；`MemoryError=0`、普通错误 `0`、最大同步 step `22.865 ms`，固定像素/工作缓冲峰值 `8296 B`（8192 B framebuffer + 104 B Plot workspace）。补充最低堆 capability/phase/round/step 后，只处理两个连续热点：固定宽度表达式构造把一次性微基准峰值从 `4352 B` 降到 `1472 B`，但矩阵最低值仍为 `9904 B`，故实现及专属测试已删除；缓存验收 `buffer_snapshot()` 的不变结果后，历史五轮通过、完成场景数从 `5` 增至 `10`，最低堆升至 `10352 B`，随后真实 `error_lifecycle` 仍低于门槛且回收 step 为 `37.657 ms`。MicroPython 自动 GC 阈值 `4096 B` 与 `20000 B` 都把最低堆抬到 `15200 B`，但最大 step 分别为 `41.810 ms` 与 `40.875 ms`，均超过 `32 ms`，实验代码已删除。最终保留的 snapshot 缓存不进入普通 release，相关回归 `94 passed`；所有设备操作后均清理临时载荷并确认 OLED 休眠。

因此按本分支降级规则不启用菜单高亮滑动或页面硬件亮度淡变，不创建任何动画状态或像素缓冲，也不继续扩张到计算核心。应用版本保持 `1.4.0`，现有即时高亮、Plot 加载进度和普通页面切换不变。

- [ ] 始终一个 8192 B GS4 framebuffer，新增像素缓冲 0 B；复用 `FrameScheduler`、`DamageMap`、`Renderer` 和 SSD1322 固定命令缓冲。
- [ ] 全部动效状态复用固定标量槽位且合计不超过 64 B；稳态逐帧堆分配为 0；按键和逐帧路径不调用 `gc.collect()` / `gc.mem_free()`。
- [ ] 普通输入边沿到可见提交不超过 20 ms；单个同步 step 和动画帧不超过 32 ms；页面过渡总时长保持 120--160 ms。
- [ ] 新输入、低内存、异常、离页、休眠或恢复会立即取消动效，恢复用户设置的正常亮度，并吸附到唯一正确终态。

## 菜单高亮滑动

- [ ] 在现有 `Menu` 固定状态中加入起点、目标、开始时间和活动标量，不新增动画类或通用插值框架。
- [ ] 使用短距离 ease-out 推进高亮；每帧只重绘旧行、新行和实际经过行，滚屏或方向反转时立即重定向且不留下残影。
- [ ] 覆盖单步、连续方向键、反向、新输入中断、滚屏边缘、无额外分配和 DamageMap 行范围。

## 页面硬件亮度淡出/淡入

- [ ] `Nav` 复用固定标量保存淡出、暗点提交、淡入三个阶段，通过 SSD1322 master-current 命令改变亮度，不读取或合成 framebuffer 像素。
- [ ] 触发后在 20 ms 内开始可见淡出；暗点按需构造目标页并只提交一次目标画面，再淡入用户亮度。目标页构造仍必须满足 32 ms step 门槛。
- [ ] 目标页首次加载若超过时延或堆预算，先保留其最小冻结运行态；仍不达标则删除页面淡入/淡出，不增加加载框架或降低门槛。

## 降级规则与完成条件

- [x] 真机重新验证最大用户状态门禁；在连续五轮 Calculator history/error 路径稳定复现低堆和超时，`MemoryError=0`，失败后恢复根页、清理载荷并休眠 OLED。
- [x] 页面淡入/淡出和菜单滑动均未启用；新增动画状态 `0 B`、新增像素缓冲 `0 B`，未降低堆、时延、OOM 或数据保护门槛。
- [x] 只诊断已测 `error_lifecycle` 热点：循环内结构符号字典候选把主机 `.` tokenizer 微测从 `1024/1711/687 B` 降至 `808/1447/639 B`（保留/峰值/瞬态），但等价 COM5 单 capability 探针从既有 `14752 B/60.456 ms` 退化至 `10208 B/61.472 ms`；已删除候选、专属测试和临时探针，不运行完整 matrix，也不降低门槛。
- [x] 被否决候选清理后重建并仅刷写正式 frozen 应用分区（`1818192 B`，SHA-256 `6fc4215f03575c692e3d0e69cd6dc8b12fc4a45434ad6e3f5cca84ff28b71acb`）；设备 acceptance support/stage 已删除并让 OLED 硬件休眠，阶段 `check.ps1` 为 `1093 passed in 26.53s`、总耗时 `31.8s`。
- [x] 同一 `error_lifecycle` 的错误格式化边界显示，把 `ParseError` 对象或其既有 `args[0]` 字符串传给当前 `ErrorPopup`，20 次均稳定分配 `2560 B`，五轮节省均为 `0 B`；不修改调用点，探针结束后 OLED 已休眠。
- [x] 已测共同规范化分配：固定分支微探针把短错误 20 次分配从 `960 B` 降至 `320 B`，五轮均节省 `640 B`；但真实 COM5 `error_lifecycle` 仍只有 `10496 B` 空闲堆且最大 step `61.235 ms`，`MemoryError=0`、普通错误 0，未接近联合门禁，故删除快路径和专属测试且不运行完整 matrix。
- [x] 第二个候选删除后再次回刷正式 frozen 镜像 `6fc4215f03575c692e3d0e69cd6dc8b12fc4a45434ad6e3f5cca84ff28b71acb`，清理 23 个 support 文件及 stage/hotspot，OLED 已休眠；阶段 `check.ps1` 为 `1093 passed in 25.92s`、总耗时 `31.3s`。
- [x] 本轮三个已测边界到此收口：ErrorPopup 绘制已复用单 framebuffer、宽度内文本和 packed direct-text 路径；剩余垃圾来自通用 tokenizer/AST/异常求值链，继续处理需要重写解析模型或固定 scratch 系统，违反本轮简单优先、1--3 个热点和不扩展方案空间的约束，因此保持两项动效禁用。

## 12 KiB 续行：Calculator 直接求值

本节由 2026-07-31 用户明确授权，只有补充 frozen 常驻模块后完整 matrix 仍未达到联合门禁时才执行。公开接口保持 `evaluate(expr, context)`；Calculator 的一次性表达式改用惰性 token cursor 和直接 Pratt 求值，不同时保留完整 token 列表与 tuple AST。`compile_expression()` / `evaluate_program()` 及 Plot 的可复用编译结果保持现有行为，不建立第二套通用计算引擎。

- [x] 四模块 frozen 候选后的完整 matrix 已将最低堆抬至 `15920 B`，但最大阻塞点变为 `calculator_history` 的 `37.761 ms`；该场景只输入 38/46 字符的 `0e+...` 纯数字。
- [x] 公开接口回归 `test_bare_numeric_literal_skips_the_allocating_general_parser` 通过，并以禁止 `_Compiler` 和 `Number.parse` 的方式证明这些零值表达式由 `_parse_bare_number()` 直接返回，不构造 token 列表或 tuple AST。因此惰性 cursor/直接 Pratt 对当前最大 step 没有可达调用路径，候选在写代码前即按主动剪枝规则否决。
- [x] 未创建直接求值实现、兼容层、专属探针或测试；也不为一个不能改善 `calculator_history` 最大 step 的候选重复运行 COM5。现有 `evaluate()`、`compile_expression()`、`evaluate_program()` 和 Plot 编译结果保持不变。
- [x] 联合门禁仍为 `15920 B / 37.761 ms`，故进入直接命中该场景的无损 Calculator 历史压缩；Stopwatch 圈速仍须等待下一次真机数据。
- [x] 固定 40 槽 Calculator 历史消除首个 `calculator_history` 阻塞后，matrix 继续到 `error_lifecycle`，新热点为 `10496 B / 61.728 ms`；因此曾以共享惰性 token cursor 和私有直接 Pratt evaluator 处理一次性 `evaluate()`，Plot 的 `compile_expression()` / `evaluate_program()` 保持原接口。禁止 `_Compiler`、20 类 ErrorPopup 位置、深度限制、插件/高精度/Plot 交叉集均通过。
- [x] 组合固件 `1824272 B`、SHA-256 `6fd20260aed99a5419ecdade329b48aea42583e6f6639410deb64616d7da822a`；COM5 `error_lifecycle` 为 `heap_min=11136 B`、`blocking_max_us=56665`、`MemoryError=0`、普通错误 0。相对固定历史单独候选仅改善 `+640 B / -5.063 ms`，仍同时违反 12 KiB/32 ms，故直接求值、两种 flat 历史实现和全部专属测试均已删除，原 parser、tuple 历史和场景租约已恢复；聚焦回归 `158 passed`。

完成后回到 [PLAN](../PLAN.md) 勾选“两项动效”，再读取[验证分支](verification.md)。

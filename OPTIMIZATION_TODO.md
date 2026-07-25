# SCI-CALC 全项目优化 TODO

> 状态：实施中 — 批次 0 主机安全门完成；真机完整矩阵仍阻塞
> 创建日期：2026-07-25
> 最后现场核验：2026-07-25，设备 `COM6`
> 审计基线：`faf408b`（应用 `1.3.0`）
> 范围：`source/`、`tests/`、`tools/`、PowerShell 脚本及全部用户/技术文档
> 绝对路径：`C:\Users\20976\Desktop\sci-calc\mp_version\OPTIMIZATION_TODO.md`

本文件是本轮“内存与逻辑、动画与流畅度、产品工作流”审计的长期事实来源。
上下文恢复后必须先读本文件和 `REFACTOR_TODO.md`，再运行命令或修改代码。
带有“待真机标定”或“探索”字样的条目不得直接进入发布版本。
`REFACTOR_TODO.md` 只保存上一轮 COM5 重构的历史证据；当前端口、门槛、优先级和
未完成状态以本文件为准。无人值守阶段在事务部署和 `finally reset` 完成前不得发布。
当前硬件连接使用 `COM6`；历史文档中的 `COM5` 是同一台硬件的旧串口号，
只保留为现场证据，任何新命令统一参数化并在本次会话使用 `COM6`。

## 当前执行检查点（2026-07-25，恢复时先读）

- [x] 已建立轻量 `RuntimeHandle` 与按需加载的
  `RuntimeAcceptanceRunner` Module；正常应用启动不常驻完整 runner，
  不创建第二个 runtime、registry 或 framebuffer。
- [x] runner 统一执行完整 rounds、物理 step 计时、最低堆、GC 后漂移、
  buffer 名称/长度/身份/峰值、OOM/普通错误、observer 故障与失败回 root。
  `32,000 us` 恰好命中也失败；benchmark 预热失败不能被正式轮次掩盖。
- [x] `PerformanceMetrics` 已移除 runtime 所有权；benchmarks、diagnostics、
  runtime monitor 与 interaction tool 已迁移到同一 seam。
- [x] 七个产品压力场景已有固定 scenario 和 CPython in-memory Adapter，
  各自独立 5 轮并在 `finally` 恢复 snapshot。生产 Adapter 当前对七项能力
  全部明确 `UNAVAILABLE`，因此这些主机结果绝不等于 COM6 真机矩阵通过。
- [x] boot probe 现在强制 resident 版本等于设备 `VERSION`、root 可见，
  且根页 buffer 必须恰好为唯一 `main:8192:<valid identity>`；probe 只加载
  轻量 `runtime_handle`。
- [x] interaction tool 仍明确是窄 `screen tracer`：只测已捕获边沿到
  screen update/present；`main_dispatch=not_measured`、扫描/去抖仅检查合同。
  输入像素提交先于独立 quiet-settle/GC step，按键路径不再同步回收。
- [x] 一键 orchestrator 已有 fake-mpremote 行为测试，覆盖 stage 非零、
  reset 非零、双失败、缺脚本和每阶段 `finally reset`；旧窄 monitor 被诚实
  命名为 `runtime_target_tracer`，不冒充七场景矩阵。
- [x] pytest 只使用项目内 `.pytest_tmp/<32hex GUID>`；显式 CLI/环境
  `basetemp` 会在 pytest 删除前被拒绝。`check.ps1` 隔离并恢复
  `PYTEST_ADDOPTS`，device tool 编译门拒绝零匹配、非零退出、缺失或空产物。
- [x] 当前完整主机门：`286 passed in 11.87s`，CPython、PowerShell AST
  7/7、全部 source 与 4 个 device tool 的 MicroPython 编译通过；
  最近 10 分钟 GUID 临时目录残留 `0`。
- [ ] 真机七场景 controller 尚未实现。当前每场景还是一个总
  `RUN_ACTION`；接入 resident 前必须改为跨多个 runner step 的有界状态机，
  并保证任意跨步异常仍事务恢复，否则会把长动作错误计成一个 32 ms step。
- [ ] 新代码尚未部署到 COM6，设备仍运行部署前的 1.3.0 资产。一次 fake
  隔离失效曾误执行只读 boot probe，因设备没有新 `runtime_handle` 而明确
  `ImportError`；orchestrator 随后真实 reset 成功，未写入设备文件。
- [ ] 在项目外曾误生成一个编译临时产物
  `C:\Users\20976\AppData\Local\Temp\runtime_scenarios.mpy`。发现后按
  “项目外只读”约束未删除或继续修改；需要用户另行授权才能清理。
- [ ] 下一实施入口改为 D-2 / E-P0-4 `ReleasePlan`：先完成本地全量构建、
  受管 manifest、staging SHA、失败 rollback 和无条件 reset，之后才允许
  把新验收面写入 COM6。现有 `deploy.ps1` 仍禁止发布。
- [x] D-2 纯计划层已完成：SOURCE/MPY 对当前 59 个 source 文件产生唯一、
  不可变的 64 条分类；canonical manifest 固定 ABI/hash/size，host-only
  不进入 device identity，settings/vars 仅 seed，清理只信任校验后的旧
  managed manifest，并按 `(zone, path)` 处理 `.py/.mpy` 切换。
- [ ] D-2 事务执行层与稳定 BootSupervisor 仍未完成；在 fake-device 的
  stage/hash/trial/smoke/promote/rollback/finally-reset 矩阵通过前，
  现有 `deploy.ps1` 和任何直接写 COM6 的命令继续禁止。
- [x] D-2 fake-device 首条 happy path 已通过：`apply_release(plan, adapter)`
  只经 `run_session(operation)`；trusted manifest SHA、精确 managed cleanup、
  seed-if-absent、用户/未知文件字节保护、唯一 confirmed/boot release 以及
  每次 session 的一次 finally reset/close 均由行为测试验证。
- [x] D-2 fake-device 事务矩阵已扩展到 133 个 apply 测试、与计划层合计
  158 个定向测试：首次 SOURCE/MPY 安装、模式双向切换、计划/manifest
  结构预检、stage 首/中/末写入和精确 verify、trial smoke 的 release/version/
  mode/ABI/resident/root/唯一 8 KiB framebuffer、promote 前后故障与回执丢失
  reconciliation、durable cleanup debt、observer、KeyboardInterrupt，以及
  rollback/reset/close primary-secondary 顺序。新增：`apply_release` 拒绝
  身份不匹配的 selection ticket；25 个故障注入点 × clean/erased-retired
  两种起始状态的 50 例断电穷举（单点断电后同计划重试必须恢复到
  confirmed=新发布、retired 清空、用户字节不变、session 计数自洽）。
  当前完整主机门为 `417 passed in 14.17s`；双轴 review 由主代理完成
  （review 子代理因外部服务 429/503 不可用），本批已提交，尚未接触 COM6。
- [x] fake 已改为 selector 单一事实源 + SD A/B slot，不再用 flat dict
  冒充事务：一次 `run_session` 恰好 finally reset/close，完整成功路径固定
  三个物理 session，并在 trial 与 confirmed 两次 reset 后分别读取独立
  `ColdBootObservation`。SOURCE/MPY 旧扩展只存在于 retired slot，无法在
  active slot 形成 shadow；finalize 失败只留下可信 `SlotRef`，不再保存或
  删除裸用户路径。
- [x] selector arm/promote 的提交前、提交后应答丢失与 read-back 失败均有
  primary/secondary 行为测试；promote 正常返回也必须 read-back。retired
  slot 在删除后、清 selector 前掉电可由同计划重试幂等恢复。
- [x] selector 不变量集中在 `_validate_selector_state`：record/generation/
  retired/confirmation 类型、A/B 槽名、confirmed/trial/retired 角色互斥、
  retired 唯一、trial metadata 无 orphan、consumed/unconsumed generation
  自洽、ref 与 slot image 一致；`_cold_boot` 先校验，损坏即 fail closed，
  不猜版本。`PhaseFailure`/`ReleaseFailure` 已移入 `release_protocol`，
  Adapter 不再反向依赖编排层。
- [x] 双固定记录 selector codec 已完成（`source/bootsel.py`，MicroPython/
  CPython 双运行时同一代码）：magic/schema/generation/flags/SHA-256 摘要，
  写入永远落到非赢家记录并 read-back，任意截断/位翻转/垃圾写入只能读出
  旧有效或新有效状态，双损 fail closed；25 个 codec 行为测试覆盖逐字节
  截断、逐字节位翻转、结构垃圾、torn-write 恢复与 read-back 失败。
  `bootsel.py` 已分类为 internal managed_release 且始终 SOURCE_MODE；
  当前树两种模式计划均为 65 个资产（61 设备 + 4 host-only）。
- [ ] 当前 A/B 仍是 host in-memory Adapter，尚不能证明真实 VFS、真实
  cold boot 或 mpremote 传输。下一结构门是 stable BootSupervisor（把
  codec 接入 `_boot.py → boot.py → main.py` 启动链）+ production
  Adapter；候选必须报告实际 selector `release_id`，不得把 fake smoke
  当作设备证据。
- [ ] 首次接管 COM6 不能把“无 confirmed manifest”解释为可覆盖旧根目录。
  只能在只读 SHA 与审计基线 1.3.0 库存完全匹配后建立 legacy adoption，
  否则 fail closed；空设备 first-install 行为不授权覆盖现有未知路径。

## 0. 不可回退的设备合同

- [ ] 所有压力与交互场景不得出现 `MemoryError`；不能只检查进程没有退出。
- [ ] 绝对硬线：任意已支持场景最低空闲堆不得低于 `8 KiB`。
- [ ] 动效上线门：扩展压力矩阵最低空闲堆先达到 `12 KiB`；达不到时动效默认降级。
- [ ] 单次主循环阻塞不得超过 `32,000 us`；计时覆盖一次 runtime step 内的
  drain / dispatch / page update / settle / GC / present，不包含等待下一 tick 的休眠。
- [ ] 普通输入帧目标 `<20,000 us`，定义为“边沿已被扫描捕获到像素可见提交”；
  物理扫描/去抖延迟另报且不得超过现有 8 ms 合同。动效后续 band 帧目标 `<12,000 us`。
- [ ] 不恢复页面 SWAP、双 framebuffer、`LazyScreen`、residency 或旧页面动画引擎。
- [ ] 新动效的像素缓冲预算必须为 `0 B`；元数据目标 `<=64 B`，稳态逐帧堆分配为 `0`。
- [ ] 动效必须支持 `reduced_motion`、低堆自动降级、任意输入中断、页面离开取消和漏帧吸附终态。
- [ ] 不在按键或逐帧路径调用 `gc.collect()` / `gc.mem_free()`。
- [ ] 不破坏延迟持久化、插件隔离、单 framebuffer 和 Plot 4 ms 可中断切片。
- [ ] P0 新增常驻产品状态合计 `<=256 B`；P1 产品状态合计 `<=512 B`，
  并至少保留约 `1.4 KiB` 未承诺窄探针余量。
- [ ] 每个实现批次可独立回滚，并先加测量/故障测试再改行为。

## 1. 审计覆盖与当前基线

### 1.1 仓库清单

以下行数按非空行统计；原始行数分别为 source 8,391、tests 3,005、tools 406、
PowerShell 347。

- [x] `source/`：50 个 Python 文件，约 7,281 行。
- [x] `tests/`：30 个 Python 文件，约 2,192 行。
- [x] `tools/`：4 个 Python 文件，约 341 行。
- [x] PowerShell：2 个脚本，约 311 行。
- [x] Markdown：5 个文件，约 1,221 行。
- [x] 未发现 `CONTEXT.md` 或 `docs/adr/`；架构建议不能假称已有领域词汇或 ADR 约束。

### 1.2 主机基线

- [x] `.\check.ps1`：`150 passed in 0.30s`。
- [x] CPython 语法检查通过。
- [x] MicroPython `v1.29.0-preview`、`-march=xtensawin` 的
  `source/**/*.py` 全部 `.mpy` 编译通过；设备工具尚未纳入编译门。
- [x] 直接 `pytest` 曾因固定 `.pytest_tmp/pytest-tmp` ACL 报 17 个 setup error；
  换唯一 basetemp 后 `150 passed`。这不是源码断言失败，但属于工具可靠性问题。

### 1.3 2026-07-25 COM6 旧窄探针基线（不是完整五轮验收）

- [x] `device_runtime_monitor.py` 的旧口径通过；实际是 5 个 target 各往返一次，
  不是完整场景矩阵重复 5 轮：
  - `MemoryError=0`
  - `heap_before=19,440 B`
  - `heap_min=10,224 B`
  - `heap_delta=-368 B`
  - `blocking_max=30,834 us`
  - Functions 前进 `30,834 us`
  - Plot settle 最大 `29,458 us`
- [x] `device_interaction_acceptance.py` 的旧窄口径通过；下列输入值只计
  `renderer.present()`，不包含扫描、分派和页面更新，不能称为端到端输入帧：
  - 菜单帧 `7,049–10,564 us`
  - 五字符状态的 render/present `16,989 us`
  - `heap_free=14,496 B`
- [x] 一次性现场启动探针通过；仓库内尚无版本化 probe/orchestrator：
  - `BOOT_RUNTIME_READY True`
  - `BOOT_ROOT_VISIBLE True`
- [x] 每次 `resume run` / `resume exec` 后均已对 COM6 reset。

结论：当前版本只在旧窄探针覆盖范围内通过硬线，尚未满足本文件 E-P0 的
完整五轮发布合同。窄探针最低堆仅比 8 KiB 高 2,032 B，最慢步骤仅比
32 ms 低 1,166 us；不能用全屏多帧动画消耗这部分余量。

### 1.4 已经正确、不得推翻的策略

- [x] 只有一个 8,192 B GS4 主 framebuffer；增量提交复用 OLED 控制器 RAM。
- [x] Plot 只有一个约 1,404 B 的按需 workspace，离页释放。
- [x] Plot 每片最多 16 点 / 4 ms；最终采样和 present 已拆成相邻循环。
- [x] 键盘使用位图状态、8 项定长队列和每轮最多 5 个边沿。
- [x] Calculator 历史、Stopwatch 圈数、输入长度和性能样本已有硬上限。
- [x] 页面常驻且导航立即反馈；不应重新引入页面装卸或页面转场。
- [x] 设置与变量延迟写入，插件重载和持久化工作安排在安静期。
- [x] 高精度数有效系数限制为 30 位。

## 2. 优先级定义

- `P0`：会破坏内存安全、状态一致性或计算正确性；先修。
- `P1`：直接增加堆余量、降低卡顿或形成高 leverage 的深 Module。
- `P2`：产品体验、维护性和低风险微动效。
- `P3`：必须先原型和真机标定，默认不发布。

## 3. A — 内存使用与代码逻辑

### A-P0-1 让 `MemoryError` 真正到达资源耗尽 seam

- [ ] `source/calc/parser.py:286-292` 在 `except Exception` 中把 OOM 包成 `ParseError`。
- [ ] `source/screens/calculator.py:66-81` 又把 OOM 当普通业务错误并尝试分配错误文本。
- [ ] `source/screens/plot.py:335-347` 会把每个采样 OOM 转成字符串并继续采样。
- [ ] `source/calc/loader.py:188-208` 可能在 `registry.merge(staging)` 后 OOM，
  报告“加载失败”却把部分函数留在 live registry。
- [ ] 在这些 broad catch 前显式 `except MemoryError: raise`。
- [ ] Plot 的本地降级必须释放 workspace 后再返回固定、低分配提示。
- [ ] `FunctionRegistry.merge()` 必须事务化：预检、受控提交、任意失败 rollback，
  OOM 继续抛给 `source/main.py:672-683` 的全局恢复 seam。

验收：

- [ ] 分别在 parser compile/evaluate、callback、Plot reserve/sample/FrameBuffer、
  plugin merge/register/metadata 点注入 OOM。
- [ ] loader 失败后 registry、exports、依赖和 revision 全部保持提交前状态。
- [ ] Calculator 不创建错误弹窗；Plot 释放 buffer 且不重复失败采样。
- [ ] 全局 reset 后导航、变量、插件和待持久化状态一致。

### A-P0-2 给变量、设置、插件和输入文件建立容量合同

- [ ] `source/calc/functions.py:28-60` 可无限新增变量。
- [ ] `source/utils/storage.py:170-215` 接受未知设置字段、任意长度插件列表和任意大小 vars JSON。
- [ ] `source/main.py:315-326,672-683` 的 OOM reset 保留导致 OOM 的变量集合。
- [ ] 限制变量数量、名称长度、序列化总字节、允许的值类型；更新已有变量始终允许。
- [ ] settings 只接受白名单字段；`enabled_functions` 去重并限制数量/名称。
- [ ] 在 `json.load` 前检查文件大小；加载后检查根层数量、深度和类型。
- [ ] 限制总插件数、单插件源码大小、函数数、依赖数、依赖深度和 exports 数。
- [ ] 超额必须返回可操作的普通错误，不得静默截断或进入 OOM。

阈值先待真机标定；候选从 32/64 个变量开始，用序列化字节预算决定最终值。

### A-P0-3 修复历史结果复用的精度损失

- [ ] `source/screens/calculator.py:275-280` 把历史结果经 `_fmt()` 后插回输入，
  因此 `Display digits` 会改变下一次计算的数值，而不只是显示。
- [ ] `Number` 使用 `to_literal()` 复用完整精度；其他结果定义明确的可输入合同。
- [ ] 引入只指向最新结果的保留符号 `Ans`（不复制 Number、不作为用户持久变量），
  或明确证明直接 lossless literal 更省内存；二选一后记录决定。
- [ ] 历史表达式的“编辑/替换”和“插入”动作要区分，避免无意拼接出非法公式。

验收：

- [ ] `display_digits=1` 下计算 `1/3`，复用结果继续运算必须与原 30 位值一致。
- [ ] 旧历史、超大指数、负数、字符串/插件值和 96 字符输入上限均有测试。

### A-P0-4 保持键盘边沿事件的原子修饰键语义

- [ ] `source/input/keyboard.py:111-120` 已把 Shift 状态原子保存在事件中。
- [ ] `source/screens/calculator.py:233-238,254-256` 和
  `source/screens/plot.py:642-645` 却重新读取实时矩阵；OLED 慢帧后快速松开
  Shift 会把排队的 Shift+ENT / Shift+8 解释成另一条命令。
- [ ] 所有边沿动作只使用 `event[2]`；`kb.is_pressed()` 只服务长按和重复，
  不重新解释已经入队的事件。

预算：零新增状态、零额外绘制。

验收：

- [ ] 排队 `(ENT, shift=True)` 后即使实时 Shift 已松开，仍执行 Shift+ENT。
- [ ] Plot 排队 Shift+8 后即使 Shift 已松开，也只执行 X 缩放。
- [ ] 非 Shift RPN、普通数字和长按重复行为不回退。

### A-P0-5 角度模式切换必须使 Plot 缓存原子失效

- [ ] `source/main.py:526-530` 当前只更新 registry/settings/sidebar。
- [ ] `source/screens/plot.py:335-341` 采样时实时读角度模式；旧曲线不会失效，
  正在执行的 job 甚至可能前半 RAD、后半 DEG。
- [ ] 增加 `PlotScreen.on_angle_mode_changed()`：取消 job、释放/清除旧曲线引用、
  标记重绘，并继续沿现有 `<=4 ms` 切片重新采样。
- [ ] 不新增 framebuffer、采样数组或同步全曲线重算。

预算：至多一个 revision/int，常驻 `<32 B`。

验收：

- [ ] 已完成和采样中的 `sin(x)` 在 DEG/RAD 切换后都只显示新模式结果。
- [ ] 旧 workspace 不泄漏，输入帧 `<32 ms`，5 轮 `MemoryError=0`。

### A-P1-1 将变量持久化峰值从多棵对象树降到固定小缓冲

证据：

- `source/utils/storage.py:65-94` 深建 encoded/decoded 新树。
- `source/utils/storage.py:218-222` 永久保留 `dict(variables)` 表副本。
- `source/utils/storage.py:126-159` 写新树时还可能完整解析旧文件。

任务：

- [ ] `_vars_cache` 与 live variables 保持同一身份，不复制 dict table。
- [ ] 为平坦变量表实现流式 Number JSON encoder；不要同时保留 live、cache、
  encoded tree 和旧文件 parsed tree。
- [ ] decode 原地替换，或使用变量 schema 专用 parser。
- [ ] storage Module 记录已知有效 primary 状态，避免提交新值时再次解析整份旧文件。
- [ ] 结构性超限采用增长 backoff，避免每 2 秒重复大分配。

预估收益：64 个 Number 时减少约 1–2 KiB 常驻表，以及约 5–15 KiB 瞬时峰值。
必须保留 `.bak/.bad` 和原子提交语义。

### A-P1-2 字体缓存改为字节预算，动态文本永不进入字符串缓存

证据：

- `source/display/xglcd_font.py:9,33-36,165-203` 只按 64 项限制，不按字节。
- `source/screens/calculator.py:149-163` 的动态历史和
  `source/ui/error_popup.py:107-124` 的不同错误会使用共享字符串缓存。
- MemoryError reset 不清该缓存。

任务：

- [ ] 历史、错误、动态结果、Stopwatch 全部走 packed direct/raw 路径。
- [ ] 静态文本缓存改为 byte-budgeted LRU；统计 framebuffer、FrameBuffer 和 key 开销。
- [ ] 建立统一 `trim_caches()` / `emergency_reclaim()`，由插件重载和 OOM reset 调用。
- [ ] cache 满后不得继续为同一动态文本反复创建临时 framebuffer。

预估可回收 6–12 KiB 最坏常驻 RAM；真机必须同时确认 present 仍小于预算。

### A-P1-3 补齐 Plot 的资源申请和失败释放

- [ ] `source/screens/plot.py:349-360` 检查 `reserve_plot_workspace()` 返回值，
  失败就终止，不能继续 compile/sample。
- [ ] 新表达式编译前先清失配的旧 AST，再 GC/compile，避免旧新 AST 同存。
- [ ] `_eval()` 对 `MemoryError` 重新抛出；普通域错误只 stringify 第一次。
- [ ] `source/screens/plot.py:530-536` 的失败路径同时清 `_curve_buf`、
  `_curve_fb`、job，并释放 1,404 B workspace。
- [ ] 把约 15 键 job dict 改为 slots/fixed fields，减少临时表。

### A-P1-4 统一所有页面的派生资源生命周期

证据：

- `source/main.py:268-281` 普通离页只完整释放 Plot。
- `source/main.py:478-480` `_managed` 只登记 5 个主页面，遗漏 About、
  Letter、FunctionPicker、VariablePanel。
- `source/main.py:315-326` reset 没有先 deactivate 当前 stack。
- `source/screens/variable_panel.py:19-30` 离页后保留完整排序名称表。

任务：

- [ ] 全部常驻页面登记到 Nav。
- [ ] reset 顺序固定为 deactivate stack → 释放派生 cache → 释放 workspace →
  锁输入 → 回 root。
- [ ] 普通离页统一调用 `release_memory()`；只释放可重建派生数据，不删业务状态。
- [ ] OOM 时清或降级 `_function_reload_pending`，防止 750 ms 后重复同一失败。
- [ ] 页面 lifecycle spy 覆盖 activate/deactivate/release 的次数和顺序。

### A-P1-5 插件扫描改为一次执行、事务提交、安静期分片

证据：

- `source/calc/loader.py:220-244` 描述函数和依赖会重复执行插件源码。
- `source/screens/function_panel.py:53-76` 手动 rescan 在输入 update 中同步完成。
- 依赖链最坏接近 `O(P²)`，且会重复插件顶层副作用。

任务：

- [ ] 深化 loader 为单次 inspect/load Module；每文件每轮最多执行一次，
  同时产出 functions、dependencies、exports、错误。
- [ ] 手动 rescan 只提交 action；main 在静默期渐进执行并显示固定进度。
- [ ] 成功后移除 callback namespace 中不再需要的 loader metadata。
- [ ] staging 容器受控转移给 live registry，避免双 dict table 峰值。
- [ ] 带副作用计数器的依赖链测试：每插件只能执行一次。

### A-P2-1 降低 parser 和高精度算术的短命对象

- [ ] `source/calc/parser.py:37-108` 改为单 token lookahead 的 streaming lexer，
  将峰值从 `O(tokens + AST)` 降到 `O(AST)`。
- [ ] structural token 映射移到模块常量；字符串无转义时直接 slice，
  有转义时再建 builder。
- [ ] list function 求值避免第二个 args list。
- [ ] `source/calc/number.py:25-26` 的 digit count 不再 `str(abs(value))`；
  用整数比较/小型 10 次幂表。
- [ ] 先把 Number 的“不可变”变成真实 interface，再允许零加法和数量级压倒返回原对象。
- [ ] `source/functions/solve.py:29-33` 的 `Number(2)`、`1e-15` 预建为模块常量。

风险：parser/舍入回归面大，必须先做随机表达式差分和逐位 Number 测试。

### A-P2-2 常驻对象和键盘热路径去掉无谓对象

- [ ] 优先给 PlotScreen、CalculatorScreen、InputBox、Menu、PerformanceMetrics、
  ErrorPopup、Sidebar、Stopwatch 等内部常驻类增加 `__slots__`。
- [ ] 暂不封死公开插件 seam（FunctionRegistry/EvalContext），除非先验证兼容。
- [ ] `source/input/keyboard.py:173-197` 的 tuple-key dict 改为 30 项定长 tuple，
  通过 `row * COLS + col` 索引。
- [ ] `source/ui/inputbox.py:328-336` 的函数映射移到模块常量。
- [ ] LetterPanel 的 tuple lookup 和 `center(3)` 改为预计算 label。
- [ ] 删除 `source/screens/main_menu.py:14-18` 从未读取的 `_items` 副本。

预估常驻收益 1–4 KiB，准确数字必须在 MicroPython 真机标定。

### A-P2-3 SD 写热路径复用 1 B 缓冲

- [ ] `source/sdcard.py:150-178` 的 `spi.read(1, ...)` 全部改为复用 `tokenbuf`
  的 `readinto()`，尤其 `_wait_not_busy()`。
- [ ] fake SPI 比较命令、CS 和超时序列；真机做 5 轮 settings/vars 原子写和 busy timeout。

### A-P2-4 晚期启动恢复不能再申请第二个 8 KiB framebuffer

- [ ] `source/internal_main.py:13-30` 的 late failure 清理不完整。
- [ ] `source/recovery.py:5-20` 新建 Display 会再申请 8,192 B。
- [ ] 首选启动器持有唯一 display 并交给 app/recovery 二选一；或 recovery 使用固定行缓冲。
- [ ] 此项先做设备故障注入，确认是否真实双峰，不凭 CPython 推断直接重构。

## 4. B — 渲染、流畅度与内存安全动效

### B-P0-1 动画前先修直接文字绘制的越界写

- [ ] `source/display/ssd1322.py:14-57,681-739` 的 Viper 和 Python packed
  路径都没有 x/y 裁剪；负 y 会形成负指针偏移。
- [ ] `source/screens/plot.py:590-598` 与 `source/ui/inputbox.py:258-261`
  仍保留“从负 y 运动”的旧假设。
- [ ] 为左右上下越界实现完整裁剪，或在 interface 上明确拒绝越界并由调用者裁剪。
- [ ] 禁止通过负坐标移动文字；任何 overlay 动效只在合法坐标内画 band。

验收：host framebuffer 四周 canary，覆盖负 x/y、右/下越界、空文本、非 ASCII 和 Viper adapter。

### B-P0-2 建立统一动效降级合同

- [ ] `reduced_motion=True` 时只提交终态。
- [ ] 在静默期采样并缓存 memory-pressure 状态；按键/逐帧路径不查堆。
- [ ] 任意输入先吸附终态，再在同一输入批次处理按键。
- [ ] 页面 deactivate、OOM reset、休眠、帧超预算时取消并吸附。
- [ ] 动效不能与 Plot 最终采样、显式 GC 或 SD flush 同一步执行。

### B-P1-1 深化 `ui.motion` 为 FrameScheduler，并集中 DamageMap

当前 `source/ui/motion.py` 只有三个常量；调度知识散落在：

- `source/main.py:87-123,504-670`
- `source/ui/renderer.py:30-62`
- 每个页面的 `get_present_rows()` / `draw_present_rows()` / `mark_presented()`

任务：

- [ ] 用一个深 Module 隐藏输入立即帧、66 ms idle、40 ms signature motion、
  Sidebar 静默轮询、Stopwatch deadline、光标闪烁、取消/吸附和低堆决策。
- [ ] Renderer 内部固定大小 DamageMap 合并 full/band 请求。
- [ ] 删除 Calculator 每帧两次 11 项 tuple 和 Plot 每帧两次约 17 项 tuple。
- [ ] `None` 不再同时表示“无变化”和“必须全帧”。
- [ ] 常用 row band 使用模块常量和预建 memoryview，不逐帧建 tuple/view。

目标：interface 缩小、调度 locality 集中；测试和真机共用同一 seam。

### B-P1-2 先拿到真实流畅度，不靠装饰动画

- [ ] ignored key 不再强制 render；区分“发生输入”和“像素发生变化”。
- [ ] `source/main.py:118-123,584-589` 的 Sidebar 先在静默期 poll，
  只有角度/电压实际变化才 invalidate。
- [ ] 不在输入帧读取 ADC，不让 Sidebar 把输入 band 升级为全屏。
- [ ] Stopwatch 增加顶部 13 行提交；圈数/footer 仅在状态变化时重画。
- [ ] Stopwatch 使用固定 bytearray 构造时间文本，避免每 66 ms 创建 f-string。
- [ ] Stopwatch 可在增量路径稳定后提高到 20 fps；顶部帧目标 `<12 ms`。
- [ ] `Display.present_rows()` 复用主 memoryview，避免每帧新建 view/slice。
- [ ] Plot 离页同时 `error_popup.release_memory()`。

### B-P1-3 消除所有逐帧临时分配

- [ ] InputBox 不再每帧创建 list/string slice、`encode()` bytes 和 `(x,y)` tuple。
- [ ] 动态 footer 的 `fit_text()` 结果按状态缓存，不在动效帧重复截断。
- [ ] FunctionPicker/VariablePanel/LetterPanel 的 label 截断与编码按 revision 预计算。
- [ ] 设备 instrumentation 记录每个动效帧前后 `mem_alloc`；稳态差值必须为 0。

### B-P2-1 唯一标志性动效：Plot “公式轨道揭示”

设计方向：把设备当作单色测量仪器，不做网页式淡入或页面滑动。顶部 14 px
公式轨道像示波器遮门一样揭示，是唯一装饰性 signature。

- [ ] 25 fps，120 ms，约 4 帧。
- [ ] 整数 quadratic ease-out，进度以 1024 为满量程。
- [ ] 只更新顶部 14 行；第一帧额外更新 footer。
- [ ] 文字和光标只在最终帧出现；不从负坐标滑入。
- [ ] 新像素缓冲 `0 B`；只使用 slots 中的时间戳/阶段/高度，元数据 `<=64 B`。
- [ ] 打开可揭示；关闭、确认、取消均立即完成，不播放反向动画。
- [ ] 任意后续输入立即吸附 14 px 并继续处理，不能排队播放旧动画。
- [ ] 低于 cached motion headroom 或 reduced motion 时同帧显示终态。

真机门：

- [ ] 首帧（顶部+footer） `<20 ms`，后续顶部帧 `<12 ms`。
- [ ] 连续 500 次触发：`MemoryError=0`、buffer 集合不变、GC 后漂移 `<=512 B`。
- [ ] 动效期间压入 5 个边沿，必须 5/5 顺序处理。
- [ ] 亮度 10/50/100% 检查残影、撕裂和可读性。

### B-P2-2 功能性微动效：编辑光标闪烁

- [ ] 500 ms 亮 / 500 ms 灭，step easing；输入后常亮至少 600 ms。
- [ ] 只重画输入所在 row band，不重画 footer。
- [ ] 元数据 `<=32 B`、像素缓冲 `0 B`、逐帧分配 0。
- [ ] reduced motion、低堆、持续按键、休眠前保持常亮。
- [ ] 不得用持续 `SETTLE_MORE`，避免饿死延迟持久化和后台插件工作。

### B-P3-1 曲线扫描渐显只做 A/B 原型

- [ ] 与公式轨道二选一，不能同时作为装饰性 signature 发布。
- [ ] 15–20 fps、180–240 ms、linear，复用 `_curve_reveal` 和现有 workspace。
- [ ] 不新增 buffer 的每帧传输约 6,912 B，先验证是否值得。
- [ ] 只有无 buffer 方案不达标时才探索 512 B Plot 局部 strip；
  分配失败必须退回无动画，不能让绘图失败。
- [ ] 不探索页面滑动、全屏 fade、双 framebuffer 或 SWAP。

## 5. C — 产品工作流与竞品对标

### 5.1 已核验的竞品模式

TI-Nspire CX II/CAS 官方资料：

- Scratchpad 用于快速计算和作图，Documents 用于组织/保存工作。
- 计算历史可把表达式、子表达式或结果复制回 entry line。
- Catalog 按函数、符号、模板、library 分类。
- application menu 与 context menu 根据当前应用/选中对象给操作。
- 变量在 Calculate/Graph 间共享；不同文档问题可以隔离命名。
- 来源：
  - <https://education.ti.com/en/products/calculators/graphing-calculators/ti-nspire-cx-ii-cx-ii-cas>
  - <https://education.ti.com/-/media/files/download-center/guidebooks/ti-nspire/5,-d-,4/gb_ti-nspire_cxii_handhelds/ti-nspire_cxii-hh_guidebook_en.aspx>

HP Prime 用户手册：

- Home 与 CAS 是相似但独立的计算工作区，各自保留历史。
- Toolbox 集中数学/CAS/Catalog 命令；Vars 区分 Home/CAS/App/User 变量。
- 数学 App 统一使用 Symbolic、Plot、Numeric 三种视图。
- 历史结果可复用；应用承担特定问题类型，而不是把所有功能塞进一个全局菜单。
- 来源：
  - <https://h30434.www3.hp.com/psg/attachments/psg/palm-webossoftware/252057/1/User_Guide_EN.pdf>
  - 可访问镜像：<https://www.hpcc.org/calculators/hpprime/HP_Prime_User_Guide_EN.pdf>

适配原则：学习“短路径、历史复用、上下文动作、多表示视图”，不照搬彩屏、
触控、文档引擎或完整 CAS 的体量。

### C-P0-1 历史必须是无损工作流

- [ ] 完成 A-P0-3。
- [ ] 历史模式 footer 明示 `ENT result`、`6 edit expr`、`DEL remove` 等动作。
- [ ] 删除/清空历史要可撤销或二次确认，不再靠隐含按键知识。

### C-P0-2 模态页和当前上下文优先解释按键

- [ ] `source/main.py:504-530` 当前在页面更新前拦截 Shift+RPN 和 ANG。
- [ ] 固定优先级为：Error/Letter 等模态状态 → 当前页面上下文 →
  该上下文允许的全局动作。
- [ ] LetterPanel 的 Bk 不得再被 ANG 抢占；ErrorPopup 的 `Any key: dismiss`
  对 ANG、Shift+RPN 同样成立。
- [ ] Plot 只有编辑态允许打开字母输入；查看态 Shift+RPN 不得写入隐藏 InputBox。
- [ ] ANG 仍是普通非模态页的快捷键，不再声称“无条件全局”。

预算：条件分支或小整数状态，常驻 `0–32 B`，无逐帧分配。

### C-P0-3 LetterPanel 必须兑现完整 A–Z 和明确符号层

- [ ] `source/screens/letter_panel.py:9-15` 当前从 T 跳到 X/Y/Z，缺 U/V/W。
- [ ] Alpha 层使用 26 个非特殊物理键承载 A–Z；物理 DEL `(4,3)` 作为 Bk，
  OK 保持 Tab，ESC/Shift 保持。
- [ ] `"`、`;` 移入明确的 `SYM` 层；Shift 循环 `ABC → abc → SYM`，
  面板必须直接显示当前图例。
- [ ] footer 的 `OK save` 改为准确的 `OK insert`。

预算：替换预计算常量映射，净增目标 `<128 B`，三态总预算 `<256 B`。

验收：A–Z/a–z 全覆盖，特别测试 U/V/W；符号仍可达；Bk 不切 DEG/RAD。

### C-P0-4 所有跨面板插入都必须确认成功

- [ ] `InputBox.insert_str()` 返回 False 时，History、Catalog、Vars、Letters
  当前都会忽略并退出，形成静默数据丢失。
- [ ] 建立统一的 `try_insert()` 结果合同；失败时留在原面板或模式，
  显示模块级静态 `Input full`，不创建异常对象或动态长文本。
- [ ] A-P0-3 的 exact recall 也必须遵守同一合同。

预算：一个 flag 或静态状态引用，常驻 `<32 B`。

验收：五条插入路径都覆盖“刚好放下”和“差一字符放不下”。

### C-P1-1 根页形成可见、无冲突的轻量 Scratchpad 入口

- [ ] `source/screens/main_menu.py:13` 只显示 4 行，但实际有 5 项；
  改成 `5 × 10 px`，首帧显示包括 Settings 在内的全部入口。
- [ ] 提供可见的 `1 → Calculator` 快捷键，并转发明确允许的首键。
- [ ] 不实现“所有数字直达”：2/8 已承担菜单上下导航；也不实现
  “除 2/8 外直达”这种不可预测规则。
- [ ] 若未来探索运算符/函数 type-through，必须先重设计并显示导航规则，
  覆盖 2、8、Shift+数字和首键转发，且不能提交无意义中间全帧。
- [ ] `ESC` 空输入回根页；计算上下文、历史和变量仍复用当前常驻页面。
- [ ] 新状态目标 0；不新增文档对象或第二个 Calculator。

### C-P1-2 把 FunctionPicker 变成轻量 Catalog

- [ ] 第一阶段先把绑死 Calculator 的 Picker 改为
  `registry + target InputBox`，Calculator 与 Plot 共用同一实例。
- [ ] 选择时插入正确模板并服从 C-P0-4；保留上次 cursor，
  registry revision 变化时只夹住索引。
- [ ] Plot 编辑态用可见的 Tab 动作进入 Catalog；查看态不打开隐藏输入。
- [ ] 第二阶段才支持按首字母跳转、核心/插件分组和 arity/kind 一行提示。
- [ ] 保留当前一个 `_names` 索引，不为每函数常驻帮助文本。
- [ ] 长按帮助降到 P2；只有证明按需生成、离页释放且无常驻 metadata 才实现。
- [ ] 与 FunctionPanel 的“启用插件”明确分名：Catalog 与 Add-ons，避免两个 Functions 页面混淆。

### C-P1-3 将 Plot 做成同一 workspace 的多表示视图

不创建多个页面 framebuffer，不常驻表格数据：

- [ ] `Edit`：现有表达式编辑。
- [ ] `Plot`：现有曲线。
- [ ] P1 `Trace`：只保存像素索引和当前 x/y，复用当前 program/workspace，
  以 5×5 小十字和按需单点求值显示读数；常驻 `<=128 B`。
- [ ] Plot 使用只读变量 overlay：共享用户 vars 引用，局部 `x` 覆盖但不写回，
  禁止绘图表达式赋值，绝不复制整份变量表。
- [ ] mode 切换立即完成；Trace/Table 不申请第二条曲线 buffer。
- [ ] P2 `Window`：复用现有 InputBox 编辑 x/y 范围，一次性校验提交，
  状态 `<=256 B`。
- [ ] P3 `Table`：只流式计算屏幕可见 4–5 行，每次一个单元/`<=4 ms`，
  缓存 `<=384 B`；不得覆盖 1,404 B 曲线 workspace。

### C-P1-4 插件 reload 是可取消后台任务

- [ ] 完成 A-P1-5。
- [ ] 用户触发后立即回显固定进度/阶段；输入可取消。
- [ ] 成功/失败后显示实际加载函数数和第一个可操作错误。
- [ ] 不在 FunctionPanel 按键 handler 中同步扫描 SD。

### C-P1-5 落实键帽上的上下文导航

- [ ] 编辑框中 Shift+7=Home、Shift+1=End，复用 InputBox 已有能力。
- [ ] History/Menu/Catalog/Vars 中 Shift+9=PgUp、Shift+3=PgDn，
  每次按当前可见行数跳转。
- [ ] 这些动作由当前页面解释，不能放在全局拦截层；Plot 查看态、
  LetterPanel、ErrorPopup 保留自己的原始键语义。
- [ ] Shift+cot 的 Back 只在明确的非模态上下文启用。

预算：替换映射和分支，常驻 `<128 B`。

### C-P1-6 增加 Normal/Sci 显示模式并与内部精度分离

- [ ] 当前 `format_number()` 总是科学计数；Normal 模式先按显示精度舍入，
  再根据宽度/数量级选择普通或科学格式。
- [ ] Sci 保留固定科学计数；两种模式都不得改变历史、变量或 `Number`。
- [ ] exact history recall 永远走 A-P0-3 的无损接口，不复用显示字符串。
- [ ] 不缓存动态格式化结果，Settings 只新增一个受 schema 白名单约束的小整数。

预算：常驻 `<128 B`。

### C-P1-7 变量删除和低内存恢复必须是显式产品动作

- [ ] VariablePanel 的单击 DEL 改为复用现有长按机制；短按不删除，
  长按一次只删一项，写失败在当前页显示。
- [ ] `source/main.py:672-683` 的 OOM reset 后，根菜单显示一次模块级静态
  `Low memory: workspace closed`；下一次有效键清除。
- [ ] OOM 路径只设置 fault bit，不创建 ErrorPopup 或格式化异常字符串。

预算：变量删除零新增状态；低内存 fault bit `<16 B`。

### C-P1-8 区分 Catalog 与 Add-ons，核心算术不可关闭

- [ ] FunctionPicker 标题为 `Catalog`，插件管理页为 `Add-ons` 或
  `Function Packs`。
- [ ] Arithmetic 包含 `+ - * / ^ =`，标记 Required 并拒绝关闭；
  不让普通产品设置使计算器失去基本运算。
- [ ] 修改选择后显示 `Applying…`，只有 live registry 已事务替换才显示
  `Applied`；失败时保持旧 registry 并显示可操作状态。

预算：一个应用状态 `<32 B`，不新增列表。

### C-P2-1 改善变量管理而不复制 Prime 的完整 Vars 系统

- [ ] 显示 `name=value` 时保证选中值可查看完整 lossless literal，但按需生成后释放。
- [ ] 容量接近上限时显示 `used/limit`，并允许替换已有变量。
- [ ] `Ans` 若采用保留符号，必须从用户变量列表中区分。
- [ ] 空变量页中央保留 `No variables defined`，footer 改成
  `Create: name=value in Calc`，不重复同一状态。

### C-P2-2 快捷设置与动效可关闭

- [ ] Settings 增加 `Reduced motion`；字段进入白名单 schema。
- [ ] DEG/RAD 在非模态页保持快捷键；Error/Letter/原始键面板优先消费。
- [ ] footer/Sidebar 在角度实际变化时只提交对应 band。
- [ ] 不增加层层设置页；亮度、显示位数、reduced motion 保持一层可调。

### C-P2-3 补齐可发现性和已有设置入口

- [ ] History footer 改为 `ENT ans  4/6 expr` 等真实动作，不只写方向键。
- [ ] Settings 用已有 `sleep_timeout_s` 提供 Off/1/3/5/10m，立即生效并延迟保存；
  不可操作的 Version 行移到 About。
- [ ] Stopwatch 离页运行时在根菜单行显示固定 `*`，只在开始/暂停时改变，
  不按时间动态重建菜单字符串；复位也改为长按 DEL。
- [ ] 保留一个 boot fault bitmask，在 About/Settings 显示
  `Storage degraded` / `Functions fallback`，不保留异常文本。

### C-P3-1 多函数绘图仅在单 workspace 内探索

- [ ] 最多 2 条表达式；逐条 compile/sample 后写入同一 mono curve buffer。
- [ ] 用点线/实线区分，不申请第二像素 buffer。
- [ ] AST 不同时常驻；每条完成后释放再处理下一条。
- [ ] 若 combined autoscale 或表达式存储使压力矩阵低于 12 KiB，放弃该特性。

### 5.2 明确不照搬的竞品能力

- [x] 不做完整 CAS、3D、几何、电子表格、Notes、传感器或文档分页系统。
- [x] 不模拟 touchpad/鼠标，也不为“像电脑”增加指针。
- [x] 不把 256×64 单色 OLED 当彩色 Prime/Nspire 屏幕使用。
- [x] 不为了多 App 重建多个运行时、多个 registry 或多个 framebuffer。
- [x] 专业感来自确定性、短路径、无损结果和上下文操作，不来自慢转场。

## 6. D — 深 Module 候选

以下名称描述设计方向，不是已经批准的 interface；最终接口要在实现批次前单独评审。
七个候选都通过 deletion test：删除后，复杂性会重新散回至少三个调用者。

### D-1 RuntimeAcceptanceRunner（Strong，最先实施）

- [ ] 用显式 `RuntimeHandle` 和 `run(runtime, scenario, observer)` 统一
  benchmarks、diagnostics、两个真机工具与主机测试。
- [ ] 固定 tuple 场景同时驱动 resident runtime Adapter 和 in-memory Adapter；
  PerformanceMetrics 只记录数据，不再拥有 `(nav, root, targets)`。
- [ ] 五轮、端到端计时、heap/drift/buffer、失败回 root 和 verdict 只有一个事实来源。
- [ ] deletion test：删除后 settle、present、reset 和预算判断会重回至少四套实现。

### D-2 ReleasePlan（Strong）

- [ ] 用纯函数从 source tree 和 build mode 生成唯一不可变计划：
  local/remote、internal/SD、source/mpy、preserve、hash、cleanup ownership。
- [ ] PowerShell 只作为执行 Adapter；首次设备写前完成全量构建，设备 staging
  通过 SHA 后再激活，失败 rollback 并在 finally reset。
- [ ] 受管 manifest 只清理项目旧资产，绝不清理用户 Add-on、settings 或 vars。
- [ ] deletion test：删除后同一资产的分类会重新散到 check、deploy 和测试的多份列表。

### D-3 FunctionEnvironment（Strong）

- [ ] 保留插件作者的 `FunctionRegistry` 注册接口，外围集中单次源码执行、
  依赖图、配额、catalog、selection 和 identity-stable 事务替换。
- [ ] live SD loader 与主机 fake filesystem 是两个真实 Adapter。
- [ ] FunctionPanel 激活不得执行插件预览；失败不得部分修改 live registry。
- [ ] deletion test：删除后依赖闭包和重载顺序会重回 loader、panel、main、diagnostics。

### D-4 RuntimeKernel（Strong）

- [ ] 以 `start()/step()/reset()/run()` 隐藏页面生命周期、动作翻译、
  FrameScheduler、DamageMap、quiet work 顺序和 emergency reclaim。
- [ ] Renderer、Keyboard、Power、Memory、DurableState、FunctionEnvironment
  和时钟由装配层注入；页面动作使用模块级小整数常量。
- [ ] 保持单 framebuffer、常驻页面、Plot 4 ms 切片和立即导航；
  稳态 `step()` 不新增堆分配。
- [ ] deletion test：删除后页面 interface、调度优先级和 OOM 恢复顺序会重新散回 main/Nav/Renderer/测试。

### D-5 CalculationSession（Worth exploring）

- [ ] 提供 `execute(expr)`、`prepare(expr)` 和变量操作，隐藏 registry revision、
  compiled evaluator、dirty、历史无损值和局部变量语义。
- [ ] Plot 以预分配 local `x` 覆盖只读全局变量，不复制 dict；
  Calculator、Plot、Solve、Diagnostics 共用计算规则。
- [ ] 避免成为万能状态对象；历史 UI 和渲染状态不进入此 Module。
- [ ] deletion test：删除后上下文、缓存失效和变量可见性会重回至少四个调用者。

### D-6 DurableState（Worth exploring）

- [ ] 用一个深 Module 取代三份默认值、分散 schema、dirty、重试 deadline、
  流式 Number codec、原子 writer 与错误 generation。
- [ ] 页面常驻后移除 owner/callback 保留协议；seed settings 由同一 schema
  生成或由测试严格比对。
- [ ] 保留 `.tmp/.bak/.bad` 恢复，输入帧不写 SD，安静循环一次至多写一类。
- [ ] deletion test：删除后默认、容量、持久化和失败状态会重回 storage/main/多个页面。

### D-7 BootSupervisor（Speculative，ReleasePlan 之后）

- [ ] internal flash 上集中 hardware profile、mount policy、entry selection、
  shadow cleanup、single-display ownership 和 recovery state。
- [ ] 先用 late-failure 注入证明 `recovery.py` 的第二个 8 KiB framebuffer 峰值，
  再决定实现形状；不得凭 CPython 推断直接重构启动链。
- [ ] machine/VFS 与 in-memory hardware/filesystem 构成两个真实 Adapter。
- [ ] deletion test：删除后启动状态机和硬件知识会重回 boot/internal_main/recovery/main/deploy。

架构可视化报告：`ARCHITECTURE_REVIEW.html`。推荐顺序是
RuntimeAcceptanceRunner → ReleasePlan → FunctionEnvironment → RuntimeKernel →
CalculationSession/DurableState；BootSupervisor 只在故障注入证实后进入实现。

## 7. E — 文档、测试与设备验收

### E-P0-1 扩展真实内存增长矩阵

当前 `tools/device_runtime_monitor.py:99-113` 的 5 次循环是 5 个 target 各一次，
不是每个 target 5 轮；`device_interaction_acceptance.py` 的 rounds=5 实际是 5 个按键样本。

- [ ] 把“一轮”定义为完整场景矩阵，固定执行 5 轮：
  1. 20 条最长 Calculator 历史并遍历。
  2. 20 种错误显示/关闭。
  3. 变量新增到配额、保存、重启、删除、再新增。
  4. Plot reserve/compile/autoscale/draw/退出，含域错误。
  5. 插件启用/禁用/rescan/reload，含依赖链和失败插件。
  6. Stopwatch 运行、20 圈、滚动、返回。
  7. 所有主/辅助页面重复进入退出。
- [ ] 每个场景单独报告 `heap_min`、GC 后 drift、blocking max、buffer set、MemoryError。
- [ ] 不再用一条 `x^2` Plot 和五个页面各一次证明“全项目五轮通过”。

### E-P0-2 增加故障注入与安全测试

- [ ] OOM 注入矩阵：parser/callback/Plot/plugin/storage/font/recovery。
- [ ] 插件 merge rollback 和源码只执行一次。
- [ ] storage 原子故障矩阵、超大/深层/未知字段 JSON。
- [ ] packed text 四边 canary 和 Viper adapter。
- [ ] 全页面 lifecycle spy。
- [ ] FrameScheduler ticks wrap-around、漏帧、输入中断和 reduced motion。
- [ ] 历史 lossless reuse。

### E-P0-3 修正真机验收的计时、堆和 runtime 身份

- [ ] `device_interaction_acceptance.py` 的计时从已捕获边沿开始，覆盖
  drain/dispatch/update/settle/render/present；字段不得再把 present-only
  数据命名为 `input_batch_us`。
- [ ] 同时记录物理扫描/去抖延迟、每一步 `heap_min` 和整轮最低值，
  不能只报告结束时 `heap_free`。
- [ ] `device_runtime_monitor.py` 找不到 resident runtime 时必须发布失败；
  独立构造只允许显式 `benchmark` 模式，并在输出中标明。
- [ ] 预热后每场景与整轮 `abs(GC 后 drift) <=512 B`，替换当前 4 KiB 宽松门。
- [ ] buffer 合同比较名称、长度、对象身份和运行中瞬时 allocation 高水位，
  不能只比较最终名称集合。
- [ ] 真机执行的 `tools/device_*.py` 也通过 MicroPython 编译门。

### E-P0-4 事务化部署、受管 manifest 与无条件 reset

- [ ] 首次设备写入前完成全部 source/mpy 构建、导入检查和本地 hash。
- [ ] 写入版本化 staging；设备端 SHA 全部通过后再激活，任一步失败恢复旧版本。
- [ ] 用上次受管资产 manifest 的差集清理改名/删除模块；未知用户文件、
  Add-on、settings 和 vars 永远不在清理集合。
- [ ] `.mpy` 与 `.py` 两种模式都做冷启动、版本、resident runtime、
  root visible 和 buffer 合同 smoke。
- [ ] 所有成功、失败、异常和用户取消路径都在 `finally` reset 所选端口；
  不能依赖 `-Reset` 的成功尾路径。
- [ ] 报告实际 manifest/hash；资产数会随首次初始化状态变化，不硬编码 55/57。

在本项完成前，禁止无人值守运行现有 `deploy.ps1` 或直接 `mpremote fs cp` 热更新。

### E-P0-5 给启动、恢复和设备工具建立行为测试

- [ ] 新增版本化 boot probe 与一键验收 orchestrator；每阶段保存/恢复页面、
  输入和 Plot 状态，并在 finally reset。
- [ ] 行为测试覆盖 runtime monitor、部署失败注入、`internal_main.py`、
  `launch.py`、recovery、无 SD、损坏应用/字体和 `.py` fallback。
- [ ] 不再仅靠 `test_deploy_script.py` 的字符串搜索或手写 FakeNav 证明流程正确。
- [ ] probe 报告应用版本、resident runtime、root visible、buffer 身份与长度。

### E-P1-1 修复检查工具的临时目录合同

- [x] `pytest.ini` 的固定 `.pytest_tmp/pytest-tmp` 会遇到残留 ACL。
- [x] `check.ps1:12-14` 当前使用系统 temp；按本项目最新操作约束，
  改成项目内唯一 `.pytest_tmp/<guid>`，并安全清理自己创建的目录。
- [x] 直接 `pytest` 与 `check.ps1` 使用同一可靠策略。

完成证据（2026-07-25，批次 0）：

- `tests/conftest.py` 每个进程生成项目内 32 位 GUID basetemp；成功与故障
  probe 均只清理本次目录，残留 `0`。
- `check.ps1` 不再调用系统 temp，也不覆盖第二套 basetemp。
- `tools/device_*.py` 通过通配加入 MicroPython 编译门；当时 3/3 产物存在。
- 完整 `check.ps1`：`164 passed`，主机语法、字体和 MicroPython 编译通过。
- 后续加固复核：`228 passed in 10.63s`；显式 hostile basetemp sentinel、
  boot/version/buffer 负例、orchestrator 原生失败 Adapter、device tool
  零匹配/非零/缺失/空产物均进入行为门，PowerShell AST 7/7 通过。

### E-P1-2 修正文档和死注释漂移

- [ ] `README.md:26,285,345-365` 仍描述 `anim/`、SWAP 和旧转场。
- [ ] `TECHNICAL_GUIDE.md:18-31,179-327,810-913` 大段把旧动画/residency 当现状。
- [ ] `check.ps1:39-40` 仍称 Viper 为 transition compositor。
- [ ] `source/display/ssd1322.py:143-144,684-685,1052-1068,1187-1195`
  保留旧转场注释/接口。
- [ ] Plot、InputBox、FunctionPanel 仍有旧动画措辞。
- [ ] 先确认设备工具仍借 `transition_title` 识别页面，再决定是否改名；不能机械删除。
- [ ] USER_GUIDE 补齐无损历史动作、Catalog、Plot modes、变量配额和 reduced motion（在功能完成后）。

### E-P1-3 端口与历史结果分离

- [ ] `REFACTOR_TODO.md` 的 COM5 数据是历史证据，不覆写成 COM6。
- [ ] 新工具/文档统一参数化 `<PORT>`；当前会话记录 COM6。
- [ ] 所有 `exec/run/resume exec/resume run`、diagnostics、benchmarks、
  boot probe 和异常退出都在 orchestrator 的 finally 中 reset 所选 port。

### E-P1-4 增加发布文档自动门

- [ ] 检查 Markdown 链接、`source/version.py` 与 README/TECHNICAL_GUIDE
  的版本一致性，以及端口示例统一使用 `<PORT>`。
- [ ] 禁止把 SWAP、LazyScreen、residency、旧全页 transition 等删除架构
  重新写成当前实现。
- [ ] 资产清单和数量由 ReleasePlan manifest 生成，不复制到文档常量。

## 8. 推荐实施批次

### 批次 0：先建立证据，不改变产品行为

- [ ] 先实现 D-1 RuntimeAcceptanceRunner。
- [ ] E-P0-1、E-P0-2、E-P0-3：扩展压力矩阵、端到端计时、
  OOM/裁剪/lifecycle/精度测试。
- [ ] E-P0-5：版本化 boot probe、恢复与设备工具行为测试。
- [ ] 加 cache 字节、逐帧 allocation、场景 heap minimum 的诊断计数。
- [ ] 记录当前 COM6 场景基线。

### 批次 1：P0 正确性和状态一致性

- [ ] A-P0-1 MemoryError seam 与 plugin rollback。
- [ ] A-P0-2 容量/schema 合同。
- [ ] A-P0-3 历史无损复用。
- [ ] A-P0-4 原子 Shift 与 A-P0-5 Plot angle invalidation。
- [ ] B-P0-1 packed text 裁剪。
- [ ] C-P0-2 模态路由、C-P0-3 完整字母、C-P0-4 插入结果合同。
- [ ] 每项独立 commit、独立故障测试。

### 批次 2：先挣出堆余量

- [ ] A-P1-1 storage 峰值。
- [ ] A-P1-2 动态字体 cache。
- [ ] A-P1-3 Plot 失败释放。
- [ ] A-P1-4 页面 lifecycle。
- [ ] A-P1-5 插件单次扫描/事务提交。
- [ ] A-P2-2 的低风险 slots/map 项与 A-P2-3。

Go/no-go：扩展 5 轮矩阵最低堆若仍 `<12 KiB`，产品功能可继续，
装饰性动效保持关闭。

### 批次 3：重建帧调度 locality

- [ ] B-P1-1 FrameScheduler/DamageMap。
- [ ] B-P1-2 ignored key、Sidebar、Stopwatch band。
- [ ] B-P1-3 零逐帧分配。
- [ ] 保持导航/菜单/结果立即反馈。

### 批次 4：高 leverage 产品工作流

- [ ] C-P1-1 可见根菜单与明确的 `1 → Calculator`。
- [ ] C-P1-2 Catalog。
- [ ] C-P1-3 只发布 Edit/Plot/Trace 和只读变量 overlay；
  Window 留 P2，Table 留 P3 原型。
- [ ] C-P1-4 后台插件 reload。
- [ ] C-P1-5 上下文导航、C-P1-6 Normal/Sci。
- [ ] C-P1-7 变量安全删除/OOM 提示、C-P1-8 Catalog/Add-ons。

### 批次 5：只上线一个 signature motion

- [ ] 先实现 B-P0-2 和 reduced motion。
- [ ] B-P2-1 公式轨道揭示。
- [ ] B-P2-2 光标闪烁单独验收。
- [ ] B-P3-1 只作为 A/B 原型，不与公式轨道同时发布。

### 批次 6：文档、完整检查、COM6/目标端口发布验收

- [ ] 修正 README/TECHNICAL_GUIDE/USER_GUIDE/注释。
- [ ] 先完成 E-P1-1，确认 `check.ps1` 只使用项目内唯一 temp，再运行完整检查。
- [ ] 先完成 D-2 / E-P0-4 的事务化部署与 finally reset；旧 deploy 不得发布。
- [ ] 由一键 orchestrator 执行版本化启动探针 → reset → 完整运行时矩阵 →
  reset → 端到端输入矩阵 → reset。
- [ ] 所有门槛通过后 staging deploy；SHA-256 校验 manifest 全部资产后原子激活。
- [ ] 最终冷启动探针、版本核对和 finally reset。

## 9. 当前暂停点 / 恢复入口

- 已完成：全仓清单、主机旧基线、COM6 旧窄探针、内存、渲染、产品、
  deep Module、测试/发布、竞品官方资料复核，以及批次 0 的主机可信验收面。
- 已生成：`OPTIMIZATION_TODO.md`（长期事实源）和
  `ARCHITECTURE_REVIEW.html`（七个 deep Module 的可视化报告）。
- 已实现但尚未真机标定：`RuntimeHandle`、shared runner、七场景 host Adapter、
  boot probe、设备 tracer/interaction Adapter 与 finally-reset orchestrator。
- 仍未完成：resident 七场景有界 controller、事务部署、COM6 新基线、
  批次 1–6 产品改动与最终版本提交。
- 无人值守禁区：现有非事务 `deploy.ps1`、直接 `mpremote fs cp`、
  无 finally-reset 包装的 COM6 exec/run/resume/diagnostics/benchmarks。
- 下一步：先实现 D-2 / E-P0-4 ReleasePlan 并用 fake device Adapter 穷举
  构建/上传/hash/激活/rollback/reset；安全门通过后再 staging 部署到 COM6，
  随即记录新 runner 的真实堆与阻塞基线。

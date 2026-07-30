# 冻结核心模块分支

## 目标

使用仓库现有 MicroPython 1.29 源码构建 SCI-CALC 固件，让稳定应用字节码和不可变常量直接位于 Flash。冻结只解决代码驻留；模块全局可变对象和页面实例仍计入堆，不能把 `.mpy` 文件大小直接当作内存收益。

## 工作项

- [x] 先运行未修改的 `check.ps1` 和 COM5 统一验收，记录固件版本、分区、启动空闲堆、最大用户状态最低堆、runtime 最低堆和各阶段耗时；结束后休眠 OLED。
- [x] 确认本机现有 ESP-IDF/MicroPython 构建环境与 ESP32-WROOM-32E 分区布局。只增加构建 SCI-CALC 固件必需的最小 manifest/board 配置，不引入应用运行依赖。
- [x] 第一批只冻结稳定的显示、输入、UI、计算核心和页面实现；动态 Add-ons、设置、变量、用户文件及设备验收工具不得冻结。启动入口和版本文件仅在能减少重复副本时冻结。
- [x] 部署清单不再向 SD 卡复制与 frozen 模块同名的生产文件，防止当前 slot 路径遮蔽 `.frozen`；设备探针必须证明实际导入的是 frozen 实现。
- [x] 固件构建使用增量编译，写入仅覆盖必需固件分区，不整片擦除，不格式化内部文件系统或 SD 卡。普通用户文件部署继续使用现有快速增量路径。
- [x] 在 COM5 比较冻结前后相同最大状态与五轮场景。一次只调整一个冻结集合；无稳定收益、导致 Flash 超限或破坏动态 Add-ons 的集合立即移除。

未改代码基线（2026-07-30）：`check.ps1` 用时 `29.9 s`，`1177 passed`，CPython 与 MicroPython 1.29 编译通过。COM5 五阶段用时 `347.5 s`；设备固件为 MicroPython 1.28.0（2026-04-06），Flash 为 `4194304 B`，运行 `factory` 分区 `(offset=65536, size=2031616)`。最大用户状态五轮 `stable_min=5760 B`、观测瞬态最低 `624 B`、漂移 `-32 B`；runtime `heap_min=4240 B`、最大 step `30.232 ms`；输入到提交最大 `18.373 ms`；16/16 帧分配为 0。验收后再次确认 `OLED_SLEEP True`。

构建环境确认（2026-07-30）：ESP-IDF 5.5.2、Xtensa GCC 14.2、CMake 3.30.2、Ninja 1.12.1 与仓库 MicroPython 1.29 可构建 `ESP32_GENERIC`。Windows qstr 超长命令由仓库内 response/pipeline 适配器处理，不修改上游源码；所有可控构建、测试、临时和编译缓存集中在 `.work/`。未冻结应用的 base 镜像为 `1695072 B`，factory 余量 `336544 B`，SHA-256 `597fee033926e764975e67d2e1206b30385420e8998291cfe56b2b1888820546`；完成剩余干净对象用时 `62.399 s`，随后空增量构建用时 `7.270 s`。

base 真机对照（2026-07-30）：只写 `0x10000` 用时 `23.848 s`（实际传输 `18.0 s`），设备端哈希校验通过；SD 顶层项、slot `B`、Functions 目录，以及 `settings.json`/`vars.json` 的尺寸和 SHA-256 均保持不变。MicroPython 1.29 base 在当前 resident 页面图冷启动时于 `StopwatchScreen.__init__` 稳定触发 `MemoryError`，随后连 recovery 的 8192 B framebuffer 也无法分配；干净 REPL 手工运行同一 slot 超过 100 s 不失败，证明这是未冻结代码与 boot 后堆驻留/碎片共同造成的内存门禁失败。base 因此只保留为 A/B 构建产物，不进入五阶段支持场景。

第一批 frozen 候选（2026-07-30）：冻结生产显示、输入、UI、计算核心和十个现有页面实现；`scenario_variables` 与 `functions/_acceptance_*` 明确保留在 SD 侧。修正 manifest 后完整重生成与重链接用时 `28.547 s`，空增量用时 `6.852 s`；镜像为 `1817264 B`，factory 余量 `214352 B`，SHA-256 为 `4056c65cbc4fbb3d85c57516ee74ad7418d3990532f9dbd2fcc6d5988ebf5ff9`。只写 `0x10000` 用时 `24.589 s`（实际传输 `19.3 s`），设备端哈希验证通过。冷启动 resident runtime 成功建立，诊断点空闲堆为 `22192 B`，随后确认 OLED 已休眠；模块来源仍为 `/sd/.slots/B/...mpy`，证明现有 slot 正在遮蔽 frozen 实现，必须完成下一项部署清单裁剪后再做正式内存对照。

slot 去重与 frozen 导入证明（2026-07-30）：发布计划从 SD managed assets 移除 43 个 frozen 模块，同时保留 `calc/scenario_variables`、各 `screens/*_scenario` 和 `functions/_acceptance_*`；manifest/发布分类有逐文件同步测试。默认 in-place fast sync 用时约 `23 s`，selector generation `123`，confirmed slot `B`，新 manifest 含 43 个 owned assets。冷启动的 `sys.path` 为 `['.frozen', '/sd/.slots/B', '/lib']`；`screens.calculator`、`calc.parser`、`display.ssd1322` 和 `ui.renderer` 的 `__file__` 均为 frozen 名称，代表性 slot 影子文件为 0，动态 scenario 文件仍存在。resident 诊断点空闲堆升至 `65120 B`，OLED 随后已休眠。真机统一场景的最低堆和时延仍由下一项记录。

最终 frozen 对照（2026-07-30）：在 Calculator 局部输入帧只重画 footer 右侧计数区、统一验收仅为需要最大用户状态的阶段预载动态 scenario 后，COM5 五阶段连续通过，`MemoryError=0`、`errors=0`。最大用户状态稳态最低空闲堆由 `5760 B` 升至 `34912 B`（`+29152 B`），观测瞬态最低由 `624 B` 升至 `15024 B`（`+14400 B`），五轮漂移 `-32 B`；runtime 最低由 `4240 B` 升至 `34464 B`（`+30224 B`），最大 step 由 `30.232 ms` 降至 `29.462 ms`；输入到提交最大值由 `18.373 ms` 降至 `17.823 ms`，16/16 帧分配仍为 0。完整验收耗时 `204.4 s`，支持场景中的 Calculator、Plot、Function Panel、Stopwatch、Settings、恢复路径和动态 Add-ons 均通过。

最终固件镜像为 `1817440 B`，factory 余量 `214176 B`，SHA-256 为 `8dcefd72b92059b100c530dca8a3673e933e094249755ba9ae3c2f7636bdd9bb`；构建用时 `48.471 s`。刷写入口现只接受 frozen profile，仅写 `0x10000` factory 应用分区，总用时 `24.442 s`，其中实际传输 `19.2 s`。主机从复位到 resident runtime 可访问并完成 OLED 休眠的保守上界为 `12217 ms`。设备数据和用户文件未改动，验收结束后确认 OLED 已休眠。

## 完成条件

- [x] 冻结固件可冷启动并运行现有 Calculator、Plot、Function Panel、Stopwatch、Settings、恢复路径和用户 Add-ons。
- [x] 一个 8192 B framebuffer、0 B 新像素缓冲、逐帧 0 分配和热路径无 GC 查询/收集保持成立。
- [x] 记录冻结前后实际最低空闲堆、启动时间、最大 step、Flash 占用和固件构建/刷写耗时；只有真机证明的净收益进入下一分支。
- [x] COM5 数据和用户文件保持不变，验收结束后 OLED 已休眠。

完成后回到 [PLAN](../PLAN.md) 勾选“冻结核心模块”，再读取[页面生命周期分支](page-lifecycle.md)。

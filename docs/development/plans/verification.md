# 最终验证与说明分支

不得新建验收框架。每个小批次运行相关测试，每个阶段只运行一次 `check.ps1`；最终扩展并复用 `tools/run_device_acceptance.ps1 -Port COM5` 覆盖 frozen 固件、页面生命周期和动效门禁。

## 主机与构建

- [x] 变更前 `check.ps1` 基线通过：`1177 passed`，CPython 与 MicroPython 1.29 编译通过。
- [x] frozen manifest、固件构建、部署清单和按需页面生命周期有聚焦回归测试；动态 Add-ons 与用户数据路径保持可写。
- [x] 唯一正式部署入口收敛为 `tools/release_deploy.py` 快速增量模式；删除旧 `deploy.ps1`、ABI 探针及专属测试，聚焦回归 `28 passed`。
- [x] 删除未发布 runtime aggregate/compatibility session、builder、wrapper 和专属测试；当前 bounded/OOM/close 恢复接口回归 `56 passed`。
- [x] 删除 1.3.0 adoption、legacy transition-slot 路径及专属夹具；`--transactional` 仍支持当前固件首次 provision，用户数据与掉电恢复回归 `256 passed`。
- [x] 普通 release 排除 24 个 acceptance/diagnostic fixture/scenario 资产：source 计划 `49 -> 25`、SD managed `33 -> 9`；现有 `tools/run_device_acceptance.ps1` 按需安装并清理 23 个固定载荷及阶段文件，相关主机回归 `370 passed`。
- [x] `Nav` 普通产品接口收窄为 `open(page_id)`、`back()` 和 `current`，验收侧只适配该生命周期；删除页面预演租约及专属夹具，净变化 `+364/-2352` 行，扩展相关回归 `301 passed`、提交前边界复核 `212 passed`，10 个变更源码经 MicroPython `v1.29.0-preview` 编译通过。
- [x] 清理 `.work` 过期产物并更新 `.work/LOCATIONS.md`：删除 `legacy`、过期 pytest 会话、旧探针/日志/缓存、临时载荷和重复 MPY，共 4844 个可重建文件；最终又删除已否决热点 MPY 和无调用方的旧 pytest-cache 修复脚本。保留 frozen 固件、`ccache`、当前 MPY 与最新验收 profile，项目缓存无 `.work` 外残留，路径回归 `15 passed`。
- [x] 有边界剪枝阶段 `check.ps1` 通过：`1090 passed in 20.63s`，脚本总耗时 25.3 s，CPython 与 MicroPython 1.29 兼容检查通过。
- [x] 最终 `check.ps1` 为 `1098 passed in 25.87s`，脚本总耗时 `31.5s`；CPython、全部 pytest 和仓库 MicroPython 1.29 `mpy-cross` 检查通过。
- [x] 严格 `<40 ms` 动画候选的阶段 `check.ps1` 为 `1108 passed in 25.78s`，脚本总耗时 `31.2s`；菜单/页面动效、统一验收扩展、CPython 与 MicroPython 1.29 全源编译均通过。
- [x] 删除动画候选后的最终 `check.ps1` 为 `1098 passed in 20.42s`，脚本总耗时 `24.5s`；CPython、全部 pytest 和仓库 MicroPython 1.29 `mpy-cross` 检查通过；设备验收门槛聚焦回归另为 `19 passed`。
- [x] 自定义固件可重复增量构建：正式四模块 frozen 集合用时 `31.521s`，镜像 `1822144 B`、SHA-256 `34fcd5bf283638d5c362cc9e5b028191c54c4b9d02e89cb5b7db968b5e0748f0`、factory 余量 `209472 B`；只写 COM5 factory 分区并校验用时 `24.402s`，普通应用继续使用独立快速增量部署。

最终边界统计（排除 `.work` 和 `.git`）：202 个文件、2015946 B（1.92 MiB），相对清理前 208 个文件约 2.17 MB 减少 6 个文件和约 0.15 MB；Python/PowerShell 为 183 个文件、53695 个物理行。以页面生命周期 checkpoint `03ac077` 为固定点，本轮有边界剪枝、动效与收口共 `+2544/-7382` 行，净减 4838 行。最终主机检查和验收后 `.work` 为 10877 个当前缓存/产物、519355953 B（495.30 MiB），位置仍符合 `.work/LOCATIONS.md`。

## COM5 统一验收

40 ms 授权前的正式候选记录（2026-07-31）：MPY release ID 为 `3499d2f0fc4323a1e578dc654e277eb8efca9f599f340453ca78e27b9397ffcc`，manifest SHA-256 为 `10ea9c1386ec661630690940a1bb30c9aee74195536a32c703b81183fe45a6fd`。boot probe 通过（版本 1.4.0、resident/root ready、单一 8192 B framebuffer、104 B Plot workspace、MPY/Viper ABI 正确）；application matrix 当时在 `calculator_history` 以 `15920 B / 37724 us`、`failure_mask=20` 停止。该记录只说明旧 32 ms 门槛，不代表扩展后的完整矩阵通过。

- [x] 运行模块来自预期 frozen/SD 位置，版本 1.4.0 和 manifest 一致，用户文件未丢失，framebuffer 始终为同一个 8192 B 对象。
- [x] 执行到门禁停止位置时最低空闲堆为 `15920 B`，达到 12 KiB；`MemoryError=0`、普通错误 0，固定缓冲峰值仍为 `8296 B`。
- [x] 已保留旧门禁停止事实：当时首轮 `calculator_history` 为 `37.724 ms > 32 ms`，后续阶段未执行；没有把该次部分运行改写为完整通过。
- [x] 两项动效按降级规则均未启用，新增动画状态/像素缓冲均为 `0 B`；低内存和异常恢复到根页，验收记录明确报告实际保留项。
- [x] 本轮 COM5 门禁结束后删除临时载荷，发送 SSD1322 硬件休眠命令并确认 OLED 已关闭。

用户于 2026-07-31 将活动最大 step/动画帧门槛调整为严格小于 `40 ms`；上面的 32 ms 停止记录保留为历史。新候选必须重新运行同一统一入口，不能把旧的部分运行改写为通过。

- [x] 动画候选没有满足完整门禁：应用矩阵在五轮和动画阶段前测得 `heap_min=10736 B < 12288 B`，虽有 `blocking_max_us=38263 < 40000`、`MemoryError=0`、普通错误 0，仍按合同拒绝。
- [x] 菜单行损伤、140 ms 页面淡变、暗点单次提交和取消恢复已通过主机候选覆盖；因 COM5 前置内存失败，两项产品实现及专属测试已删除。
- [x] COM5 临时载荷已删除并让 OLED 硬件休眠；无动画正式固件和 MPY release 已恢复，README/TECHNICAL_GUIDE 与主树干在最终主机检查前同步。

首个动画候选的统一验收在 application matrix 停止：`heap_min=10736 B`、`blocking_max_us=38263`、`MemoryError=0`、普通错误 0、`failure_mask=8`，热点为既有 `error_lifecycle`。它虽通过严格 `<40 ms`，但距 12 KiB 仍缺 `1552 B`；动画交互阶段没有执行，支持载荷已清理且 OLED 已硬件休眠。全部动画状态仅约 28 B，删减单项不足以改变门禁结论，因此按动效分支删除两项产品动画并恢复无动画正式候选。

恢复后的正式 frozen 镜像为 `1822144 B`、SHA-256 `34fcd5bf283638d5c362cc9e5b028191c54c4b9d02e89cb5b7db968b5e0748f0`；factory 回刷校验通过。正式 release ID 仍为 `3499d2f0fc4323a1e578dc654e277eb8efca9f599f340453ca78e27b9397ffcc`，manifest SHA-256 为 `10ea9c1386ec661630690940a1bb30c9aee74195536a32c703b81183fe45a6fd`；用 `connect COM5 resume exec` 确认 `RUNTIME_READY True` 后让 OLED 硬件休眠。无动画正式固件只比失败候选少约 28 B 动画状态，不能补足 1552 B，故不重复运行相同完整矩阵。

## 说明与提交

- [x] 已按最终代码和真机数据同步 `README.md`、`TECHNICAL_GUIDE.md`；本批没有新增用户操作，现有 `USER_GUIDE.md` 已覆盖 Plot 进度、Settings 和无动效行为，无需改动。
- [x] 已在当前分支和主树干逐项更新复选框，没有创建其他总结、历史日志、架构报告或 ADR。
- [x] 用户允许本地 Git 提交；每个完整且已验证阶段可建立清晰检查点，但不得推送远端。
- [x] 以当前 `HEAD` 为固定点完成单代理 Standards/Spec 差异复核；唯一发现的设备交互 40 ms 合同断言已补入现有测试，复核后无未解决发现，本提交作为本地 checkpoint。

完成后回到 [PLAN](../PLAN.md) 勾选“最终验证与说明”。

## v1.7.0 稳定 MicroPython 与单固件

- [x] 主机测试证明固件 manifest 包含唯一 frozen 启动器和全部普通产品模块，普通 release 不再生成或同步 SD 槽应用资产，受保护用户路径仍永不进入删除候选；旧发布/启动/验收 slot 耦合净删除超过 15,000 行。
- [x] 稳定 MicroPython 的精确 tag/commit/Git tree、v1.28.0 `mpy-cross` 版本/ABI、固件版本和可重复构建结果均被现有检查覆盖；编译器替换会强制重生成 frozen bytecode。最终 `check.ps1` 为 `764 passed in 17.76s`，全源和设备载荷 MPY 编译通过；增量固件构建为 `27.640 s`、`1836528 B`、SHA-256 `22002f2280af961c0e2dbd0d7bddbf6c0e96307f8ca42963b02b07e3d60a63f5`。旧 preview 源码、旧编译器及可重建中间文件已删除。
- [x] COM5 只写必要的 `0x10000` 应用分区；冷启动 `sys.path=['.frozen','/lib']` 且不加载旧 slot 模块。SD 在刷写及验收清理后仍为 `100` 文件、`271089 B`，Settings/vars 未变，Add-ons 原本不存在；临时 support/stage 路径已删除。统一五阶段为最低堆 `15584 B`、普通 step `25.188 ms`、输入 `16.321 ms`、动画 `16.756 ms`、逐帧 0 分配、错误 0。
- [x] 验收载荷已删除且 OLED 已硬件休眠；README、USER_GUIDE、TECHNICAL_GUIDE 已按实际单固件安装方式更新。`.work/releases/v1.7.0/` 的最终附件为 `1836528 B`，SHA-256 `22002f2280af961c0e2dbd0d7bddbf6c0e96307f8ca42963b02b07e3d60a63f5`；以 `710ec14` 为固定点的单代理 Standards/Spec review 无未解决发现，本 release commit 建立 annotated tag `v1.7.0` 且不 push。非生成文件从清理前 208 个降至 176 个，Python/PowerShell 从约 48988 行降至 35149 行。

## v1.5.0 文档与发布

- [x] 文档已归入 `docs/`，根目录只保留 `README.md` 作为入口，权限文件改名为 `PERMISSIONS.md`，11 份文档的本地链接检查通过。
- [x] `Loading screens...` 不再绘制 `import screens.*` 详情行；版本聚焦回归通过，完整 `check.ps1` 为 `1116 passed in 18.22s`，CPython 与 MicroPython 1.29 检查通过。
- [x] 新 frozen 固件仅写入 COM5 factory 应用分区 `0x10000`，快速 MPY 增量部署及统一验收通过：`heap_min=10080 B`、普通最大 step `23.554 ms`、输入 `19.012 ms`、动画最大 `17.058 ms`、逐帧分配 `0 B`、错误 0；临时载荷已删除，用户数据未受影响，收尾 OLED 休眠。
- [x] `.work/releases/v1.5.0/` 中保留 `1823232 B` 可上传固件及 SHA-256 摘要；README/技术说明已写入实际结果，单人审查无未解决发现，本批完成本地 release commit 和 annotated tag `v1.5.0`，不 push。

## v1.6.0 横向滑动验证

- [x] 版本、Display 原地位移/裁剪、Renderer 同帧组合和 Nav 前进/返回方向有聚焦回归；旧亮度过渡实现与专属测试已删除。
- [x] 单一 8192 B framebuffer、0 B 新像素缓冲、10 个动画固定标量约 40 B、逐帧分配 0 B，以及中断/异常/休眠终态由现有回归覆盖。
- [x] 最终 `check.ps1` 为 `1125 passed`；frozen 固件与 MPY 已部署 COM5，统一五阶段为最低堆 `10528 B`、普通最大 step `26.939 ms`、输入 `16.608 ms`、动画最大 `20.030 ms`、逐帧分配 `0 B`、错误 0 和五轮堆稳定。
- [x] COM5 临时载荷已删除并硬件休眠 OLED；README、USER_GUIDE 和 TECHNICAL_GUIDE 已写入实际行为与数据，单人双轴 review 无未解决发现，本批建立本地提交且不 push。

## v1.6.0 显示与流畅度修正

- [x] About 内容边界和 24 ms 动画节拍已有红灯回归；修正后署名右边界不超过 210 px，动画至少 10 个严格单调位置并保持正确终态。
- [x] 聚焦测试和一次 `check.ps1`（`1127 passed in 19.29s`）通过；COM5 `interaction_screen_tracer` 为 80 帧、最大 `19.979 ms`、输入 `16.630 ms`、分配 0 B、错误 0，应用矩阵最低堆 `10544 B`、错误 0；两阶段均删除载荷并休眠 OLED。
- [x] README、TECHNICAL_GUIDE 与最终实测一致；单人 review 发现的定向/完整验收数据来源措辞已修正，无未解决发现，本批建立本地提交且不 push。

## 8 KiB 动画优先续行

- [x] 已把无动画发布基线 `7e4e807` 标记为本地 annotated tag `v1.4.0`，工作树在续行前干净，未 push。
- [x] 应用矩阵最低堆常量为 `8192 B`；无反馈普通 step 与动画帧严格 `<40000 us`，输入提交 `<20000 us`，`MemoryError=0`、普通错误 0、堆漂移限制不变；只有已显示固定加载条的动态 `plugin_reload` 严格 `<160000 us`，边界测试覆盖；设备交互阶段不在逐帧路径新增 `gc.mem_free()`。
- [x] 两项最小动画候选通过聚焦测试和一次阶段 `check.ps1`：`1105 passed`，全部 CPython 与 MicroPython 兼容编译通过；维持单 framebuffer、0 B 新像素缓冲、菜单 4 + Nav 3 个固定标量，并由现有五轮 tracer 对动画帧非零堆变化硬失败。
- [x] COM5 统一入口完整执行五阶段：应用 `heap_min=10576 B`、漂移 `+1536 B`、插件加载 `140261 us`；预热后普通最大 step `24817 us`、漂移 `-80 B`；输入提交 `19011 us`；动画 85 帧/最大 `17071 us`/净分配 `0 B`；Stopwatch 16 帧净分配 `0 B`，全部错误为 0。
- [x] 同一 COM5 场景复核最新 `error_lifecycle` / `plot_pipeline` 最低点，候选范围 `10224--10736 B`、无持续下降；未恢复已否决实现，最终保留两项动画并以 `10576 B` 提供高于 8 KiB 的 `2384 B` 余量。
- [x] 正式 frozen 镜像 `1823232 B`（SHA-256 `993b0a1778d140cd7ac529e1f62df6a3850912536871ef18b04b45c7afa1a6e5`）与 MPY release `c5894ef16861a51e0c4383985eab17482906f7b02c187d711edb4e91ac09e3f1` 已部署；manifest SHA-256 为 `3066a43a9eea7adcf3e53ea4837131337cc4af6e1e9cf221555692c5e86c47da`，boot probe 确认 resident/root ready；验收载荷已删除且 SSD1322 硬件休眠，部署未覆盖 Add-ons、设置、变量或未知文件。
- [x] 同步 README/TECHNICAL_GUIDE 与主树干复选框，完成单代理 review 和本地 checkpoint commit，不 push。

## v1.6.1 回归与 COM5 验收

- [x] 确定性红灯集合连续两次得到同一 11 个失败，准确覆盖：最新历史两行加两条旧历史、无固定 78 px 限制、历史选择运动、输入光标 ease-out/闪烁、Calculator→RPN→返回、Function Picker 同页/跨页运动、启动无内部详情、内置函数组摘要、Variable Panel 光标和版本 `1.6.1`；实现后同一 1.6.1 聚焦集合全部通过。
- [x] 最终 `check.ps1` 为 `1142 passed in 23.25s`，CPython 与 MicroPython 1.29 兼容编译通过；保持单 8192 B framebuffer、0 B 新像素缓冲和最坏活动动画状态约 60 B。
- [x] 定向 profile 覆盖 Calculator 历史、光标、RPN 往返、Function Picker 翻页、Variable Panel 与 Stopwatch：71 帧最大 `19.561 ms`，Stopwatch 4 帧最大 `19.872 ms`，动画分配 0 B；随后复用 `tools/run_device_acceptance.ps1 -Port COM5` 完成五阶段验收。
- [x] 最终统一验收为 `heap_min=10832 B`、预热普通最大 step `24.066 ms`、输入 `18.916 ms`、动画最大 `17.959 ms`、逐帧分配 0 B、错误 0；加载条覆盖的插件重载 `142.466 ms`，载荷已清理且 OLED 已硬件休眠。
- [x] README、USER_GUIDE、TECHNICAL_GUIDE 已同步最终行为和数据；差异审查确认无调试插桩或 `.work` 外生成物，本批建立本地 `v1.6.1` checkpoint commit，不创建 tag、不 push。
- [x] 用户随后授权发布：`check.ps1` 为 `1142 passed in 27.60s`；frozen 镜像重建为 `1832560 B`、SHA-256 `ea9f46c2f1037ffe5ba02c215dca99c7ece1acf061dd931b896dc87795c544ed`，`.work/releases/v1.6.1/` 附件与摘要已核对，并建立本地 annotated tag `v1.6.1`，不 push。

## v1.6.2 Plot 光标与发布

- [x] 首轮离页/重建测试只覆盖中断动画，未捕获用户照片中的同页 `ESC → ENT`；原 `1832576 B` / `08d4ebb2...fca5fc` 附件与本地 tag 已被新证据否决，不作为最终 v1.6.2 候选。
- [x] 照片路径连续两次稳定复现空文本和 7 px 错位；复用 Calculator 的即时恢复/激活目标重算后，照片路径与原 Nav 路径均通过，相关聚焦集合 `109 passed`，新增状态与像素缓冲 0 B。
- [x] 修订候选 `check.ps1` 为 `1144 passed in 26.45s`；COM5 照片路径严格通过，统一验收最低堆 `10608 B`、普通 step `24.122 ms`、输入 `19.102 ms`、动画 `18.129 ms`、逐帧分配 0 B、错误 0，载荷已清理且 OLED 已休眠。
- [x] 最终 v1.6.2 frozen 附件为 `1832608 B`、SHA-256 `cec1c7e4b94505ce7000491bad7c7bb6e820fb08c45b4d3bd67e72c286057b2b`，固定修正提交后的增量重建为 `4.246 s`；附件、摘要、README/USER_GUIDE/TECHNICAL_GUIDE 和单代理审查均已核对，本地 annotated tag 移至修订 release commit，不 push。
- [x] 用户真机复测否决上述附件作为最终版本：它只验证了静态恢复终点，没有让 Plot 页面经普通调度器推进输入光标。精确屏幕级红灯连续两次失败于缺少 `PlotScreen.motion_active`；复用 Calculator 的两项无状态转发后，完整 8 px 终点测试及四组相关 101 项通过。
- [x] 二次修订 `check.ps1` 为 `1145 passed in 26.49s`。COM5 逐字符定向探针完整到达 8 px，3 帧最大 `10.929 ms`、分配 0 B；统一五阶段最低堆 `10576 B`、普通 step `23.801 ms`、输入 `19.091 ms`、动画 `18.046 ms`、逐帧分配 0 B、错误 0，载荷已清理且 OLED 已休眠。
- [x] 最终 v1.6.2 frozen 附件为 `1832672 B`、SHA-256 `7f290d2e623ea596ad6ebd2a0fb6a6da212eaad88a67c95d218ec31da9acf8d9`，固定实现提交后的可复现增量重建为 `8.405 s`；附件、摘要、README/USER_GUIDE/TECHNICAL_GUIDE 和单代理审查均已核对，本地 annotated tag 移至最终 release commit，不 push。

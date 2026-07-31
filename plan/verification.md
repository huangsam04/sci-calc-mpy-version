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

最终边界统计（排除 `.work` 和 `.git`）：201 个文件、1956716 B（1.87 MiB），相对清理前 208 个文件约 2.17 MB 减少 7 个文件和约 0.21 MB；Python/PowerShell 为 183 个文件、52624 个物理行。以页面生命周期 checkpoint `03ac077` 为固定点，本轮有边界剪枝与收口共 `+1328/-7196` 行，净减 5868 行。最终主机检查后 `.work` 为 10598 个当前缓存/产物、509600027 B（485.99 MiB），位置仍符合 `.work/LOCATIONS.md`。

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

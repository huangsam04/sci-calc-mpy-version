# 最终验证与说明分支

不得新建验收框架。每个小批次运行相关测试，每个阶段只运行一次 `check.ps1`；最终扩展并复用 `tools/run_device_acceptance.ps1 -Port COM5` 覆盖 frozen 固件、页面生命周期和动效。

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
- [x] 自定义固件可重复增量构建：正式四模块 frozen 集合用时 `31.521s`，镜像 `1822144 B`、SHA-256 `34fcd5bf283638d5c362cc9e5b028191c54c4b9d02e89cb5b7db968b5e0748f0`、factory 余量 `209472 B`；只写 COM5 factory 分区并校验用时 `24.402s`，普通应用继续使用独立快速增量部署。

最终边界统计（排除 `.work` 和 `.git`）：201 个文件、1956716 B（1.87 MiB），相对清理前 208 个文件约 2.17 MB 减少 7 个文件和约 0.21 MB；Python/PowerShell 为 183 个文件、52624 个物理行。以页面生命周期 checkpoint `03ac077` 为固定点，本轮有边界剪枝与收口共 `+1328/-7196` 行，净减 5868 行。最终主机检查后 `.work` 为 10598 个当前缓存/产物、509600027 B（485.99 MiB），位置仍符合 `.work/LOCATIONS.md`。

## COM5 统一验收

最终正式候选（2026-07-31）：MPY release ID 为 `3499d2f0fc4323a1e578dc654e277eb8efca9f599f340453ca78e27b9397ffcc`，manifest SHA-256 为 `10ea9c1386ec661630690940a1bb30c9aee74195536a32c703b81183fe45a6fd`。统一验收的 boot probe 通过（版本 1.4.0、resident/root ready、单一 8192 B framebuffer、104 B Plot workspace、MPY/Viper ABI 正确）；`main` 来自 slot `main.mpy`，`performance`、`runtime_handle` 和 `version` 来自 frozen，`approot` 在根页阶段尚未加载。application matrix 在首轮 `calculator_history` 门禁停止：完成 `5` 个场景，`heap_min=15920 B >= 12288 B`、`MemoryError=0`、普通错误 `0`，但 `blocking_max_us=37724 > 32000`，因此 `rounds=0`、`failure_mask=20` 且不输出 `ACCEPTANCE_COMPLETE`。按门禁不启用两项动画；临时 support/stage 载荷均已删除，OLED 已休眠。

- [x] 运行模块来自预期 frozen/SD 位置，版本 1.4.0 和 manifest 一致，用户文件未丢失，framebuffer 始终为同一个 8192 B 对象。
- [x] 执行到门禁停止位置时最低空闲堆为 `15920 B`，达到 12 KiB；`MemoryError=0`、普通错误 0，固定缓冲峰值仍为 `8296 B`。
- [ ] 最大用户状态连续五轮及后续交互/逐帧阶段未完成：首轮 `calculator_history` 为 `37.724 ms > 32 ms`，统一入口按合同停止，不得宣称输入、动画帧或页面过渡门槛通过。
- [x] 两项动效按降级规则均未启用，新增动画状态/像素缓冲均为 `0 B`；低内存和异常恢复到根页，验收记录明确报告实际保留项。
- [x] 本轮 COM5 门禁结束后删除临时载荷，发送 SSD1322 硬件休眠命令并确认 OLED 已关闭。

## 说明与提交

- [x] 已按最终代码和真机数据同步 `README.md`、`TECHNICAL_GUIDE.md`；本批没有新增用户操作，现有 `USER_GUIDE.md` 已覆盖 Plot 进度、Settings 和无动效行为，无需改动。
- [x] 已在当前分支和主树干逐项更新复选框，没有创建其他总结、历史日志、架构报告或 ADR。
- [x] 用户允许本地 Git 提交；每个完整且已验证阶段可建立清晰检查点，但不得推送远端。

完成后回到 [PLAN](../PLAN.md) 勾选“最终验证与说明”。

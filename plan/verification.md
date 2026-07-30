# 最终验证与说明分支

不得新建验收框架。每个小批次运行相关测试，每个阶段只运行一次 `check.ps1`；最终扩展并复用 `tools/run_device_acceptance.ps1 -Port COM5` 覆盖 frozen 固件、页面生命周期和动效。

## 主机与构建

- [x] 变更前 `check.ps1` 基线通过：`1177 passed`，CPython 与 MicroPython 1.29 编译通过。
- [ ] frozen manifest、固件构建、部署清单和按需页面生命周期有聚焦回归测试；动态 Add-ons 与用户数据路径保持可写。
- [x] 唯一正式部署入口收敛为 `tools/release_deploy.py` 快速增量模式；删除旧 `deploy.ps1`、ABI 探针及专属测试，聚焦回归 `28 passed`。
- [x] 删除未发布 runtime aggregate/compatibility session、builder、wrapper 和专属测试；当前 bounded/OOM/close 恢复接口回归 `56 passed`。
- [x] 删除 1.3.0 adoption、legacy transition-slot 路径及专属夹具；`--transactional` 仍支持当前固件首次 provision，用户数据与掉电恢复回归 `256 passed`。
- [x] 普通 release 排除 24 个 acceptance/diagnostic fixture/scenario 资产：source 计划 `49 -> 25`、SD managed `33 -> 9`；现有 `tools/run_device_acceptance.ps1` 按需安装并清理 23 个固定载荷及阶段文件，相关主机回归 `370 passed`。
- [x] `Nav` 普通产品接口收窄为 `open(page_id)`、`back()` 和 `current`，验收侧只适配该生命周期；删除页面预演租约及专属夹具，净变化 `+364/-2352` 行，扩展相关回归 `301 passed`、提交前边界复核 `212 passed`，10 个变更源码经 MicroPython `v1.29.0-preview` 编译通过。
- [x] 清理 `.work` 过期产物并更新 `.work/LOCATIONS.md`：删除 `legacy`、过期 pytest 会话、旧探针/日志/缓存、临时载荷和重复 MPY，共 4844 个可重建文件；保留 frozen 固件、`ccache`、当前 MPY 与最新验收 profile。清理后为 10328 个文件、506655645 B（483.18 MiB），项目缓存无 `.work` 外残留，路径回归 `15 passed`。
- [x] 有边界剪枝阶段 `check.ps1` 通过：`1090 passed in 20.63s`，脚本总耗时 25.3 s，CPython 与 MicroPython 1.29 兼容检查通过。
- [ ] 最终 `check.ps1` 通过 CPython、全部 pytest 和仓库 MicroPython 1.29 `mpy-cross` 检查。
- [ ] 自定义固件可重复增量构建；记录干净/增量构建与刷写耗时，不把慢速全量构建塞进普通应用增量部署。

## COM5 统一验收

最新候选（2026-07-31）：快速增量部署成功，耗时 `31.355 s`，release ID `f8a6badf6054926605642a5bac6725b57e2ce876c6221a0c7b8381152687b9f9`。统一验收的 boot probe 通过（版本 1.4.0、resident/root ready、单一 8192 B framebuffer、104 B Plot workspace、MPY/Viper ABI 正确）；application matrix 因最低堆 `9888 B < 12288 B` 按门槛停止，`MemoryError=0`、普通错误 `0`、最大 step `22.774 ms`。临时 support/stage 载荷已删除，OLED 已休眠。当前输出只能把 `calculator_history phase=5` 归因给最慢 step；下一批先补齐最低堆位置数据，再选择一个热点，不得把该轮勾为通过。

- [ ] 确认运行模块来自预期 frozen/SD 位置，版本和 manifest 一致，用户文件未丢失，framebuffer 始终为同一个 8192 B 对象。
- [ ] 最大用户状态连续五轮覆盖入口、计算、函数面板、绘图、Stopwatch、Settings、辅助面板、错误恢复和快速往返；`MemoryError=0`，堆不持续下降。
- [ ] 全过程最低空闲堆不小于 12 KiB；逐帧分配为 0；普通输入到提交不超过 20 ms；step/动画帧不超过 32 ms；页面过渡为 120--160 ms。
- [ ] 验证动画中断、低内存、异常、休眠与恢复都回到正常亮度和正确页面终态；若动效按降级规则删除，验收必须明确报告实际保留项。
- [ ] 最终验收完成后发送 SSD1322 硬件休眠命令，并确认 OLED 已关闭。

## 说明与提交

- [ ] 只在代码和真机数据确定后同步 `README.md`、`TECHNICAL_GUIDE.md`，并在用户操作确有变化时同步 `USER_GUIDE.md`。
- [ ] 在当前分支和主树干逐项勾选，不创建其他总结、历史日志、架构报告或 ADR。
- [ ] 用户允许本地 Git 提交；每个完整且已验证阶段可建立清晰检查点，但不得推送远端。

完成后回到 [PLAN](../PLAN.md) 勾选“最终验证与说明”。

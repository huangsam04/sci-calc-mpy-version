# 最终验证与说明分支

不得新建验收框架。每个小批次运行相关测试，每个阶段只运行一次 `check.ps1`；最终扩展并复用 `tools/run_device_acceptance.ps1 -Port COM5` 覆盖 frozen 固件、页面生命周期和动效。

## 主机与构建

- [x] 变更前 `check.ps1` 基线通过：`1177 passed`，CPython 与 MicroPython 1.29 编译通过。
- [ ] frozen manifest、固件构建、部署清单和按需页面生命周期有聚焦回归测试；动态 Add-ons 与用户数据路径保持可写。
- [x] 唯一正式部署入口收敛为 `tools/release_deploy.py` 快速增量模式；删除旧 `deploy.ps1`、ABI 探针及专属测试，聚焦回归 `28 passed`。
- [x] 删除未发布 runtime aggregate/compatibility session、builder、wrapper 和专属测试；当前 bounded/OOM/close 恢复接口回归 `56 passed`。
- [ ] 删除 1.3.0、legacy slot 和旧 adoption 专属路径及夹具，保留首次 provision、用户数据与掉电恢复。
- [ ] 普通 release 排除 acceptance fixture/scenario；验收载荷由现有 `tools/run_device_acceptance.ps1` 临时上传和删除。
- [ ] 收窄 `Nav` 普通接口并清理 `.work` 过期产物，更新 `.work/LOCATIONS.md`；内存、时延、OOM 与数据保护合同不变。
- [ ] 最终 `check.ps1` 通过 CPython、全部 pytest 和仓库 MicroPython 1.29 `mpy-cross` 检查。
- [ ] 自定义固件可重复增量构建；记录干净/增量构建与刷写耗时，不把慢速全量构建塞进普通应用增量部署。

## COM5 统一验收

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

# 验证与说明分支

每个小批次只运行其相关测试；阶段完成运行一次 `check.ps1`。最终验收只使用既有 `tools/run_device_acceptance.ps1 -Port COM5`，不得新建验收框架。

## 验收

- [x] 变更前主机基线：`check.ps1` 于 2026-07-30 通过，`1159 passed`。
- [x] 主机：最终 `check.ps1` 为 `1177 passed`，CPython 和 MicroPython 1.29 全源编译通过。
- [x] 真机：连续五轮入口、计算、函数面板、绘图、错误恢复；`MemoryError=0`、堆漂移 `-32 B`、一个 8192 B framebuffer。
- [x] 真机：输入到可见提交最大 `19.226 ms`，单 step 最大 `30.165 ms`；动效未启用。
- [x] 真机：五阶段输出 `ACCEPTANCE_COMPLETE COM5 stages=5 animation=removed_heap_below_12k`，结束后 OLED 硬件休眠。

## 说明与提交

- [x] 只在实际代码和测量确定后同步 `README.md`、`TECHNICAL_GUIDE.md` 和 `USER_GUIDE.md`。
- [x] 更新本树的复选框；不创建额外总结或历史文件。
- [x] 用户已允许 Git 提交。每个可验证阶段可建立清晰检查点，但不推送远端。

完成后回到 [PLAN](../PLAN.md) 勾选“验证与说明”。

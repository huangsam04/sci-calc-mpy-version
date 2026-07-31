# 入口修复分支

从主菜单按 `ENT` 后，`Calculator` 和 `Function Panel` 必须进入目标页，不得留在菜单、抛出异常、重置到根页或消耗后续按键。

## 工作项

- [x] 用现有导航/页面测试或最小宿主回放复现两个入口；该命令必须能对“当前页面身份正确”给出失败结果。
- [x] 只修复经复现确认的导航映射、页面激活或资源生命周期问题。
- [x] 为两个入口保留最小回归测试，并覆盖一次返回主菜单后的再次进入。
- [x] 运行相关 `tests/test_navigation.py`、`tests/test_calculator_screen.py` 和 `tests/test_function_panel.py`。

## 完成条件

- [x] 两条入口都可从初始主菜单和一次往返后进入正确页面。
- [x] 修复不增加 framebuffer 或常驻页面副本。

完成后回到 [PLAN](../PLAN.md) 勾选“入口修复”。

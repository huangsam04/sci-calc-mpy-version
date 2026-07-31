# 页面按需加载与彻底卸载分支

## 目标与接口

把现有十页面常驻拓扑改为 `Nav` 独占的页面所有权。对主循环和菜单只保留小接口：`open(page_id, trigger_event=None)`、`back(trigger_event=None)` 和 `current`；页面导入、构造、激活、释放、模块清理和失败回滚全部留在 `Nav` 的实现内，不建立通用工厂框架或恢复 `LazyScreen`。

## 工作项

- [x] 先用现有设备探针分别测量页面模块导入、页面实例构造、激活、离页和安静回收后的堆变化，只选择稳定可复现的页面驻留热点。
- [x] 主菜单条目改存固定 `page_id`，不再持有页面对象；删除 `Nav._managed`、`resident_screens` 和 `ApplicationBinding` 对十个页面的常驻强引用，并让既有验收通过 `Nav` 接口观察行为。
- [x] 启动只构造根菜单和核心运行状态。Calculator、Plot、Function Panel、Stopwatch、Settings 及辅助面板在首次进入时按需导入并构造；同一时刻只保留根页、当前页及当前交互确实需要的父状态。
- [x] 把必须跨页面保留的 Calculator 历史、变量、Plot 表达式/视图、Stopwatch 圈数、设置和插件选择留在现有共享状态或一个紧凑固定状态表中；页面控件、绘制缓存、派生行和临时工作对象离页即失去所有强引用。
- [x] 离页依次停用、释放可重建资源、清除导航/菜单/回调引用，并清理经测量仍占堆的 frozen 页面模块运行态及包属性。冻结字节码本身留在 Flash；不得为“彻底卸载”重复复制代码。
- [x] GC 只复用已有安静后台回收点，绝不在触发导航的按键 step 或逐帧路径调用。快速连续切页若不能在此前提下稳定运行，则保留经测量必要的最小页面运行态，不放宽时延和内存合同。
- [x] 页面导入、构造或激活发生异常/OOM 时，删除部分构造状态，恢复原页或根页并保持正常亮度；后续输入仍能进入正确终态。
- [x] 修正本轮真机发现的用户可见回归：启动加载文案不再显示 `import(...)` 外层括号；Settings 修改小数位数能够写入现有设置存储，不出现 `Save Failed`，且不得削弱 `settings.json` 保护或掉电恢复。

未修改页面拓扑基线（2026-07-30）：在同一 frozen 固件的干净 VM 中复用共享 registry、设置和 104 B Plot workspace，逐页执行 import、构造、activate、deactivate、`release_memory()`、删除实例及页面模块，再安静 `gc.collect()`；一次性探针位于 `.work/`，三轮结果逐项完全一致。Calculator 从 import 到 active 使用 `7312 B`，仅删除页面模块后仍有 `6912 B` 页面运行态；Plot 使用 `2608 B`，残留 `1840 B`；Function Panel 使用 `2704 B`，其中 activate 使用 `544 B`、离页立即回收 `512 B`，删除页面模块再回收 `224 B`，残留 `1968 B`。Stopwatch 总量仅 `608 B`；Settings 的 import 夹带前序延迟回收而出现 `-656 B`，实例和 activate 各 `192 B`，不作为热点证据。因此本分支只优先处理 Calculator、Plot 和 Function Panel 的所有权/运行态；设备恢复 resident runtime 后确认 `OLED_SLEEP True`。

完成数据（2026-07-31）：真实产品路径不加载任何 scenario/fixture，预热后用 `Nav.open/back/current` 连续五轮覆盖页面 1--6 及 Calculator 下的辅助页 7--9；最大 open/back 为 `8.034 ms`，安静回收最大 `10.678 ms`，最低空闲堆 `81712 B`，起止均为 `82944 B`、漂移 `0 B`，普通异常与 `MemoryError` 均为 0。Function Panel 首次预备事务原先因未激活 toggle 槽为 `None` 失败；改为构造时建立并在 activate 时复用同一空字典后，单步真机复现和全部页面往返均通过。Function Picker 复用同一列表和原地插入排序，每 step 最多执行 1280 个固定操作；未提高原 64-step 门槛，五轮 page-round-trip 最大 step `27.929 ms`、`errors=0`、`MemoryError=0`。该旧验收载荷同时预载十个 scenario 模块时最低堆仅 `10240 B`、漂移 `-1056 B`，故其 `FAIL_HEAP|FAIL_DRIFT` 不作为动画门禁通过项，必须由验证分支既定的验收资产移出普通发布面后重新统一测量。启动加载详情已去掉外层括号；Settings 真机同值保存返回 `PASS display_digits=3 save_flags=4`，重新读取仍为 3。阶段 `check.ps1` 为 `1199 passed`，CPython 与 MicroPython 1.29 检查通过；frozen 镜像 `1818688 B`、SHA-256 `d8d5e517f3b695d82ca7d31a9d34282b7e97b48546abe72b4560ddf8dfd6a01c`，动态 release ID `82b5d8fb1cce70313b14a54d092236d17b267449c0aa1bcb6f17001a3f67a682`，所有设备操作后均确认 OLED 休眠。

## 聚焦验证

- [x] 覆盖每个主页面首次进入、返回、再次进入、状态保留、辅助面板往返、快速连续输入和注入 OOM 回滚。
- [x] 覆盖启动加载文案和 Settings 小数位保存/失败恢复，确保 frozen 核心与动态用户数据边界保持正确。
- [x] 证明离页后不存在菜单、栈、绑定或回调残留引用；安静回收后相应页面实例和可卸载模块运行态不再驻留。
- [x] 记录每页首次/再次打开的最大 step、输入到首次可见反馈、打开峰值、关闭后空闲堆和五轮堆漂移。
- [x] 最大用户状态和连续五轮操作 `MemoryError=0`；一个 framebuffer、0 B 新像素缓冲、稳态逐帧 0 分配保持成立。

## 12 KiB 续行：无损用户状态压缩

只有前两批仍未提供足够稳定堆余量时才执行。Calculator 的 20 条历史改为同一列表中的 `expression, result` 交替槽，逻辑计数、768 字符预算、顺序、召回和跨页面保留行为不变。Stopwatch 圈速列表只保存 elapsed；显示编号按 `next_number - 1 - index` 推导，20 圈上限、最新优先、滚动、重置、离页恢复和验收租约行为不变。不保留旧 tuple 状态兼容层。

- [x] 首个动态 flat 列表候选的产品、缓存、导航和场景事务聚焦集 `237 passed`；固件为 `1822192 B`，SHA-256 `76f44e92102570ec168029319b92df117775ffb0c931b01652f3dc897cdb4957`。COM5 `heap_min` 从 `15920 B` 小幅升至 `16160 B`，但同一 `calculator_history` 阻塞 step 从 `37.761 ms` 恶化至 `59.946 ms`，`MemoryError=0`、普通错误 0；两次头部 `insert()` 与动态扩容实现被否决，不作为最终表示。
- [x] 第二个候选改为一次性 40 槽表，并用两个既有状态表标量保存逻辑条目数和 768 字符总量；每条历史不再分配 tuple、扩容列表或重扫全部表达式。真实调用方聚焦集 `237 passed`；固件 `1822256 B`、SHA-256 `d858f28f590f31dafa9d71d87ddf32a069d3257b29d6382c61dd17fa4234faae`。COM5 已越过原 `calculator_history` 阻塞并从 5 个场景推进到 10 个场景，随后在 `error_lifecycle` 测得 `heap_min=10496 B`、`blocking_max_us=61728`、`MemoryError=0`、普通错误 0。该候选尚未接受；只允许与新暴露热点对应的 Calculator 直接求值再组合测量一次，仍不过门则两项一起删除。

- [x] Calculator 产品、缓存、导航、detach/rebuild、OOM 回滚和 20 条场景租约均先以行为测试锁定；动态 flat 与固定 40 槽两个实现都未保留。Stopwatch 位于当前失败的 `error_lifecycle` 之后，压缩圈速不可能改变当前最低堆或最大 step，故不创建无收益实现或专属测试。
- [x] Calculator 动态 flat 单批与固定 flat 单批已分别测量，没有同时维护；动态实现为 `16160 B / 59.946 ms`，固定实现使场景继续前进但在下一热点为 `10496 B / 61.728 ms`，均未达到联合门禁。
- [x] 两批期间现有 Calculator scenario/acceptance 曾直接适配产品生命周期并覆盖恢复；候选否决后实现、适配断言和内部形状测试已一起删除，原场景租约回归通过。
- [x] Calculator 固定历史与直接求值组合最终为 `11136 B / 56.665 ms`，`MemoryError=0`、普通错误 0、固定缓冲峰值 `8296 B`；未达到 12 KiB/32 ms，故恢复正式 tuple 历史，不进入 Stopwatch 或动画实现。

完成后回到 [PLAN](../PLAN.md) 勾选“页面按需加载与彻底卸载”，再读取[动效分支](motion.md)。

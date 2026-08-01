# SCI-CALC 权限牢笼

本文限定自动执行 `PLAN.md` 所需的最大权限范围。用户授予的更宽泛系统权限不自动扩大本项目的操作范围；超出本文的动作必须重新取得明确授权。

## 本地文件与进程

- 可读取、创建、修改和删除 `mp_version/` 内与当前计划直接相关的源码、测试、现有文档及本任务生成物。必须保留工作树中已有修改、删除记录和无法确认归属的文件。
- 所有可控的固件、MPY、pytest、ccache、探针、日志和临时产物只能写入 `mp_version/.work/`；可删除其中由本任务生成且已确认过期的内容。
- 可读取并执行工作区现有 `.venv`、`.tools`、MicroPython、ESP-IDF 和 `.espressif` 工具链。为 `PLAN.md` 的稳定 MicroPython 迁移，可用官方 `https://github.com/micropython/micropython.git` 查询 tag/commit、下载一个锁定的稳定 Release，并替换同一工作区中明确命名的 `../micropython/` 源码树；只可保留或增加 SCI-CALC 构建必需的最小 board/startup 适配。不得修改 ESP-IDF、安装依赖或向其他项目写入文件。
- 可运行当前计划要求的主机测试、`check.ps1`、固件构建、发布工具和设备验收工具。不得启动与 SCI-CALC 无关的服务、GUI、外部通信或后台任务。

## Git

- 可检查 `status`、`diff`、历史和当前分支；可 `add` 并创建仅包含已审查当前成果的本地 checkpoint commit；可按用户明确要求为已验证发布检查点创建本地 annotated tag。
- 不得 `push`、`reset`、`checkout`、`restore`、`clean`、rebase、强制更新引用、删除分支或改写历史。
- 不得恢复旧 TODO、`ARCHITECTURE_REVIEW.html`、已删除计划或已淘汰实现，不得覆盖用户未提交成果。

## COM5 与设备

- 唯一获准设备为 COM5 的 SCI-CALC。可连接、进入/退出 REPL、复位、读取诊断信息、上传和删除本次验收的临时载荷，并使用现有快速增量发布入口部署当前应用。
- 可用现有 flasher 只写 factory 应用分区 `0x10000` 中的 `micropython.bin`，并校验设备端镜像。不得写 bootloader、partition table、NVS 或其他分区。
- 不得整片擦除、擦除分区、格式化 Flash/SD、重建文件系统或连接其他串口设备。
- 必须保护 `/sd/Add-ons/`、`/sd/settings.json`、`/sd/vars.json`、未知用户文件及其他不可复现数据；不得以部署或清理名义覆盖或删除它们。
- 设备操作只限 `PLAN.md` 的构建、部署、测量、恢复验证和 OLED 休眠。每次设备阶段结束必须删除临时验收载荷并让 SSD1322 进入硬件休眠。

## 实现边界

- 只按 `PLAN.md` 及其当前分支顺序工作；一次处理一个有测量证据的候选。不得引入新依赖、Subagent、验收框架、多套并行实现或无关重构。
- 必须保持单一 8192 B framebuffer、0 B 新像素缓冲、用户数据保护、OOM 恢复和活动门槛：最低空闲堆不小于 8 KiB、无加载反馈的普通 step 和所有动画帧严格 `<40 ms`、输入到可见提交 `<20 ms`、`MemoryError=0`、普通错误 0、堆无持续下降。动态 Add-ons 的不可切分源码编译只有在 Function Panel 已显示 `Loading add-ons` 条时可使用严格 `<160 ms` 上限，边界值仍失败。8--12 KiB 是可接受但仍应优化的区间，不得据此提前停止动画验收；候选低于 8 KiB 或违反其他门槛时只能优化、简化或删除，不得再降低标准。
- 可删除本任务创建且被真机数据否决的候选实现、专属测试和生成物；不得删除用户源码、未提交成果或无法确认可重建的内容。
- 除上述官方 MicroPython Git/tag 发现与源码下载外，不得访问网络、云服务、账户、消息系统或仓库外设备。不得更换 ESP-IDF 或下载无关依赖。

若操作目标、归属或可恢复性不明确，停止该动作并继续其他安全工作；只有确实无法推进时才向用户请求扩大权限。

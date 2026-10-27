# DataMonitor 开发者笔记

> 面向**维护者 / 二次开发者**。使用者请看 [README](../README.md)。
> 本文件承接原 README 的技术细节：目录结构、数据流、配置全表、设计说明、界面设计与已知限制。

---

## 一、目录结构

```
DataMonitor/
├─ main.py               # 命令行入口：加载配置、启动引擎、等待退出
├─ gui.py                # 图形界面入口（Tkinter）
├─ appicon.py            # 窗口图标：加载 assets/ 下的图标并应用到 Tk 窗口
├─ monitor.py            # 监控引擎：可启停的取数—触发—推送循环 + 日志配置
├─ collector.py          # 取数：新浪主源 + 跨源校验
├─ trigger.py            # 触发判定与状态持久化
├─ trading_calendar.py   # 交易日历：交易日 / 日盘 / 夜盘 判定
├─ notifier.py           # 通知：QQ（OneBot v11）推送与消息模板
├─ qq_bot.py             # QQ 指令服务：接收 NapCat 上报，回复「#查询金价」
├─ test.py               # 单元测试（38 组）
├─ config.json           # 全部配置（应用信息/数据源/告警/调度/通知）
├─ state.json            # 运行时生成的触发状态
├─ monitor.log           # 运行时生成的轮转日志
├─ assets/               # 窗口图标（appicon.ico / .png / .gif + 1024px 母版）
├─ tools/                # 一次性工具（make_appicon.py 像素画转图标）
├─ docs/                 # 文档（本文件、NapCat 部署教程）
└─ backup/               # 历史遗留文件归档（Selenium 时代的产物）
```

---

## 二、数据流

命令行（`main.py`）与图形界面（`gui.py`）**共用同一引擎** `MonitorEngine`，行为完全一致：

```
MonitorEngine（后台线程）
   └─ collector.fetch_all()        取数 + 跨源校验
        └─ trigger.evaluate()      判定是否触发（含冷却/日切）
             ├─ trigger.save_state()   持久化状态
             └─ notifier.notify()      推送告警  ──→ QQ
   └─ next_interval()               监控时段内取配置间隔，否则睡到下一个开盘

GUI 侧：引擎回调 → 线程安全队列 → 界面 after() 定时刷新（不跨线程操作控件）
```

启动时额外调用一次 `notifier.notify_startup()` 推送启动摘要（命令行入口启动即推送；图形界面在打开监控开关后推送）。

**指令查询**走独立通道，完全不经过 `MonitorEngine`：

```
NapCat --HTTP POST（127.0.0.1:3001）--> qq_bot.QueryServer
   ├─ collector.fetch_all()        实时取数（与监控同一套取数与交叉校验）
   ├─ trigger.load_state()         只读取 state.json 的当日极值，不写入
   └─ notifier.push_qq_to()        回复到事件所在群 / 好友
```

查询随时可用，与监控开关状态无关；代价是每次查询单独取一次数（非交易时段返回上一交易日收盘价，消息中会标注）。

---

## 三、配置全表（`config.json`）

### `app` — 应用信息
| 字段 | 默认 | 说明 |
|---|---|---|
| `name` | `DataMonitor` | 应用名，用于界面标题与启动日志 |
| `version` | `v0.0.0` | **版本号唯一来源**，代码运行时经 `monitor.app_info()` 读取 |

### `sources` — 数据源
| 字段 | 说明 |
|---|---|
| `primary.code` | 主源代码，默认 `gds_AUTD`（黄金延期，元/克） |
| `reference` | 校验源：伦敦金 `hf_XAU` ÷ 31.1035 × `USDCNY` 推算国内理论价 |
| `fallback` | 金投网兜底（默认 `enabled: false`） |

### `cross_check` — 交叉校验
| 字段 | 说明 |
|---|---|
| `enabled` | 是否启用校验 |
| `tolerance_pct` | 主源与推算价允许偏差，默认 `2.5`（%），超出则标记「数据源不一致」 |

### `alert` — 告警
| 字段 | 默认 | 说明 |
|---|---|---|
| `mode` | `high_low` | 创当日新高/新低即告警 |
| `cooldown_seconds` | `300` | 同一方向告警冷却时间 |
| `price_tolerance` | `0.01` | 价格比较容差（元/克） |
| `buy_below` / `sell_above` | `null` | 可选：自定义买入/卖出关键价位 |

### `schedule` — 调度与交易日历
| 字段 | 默认 | 说明 |
|---|---|---|
| `trading_interval_seconds` | `15` | 监控时段内的取数间隔 |
| `day_sessions` | `09:00-11:30`、`13:30-15:30` | 日盘时段，也是唯一的监控窗口 |
| `night_session` | `20:00-02:30` | 夜盘时段（归下一交易日）；**仅用于日历判定与状态展示，不监控** |
| `use_cn_calendar` | `true` | 使用 `chinese_calendar` 自动识别节假日 |
| `holidays` | `[]` | 自定义节假日（库未覆盖年份时使用），如 `["2027-01-01"]` |

### `notify.qq` — 通知（QQ）
| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否启用 QQ 通道 |
| `api_base` | `http://127.0.0.1:3000` | OneBot v11 HTTP 服务地址 |
| `access_token` | — | **须与 NapCat 中配置的 token 完全一致** |
| `timeout_seconds` | `10` | 请求超时 |
| `rate_limit_per_minute` | `20` | 本地限流 |
| `targets.groups` | `[]` | 目标 QQ 群号列表 |
| `targets.users` | `[]` | 目标好友 QQ 号列表 |

### `query` — QQ 指令查询
| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 是否启动查询服务；关闭后发指令无响应 |
| `listen_host` | `127.0.0.1` | 监听地址，**保持回环地址**，勿改 `0.0.0.0` |
| `listen_port` | `3001` | 监听端口，须与 NapCat「HTTP 上报」的 URL 一致，且不能与 `api_base` 的 3000 冲突 |
| `commands` | `["#查询金价"]` | 触发回复的指令列表；匹配前会剥离 CQ 码并把全角 `＃` 归一 |
| `access_token` | `""` | 校验上报请求的 `Authorization: Bearer`，留空即不校验（非人员权限） |
| `reply_cooldown_seconds` | `10` | 同一提问者重复查询的最小间隔，防刷屏；`0` 关闭 |

无人员 / 群白名单：能给该 QQ 号发消息的人都能查询。服务只监听 `127.0.0.1`，外网无法直接调用。

### `runtime`
`state_file` / `log_file`，均为相对项目根目录的路径。

---

## 四、关键设计说明

**1. 交易日历（`trading_calendar.py`）**
依据上金所官方交易时间：日盘 `09:00-11:30`、`13:30-15:30`；夜盘 `20:00-次日 02:30`，且**夜盘归属下一个交易日**。
- `20:00` 之后开市条件：当日为交易日 **且** 次日不是法定节假日（故节前无夜市）
- `02:30` 之前开市条件：前一日为交易日 **且** 当日不是法定节假日（故周五夜盘延续至周六凌晨）
- 交易日 = 周一至周五且非法定节假日（**调休上班的周末交易所仍休市**）
- 节假日来源：优先 `chinese_calendar`（覆盖 2004–2026），超出范围自动回退到 `schedule.holidays`

**2. 触发逻辑（`trigger.py`）**

以**每轮取到的最新价自行累计当日高/低**作为判定依据，不采信数据源的 `high` / `low` 字段——新浪 `gds_AUTD` 的「最高」曾长期停留在 `890.00` 的陈旧值，导致「最新价 862 却报创当日新高」的误报。

- 当前价 > 已记录高点 + `price_tolerance` → 创新高；反向同理。两方向独立冷却 `cooldown_seconds`，冷却期内极值继续更新但不推送。
- 日切按自然日重置，基准取当前价，因此次日跳空不会被误判为瞬间新高。
- 日盘开盘后 5 分钟内的首轮取数，额外产生一条「今日开盘」播报；盘中启动不补发。
- `cross_ok is False`（跨源偏差过大）时跳过整轮判定，不更新极值、不播报。

**3. 通知抽象（`notifier.py`）**
推送层与业务逻辑解耦，`notify()` / `notify_opening()` / `notify_startup()` 统一走分发入口。消息渲染支持 `text` 与 `markdown` 两种样式，便于日后接入其它渠道。

**4. 容错**
取数失败重试 1 次；仍失败则该轮跳过、不参与触发判定（**绝不以 `None` 价格触发告警**）；全流程异常兜底，不使进程崩溃。

**5. 指令查询（`qq_bot.py`）**

NapCat 以「HTTP 上报」把消息事件 POST 到本模块起的本地服务（默认 `127.0.0.1:3001`），识别到指令后实时取数并回复到事件所在会话。四项不可省的实现约束：

- **先回 204 再异步处理**：取数需 1~3 秒，同步等待会触发 NapCat 上报超时重试，导致同一条指令被重复回复。回执与业务处理分离在 `_on_post` / `_handle` 两个方法。
- **阈值只有一处口径**：`trigger.thresholds()` 与 `trigger.evaluate()` 共用 `_break_price()`，避免「显示涨破 908.21、实际 908.30 才响」这类展示与判定漂移。
- **阈值按日校验**：`state.json` 的 `date` 不是当日时，旧极值会在下一轮日切被重置，因此消息只提示「今日尚未建立基准」，不展示过期数字。
- **查询回复不占告警限流桶**：`push_qq_to()` 直接走 `_post_onebot()`，不经 `_dispatch()` 的 `rate_limit_per_minute`，否则群友连续查询会把真正的新高/新低告警挤掉。

查不到的两种失败都如实说明：取数失败回「取数失败，请稍后重试」，不编造价格。

---

## 五、图形界面（`gui.py`）

基于 **Tkinter**（Python 内置，零额外依赖）。

### 5.1 布局

| 区域 | 内容 |
|---|---|
| **顶部状态栏** | 应用名与版本 · 状态徽标（绿点「监控中」/ 灰点「已暂停」）· 监控开关 |
| **左侧指标区** | 指标卡：最新价、涨跌幅、监控最高、监控最低；下方「数据质量」卡：数据源、交叉校验、最近更新、下次唤醒倒计时 |
| **右侧日志区** | 运行日志，按级别着色（INFO 常规 / WARNING 琥珀 / ERROR 红），自动换行（续行缩进），自动滚动，最多保留 500 行 |
| **底部状态栏** | 今日交易日 · 市场状态 · 轮询状态 · QQ 通道状态 · 查询服务状态 |

### 5.2 显示字段与数据来源

| 界面字段 | 数据来源 |
|---|---|
| 最新价 / 涨跌幅 / 监控最高 / 监控最低 | `collector.fetch_all()` 的 `price` / `change_pct`，高/低取 `trigger` 回写的 `range_high` / `range_low`（**不使用数据源的 `high` / `low`**，该字段可能失真） |
| 涨跌幅颜色 | **红涨绿跌**（国内习惯）：`> 0` 红、`< 0` 绿、`= 0` 常规灰 |
| 数据源 / 交叉校验 | `data["source"]`、`cross_ok` 与 `reference_price` |
| 最近更新 | 收到数据回调时的本机时间 |
| 下次唤醒 | 引擎 `next_poll_at` 倒计时（监控时段内显示「Ns 后」，暂停时显示「距开盘 N 分」） |
| 市场状态 / 轮询状态 | `trading_calendar.is_monitor_window()` + 引擎 `current_interval` |
| QQ 通道状态 | `notifier.channel_status()` |
| 查询服务状态 | `QueryServer.enabled` / `is_running` / `port`：未启用 / 端口监听中 / 启动失败 |

### 5.3 交互方式

- **启动状态**：界面启动时监控**默认暂停**——不自动取数、不推送、不发启动通知，需手动打开开关。避免程序一打开就产生网络请求与 QQ 消息。
- **查询服务**：只要 `query.enabled` 为真，**随窗口启动**，与监控开关无关（关掉监控仍可查价）。它只在收到上报时才取数，本身不发任何主动请求；关闭窗口时随引擎一起停止。
- **监控开关**：开启 → `MonitorEngine.start()`，立即取数并推送一条启动摘要；关闭 → `MonitorEngine.stop()`，停止取数与推送。
- **状态一致性**：开关是**唯一状态来源**，徽标只做呈现。切换后按引擎**实际运行态**（`is_running`）立即刷新，不等待轮询周期；引擎线程自然结束（如异常）时亦由状态回调同步。
- **关闭窗口**：先 `stop()` 优雅停止线程，再销毁窗口，避免进程残留。
- **日志**：自动换行（`wrap="word"`，续行缩进到消息起始列），自动滚动到底部，超过 500 行自动裁剪旧行。

### 5.4 状态机

| 状态 | 进入条件 | 界面表现 |
|---|---|---|
| `STOPPED`（已暂停） | **界面启动时的默认状态** / 关闭开关 | 徽标灰点「已暂停」，指标区保留最后一次值（首次启动为 `--`），下次轮询显示 `--` |
| `RUNNING`（监控中） | 打开开关 | 徽标绿点「监控中」，指标与日志持续刷新，倒计时运行 |
| 异常态 | 取数 / 推送出错 | 状态保持 `RUNNING`，错误以 ERROR 级写入日志面板与 `monitor.log`，下一轮自动重试 |

初始状态为 `STOPPED`，状态迁移：`STOPPED --打开开关--> RUNNING --关闭开关--> STOPPED`。

### 5.5 与文档同步的实现要求

界面属**对外可见的设计契约**，以下变更**必须同步更新本节**：

1. **布局** —— 新增 / 删除 / 移动区域或控件；
2. **字段** —— 指标卡、数据质量、状态栏显示项的增删，或数据来源变化；
3. **交互** —— 开关语义、状态刷新时机、窗口关闭行为等变更；
4. **状态机** —— 新增状态或迁移条件变化；
5. **图标** —— 替换窗口图标时须经 `tools/make_appicon.py` 重新生成 `assets/`，并同步本节 5.6。

> **同步范围仅限设计说明**。运行时的数据更新与状态变更**不写入文档**，只写入 `monitor.log` 与界面日志面板，避免技术文档被运行数据污染。

### 5.6 窗口图标

Tkinter 默认图标是 Tk 的羽毛，与应用无关。界面图标由 `appicon.py` 从 `assets/` 加载，三种格式互为降级：

| 文件 | 尺寸 | 用途 |
|---|---|---|
| `appicon.ico` | 9 档（16~256px） | **Windows 首选**：标题栏 / 任务栏 / Alt-Tab 由系统按 DPI 取最合适的一档 |
| `appicon.png` | 256px | Tk 8.6+ 的 `iconphoto`，跨平台（macOS / Linux） |
| `appicon.gif` | 64px | 旧版 Tk 无 PNG 支持时的兜底 |
| `appicon@1024.png` | 1024px | 母版，供再生成或打包使用 |

- **加载顺序**：`iconphoto(png)` → `iconbitmap(default=ico)` → `iconbitmap(ico)`。后设置者覆盖前者，因此 Windows 最终使用多尺寸 ICO，而不是把单张位图交给系统缩放。
- **必须在窗口显示前设置**（`main()` 中紧跟 `tk.Tk()`），否则标题栏会先闪一下默认羽毛图标。
- **三级降级**：任一资源缺失或加载失败都不影响启动，仅在日志中记录。
- **重新生成**（源图为 16px 方块构成的 64×64 像素画）：

```bash
pip install Pillow                 # 仅生成图标时需要，运行时不依赖
python tools/make_appicon.py [源图路径]
```

---

## 六、已知限制与注意事项

1. **数据源较窄**：实测新浪仅 `gds_AUTD` 有效（`gds_au9999` 等返回空），腾讯接口不支持该代码，金投网为 JS 动态加载。因此校验采用「合理性区间」而非同标的精确比对。
2. **节假日库年限**：`chinese_calendar` 覆盖至 **2026 年**，2027 年起需升级该库或改用 `schedule.holidays` 自定义维护。
3. **第三方协议风险**：QQ 通道依赖第三方协议端，**违反 QQ 用户协议**，存在封号/掉线风险。务必使用小号、HTTP 服务仅监听 `127.0.0.1`、设置强 `access_token`、保持低发送频率。
4. **凭据为明文**：`config.json` 中的 `access_token` 为明文存储，注意不要外传该文件。
5. **免费接口礼仪**：公共行情接口属他人服务，个人低频使用无碍，**勿高频轮询或用于商业用途**。
6. **图形界面运行环境**：`gui.py` 基于 Tkinter，需在有图形会话（桌面环境）的机器上运行；服务器 / 无头环境请使用命令行 `main.py`。
7. **查询服务静默失效**：指令查询依赖 NapCat 侧「HTTP 上报」把事件推过来，该通道未开启或 `reportSelfMessage` 未关闭时，发指令不会有任何响应或报错。排查顺序：看界面底部「查询服务」状态 → 看 `monitor.log` 有无「查询服务已启动」→ 确认 NapCat 上报 URL 与 `query.listen_port` 一致。

---

## 七、版本号约定

- 版本号**唯一来源**：`config.json` → `app.version`。
- 代码在运行时统一通过 `monitor.app_info(config)` 读取（启动日志、启动通知、界面标题均取自此处），避免散落硬编码。
- 修改版本时只需改 `config.json` 一处；README 与本文档中的版本号请手动同步。

---

## 八、运行与验证

```bash
# 安装依赖（项目已含 .venv）
.venv/Scripts/python.exe -m pip install requests chinesecalendar

# 自测推送链路
.venv/Scripts/python.exe notifier.py

# 运行监控（命令行）
.venv/Scripts/python.exe main.py

# 运行监控（图形界面；启动后监控默认暂停，需手动打开右上角开关）
.venv/Scripts/python.exe gui.py

# 运行测试（38 组）
.venv/Scripts/python.exe test.py
```

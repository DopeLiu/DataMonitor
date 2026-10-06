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
├─ notifier.py           # 通知：QQ（OneBot v11）推送
├─ test.py               # 单元测试（23 组）
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
   └─ next_interval()               按市场状态决定下次轮询间隔

GUI 侧：引擎回调 → 线程安全队列 → 界面 after() 定时刷新（不跨线程操作控件）
```

启动时额外调用一次 `notifier.notify_startup()` 推送启动摘要（命令行入口启动即推送；图形界面在打开监控开关后推送）。

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
| `trading_interval_seconds` | `15` | 开市时的轮询间隔 |
| `off_hours_interval_seconds` | `180` | 交易日的非交易时段 |
| `closed_interval_seconds` | `1800` | 休市日（周末/节假日） |
| `day_sessions` | `09:00-11:30`、`13:30-15:30` | 日盘时段 |
| `night_session` | `20:00-02:30` | 夜盘时段（归下一交易日） |
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
以**交易所给出的当日最高/最低**是否刷新作为判定依据（权威且无需自行累计），并做价格精度（`round(x, 2)`）、方向独立冷却与跨日重置。

**3. 通知抽象（`notifier.py`）**
推送层与业务逻辑解耦，`notify()` / `notify_startup()` 统一走分发入口。消息渲染支持 `text` 与 `markdown` 两种样式，便于日后接入其它渠道。

**4. 容错**
取数失败重试 1 次；仍失败则该轮跳过、不参与触发判定（**绝不以 `None` 价格触发告警**）；全流程异常兜底，不使进程崩溃。

---

## 五、图形界面（`gui.py`）

基于 **Tkinter**（Python 内置，零额外依赖）。

### 5.1 布局

| 区域 | 内容 |
|---|---|
| **顶部状态栏** | 应用名与版本 · 状态徽标（绿点「监控中」/ 灰点「已暂停」）· 监控开关 |
| **左侧指标区** | 指标卡：最新价、涨跌幅、今日最高、今日最低；下方「数据质量」卡：数据源、交叉校验、最近更新、下次轮询倒计时 |
| **右侧日志区** | 运行日志，按级别着色（INFO 常规 / WARNING 琥珀 / ERROR 红），自动换行（续行缩进），自动滚动，最多保留 500 行 |
| **底部状态栏** | 今日交易日 · 市场状态 · 轮询间隔 · QQ 通道状态 |

### 5.2 显示字段与数据来源

| 界面字段 | 数据来源 |
|---|---|
| 最新价 / 涨跌幅 / 今日最高 / 今日最低 | `collector.fetch_all()` 的 `price` / `change_pct` / `high` / `low` |
| 涨跌幅颜色 | **红涨绿跌**（国内习惯）：`> 0` 红、`< 0` 绿、`= 0` 常规灰 |
| 数据源 / 交叉校验 | `data["source"]`、`cross_ok` 与 `reference_price` |
| 最近更新 | 收到数据回调时的本机时间 |
| 下次轮询 | 引擎 `next_poll_at` 倒计时（每秒刷新） |
| 市场状态 / 轮询间隔 | `trading_calendar` 判定 + 引擎 `current_interval` |
| QQ 通道状态 | `notifier.channel_status()` |

### 5.3 交互方式

- **启动状态**：界面启动时监控**默认暂停**——不自动取数、不推送、不发启动通知，需手动打开开关。避免程序一打开就产生网络请求与 QQ 消息。
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

# 运行测试（23 组）
.venv/Scripts/python.exe test.py
```

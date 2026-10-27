"""
金价监控图形界面（Tkinter）。

布局：
    顶部 —— 应用名 / 版本 / 状态徽标 / 监控开关
    左侧 —— 关键指标卡（最新价、涨跌幅、监控最高、监控最低）+ 数据质量卡
    右侧 —— 运行日志（按级别着色，自动换行并续行缩进，自动滚动）
    底部 —— 今日交易日 / 市场状态 / 轮询状态 / QQ 通道状态

启动状态：
    界面启动时监控**默认关闭**（不自动取数、不推送），需用户打开右上角
    开关，避免程序一启动就产生网络请求与 QQ 消息。

线程模型：
    MonitorEngine 在后台线程运行；回调只向线程安全队列投递消息，
    界面用 after() 定时取出并刷新，避免跨线程操作控件。

字体清晰度（Windows 文字发虚的三个成因与对策）：
    1. 应用未声明 DPI 感知 → 系统按位图拉伸整窗，整体发虚。
       对策：进程启动时调用 SetProcessDpiAwareness，按物理像素渲染。
    2. 点值字号会被 tk scaling（约 1.333）折算成非整数像素（如 10pt→13.33px），
       字形无法对齐像素网格。
       对策：字号一律用「负值 = 像素单位」并取整数（px()）。
    3. 文字与背景对比度不足会放大发虚观感。
       对策：正文用近黑（#1a1a1a），弱化色不超过 #7a7a7a。
"""

import ctypes
import logging
import os
import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime

import appicon
import notifier
import qq_bot
import trading_calendar
from monitor import (
    LOGGER_NAME,
    MonitorEngine,
    app_info,
    base_dir,
    load_config,
    setup_logging,
)

CONFIG_PATH = os.path.join(base_dir(), "config.json")

# 语义色：涨红跌绿（国内习惯）
COLOR_RED = "#c0392b"
COLOR_GREEN = "#2e7d4f"
COLOR_AMBER = "#b07000"

# 文字与中性色（提高对比度，降低发虚观感）
COLOR_TEXT = "#1a1a1a"
COLOR_SUBTEXT = "#5a5a5a"
COLOR_MUTED = "#7a7a7a"
COLOR_HAIRLINE = "#c8c8c8"
COLOR_CARD_BG = "#f2f2f2"
COLOR_LOG_BG = "#ffffff"
COLOR_LOG_TEXT = "#2b2b2b"

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

# 96dpi 下的设计像素基准值
SIZE_TITLE = 15
SIZE_BODY = 13
SIZE_SMALL = 12
SIZE_METRIC = 22


def enable_dpi_awareness():
    """声明进程 DPI 感知，避免系统对窗口做位图拉伸（发虚的首要成因）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


class QueueLogHandler(logging.Handler):
    """把日志记录投递到界面队列（线程安全）。"""

    def __init__(self, q):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        try:
            self.q.put(("log", record.levelname, self.format(record)))
        except Exception:  # noqa: BLE001
            self.handleError(record)


class MonitorApp:
    """界面主控：负责布局、开关交互与队列刷新。"""

    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.q = queue.Queue()
        self.app_name, self.version = app_info(config)

        self.dpr = self._device_pixel_ratio()
        self.state_path = os.path.join(
            base_dir(), config["runtime"].get("state_file", "state.json"))
        self.engine = MonitorEngine(
            config, self.state_path,
            on_data=lambda data: self.q.put(("data", data)),
            on_state=lambda running: self.q.put(("state", running)),
        )

        self._build_ui()
        self._attach_log_handler()

        # 查询服务与监控开关解耦：配置启用即监听，关掉监控也能随时在 QQ 里查价
        self.query_server = qq_bot.QueryServer(
            config, self.state_path, on_status=lambda: self.engine.is_running
        )
        self.query_server.start()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._drain_queue)
        self.root.after(1000, self._tick)

        # 启动时监控默认关闭：不自动取数、不推送，由用户打开开关启动
        # （开关是唯一状态来源，徽标只做呈现）
        self._set_state(False)
        self._append_log("INFO", "监控未启动：打开右上角「监控开关」后开始取数")

    # ------------------------------------------------------------------ #
    # 字体：负值 = 像素单位，且取整数，避开 tk scaling 的小数折算
    # ------------------------------------------------------------------ #
    def _device_pixel_ratio(self):
        try:
            return max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        except Exception:  # noqa: BLE001
            return 1.0

    def px(self, size):
        """把 96dpi 设计像素换算为本机整数像素（返回负值表示像素单位）。"""
        return -max(9, int(round(size * self.dpr)))

    def ui_font(self, size=SIZE_BODY, bold=False):
        return (FONT_UI, self.px(size), "bold") if bold else (FONT_UI, self.px(size))

    def mono_font(self, size=SIZE_SMALL, bold=False):
        return (FONT_MONO, self.px(size), "bold") if bold else (FONT_MONO, self.px(size))

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.root.title(f"{self.app_name} {self.version}")
        self.root.geometry(f"{int(940 * self.dpr)}x{int(580 * self.dpr)}")
        self.root.minsize(int(780 * self.dpr), int(460 * self.dpr))
        self.root.option_add("*Font", self.ui_font(SIZE_BODY))
        pad = {"padx": 14, "pady": 10}

        # ---- 顶部状态栏 ----
        top = tk.Frame(self.root)
        top.pack(fill="x", **pad)
        tk.Label(top, text=f"金价监控 · {self.app_name}", fg=COLOR_TEXT,
                 font=self.ui_font(SIZE_TITLE, bold=True)).pack(side="left")
        tk.Label(top, text=self.version, fg=COLOR_SUBTEXT,
                 font=self.ui_font(SIZE_SMALL)).pack(side="left", padx=(8, 0))

        self.toggle_var = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text="监控开关", variable=self.toggle_var,
                       command=self._on_toggle, fg=COLOR_TEXT,
                       activeforeground=COLOR_TEXT,
                       font=self.ui_font(SIZE_BODY)).pack(side="right")
        self.status_label = tk.Label(top, text="● 已暂停", fg=COLOR_MUTED,
                                     font=self.ui_font(SIZE_BODY, bold=True))
        self.status_label.pack(side="right", padx=(0, 16))

        tk.Frame(self.root, height=1, bg=COLOR_HAIRLINE).pack(fill="x")

        # ---- 主体：左指标 / 右日志 ----
        body = tk.Frame(self.root)
        body.pack(fill="both", expand=True, **pad)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        right = tk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        # 指标卡（2×2）
        for i in range(2):
            left.columnconfigure(i, weight=1, uniform="metric")
        self.metric_labels = {}
        metrics = [
            ("price", "最新价（元/克）"), ("change_pct", "涨跌幅"),
            ("high", "监控最高"), ("low", "监控最低"),
        ]
        for index, (key, caption) in enumerate(metrics):
            card = tk.Frame(left, bg=COLOR_CARD_BG)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=4, pady=4)
            tk.Label(card, text=caption, bg=COLOR_CARD_BG, fg=COLOR_SUBTEXT,
                     font=self.ui_font(SIZE_SMALL)).pack(anchor="w", padx=12, pady=(10, 0))
            value = tk.Label(card, text="--", bg=COLOR_CARD_BG, fg=COLOR_TEXT,
                             font=self.mono_font(SIZE_METRIC, bold=True))
            value.pack(anchor="w", padx=12, pady=(0, 10))
            self.metric_labels[key] = value

        # 数据质量
        self.quality_labels = {}
        quality = tk.LabelFrame(left, text=" 数据质量 ", fg=COLOR_SUBTEXT,
                                font=self.ui_font(SIZE_SMALL))
        quality.grid(row=2, column=0, columnspan=2, sticky="ew", padx=4, pady=(12, 4))
        for key, caption in [("source", "数据源"), ("cross", "交叉校验"),
                             ("updated", "最近更新"), ("next", "下次轮询")]:
            row = tk.Frame(quality)
            row.pack(fill="x", padx=10, pady=4)
            tk.Label(row, text=caption, fg=COLOR_SUBTEXT, font=self.ui_font(SIZE_SMALL),
                     width=8, anchor="w").pack(side="left")
            value = tk.Label(row, text="--", fg=COLOR_TEXT,
                             font=self.ui_font(SIZE_SMALL), anchor="e")
            value.pack(side="right")
            self.quality_labels[key] = value

        # 运行日志：wrap="word" 自动换行（面板窄且无横向滚动条，不换行会看不全）；
        # lmargin2 让续行缩进到消息起始列，换行内容与上一条记录一眼可分
        tk.Label(right, text="运行日志", fg=COLOR_TEXT,
                 font=self.ui_font(SIZE_SMALL)).pack(anchor="w")
        wrap = tk.Frame(right, bg=COLOR_LOG_BG)
        wrap.pack(fill="both", expand=True, pady=(6, 0))
        log_font = self.mono_font(SIZE_SMALL)
        hanging = tkfont.Font(font=log_font).measure("[00:00:00] INFO    ")
        self.log_text = tk.Text(wrap, state="disabled", wrap="word",
                                font=log_font, bg=COLOR_LOG_BG,
                                fg=COLOR_LOG_TEXT, relief="flat",
                                highlightthickness=0, padx=8, pady=6)
        scrollbar = tk.Scrollbar(wrap, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        # 续行缩进 lmargin2 是**标签级**属性（控件级无此选项），四个级别统一设置
        for tag, color in (("INFO", COLOR_LOG_TEXT), ("WARNING", COLOR_AMBER),
                           ("ERROR", COLOR_RED), ("CRITICAL", COLOR_RED)):
            self.log_text.tag_configure(tag, foreground=color, lmargin2=hanging)

        # ---- 底部状态栏 ----
        tk.Frame(self.root, height=1, bg=COLOR_HAIRLINE).pack(fill="x")
        bottom = tk.Frame(self.root)
        bottom.pack(fill="x", **pad)
        self.bottom_label = tk.Label(bottom, text="--", fg=COLOR_SUBTEXT,
                                     font=self.ui_font(SIZE_SMALL), anchor="w")
        self.bottom_label.pack(fill="x")

    def _attach_log_handler(self):
        logging.getLogger(LOGGER_NAME).addHandler(QueueLogHandler(self.q))

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _on_toggle(self):
        """开关是唯一状态来源；徽标以引擎实际运行态为准，立即刷新。"""
        if self.toggle_var.get():
            self.engine.start()
        else:
            self.engine.stop()
        self._set_state(self.engine.is_running)

    def _on_close(self):
        try:
            self.engine.stop()
            self.query_server.stop()
        finally:
            self.root.destroy()

    def _query_status(self):
        """查询服务状态文案，供底部状态栏展示。"""
        if not self.query_server.enabled:
            return "未启用"
        if self.query_server.is_running:
            return f"{self.query_server.port} 监听中"
        return "启动失败"

    def _set_state(self, running):
        if running:
            self.status_label.config(text="● 监控中", fg=COLOR_GREEN)
        else:
            self.status_label.config(text="● 已暂停", fg=COLOR_MUTED)

    def _on_data(self, data):
        self.metric_labels["price"].config(text=f"{data['price']:.2f}", fg=COLOR_TEXT)

        pct = data.get("change_pct", 0.0)
        if pct > 0:       # 国内习惯：涨为红
            color = COLOR_RED
        elif pct < 0:     # 跌为绿
            color = COLOR_GREEN
        else:
            color = COLOR_TEXT
        self.metric_labels["change_pct"].config(text=f"{pct:+.2f}%", fg=color)

        # 展示本工具实际监控到的极值，不用数据源的 high/low（该字段可能失真）
        self.metric_labels["high"].config(text=f"{data.get('range_high', data['price']):.2f}")
        self.metric_labels["low"].config(text=f"{data.get('range_low', data['price']):.2f}")

        self.quality_labels["source"].config(text=data.get("source", "--"))
        if data.get("cross_ok") is False:
            self.quality_labels["cross"].config(text="不一致", fg=COLOR_RED)
        elif data.get("reference_price") is not None:
            self.quality_labels["cross"].config(
                text=f"一致（推算 {data['reference_price']}）", fg=COLOR_GREEN)
        else:
            self.quality_labels["cross"].config(text="未执行", fg=COLOR_MUTED)
        self.quality_labels["updated"].config(text=time.strftime("%H:%M:%S"))

    def _append_log(self, level, text):
        tag = level if level in ("INFO", "WARNING", "ERROR", "CRITICAL") else "INFO"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag)
        if float(self.log_text.index("end-1c")) > 500:  # 限制行数，避免长时间运行卡顿
            self.log_text.delete("1.0", "100.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # 定时刷新
    # ------------------------------------------------------------------ #
    def _drain_queue(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1], item[2])
                elif kind == "data":
                    self._on_data(item[1])
                elif kind == "state":
                    self._set_state(item[1])
        except queue.Empty:
            pass
        self.root.after(200, self._drain_queue)

    def _tick(self):
        engine = self.engine
        if engine.next_poll_at:
            remain = max(0, int(engine.next_poll_at - time.time()))
            if engine.current_interval:      # 监控时段内
                self.quality_labels["next"].config(text=f"{remain}s 后")
            else:                            # 已暂停，等待下一个开盘
                self.quality_labels["next"].config(text=f"距开盘 {remain // 60} 分")
        elif engine.is_running:
            self.quality_labels["next"].config(text="取数中…")
        else:
            self.quality_labels["next"].config(text="--")

        schedule = self.config["schedule"]
        today = datetime.now().date()
        trading = trading_calendar.day_status_text(today, schedule)

        if not engine.is_running:
            market, interval = "已暂停", "--"
        else:
            if trading_calendar.is_monitor_window(datetime.now(), schedule):
                market = "监控中"
            elif trading_calendar.is_trading_day(today, schedule):
                market = "非监控时段"
            else:
                market = "休市"
            interval = f"{engine.current_interval}s" if engine.current_interval else "暂停"

        self.bottom_label.config(
            text=f"今日交易日：{trading}    ｜    市场状态：{market}    ｜    "
                 f"轮询间隔：{interval}    ｜    QQ 通道：{notifier.channel_status(self.config)}    ｜    "
                 f"查询服务：{self._query_status()}")
        self.root.after(1000, self._tick)


def main():
    config = load_config(CONFIG_PATH)
    log = setup_logging(
        os.path.join(base_dir(), config["runtime"].get("log_file", "monitor.log")),
        console=False,
    )
    enable_dpi_awareness()   # 必须在创建 Tk 之前
    root = tk.Tk()
    # 图标要在窗口显示前设置，避免标题栏先闪一下 Tk 默认的羽毛图标
    log.info("窗口图标：%s", appicon.apply_icon(root, logger=log) or "未应用")
    MonitorApp(root, config)
    root.mainloop()


if __name__ == "__main__":
    main()

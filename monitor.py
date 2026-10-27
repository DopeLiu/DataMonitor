"""
监控引擎：可启停的取数—触发—推送循环，供命令行与图形界面共用。

对外接口：
    MonitorEngine(config, state_path, on_data=None, on_state=None)
        .start() / .stop() / .is_running
        .next_interval(now=None)   距下一次唤醒的秒数（非监控时段为距开盘）
        .current_interval / .next_poll_at   供界面显示倒计时

监控只在日盘进行：非监控时段不取数、不判定、不推送，直接睡到下一个开盘时刻。

线程模型：start() 在后端线程运行循环，GUI 需把 on_data/on_state 回调
仅用于向线程安全队列投递消息，不得直接操作控件。
"""

import logging
import os
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

import collector
import notifier
import trading_calendar
import trigger

LOGGER_NAME = "datamonitor"

# 应用名与版本号的唯一来源是 config.json 的 app 段；以下仅为缺失时的兜底
DEFAULT_APP_NAME = "DataMonitor"
DEFAULT_VERSION = "v0.0.0"

log = logging.getLogger(LOGGER_NAME)


def app_info(config):
    """从 config.json 的 app 段读取应用名与版本号。

    返回 (name, version)。所有需要版本号的位置（日志、启动通知、界面标题）
    都应通过本函数获取，避免散落硬编码。
    """
    app = config.get("app") or {}
    return app.get("name") or DEFAULT_APP_NAME, app.get("version") or DEFAULT_VERSION


def setup_logging(log_file, console=True):
    """配置日志：轮转文件 + 可选控制台。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y.%m.%d %H:%M:%S")

    file_handler = RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(fmt)
        logger.addHandler(stream_handler)
    return logger


def _human_wait(seconds):
    """把秒数转成便于阅读的等待时长。"""
    seconds = max(int(seconds), 1)
    if seconds >= 3600:
        return f"{seconds // 3600} 小时 {seconds % 3600 // 60} 分"
    return f"{max(seconds // 60, 1)} 分钟"


class MonitorEngine:
    """金价监控循环，支持随时启停。"""

    def __init__(self, config, state_path, on_data=None, on_state=None):
        self.config = config
        self.state_path = state_path
        self.on_data = on_data      # callback(data: dict) -> None
        self.on_state = on_state    # callback(running: bool) -> None
        self.app_name, self.version = app_info(config)

        self._thread = None
        self._stop_event = threading.Event()
        self._state = trigger.load_state(state_path)

        self.current_interval = None
        self.next_poll_at = None

    # ------------------------------------------------------------------ #
    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        """启动监控线程（重复调用安全）。"""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="datamonitor", daemon=True)
        self._thread.start()
        if self.on_state:
            self.on_state(True)

    def stop(self, timeout=5):
        """停止监控线程并等待退出（重复调用安全）。"""
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=timeout)
        self._thread = None
        self.current_interval = None
        self.next_poll_at = None
        if self.on_state:
            self.on_state(False)

    # ------------------------------------------------------------------ #
    def next_interval(self, now=None):
        """距下一次唤醒的秒数：监控时段内按配置间隔，其余等到下一个开盘。"""
        schedule = self.config["schedule"]
        now = now or datetime.now()
        if trading_calendar.is_monitor_window(now, schedule):
            return schedule["trading_interval_seconds"]
        return trading_calendar.seconds_to_next_open(now, schedule)

    def _run(self):
        log.info("%s %s 监控已启动", self.app_name, self.version)
        try:
            self._notify_startup()
            while not self._stop_event.is_set():
                now = datetime.now()
                schedule = self.config["schedule"]

                # 非监控时段：不取数，直接睡到下一个开盘
                if not trading_calendar.is_monitor_window(now, schedule):
                    wait_seconds = trading_calendar.seconds_to_next_open(now, schedule)
                    self.current_interval = None
                    self.next_poll_at = time.time() + wait_seconds
                    log.info("非监控时段，暂停检测，约 %s 后开盘", _human_wait(wait_seconds))
                    if self._stop_event.wait(wait_seconds):
                        break
                    continue

                started = time.time()
                self._cycle()
                interval = self.next_interval()
                self.current_interval = interval
                wait_seconds = max(interval - (time.time() - started), 1)
                self.next_poll_at = time.time() + wait_seconds
                log.info("等待 %d 秒后运行...", int(wait_seconds))
                if self._stop_event.wait(wait_seconds):  # 可被 stop() 立即打断
                    break
        except Exception as exc:  # noqa: BLE001
            log.exception("监控线程异常退出: %s", exc)
        finally:
            self.next_poll_at = None
            self.current_interval = None
            log.info("监控已停止")

    def _notify_startup(self):
        """启动通知：开市时附带一次实时价，非监控时段不取数。"""
        schedule = self.config["schedule"]
        data = None
        if trading_calendar.is_monitor_window(datetime.now(), schedule):
            data = collector.fetch_all(self.config)
        notifier.notify_startup(data, self.config, self.version)

    def _cycle(self):
        """执行一轮：取数 -> 触发 -> 推送。"""
        data = collector.fetch_all(self.config)
        if not data:
            log.warning("本轮取数失败，跳过")
            return

        alerts = trigger.evaluate(data, self._state, self.config)
        trigger.save_state(self._state, self.state_path)

        # 开盘播报与价格告警是两类消息，分开推送
        openings = [a for a in alerts if a["type"] == "open"]
        price_alerts = [a for a in alerts if a["type"] != "open"]

        if openings:
            notifier.notify_opening(data, self.config)
        if price_alerts:
            for alert in price_alerts:
                log.info("告警: %s 价格=%.2f", alert["label"], alert["price"])
            notifier.notify(price_alerts, data, self.config)
        if not alerts:
            flag = "" if data.get("cross_ok", True) else " [数据源不一致]"
            log.info(
                "最新=%.2f 监控高=%.2f 监控低=%.2f 无告警%s",
                data["price"], data.get("range_high", data["price"]),
                data.get("range_low", data["price"]), flag,
            )

        if self.on_data:
            self.on_data(data)


def load_config(path):
    import json
    with open(path, "r", encoding="utf-8") as fp:
        return json.load(fp)


def base_dir():
    return os.path.dirname(os.path.abspath(__file__))

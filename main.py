"""金价监控主程序（命令行入口）。图形界面入口见 gui.py。"""

import os
import time
from datetime import datetime

import notifier
import trading_calendar
from monitor import (
    MonitorEngine,
    app_info,
    base_dir,
    load_config,
    setup_logging,
)

CONFIG_PATH = os.path.join(base_dir(), "config.json")


def main():
    config = load_config(CONFIG_PATH)
    runtime = config.get("runtime", {})
    log = setup_logging(os.path.join(base_dir(), runtime.get("log_file", "monitor.log")))
    state_path = os.path.join(base_dir(), runtime.get("state_file", "state.json"))

    today = datetime.now().date()
    app_name, version = app_info(config)
    log.info("=" * 52)
    log.info("%s %s 启动 (%s)", app_name, version,
             datetime.now().strftime("%Y.%m.%d %H:%M:%S"))
    log.info("主源: %s %s", config["sources"]["primary"]["code"],
             config["sources"]["primary"].get("unit", "元/克"))
    log.info("通知渠道: %s", notifier.channel_status(config))
    log.info("今日 %s 是否交易日: %s", today,
             trading_calendar.day_status_text(today, config["schedule"]))
    log.info("=" * 52)

    engine = MonitorEngine(config, state_path)
    engine.start()
    try:
        while engine.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        print("\n已停止监控。")


if __name__ == "__main__":
    main()

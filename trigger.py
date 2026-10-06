"""
触发逻辑与状态持久化。

- 以「交易所给出的当日最高/最低」是否刷新来判定创当日新高/新低（权威且无需自行累计）
- 新高 / 新低 各自独立冷却，避免极值附近震荡反复轰炸
- 价格统一 round(x, 2) 比较（修正旧版 int() 截断问题）
- 状态写入 state.json，进程重启不丢
"""

import json
import logging
from datetime import datetime

log = logging.getLogger("datamonitor")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as fp:
            return json.load(fp)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("读取状态文件失败，使用空状态: %s", exc)
        return {}


def save_state(state, path):
    try:
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(state, fp, ensure_ascii=False, indent=2)
    except OSError as exc:
        log.error("写入状态文件失败: %s", exc)


def _round2(value, default=0.0):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def evaluate(data, state, config, now=None):
    """判定是否产生告警，并在 state 上就地更新状态。

    返回告警列表（可能为空）。调用方负责随后 save_state。
    告警项：{"type": "high"/"low"/"buy"/"sell", "label": str, "price": float}
    """
    alerts = []
    if not data or not data.get("price"):
        return alerts

    now = now or datetime.now()
    alert_cfg = config.get("alert", {})
    cooldown = alert_cfg.get("cooldown_seconds", 300)
    tolerance = alert_cfg.get("price_tolerance", 0.01)

    today = now.strftime("%Y-%m-%d")
    price = _round2(data["price"])
    day_high = _round2(data.get("high") or price)
    day_low = _round2(data.get("low") or price)
    now_ts = now.timestamp()

    # 日切：重置当日基准，本轮不告警，避免跨日误报
    if state.get("date") != today:
        state.update({
            "date": today,
            "seen_high": day_high,
            "seen_low": day_low,
            "last_high_alert_ts": 0,
            "last_low_alert_ts": 0,
        })
        log.info("日切重置：date=%s 基准高/低=%.2f / %.2f", today, day_high, day_low)
        return alerts

    # 创当日新高
    if day_high > state.get("seen_high", day_high) + tolerance:
        state["seen_high"] = day_high
        if now_ts - state.get("last_high_alert_ts", 0) >= cooldown:
            state["last_high_alert_ts"] = now_ts
            alerts.append({"type": "high", "label": "创当日新高", "price": price})

    # 创当日新低
    if day_low < state.get("seen_low", day_low) - tolerance:
        state["seen_low"] = day_low
        if now_ts - state.get("last_low_alert_ts", 0) >= cooldown:
            state["last_low_alert_ts"] = now_ts
            alerts.append({"type": "low", "label": "创当日新低", "price": price})

    # 可选：自定义关键价位
    buy_below = alert_cfg.get("buy_below")
    if buy_below and price <= buy_below:
        alerts.append({"type": "buy", "label": f"跌至买入价 {buy_below}", "price": price})
    sell_above = alert_cfg.get("sell_above")
    if sell_above and price >= sell_above:
        alerts.append({"type": "sell", "label": f"涨至卖出价 {sell_above}", "price": price})

    return alerts

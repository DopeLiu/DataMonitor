"""
触发逻辑与状态持久化。

- 当日高/低由本模块用每轮最新价自行累计，不采信数据源的 high/low 字段
  （新浪 gds_AUTD 的「最高」曾长期停留在 890.00 的陈旧值，导致最新价 862
  却报「创当日新高」的误报）
- 新高 / 新低 各自独立冷却，避免极值附近震荡反复轰炸
- 日切按自然日重置；非日盘时段不取数，不存在跨日行情
- 日盘首个时段开盘后 5 分钟内的首轮取数，额外给出一条「今日开盘」播报
- 状态写入 state.json，进程重启不丢
"""

import json
import logging
from datetime import datetime

log = logging.getLogger("datamonitor")

# 开盘播报窗口：开盘后 5 分钟内才播报，盘中重启不补发
OPEN_NOTICE_SECONDS = 300


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


def _in_open_notice_window(now, schedule):
    """是否处于开盘播报窗口：日盘首个时段开始后的 OPEN_NOTICE_SECONDS 内。"""
    sessions = schedule.get("day_sessions") or []
    if not sessions:
        return False
    try:
        hour, minute = sessions[0][0].split(":")
        start = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    except (ValueError, IndexError):
        return False
    return 0 <= (now - start).total_seconds() <= OPEN_NOTICE_SECONDS


def evaluate(data, state, config, now=None):
    """判定是否产生告警，并在 state 上就地更新状态。

    返回告警列表（可能为空）。调用方负责随后 save_state。
    告警项：{"type": "high"/"low"/"open"/"buy"/"sell", "label": str, "price": float}
    同时把实际监控到的当日高/低写入 data["range_high"]/["range_low"]，供消息展示。
    """
    alerts = []
    if not data or not data.get("price"):
        return alerts

    now = now or datetime.now()
    alert_cfg = config.get("alert", {})
    cooldown = alert_cfg.get("cooldown_seconds", 300)
    tolerance = alert_cfg.get("price_tolerance", 0.01)
    schedule = config.get("schedule", {})

    today = now.strftime("%Y-%m-%d")
    price = _round2(data["price"])
    now_ts = now.timestamp()

    # 数据源交叉校验未通过时，价格不可信：不重置基准、不判定、不播报
    if data.get("cross_ok") is False:
        log.warning(
            "数据源交叉校验未通过，本轮跳过判定（最新=%.2f 偏差=%s%%）",
            price, data.get("deviation_pct"),
        )
        return alerts

    # 日切：以当前价作为新基准，本轮天然不会触发高低告警
    if state.get("date") != today:
        state.update({
            "date": today,
            "seen_high": price,
            "seen_low": price,
            "last_high_alert_ts": 0,
            "last_low_alert_ts": 0,
        })
        log.info("日切重置：date=%s 基准价=%.2f", today, price)
        if _in_open_notice_window(now, schedule):
            alerts.append({"type": "open", "label": "今日开盘", "price": price})

    # 创当日新高：以最新价累计，不依赖数据源的最高字段
    if price > state.get("seen_high", price) + tolerance:
        state["seen_high"] = price
        if now_ts - state.get("last_high_alert_ts", 0) >= cooldown:
            state["last_high_alert_ts"] = now_ts
            alerts.append({"type": "high", "label": "创当日新高", "price": price})

    # 创当日新低
    if price < state.get("seen_low", price) - tolerance:
        state["seen_low"] = price
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

    data["range_high"] = _round2(state.get("seen_high", price))
    data["range_low"] = _round2(state.get("seen_low", price))
    return alerts

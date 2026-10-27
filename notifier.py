"""
通知模块：QQ 推送（第三方协议端 OneBot v11，如 NapCat / SnowLuma / LLOneBot）。

· 客户只需在 QQ 内接收，无需安装任何额外软件
· 通过 HTTP 接口 /send_group_msg、/send_private_msg 主动推送
· ⚠ 第三方协议违反 QQ 用户协议，存在封号/掉线风险：
    务必使用小号、HTTP 服务仅监听 127.0.0.1、必须设置强 access_token，
    并保持较低发送频率。

消息渲染支持 text（默认，QQ 使用）与 markdown 两种样式，便于日后接入其它渠道。
"""

import json
import logging
import os
import platform
import time
from datetime import datetime

import requests

import trading_calendar
import trigger

log = logging.getLogger("datamonitor")

# 按通道记录最近一分钟内的发送时间戳，用于本地限流
_SENT_TS = {}
# 配置类问题只提示一次，避免告警频繁刷屏
_WARNED = set()


def _warn_once(key, message):
    if key not in _WARNED:
        _WARNED.add(key)
        log.warning(message)


def _rate_ok(channel, limit_per_minute):
    now = time.time()
    bucket = _SENT_TS.setdefault(channel, [])
    bucket[:] = [t for t in bucket if now - t < 60]
    if len(bucket) >= limit_per_minute:
        log.warning("[%s] 触发本地限流(%s 条/分钟)，本条暂缓", channel, limit_per_minute)
        return False
    bucket.append(now)
    return True


# --------------------------------------------------------------------------- #
# QQ（OneBot v11）
# --------------------------------------------------------------------------- #
def _post_onebot(url, payload, headers, timeout, label):
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.error("QQ 推送异常(%s): %s", label, exc)
        return False
    if data.get("status") == "ok" or data.get("retcode") == 0:
        return True
    log.error("QQ 推送失败(%s): %s", label, data)
    return False


def push_qq(text, qq_cfg):
    """通过 OneBot v11 协议端向配置的群/好友推送纯文本消息。

    返回 True 表示所有目标均发送成功。
    """
    base = (qq_cfg.get("api_base") or "").rstrip("/")
    token = qq_cfg.get("access_token") or ""
    timeout = qq_cfg.get("timeout_seconds", 10)
    targets = qq_cfg.get("targets") or {}
    groups = targets.get("groups") or []
    users = targets.get("users") or []

    if not base:
        _warn_once("qq_base", "QQ 通道未配置 api_base，跳过")
        return False
    if not groups and not users:
        _warn_once("qq_targets", "QQ 通道未配置推送目标(targets)，跳过")
        return False

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    all_ok = True
    for group_id in groups:
        ok = _post_onebot(
            f"{base}/send_group_msg",
            {"group_id": group_id, "message": text},
            headers, timeout, f"群 {group_id}",
        )
        all_ok = all_ok and ok
    for user_id in users:
        ok = _post_onebot(
            f"{base}/send_private_msg",
            {"user_id": user_id, "message": text},
            headers, timeout, f"好友 {user_id}",
        )
        all_ok = all_ok and ok
    return all_ok


def push_qq_to(text, qq_cfg, group_id=None, user_id=None, at_user=None):
    """向指定群/好友发送一条消息（被动回复用，不走 targets、不占告警限流）。

    群聊且指定 at_user 时以 message 数组形式 @ 提问者，私聊发送纯文本。
    """
    base = (qq_cfg.get("api_base") or "").rstrip("/")
    if not qq_cfg.get("enabled") or not base:
        _warn_once("qq_reply_cfg", "QQ 通道未启用或未配置 api_base，无法回复查询")
        return False

    headers = {"Content-Type": "application/json"}
    token = qq_cfg.get("access_token") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    timeout = qq_cfg.get("timeout_seconds", 10)

    if group_id:
        message = text
        if at_user:
            message = [
                {"type": "at", "data": {"qq": at_user}},
                {"type": "text", "data": {"text": "\n" + text}},
            ]
        return _post_onebot(
            f"{base}/send_group_msg",
            {"group_id": group_id, "message": message},
            headers, timeout, f"群 {group_id}",
        )
    if user_id:
        return _post_onebot(
            f"{base}/send_private_msg",
            {"user_id": user_id, "message": text},
            headers, timeout, f"好友 {user_id}",
        )
    _warn_once("qq_reply_target", "查询回复缺少会话标识，跳过")
    return False


# --------------------------------------------------------------------------- #
# 消息渲染
# --------------------------------------------------------------------------- #
def _render(title, fields, style):
    """按样式渲染消息：text（默认，QQ）或 markdown（供其它渠道扩展）。"""
    lines = [f"【{title}】"]
    for key, value in fields:
        lines.append(f"> {key}：{value}" if style == "markdown" else f"{key}：{value}")
    if style == "markdown":
        lines[0] = f"**{lines[0]}**"
    return "\n".join(lines)


def _cross_text(data):
    if data.get("cross_ok") is False:
        return f"⚠不一致(推算 {data.get('reference_price')}，偏差 {data.get('deviation_pct')}%)"
    if data.get("reference_price") is not None:
        return f"一致(推算 {data['reference_price']})"
    return "未执行"


def _alert_fields(alerts, data):
    unit = data.get("unit", "元/克")
    # 高/低用本工具实际监控到的值，不展示数据源的 high/low（该字段可能失真）
    range_high = data.get("range_high", data["price"])
    range_low = data.get("range_low", data["price"])
    return [
        ("时间", datetime.now().strftime("%Y.%m.%d %H:%M:%S")),
        ("触发", "、".join(a["label"] for a in alerts)),
        ("最新价", f"{data['price']:.2f} {unit}"),
        ("今开/昨收", f"{data['open']:.2f} / {data['prev_close']:.2f}"),
        ("监控高/低", f"{range_high:.2f} / {range_low:.2f}"),
        ("涨跌幅", f"{data['change_pct']:+.2f}%"),
        ("数据源", f"{data['source']} ｜ 校验：{_cross_text(data)}"),
    ]


def build_message(alerts, data, style="text"):
    """组装告警消息。"""
    return _render("金价提醒", _alert_fields(alerts, data), style)


def build_opening_message(data, style="text"):
    """组装开盘播报消息，与「创新高/新低」告警完全分开。"""
    unit = data.get("unit", "元/克")
    fields = [
        ("时间", datetime.now().strftime("%Y.%m.%d %H:%M:%S")),
        ("最新价", f"{data['price']:.2f} {unit}"),
        ("昨收", f"{data['prev_close']:.2f} {unit}"),
        ("涨跌幅", f"{data['change_pct']:+.2f}%"),
        ("数据源", f"{data['source']} ｜ 校验：{_cross_text(data)}"),
    ]
    return _render("今日开盘", fields, style)


def _quote_time_text(data):
    """行情时间；非当日数据额外标注，避免休市时的收盘价被当成实时价。"""
    stamp = " ".join(x for x in (data.get("date"), data.get("time")) if x).strip()
    if not stamp:
        return datetime.now().strftime("%Y.%m.%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")
    if data.get("date") and data["date"] != today:
        return f"{stamp}（上一交易日，当前休市）"
    return stamp


def _threshold_fields(state, config):
    """触发阈值行。

    只展示今日基准：日切未发生时旧极值会在下一轮重置，展示出来会误导。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    recorded = (state or {}).get("date")
    if recorded != today:
        hint = f"（记录停留在 {recorded}）" if recorded else ""
        return [("触发阈值", f"今日尚未建立基准{hint}")]

    limits = trigger.thresholds(state, config)
    if not limits:
        return [("触发阈值", "今日尚未建立基准")]
    return [
        ("监控高/低", f"{limits['seen_high']:.2f} / {limits['seen_low']:.2f}"),
        ("触发阈值", f"涨破 {limits['high']:.2f} 提醒 ｜ 跌破 {limits['low']:.2f} 提醒"),
    ]


def _status_text(config, running):
    """市场状态；running 为 None 表示调用方不提供监控运行状态。"""
    schedule = config.get("schedule", {})
    parts = [trading_calendar.window_status_text(datetime.now(), schedule)]
    if running is not None:
        parts.append("监控运行中" if running else "监控已暂停")
    return " · ".join(parts)


def build_query_message(data, state, config, running=None, style="text"):
    """组装「查询金价」回复：实时行情 + 当前告警阈值。

    data 为 None 表示取数失败，只回一条提示，不编造价格。
    """
    if not data:
        return _render("金价查询", [("结果", "取数失败，请稍后重试")], style)

    unit = data.get("unit", "元/克")
    alert_cfg = config.get("alert", {})
    cooldown = alert_cfg.get("cooldown_seconds", 300)

    fields = [
        ("最新价", f"{data['price']:.2f} {unit}（{data['change_pct']:+.2f}%）"),
        ("行情时间", _quote_time_text(data)),
        ("今开/昨收", f"{data['open']:.2f} / {data['prev_close']:.2f}"),
    ]
    fields.extend(_threshold_fields(state, config))
    fields.append(("告警冷却", f"同一方向 {cooldown} 秒内只提醒一次"))

    buy_below = alert_cfg.get("buy_below")
    sell_above = alert_cfg.get("sell_above")
    if buy_below or sell_above:
        prices = []
        if buy_below:
            prices.append(f"买入 {buy_below:.2f}")
        if sell_above:
            prices.append(f"卖出 {sell_above:.2f}")
        fields.append(("自定义价位", " / ".join(prices)))

    fields.append(("数据源", f"{data['source']}｜校验：{_cross_text(data)}"))
    fields.append(("状态", _status_text(config, running)))
    return _render("金价查询", fields, style)


def _startup_fields(data, config, version, pid):
    schedule = config.get("schedule", {})
    alert_cfg = config.get("alert", {})
    primary = config["sources"]["primary"]
    unit = primary.get("unit", "元/克")
    day_sessions = " / ".join(f"{s}-{e}" for s, e in schedule.get("day_sessions", []))
    sessions = f"{day_sessions}（夜盘不监控）"
    today = datetime.now().date()

    fields = [
        ("启动时间", datetime.now().strftime("%Y.%m.%d %H:%M:%S")),
        ("程序版本", f"DataMonitor {version} ｜ Python {platform.python_version()} ｜ PID {pid}"),
        ("监控标的", f"{primary.get('name', primary.get('code'))} {unit}"),
        ("轮询频率",
         f"开市 {schedule.get('trading_interval_seconds')}s ｜ "
         f"非交易时段暂停检测 ｜ "
         f"告警冷却 {alert_cfg.get('cooldown_seconds')}s"),
        ("交易时段", sessions),
        ("今日交易日", trading_calendar.day_status_text(today, schedule)),
    ]

    if data:
        fields.append((
            "当前金价",
            f"{data['price']:.2f} {unit}（涨跌 {data['change_pct']:+.2f}%）",
        ))
        fields.append(("数据源", f"{data['source']} ｜ 校验：{_cross_text(data)}"))
    elif trading_calendar.is_monitor_window(datetime.now(), schedule):
        fields.append(("当前金价", "⚠ 本次启动取数失败，稍后重试"))
        fields.append(("数据源", f"新浪财经({primary.get('code')}) ｜ 校验：未执行"))
    else:
        fields.append(("当前金价", "非监控时段（休市），开盘后开始检测"))
        fields.append(("数据源", f"新浪财经({primary.get('code')}) ｜ 校验：未执行"))

    fields.append(("通知渠道", channel_status(config)))
    return fields


def build_startup_message(data, config, version, pid=None, style="text"):
    """组装启动通知。data 允许为 None（启动取数失败时）。"""
    pid = pid if pid is not None else os.getpid()
    return _render("金价监控已启动", _startup_fields(data, config, version, pid), style)


# --------------------------------------------------------------------------- #
# 渠道状态与分发
# --------------------------------------------------------------------------- #
def _qq_targets(qq_cfg):
    targets = qq_cfg.get("targets") or {}
    return (targets.get("groups") or []) + (targets.get("users") or [])


def _qq_ready(notify_cfg):
    qq = notify_cfg.get("qq", {})
    return bool(qq.get("enabled")) and bool(qq.get("api_base")) and bool(_qq_targets(qq))


def channel_status(config):
    """返回通知渠道配置状态的简要描述，用于启动日志/通知。"""
    notify_cfg = config.get("notify", {})
    qq = notify_cfg.get("qq", {})
    if not qq.get("enabled"):
        return "QQ 通道未启用"
    n = len(_qq_targets(qq))
    return f"QQ(OneBot){'✅' if _qq_ready(notify_cfg) else '⚠未配置'}" + (f"[{n}个目标]" if n else "")


def _dispatch(text, config, kind):
    """推送到启用的渠道；返回 True 表示发送成功。"""
    notify_cfg = config.get("notify", {})
    qq_cfg = notify_cfg.get("qq", {})

    if not qq_cfg.get("enabled"):
        _warn_once("qq_disabled", "QQ 通道未启用，跳过推送")
        return False
    if not _qq_ready(notify_cfg):
        _warn_once("qq_cfg", "QQ 通道未配置 api_base 或 targets，跳过")
        return False
    if not _rate_ok("qq", qq_cfg.get("rate_limit_per_minute", 20)):
        return False
    if push_qq(text, qq_cfg):
        log.info("✅ %s已推送到 QQ", kind)
        return True
    return False


def notify(alerts, data, config):
    """推送告警。返回 True 表示发送成功。"""
    if not alerts:
        return False
    return _dispatch(build_message(alerts, data, "text"), config, "告警")


def notify_opening(data, config):
    """推送开盘播报。返回 True 表示发送成功。"""
    return _dispatch(build_opening_message(data, "text"), config, "开盘播报")


def notify_startup(data, config, version, pid=None):
    """推送启动通知。返回 True 表示发送成功。"""
    return _dispatch(
        build_startup_message(data, config, version, pid, "text"), config, "启动通知"
    )


if __name__ == "__main__":
    # 用伪造数据自测推送链路（需先在 config.json 填好 QQ 通道）
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "config.json"), "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    demo_data = {
        "price": 906.80, "open": 906.80, "prev_close": 905.90,
        "high": 908.20, "low": 896.33, "change_pct": 0.10,
        "unit": "元/克", "source": "新浪财经(gds_AUTD)(自测)",
        "cross_ok": True, "reference_price": 892.30, "deviation_pct": 1.63,
    }
    demo_alerts = [{"type": "high", "label": "创当日新高(自测)", "price": 906.80}]
    print("渠道状态:", channel_status(cfg))
    print("--- 消息预览 ---")
    print(build_message(demo_alerts, demo_data, "text"))
    print("--- 发送 ---")
    print(notify(demo_alerts, demo_data, cfg))

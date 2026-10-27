"""
交易日历：判断国内市场（上海黄金交易所 Au(T+D)）是否开市。

依据上金所官方交易时间：
- 日盘：09:00-11:30、13:30-15:30（仅交易日）
- 夜盘：20:00-次日 02:30，且夜盘归属「下一个交易日」：
    · 20:00-24:00 段 = 当日为交易日，且次日不是法定节假日
    · 00:00-02:30 段 = 前一日为交易日，且当日不是法定节假日
  由此可推导：周五晚 20:00-周六 02:30 属于下周一交易日；
             法定节假日前一晚不设夜盘（如上金所公告「节前一日晚上不进行夜市交易」）。
- 交易日 = 周一至周五 且 非法定节假日（调休上班的周末交易所同样休市）。

监控窗口 vs 市场开市：
- `is_market_open` 描述市场真实状态（含夜盘），用于界面展示；
- `is_monitor_window` 描述本工具是否在工作，只认日盘。夜盘不取数、不判定。

节假日来源（任一判定为节假日即为节假日）：
1. config.schedule.holidays：自定义日期列表，形如 ["2026-10-01", ...]
2. chinese_calendar 库：自动识别法定节假日与调休，覆盖 2004-2026；
   超出覆盖年份时自动回退到规则 1，仅记录一次告警。
"""

import datetime
import logging

log = logging.getLogger("datamonitor")

try:
    import chinese_calendar as _cc
except ImportError:  # pragma: no cover
    _cc = None

_warned_years = set()

# chinese_calendar 返回英文节日名，映射为中文便于展示
_HOLIDAY_CN = {
    "New Year's Day": "元旦",
    "Spring Festival": "春节",
    "Tomb-sweeping Day": "清明节",
    "Labour Day": "劳动节",
    "Labor Day": "劳动节",
    "Dragon Boat Festival": "端午节",
    "Mid-autumn Festival": "中秋节",
    "National Day": "国庆节",
}


def _lib_ready(schedule, day):
    """节假日库是否可用且覆盖该日期。"""
    if not schedule.get("use_cn_calendar", True) or _cc is None:
        return False
    try:
        _cc.is_workday(day)
        return True
    except NotImplementedError:
        if day.year not in _warned_years:
            _warned_years.add(day.year)
            log.warning("节假日库未覆盖 %s 年，回退为『工作日+自定义节假日』规则", day.year)
        return False
    except Exception:  # noqa: BLE001
        return False


def _holiday_set(schedule):
    return set(schedule.get("holidays") or [])


def is_statutory_holiday(day, schedule):
    """是否为法定节假日（不含普通周末；调休上班的周末不算）。"""
    if day.isoformat() in _holiday_set(schedule):
        return True
    if _lib_ready(schedule, day):
        on_holiday, name = _cc.get_holiday_detail(day)
        return bool(on_holiday and name)
    return False


def holiday_name(day, schedule):
    """返回节假日名称；普通周末或非节假日返回 None。"""
    if day.isoformat() in _holiday_set(schedule):
        return "自定义节假日"
    if _lib_ready(schedule, day):
        on_holiday, name = _cc.get_holiday_detail(day)
        if on_holiday and name:
            return _HOLIDAY_CN.get(name, name)
    return None


def is_trading_day(day, schedule):
    """是否为交易日：周一至周五且非法定节假日。"""
    if day.weekday() >= 5:
        return False
    if day.isoformat() in _holiday_set(schedule):
        return False
    if _lib_ready(schedule, day):
        return bool(_cc.is_workday(day))
    return True


def day_status_text(day, schedule):
    """交易日状态的中文描述，用于日志/通知。"""
    if is_trading_day(day, schedule):
        return "是"
    name = holiday_name(day, schedule)
    if name:
        return f"否（{name}休市）"
    if day.weekday() >= 5:
        return "否（周末休市）"
    return "否（休市）"


def _parse(hhmm):
    hour, minute = hhmm.split(":")
    return datetime.time(int(hour), int(minute))


def is_market_open(now, schedule):
    """给定时刻市场是否开市。"""
    day = now.date()
    current = now.time()
    one_day = datetime.timedelta(days=1)

    # 日盘（不跨日）
    if is_trading_day(day, schedule):
        for start_str, end_str in schedule.get("day_sessions", []):
            if _parse(start_str) <= current <= _parse(end_str):
                return True

    # 夜盘（跨日，归属下一交易日）
    night = schedule.get("night_session")
    if night:
        start, end = _parse(night[0]), _parse(night[1])
        if current >= start:  # 当日 20:00 之后
            return is_trading_day(day, schedule) and not is_statutory_holiday(day + one_day, schedule)
        if current <= end:    # 次日 02:30 之前（承接前一日夜盘）
            return is_trading_day(day - one_day, schedule) and not is_statutory_holiday(day, schedule)
    return False


def is_monitor_window(now, schedule):
    """当前是否处于监控窗口内：仅日盘，不含夜盘。

    夜盘行情照常存在，但本工具只在日盘取数，其余时间完全暂停。
    """
    if not is_trading_day(now.date(), schedule):
        return False
    current = now.time()
    for start_str, end_str in schedule.get("day_sessions", []):
        if _parse(start_str) <= current <= _parse(end_str):
            return True
    return False


def window_status_text(now, schedule):
    """当前市场状态的中文描述，用于查询回复等需要「一句话说明」的场景。

    比 day_status_text 更细：交易日再区分开盘前 / 交易中 / 午休 / 已收盘。
    """
    if is_monitor_window(now, schedule):
        return "交易中"

    day = now.date()
    if not is_trading_day(day, schedule):
        name = holiday_name(day, schedule)
        if name:
            return f"{name}休市"
        return "周末休市" if day.weekday() >= 5 else "休市"

    sessions = schedule.get("day_sessions") or []
    if not sessions:
        return "休市"
    current = now.time()
    if current < _parse(sessions[0][0]):
        return "开盘前"
    if current > _parse(sessions[-1][1]):
        return "已收盘"
    return "午休"


def seconds_to_next_open(now, schedule):
    """距下一个监控窗口开始的秒数。

    午休返回当天 13:30 之前的剩余秒数；收盘后返回下一个交易日 09:00 之前的
    剩余秒数。已在窗口内时返回 1（调用方本不应在窗口内调用）。
    """
    if is_monitor_window(now, schedule):
        return 1

    starts = [_parse(start) for start, _ in schedule.get("day_sessions", [])]
    if not starts:
        starts = [datetime.time(9, 0)]

    # 当天还没开始，或正处午休：等当前交易日的下一个时段
    if is_trading_day(now.date(), schedule):
        for start in starts:
            if now.time() < start:
                target = datetime.datetime.combine(now.date(), start)
                return max(int((target - now).total_seconds()), 1)

    # 收盘后 / 周末 / 节假日：向后找最近的交易日
    for offset in range(1, 16):
        day = now.date() + datetime.timedelta(days=offset)
        if is_trading_day(day, schedule):
            target = datetime.datetime.combine(day, starts[0])
            return max(int((target - now).total_seconds()), 1)
    return 3600  # 兜底：极端情况下 1 小时后重试

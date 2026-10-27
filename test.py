"""trigger.evaluate 单元测试（不依赖网络）。"""

from datetime import date, datetime, timedelta

import trading_calendar as tc
import trigger

SCHED_DAY = {"day_sessions": [["09:00", "11:30"], ["13:30", "15:30"]]}

CFG = {
    "alert": {
        "cooldown_seconds": 300,
        "price_tolerance": 0.01,
        "buy_below": None,
        "sell_above": None,
    },
    "schedule": SCHED_DAY,
}


def make_data(price, high=None, low=None, open_=None, prev=None, cross_ok=True):
    """high/low 仅作占位：判定已改为只看最新价，不再采信数据源极值字段。"""
    return {
        "price": price, "high": high if high is not None else price,
        "low": low if low is not None else price,
        "open": open_ if open_ is not None else price,
        "prev_close": prev if prev is not None else price,
        "cross_ok": cross_ok,
    }


def run():
    t0 = datetime(2026, 10, 5, 10, 0, 0)   # 盘中，非开盘播报窗口
    state = {}

    # 1. 日切首轮：以当前价初始化基准，不告警
    a = trigger.evaluate(make_data(900, 905, 895), state, CFG, now=t0)
    assert a == [], f"日切首轮不应告警: {a}"
    assert state["seen_high"] == 900 and state["seen_low"] == 900, state
    print("✅ 1 日切初始化（基准取当前价）")

    # 2. 价格刷新当日新高 -> 告警
    a = trigger.evaluate(make_data(908), state, CFG, now=t0 + timedelta(seconds=15))
    assert len(a) == 1 and a[0]["type"] == "high", f"应触发新高: {a}"
    print("✅ 2 创当日新高告警")

    # 3. 冷却期内再创新高 -> 不告警，但基准已更新
    a = trigger.evaluate(make_data(910), state, CFG, now=t0 + timedelta(seconds=60))
    assert a == [], f"冷却期内不应告警: {a}"
    assert state["seen_high"] == 910, state
    print("✅ 3 冷却期抑制重复告警")

    # 4. 冷却期过后再创新高 -> 告警
    a = trigger.evaluate(make_data(912), state, CFG, now=t0 + timedelta(seconds=400))
    assert len(a) == 1 and a[0]["type"] == "high", f"冷却后应告警: {a}"
    print("✅ 4 冷却期后恢复告警")

    # 5. 创当日新低 -> 告警（与新高独立冷却）
    a = trigger.evaluate(make_data(890), state, CFG, now=t0 + timedelta(seconds=420))
    assert len(a) == 1 and a[0]["type"] == "low", f"应触发新低: {a}"
    print("✅ 5 创当日新低告警")

    # 6. 无变化 -> 不告警
    a = trigger.evaluate(make_data(890), state, CFG, now=t0 + timedelta(seconds=440))
    assert a == [], f"无变化不应告警: {a}"
    print("✅ 6 无变化不告警")

    # 7. 跨日 -> 重置基准，不告警
    a = trigger.evaluate(make_data(880), state, CFG, now=t0 + timedelta(days=1))
    assert a == [] and state["seen_high"] == 880, (a, state)
    print("✅ 7 跨日重置")

    # 8. 数据源「最高」失真（890）不得触发新高
    st = {"date": "2026-10-05", "seen_high": 862.70, "seen_low": 862.30,
          "last_high_alert_ts": 0, "last_low_alert_ts": 0}
    a = trigger.evaluate(make_data(862.60, 890.00, 862.30), st, CFG, now=t0)
    assert a == [], f"high 失真不应告警: {a}"
    assert st["seen_high"] == 862.70, st
    print("✅ 8 数据源最高字段失真时不误报")

    # 9. 开盘窗口内首轮 -> 开盘播报
    st = {}
    a = trigger.evaluate(make_data(862.61), st, CFG, now=datetime(2026, 10, 6, 9, 2, 0))
    assert [x["type"] for x in a] == ["open"], a
    print("✅ 9 开盘后 5 分钟内首轮播报")

    # 10. 开盘窗口外启动 -> 不补发播报
    st = {}
    a = trigger.evaluate(make_data(862.61), st, CFG, now=datetime(2026, 10, 6, 9, 10, 0))
    assert a == [], f"窗口外不应播报: {a}"
    print("✅ 10 盘中启动不补发开盘播报")

    # 11. 交叉校验失败 -> 不判定、不改基准
    st = {"date": "2026-10-05", "seen_high": 862.30, "seen_low": 862.30,
          "last_high_alert_ts": 0, "last_low_alert_ts": 0}
    a = trigger.evaluate(make_data(900, cross_ok=False), st, CFG, now=t0)
    assert a == [] and st["seen_high"] == 862.30, (a, st)
    print("✅ 11 交叉校验失败时跳过判定")

    # 12. 价格精度：小数不再被 int() 截断
    cfg2 = dict(CFG, alert=dict(CFG["alert"], price_tolerance=0.001))
    st2 = {"date": "2026-10-05", "seen_high": 991.64, "seen_low": 991.00,
           "last_high_alert_ts": 0, "last_low_alert_ts": 0}
    a = trigger.evaluate(make_data(991.65), st2, cfg2, now=t0)
    assert len(a) == 1 and a[0]["type"] == "high", f"0.01 级变化应被识别: {a}"
    print("✅ 12 小数精度识别")

    # 13. 自定义买入价
    cfg3 = {"alert": {"cooldown_seconds": 300, "price_tolerance": 0.01,
                      "buy_below": 900, "sell_above": None},
            "schedule": SCHED_DAY}
    st3 = {"date": "2026-10-05", "seen_high": 1000, "seen_low": 850,
           "last_high_alert_ts": t0.timestamp(), "last_low_alert_ts": t0.timestamp()}
    a = trigger.evaluate(make_data(899), st3, cfg3, now=t0)
    assert any(x["type"] == "buy" for x in a), f"应触发买入价告警: {a}"
    print("✅ 13 自定义买入价告警")

    # 14. 消息展示的高/低取监控值，而非数据源字段
    st4 = {"date": "2026-10-05", "seen_high": 862.70, "seen_low": 862.30,
           "last_high_alert_ts": 0, "last_low_alert_ts": 0}
    d4 = make_data(862.60, 890.00, 857.00)
    trigger.evaluate(d4, st4, CFG, now=t0)
    assert (d4["range_high"], d4["range_low"]) == (862.70, 862.30), d4
    print("✅ 14 消息高/低取监控值")

    print("\n🎉 全部 trigger 测试通过")


SCHED = {
    "day_sessions": [["09:00", "11:30"], ["13:30", "15:30"]],
    "night_session": ["20:00", "02:30"],
    "use_cn_calendar": True,
    "holidays": [],
}

SCHED_NO_LIB = {
    "day_sessions": [["09:00", "11:30"], ["13:30", "15:30"]],
    "night_session": ["20:00", "02:30"],
    "use_cn_calendar": False,
    "holidays": ["2026-10-01"],
}


def run_calendar():
    # 交易日判定
    assert tc.is_trading_day(date(2026, 10, 8), SCHED) is True, "节后周四应为交易日"
    assert tc.is_trading_day(date(2026, 10, 1), SCHED) is False, "国庆应为休市"
    assert tc.is_trading_day(date(2026, 10, 4), SCHED) is False, "周日应休市"
    assert tc.is_trading_day(date(2026, 10, 10), SCHED) is False, "调休周六交易所仍休市"
    assert tc.is_trading_day(date(2026, 10, 12), SCHED) is True, "周一应为交易日"
    print("✅ 15 交易日判定（含节假日/调休）")

    # 日盘
    assert tc.is_market_open(datetime(2026, 10, 12, 10, 0), SCHED) is True
    assert tc.is_market_open(datetime(2026, 10, 12, 12, 0), SCHED) is False, "午休应休市"
    assert tc.is_market_open(datetime(2026, 10, 4, 10, 0), SCHED) is False, "周日日盘应休市"
    print("✅ 16 日盘开市判定（含午休）")

    # 夜盘：归属下一交易日
    assert tc.is_market_open(datetime(2026, 10, 12, 21, 0), SCHED) is True, "周一晚应开市"
    assert tc.is_market_open(datetime(2026, 10, 13, 1, 0), SCHED) is True, "周二凌晨承接周一晚"
    assert tc.is_market_open(datetime(2026, 10, 12, 1, 0), SCHED) is False, "周一凌晨无盘（周日夜不交易）"
    assert tc.is_market_open(datetime(2026, 10, 9, 21, 0), SCHED) is True, "周五晚应开市（归属下周一）"
    assert tc.is_market_open(datetime(2026, 10, 10, 1, 0), SCHED) is True, "周六凌晨承接周五晚"
    print("✅ 17 夜盘开市判定（跨日/跨周末归属）")

    # 节假日前一晚无夜市
    assert tc.is_market_open(datetime(2026, 9, 30, 21, 0), SCHED) is False, "国庆前夜应无夜市"
    assert tc.is_market_open(datetime(2026, 9, 24, 21, 0), SCHED) is False, "中秋前夜应无夜市"
    print("✅ 18 节假日前夜无夜市")

    # 无节假日库时的回退（自定义 holidays + 工作日规则）
    assert tc.is_trading_day(date(2026, 10, 1), SCHED_NO_LIB) is False, "自定义节假日应休市"
    assert tc.is_trading_day(date(2026, 10, 2), SCHED_NO_LIB) is True, "无库时按工作日"
    assert tc.is_trading_day(date(2026, 10, 3), SCHED_NO_LIB) is False, "无库时周六仍休市"
    print("✅ 19 无节假日库时回退")

    # 监控窗口：只认日盘，夜盘不监控
    assert tc.is_monitor_window(datetime(2026, 10, 12, 10, 0), SCHED) is True
    assert tc.is_monitor_window(datetime(2026, 10, 12, 12, 0), SCHED) is False, "午休应暂停"
    assert tc.is_monitor_window(datetime(2026, 10, 12, 21, 0), SCHED) is False, "夜盘不监控"
    assert tc.is_monitor_window(datetime(2026, 10, 13, 1, 0), SCHED) is False, "凌晨不监控"
    assert tc.is_monitor_window(datetime(2026, 10, 4, 10, 0), SCHED) is False, "周日不监控"
    print("✅ 20 监控窗口仅日盘")

    # 距下次开盘：开盘前、午休、收盘后、周末收盘
    assert tc.seconds_to_next_open(datetime(2026, 10, 12, 8, 0), SCHED) == 3600
    assert tc.seconds_to_next_open(datetime(2026, 10, 12, 12, 0), SCHED) == 5400
    assert tc.seconds_to_next_open(datetime(2026, 10, 12, 16, 0), SCHED) == 17 * 3600
    assert tc.seconds_to_next_open(datetime(2026, 10, 9, 16, 0), SCHED) == 65 * 3600, "周五收盘应等到周一 09:00"
    print("✅ 21 距下次开盘计算")

    print("\n🎉 全部交易日历测试通过")


def run_qq_channel():
    """用本地 mock 模拟 OneBot v11 协议端，验证 QQ 推送链路。"""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    import notifier

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            received.append((self.path, self.headers.get("Authorization"), body))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","retcode":0}')

        def log_message(self, *args):  # 静音 HTTP 日志
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    cfg = {"notify": {"qq": {
        "enabled": True,
        "api_base": f"http://127.0.0.1:{port}",
        "access_token": "testtoken",
        "timeout_seconds": 5,
        "rate_limit_per_minute": 20,
        "targets": {"groups": [123456], "users": [654321]},
    }}}
    data = {
        "price": 906.80, "open": 906.80, "prev_close": 905.90,
        "high": 908.20, "low": 896.33, "change_pct": 0.10, "unit": "元/克",
        "range_high": 906.80, "range_low": 896.33,
        "source": "新浪财经(gds_AUTD)", "cross_ok": True,
        "reference_price": 892.31, "deviation_pct": 1.62,
    }
    alerts = [{"type": "high", "label": "创当日新高", "price": 906.80}]

    assert notifier.notify(alerts, data, cfg) is True, "QQ 推送应成功"
    server.shutdown()
    server.server_close()

    paths = [p for p, _, _ in received]
    assert "/send_group_msg" in paths, f"缺少群消息请求: {paths}"
    assert "/send_private_msg" in paths, f"缺少私聊请求: {paths}"
    assert all(auth == "Bearer testtoken" for _, auth, _ in received), "鉴权头不正确"

    group_body = [b for p, _, b in received if p.endswith("send_group_msg")][0]
    assert group_body["group_id"] == 123456, group_body
    assert "创当日新高" in group_body["message"] and "906.80" in group_body["message"]
    user_body = [b for p, _, b in received if p.endswith("send_private_msg")][0]
    assert user_body["user_id"] == 654321, user_body
    print("✅ 22 QQ(OneBot) 群/私聊推送与鉴权")

    # 纯文本样式不应包含 markdown 标记
    text = notifier.build_message(alerts, data, "text")
    assert "**" not in text and "> " not in text and "创当日新高" in text
    print("✅ 23 QQ 纯文本样式渲染")

    # 开盘播报为独立模板，不含「创新高」字样
    opening = notifier.build_opening_message(data, "text")
    assert "今日开盘" in opening and "创新高" not in opening and "906.80" in opening
    print("✅ 24 开盘播报模板")

    # 未配置目标时应安全跳过
    empty_cfg = {"notify": {"qq": {"enabled": True, "api_base": "http://127.0.0.1:1",
                                   "access_token": "x", "targets": {}}}}
    assert notifier.notify(alerts, data, empty_cfg) is False, "未配置目标不应发送"
    print("✅ 25 未配置目标时安全跳过")

    print("\n🎉 全部 QQ 通道测试通过")


def run_engine():
    """验证监控引擎的启停、回调与分档间隔（mock 取数，不依赖网络）。"""
    import os
    import tempfile
    import time
    from unittest import mock

    import monitor

    fake = {
        "price": 906.80, "open": 906.80, "prev_close": 905.90,
        "high": 908.20, "low": 896.33, "change_pct": 0.10, "unit": "元/克",
        "source": "mock", "cross_ok": True,
        "reference_price": 892.31, "deviation_pct": 1.62,
    }
    cfg = {
        "sources": {"primary": {"code": "gds_AUTD", "unit": "元/克"}},
        "alert": {"cooldown_seconds": 300, "price_tolerance": 0.01,
                  "buy_below": None, "sell_above": None},
        "schedule": {
            "trading_interval_seconds": 15,
            "day_sessions": [["09:00", "11:30"], ["13:30", "15:30"]],
            "night_session": ["20:00", "02:30"],
            "use_cn_calendar": True, "holidays": [],
        },
        "notify": {"qq": {"enabled": False}},
    }

    events = []
    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "state.json")
        engine = monitor.MonitorEngine(
            cfg, state_path,
            on_data=lambda d: events.append(("data", d)),
            on_state=lambda r: events.append(("state", r)),
        )

        # 未启动时 stop 应安全
        engine.stop()
        assert not engine.is_running, "未启动时不应处于运行态"

        # 下次唤醒：监控时段内按间隔，其余等到下一个开盘
        assert engine.next_interval(datetime(2026, 10, 12, 10, 0)) == 15
        assert engine.next_interval(datetime(2026, 10, 12, 12, 0)) == 5400, "午休应等到 13:30"
        assert engine.next_interval(datetime(2026, 10, 12, 16, 0)) == 17 * 3600, "收盘后应等到次日 09:00"
        assert engine.next_interval(datetime(2026, 10, 11, 10, 0)) == 23 * 3600, "周日应等到周一 09:00"
        print("✅ 26 引擎下次唤醒时刻")

        with mock.patch.object(monitor.collector, "fetch_all", return_value=fake), \
             mock.patch.object(monitor.notifier, "notify_startup", return_value=True), \
             mock.patch.object(monitor.notifier, "notify", return_value=True), \
             mock.patch.object(monitor.trading_calendar, "is_monitor_window", return_value=True):
            engine.start()
            deadline = time.time() + 10
            while not any(e[0] == "data" for e in events) and time.time() < deadline:
                time.sleep(0.05)
            assert engine.is_running, "启动后应处于运行态"
            engine.stop()

        assert any(e[0] == "state" and e[1] is True for e in events), "应收到启动状态回调"
        assert any(e[0] == "state" and e[1] is False for e in events), "应收到停止状态回调"
        assert any(e[0] == "data" for e in events), "应收到数据回调"
        assert not engine.is_running, "stop 后不应处于运行态"
        assert os.path.exists(state_path), "应写入状态文件"
    print("✅ 27 引擎启停、回调与状态持久化")


def run_version():
    """版本号唯一来源校验：代码读取 config，文档标记与 config 一致。"""
    import json as _json
    import os as _os
    import tempfile

    import monitor
    import notifier

    base = _os.path.dirname(_os.path.abspath(__file__))
    with open(_os.path.join(base, "config.json"), encoding="utf-8") as fp:
        cfg = _json.load(fp)

    app_name, version = monitor.app_info(cfg)
    assert app_name and version, "config 应配置 app.name 与 app.version"

    # 缺失 app 段时回退到兜底值，不应抛异常
    assert monitor.app_info({}) == (monitor.DEFAULT_APP_NAME, monitor.DEFAULT_VERSION)

    # 引擎应从 config 读取版本号
    with tempfile.TemporaryDirectory() as tmp:
        engine = monitor.MonitorEngine(cfg, _os.path.join(tmp, "state.json"))
        assert engine.version == version, "引擎版本号应取自 config"
        assert engine.app_name == app_name

    # 启动通知应包含该版本号
    assert version in notifier.build_startup_message(None, cfg, version)



def run_icon():
    """窗口图标资源校验：文件齐备、ICO 结构合法且档位齐全、Tk 能实际加载。"""
    import os as _os
    import struct
    import sys as _sys
    from unittest import mock

    import appicon

    d = appicon.assets_dir()
    assert _os.path.isdir(d), f"图标目录不存在：{d}"
    for name in (appicon.ICO_NAME, appicon.PNG_NAME, appicon.GIF_NAME):
        path = _os.path.join(d, name)
        assert _os.path.isfile(path) and _os.path.getsize(path) > 0, f"图标缺失或为空：{name}"

    # ICO 目录结构：档位应为 16~256px 共 9 档，表项与数据偏移均须合法
    with open(_os.path.join(d, appicon.ICO_NAME), "rb") as fp:
        blob = fp.read()
    reserved, kind, count = struct.unpack("<HHH", blob[:6])
    assert reserved == 0 and kind == 1, "ICO 文件头非法"
    sizes, offsets = [], []
    for i in range(count):
        w, h, _, _, planes, bpp, length, offset = struct.unpack(
            "<BBBBHHII", blob[6 + 16 * i:22 + 16 * i])
        assert w == h and planes == 1 and bpp == 32, f"第 {i} 档 ICO 表项非法"
        assert offset + length <= len(blob), f"第 {i} 档数据越界"
        sizes.append(w or 256)
        offsets.append(offset)
    assert tuple(sizes) == appicon.EXPECTED_ICO_SIZES, f"ICO 档位不符：{sizes}"
    assert len(set(offsets)) == count, "ICO 各档数据偏移重复"
    print("✅ 28 图标资源齐备且 ICO 含 9 档尺寸")

    # 打包模式：存在 sys._MEIPASS 时应指向解包目录
    with mock.patch.object(_sys, "_MEIPASS", _os.path.join("fake", "meipass"), create=True):
        assert appicon.assets_dir() == _os.path.join("fake", "meipass", appicon.ASSET_DIR), \
            "打包模式下图标目录应取 sys._MEIPASS"

    # Windows 能否实际解析该 ICO（手工封装的容器需经系统 LoadImage 验证）
    if _os.name == "nt":
        import ctypes
        handle = ctypes.windll.user32.LoadImageW(
            None, _os.path.join(d, appicon.ICO_NAME), 1, 0, 0, 0x0010)  # IMAGE_ICON, LR_LOADFROMFILE
        assert handle, "Windows LoadImage 无法加载该 ICO"
        ctypes.windll.user32.DestroyIcon(handle)
        print("✅ 29 Windows 可正常加载该 ICO")

    # 真实加载（无图形环境时跳过，不影响其余用例）
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"⏭️  30 跳过 Tk 加载校验（无图形环境：{exc}）")
        return
    root.withdraw()
    try:
        applied = appicon.apply_icon(root)
        assert "png" in applied and "ico" in applied, f"图标未生效：{applied}"
        assert appicon._keep_alive, "PhotoImage 引用未保留，可能被 GC 回收"
    finally:
        root.destroy()
    print(f"✅ 30 Tk 实际加载生效（{applied}）")


def run_gui_defaults():
    """界面默认状态：监控默认关闭（不启引擎、不发通知）、日志区自动换行。"""
    import os as _os
    import tempfile
    import tkinter as tk

    import gui

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"⏭️  31 跳过界面默认状态校验（无图形环境：{exc}）")
        return
    root.withdraw()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {
                "app": {"name": "T", "version": "v9.9.9"},
                "runtime": {"state_file": _os.path.join(tmp, "state.json")},
            }
            app = gui.MonitorApp(root, cfg)
            assert app.toggle_var.get() is False, "界面启动时监控开关应为关闭"
            assert not app.engine.is_running, "界面启动时不应运行监控引擎"
            assert app.status_label.cget("text") == "● 已暂停", "启动时徽标应为「已暂停」"
            assert str(app.log_text.cget("wrap")) == "word", "日志区应自动换行"
            assert int(app.log_text.tag_cget("INFO", "lmargin2")) > 0, "日志续行应缩进"
            assert app.log_text.master.winfo_class() == "Frame", "日志文本框应位于容器 Frame 内"
            # 必须嵌在根窗口之下若干层：漏传 master 会让它直接挂到根窗口，挤坏整个布局
            # （缩放的窗口不参与真实布局计算，故用结构层级而非像素宽度来判定）
            depth, node = 0, app.log_text.master
            while node is not app.root and depth < 20:
                node, depth = node.master, depth + 1
            assert node is app.root and depth >= 3, f"日志文本框嵌套层级异常：depth={depth}"
            assert app.metric_labels["price"].cget("text") == "--", "未启动时指标应为占位符"
    finally:
        root.destroy()
    print("✅ 31 界面默认暂停且日志自动换行")


if __name__ == "__main__":
    run()
    run_calendar()
    run_qq_channel()
    run_engine()
    run_version()
    run_icon()
    run_gui_defaults()

"""
QQ 指令服务：接收 NapCat（OneBot v11）的 HTTP 上报，响应「查询金价」等指令。

数据流：
    NapCat --HTTP POST--> QueryServer --取数/读阈值--> notifier.push_qq_to() --回复原会话

· 只监听 127.0.0.1，零额外依赖（标准库 http.server）
· 先回 204 再异步处理：取数耗时 1~3 秒，同步等待会触发 NapCat 上报超时重试并重复回复
· 与监控循环完全解耦：不依赖 MonitorEngine，非监控时段也能按需取数
· 只读 state.json，不写入，不影响告警判定
· 不做人员/群白名单：能给该 QQ 发消息的人都能查询
"""

import json
import logging
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import collector
import notifier
import trigger

log = logging.getLogger("datamonitor")

# 上报内容体积上限（正常事件仅几 KB，超过视为异常请求）
MAX_BODY = 1 << 20
# 冷却记录表的清理阈值，避免长期运行无限增长
_COOLDOWN_PRUNE = 512

_CQ_RE = re.compile(r"\[CQ:[^\]]*\]")


def normalize(text):
    """规范化指令文本：剥离 CQ 码、统一全角井号、去掉首尾空白。

    群聊中 @ 机器人时 raw_message 形如 "[CQ:at,qq=123] #查询金价"，
    必须先剥离 CQ 码才能与配置的指令精确比对。
    """
    text = _CQ_RE.sub("", text or "")
    return text.replace("＃", "#").strip()


def _message_text(message):
    """取消息正文：string 格式直接返回，array 格式拼接各 text 段。"""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "".join(
            (seg.get("data") or {}).get("text", "")
            for seg in message
            if isinstance(seg, dict) and seg.get("type") == "text"
        )
    return ""


def _read_chunked(rfile, limit=MAX_BODY):
    """解析 chunked 请求体（http.server 不会自动解码）。

    NapCat 的 HTTP Webhook 由 Node 发送，未显式设置 Content-Length 时使用
    Transfer-Encoding: chunked，必须逐块读取，否则读到的长度恒为 0。
    """
    body = bytearray()
    while True:
        line = rfile.readline(64).strip()
        if not line:
            break
        try:
            size = int(line.split(b";")[0], 16)
        except ValueError:
            raise ValueError(f"chunked 长度非法：{line!r}") from None
        if size == 0:                       # 结束块，读掉可能的 trailer
            while True:
                trailer = rfile.readline(256)
                if trailer in (b"\r\n", b"\n", b""):
                    break
            break
        chunk = rfile.read(size)
        if len(chunk) != size:
            raise ValueError("chunked 数据不完整")
        body.extend(chunk)
        if len(body) > limit:
            raise ValueError(f"请求体过大：超过 {limit} 字节")
        rfile.read(2)                       # 丢弃 chunk 末尾的 CRLF
    if not body:
        raise ValueError("chunked 请求体为空")
    return bytes(body)


class QueryServer:
    """接收 OneBot v11 上报并回复查询指令的本地 HTTP 服务。"""

    def __init__(self, config, state_path, on_status=None):
        qcfg = config.get("query") or {}
        self.config = config
        self.state_path = state_path
        self.on_status = on_status        # callback() -> bool，用于展示监控运行状态
        self.enabled = bool(qcfg.get("enabled"))
        self.host = qcfg.get("listen_host") or "127.0.0.1"
        port = qcfg.get("listen_port")
        # 显式写 0 表示由系统分配空闲端口（测试用）；缺省才回落到 3001
        self.port = 3001 if port is None else int(port)
        self.commands = set(qcfg.get("commands") or ["#查询金价"])
        self.token = qcfg.get("access_token") or ""
        self.cooldown = float(qcfg.get("reply_cooldown_seconds") or 0)

        self._httpd = None
        self._thread = None
        self._last_reply = {}             # {(group_id, user_id): 上次回复时间戳}

    # ------------------------------------------------------------------ #
    @property
    def is_running(self):
        return self._httpd is not None

    def start(self):
        """启动监听（重复调用安全）。端口占用等失败只记日志，不影响主程序。"""
        if not self.enabled or self.is_running:
            return False
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), self._handler())
            self._httpd.daemon_threads = True
            self.port = self._httpd.server_address[1]   # 配置端口为 0 时取实际端口
        except OSError as exc:
            log.error("查询服务启动失败(%s:%s)：%s", self.host, self.port, exc)
            self._httpd = None
            return False
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="qq-query", daemon=True
        )
        self._thread.start()
        log.info("查询服务已启动：http://%s:%s ｜ 指令：%s ｜ 上报鉴权：%s",
                 self.host, self.port, "、".join(sorted(self.commands)),
                 "开启" if self.token else "关闭")
        return True

    def stop(self, timeout=5):
        """停止监听（重复调用安全）。"""
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        log.info("查询服务已停止")

    # ------------------------------------------------------------------ #
    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                outer._on_post(self)

            def log_message(self, *args):   # 静音 http.server 的默认访问日志
                pass

        return Handler

    def _on_post(self, handler):
        """接收上报：鉴权 -> 读 body -> 立即回执 -> 异步处理。"""
        auth = handler.headers.get("Authorization")
        if not self._auth_ok(auth):
            # 两侧 token 不一致是这块最常见的故障：把收到值打出来，省得反复猜
            log.warning(
                "上报鉴权失败：收到 %s，期望 Bearer ****%s",
                auth or "（无 Authorization 头）", self.token[-4:],
            )
            handler.send_error(401, "Unauthorized")
            return
        try:
            event = json.loads(self._read_body(handler).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            # 解析失败时连同请求头一起记录：多半是上报格式与预期不符，便于定位
            log.warning("上报解析失败: %s ｜ 请求头: %s", exc, dict(handler.headers))
            handler.send_error(400, "Bad Request")
            return

        handler.send_response(204)          # 先回执：取数耗时不能阻塞 NapCat
        handler.end_headers()
        threading.Thread(target=self._handle, args=(event,), daemon=True).start()

    def _read_body(self, handler):
        """读取请求体，兼容 Content-Length 与 chunked 两种传输方式。

        NapCat 的 HTTP Webhook 由 Node 发送，未显式设置 Content-Length 时会使用
        Transfer-Encoding: chunked —— 此时 Content-Length 为空，http.server 也不会
        自动解码，必须自己逐块读取。早期版本只认 Content-Length，导致全部上报被
        以 400 拒绝、NapCat 反复重试。
        """
        if "chunked" in (handler.headers.get("Transfer-Encoding") or "").lower():
            return _read_chunked(handler.rfile)
        length = int(handler.headers.get("Content-Length") or 0)
        if length <= 0:
            raise ValueError("请求体为空（既无 Content-Length，也不是 chunked）")
        if length > MAX_BODY:
            raise ValueError(f"请求体过大：{length} 字节")
        return handler.rfile.read(length)

    def _auth_ok(self, header):
        """校验上报来源；未配置 access_token 时一律放行（端口仅监听 127.0.0.1）。"""
        if not self.token:
            return True
        return (header or "") == f"Bearer {self.token}"

    # ------------------------------------------------------------------ #
    def _handle(self, event):
        """处理单条事件：过滤 -> 识别指令 -> 防抖 -> 回复。"""
        try:
            if not isinstance(event, dict) or event.get("post_type") != "message":
                return
            msg_type = event.get("message_type")
            if msg_type not in ("group", "private"):
                return

            user_id = event.get("user_id")
            if user_id and user_id == event.get("self_id"):
                return      # 自身消息：即便 NapCat 未关闭上报也不会自问自答

            text = normalize(
                event.get("raw_message") or _message_text(event.get("message"))
            )
            if text not in self.commands:
                if text.startswith("#"):    # 疑似指令却没命中，留痕便于排查错字/空格
                    log.info("指令未匹配: %r ｜ 已配置: %s", text, sorted(self.commands))
                return

            group_id = event.get("group_id") if msg_type == "group" else None
            log.info("收到查询指令：群=%s 用户=%s", group_id or "-", user_id)
            if not self._debounce_ok(group_id, user_id):
                return
            self._reply(group_id, user_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("处理 QQ 指令失败: %s", exc)

    def _debounce_ok(self, group_id, user_id):
        """同一提问者在冷却时间内重复查询只回一次，避免刷屏与触发风控。"""
        if self.cooldown <= 0:
            return True
        now = time.time()
        key = (group_id, user_id)
        if now - self._last_reply.get(key, 0) < self.cooldown:
            log.info("查询冷却中，忽略：群=%s 用户=%s", group_id, user_id)
            return False
        self._last_reply[key] = now
        if len(self._last_reply) > _COOLDOWN_PRUNE:
            cutoff = now - max(self.cooldown, 60) * 2
            self._last_reply = {
                k: v for k, v in self._last_reply.items() if v >= cutoff
            }
        return True

    def _reply(self, group_id, user_id):
        """取数 -> 读阈值 -> 渲染 -> 发回事件所在会话。"""
        qq_cfg = (self.config.get("notify") or {}).get("qq") or {}
        data = collector.fetch_all(self.config)
        state = trigger.load_state(self.state_path) if data else None
        running = self.on_status() if self.on_status else None
        text = notifier.build_query_message(data, state, self.config, running)

        target = f"群 {group_id}" if group_id else f"好友 {user_id}"
        at_user = user_id if group_id else None
        if notifier.push_qq_to(text, qq_cfg, group_id=group_id,
                               user_id=user_id, at_user=at_user):
            log.info("✅ 金价查询已回复 %s", target)
        else:
            log.error("金价查询回复失败 %s", target)

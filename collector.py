"""
金价取数模块（基于 requests，无 Selenium / 浏览器依赖）。

主源：新浪财经 gds_AUTD（黄金延期 Au(T+D)，单位 元/克）
校验：伦敦金 hf_XAU（美元/盎司） × 美元人民币 USDCNY 推算的国内理论价
      用于检测主源是否返回异常（容差 cross_check.tolerance_pct）

新浪接口要点：
- 必须携带 Referer: https://finance.sina.com.cn，否则返回空
- 返回 GBK 编码文本，需手动指定 encoding
- 返回形如 var hq_str_gds_AUTD="最新价,涨跌额,昨收,今开,最高,最低,时间,买一,卖一,成交量,买一量,卖一量,日期,名称";
"""

import logging
import re
from datetime import datetime

import requests

log = logging.getLogger("datamonitor")

SINA_QUOTE_URL = "https://hq.sinajs.cn/list={codes}"
SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}
# 金衡盎司 -> 克
OUNCE_IN_GRAM = 31.1034768
_LINE_RE = re.compile(r'var hq_str_(\w+)="([^"]*)"')


def _fetch_sina_raw(codes, timeout=10):
    """请求新浪行情接口，返回 {code: 原始字段串}。"""
    resp = requests.get(
        SINA_QUOTE_URL.format(codes=",".join(codes)),
        headers=SINA_HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    resp.encoding = "gbk"  # 新浪返回 GBK，不指定会乱码
    result = {m.group(1): m.group(2) for m in _LINE_RE.finditer(resp.text)}
    if not result:
        raise ValueError("新浪接口未返回任何有效数据（可能被限流或网络异常）")
    return result


def _to_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_sina(code):
    """获取单个新浪品种的行情，返回统一 dict。数据为空或价格异常时抛异常。"""
    raw = _fetch_sina_raw([code])
    line = raw.get(code, "")
    if not line:
        raise ValueError(f"新浪代码 {code} 返回为空")

    f = line.split(",")
    quote = {
        "code": code,
        "name": f[13] if len(f) > 13 else code,
        "price": _to_float(f[0]),
        "change": _to_float(f[1]) if len(f) > 1 else 0.0,
        "prev_close": _to_float(f[2]) if len(f) > 2 else 0.0,
        "open": _to_float(f[3]) if len(f) > 3 else 0.0,
        "high": _to_float(f[4]) if len(f) > 4 else 0.0,
        "low": _to_float(f[5]) if len(f) > 5 else 0.0,
        "time": f[6] if len(f) > 6 else "",
        "bid": _to_float(f[7]) if len(f) > 7 else 0.0,
        "ask": _to_float(f[8]) if len(f) > 8 else 0.0,
        "date": f[12] if len(f) > 12 else "",
        "source": f"新浪财经({code})",
    }
    if quote["price"] <= 0:
        raise ValueError(f"新浪代码 {code} 价格异常: {quote['price']}")
    return quote


def fetch_fx_implied(gold_code, fx_code):
    """用 伦敦金(美元/盎司) 与 美元人民币汇率 推算国内理论金价（元/克）。"""
    raw = _fetch_sina_raw([gold_code, fx_code])
    gold = raw.get(gold_code, "").split(",")
    fx = raw.get(fx_code, "").split(",")
    if not gold or not gold[0]:
        raise ValueError(f"{gold_code} 返回为空")
    if len(fx) < 2 or not fx[1]:
        raise ValueError(f"{fx_code} 返回为空")
    usd_per_oz = _to_float(gold[0])
    usdcny = _to_float(fx[1])
    if usd_per_oz <= 0 or usdcny <= 0:
        raise ValueError("推算源数值异常")
    return usd_per_oz / OUNCE_IN_GRAM * usdcny


def fetch_all(config, retries=1):
    """取主源并做交叉校验，返回统一结构；彻底失败返回 None。

    返回字段：price/prev_close/open/high/low/bid/ask/change_pct/source/ts/
              cross_ok/reference_price/deviation_pct
    """
    src = config["sources"]
    primary_cfg = src["primary"]

    quote = None
    last_err = None
    for attempt in range(retries + 1):
        try:
            quote = fetch_sina(primary_cfg["code"])
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log.warning("主源取数失败(第 %s 次): %s", attempt + 1, exc)

    if quote is None:
        log.error("主源取数彻底失败: %s", last_err)
        return None

    result = dict(quote)
    result["unit"] = primary_cfg.get("unit", "元/克")
    result["ts"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["cross_ok"] = True
    result["reference_price"] = None
    result["deviation_pct"] = None

    if quote["prev_close"]:
        result["change_pct"] = round(
            (quote["price"] - quote["prev_close"]) / quote["prev_close"] * 100, 2
        )
    else:
        result["change_pct"] = 0.0

    # 交叉校验：主源 vs 伦敦金+汇率推算价
    cc = config.get("cross_check", {})
    ref_cfg = src.get("reference")
    if cc.get("enabled") and ref_cfg and ref_cfg.get("type") == "fx_implied":
        try:
            ref = fetch_fx_implied(ref_cfg["gold_code"], ref_cfg["fx_code"])
            dev = (quote["price"] - ref) / ref * 100
            result["reference_price"] = round(ref, 2)
            result["deviation_pct"] = round(dev, 2)
            if abs(dev) > cc.get("tolerance_pct", 2.5):
                result["cross_ok"] = False
                log.warning(
                    "数据源偏差过大：主源=%.2f 推算=%.2f 偏差=%.2f%%",
                    quote["price"], ref, dev,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("交叉校验失败(不影响主源): %s", exc)

    log.info(
        "取数成功 %s | 最新=%.2f 高=%.2f 低=%.2f 昨收=%.2f 涨跌=%.2f%%",
        result["source"], result["price"], result["high"],
        result["low"], result["prev_close"], result["change_pct"],
    )
    return result


if __name__ == "__main__":
    import json
    import os

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    base = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base, "config.json"), "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    print(json.dumps(fetch_all(cfg), ensure_ascii=False, indent=2))

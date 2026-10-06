"""
窗口图标：把 assets/ 下的图标应用到 Tk 窗口。

资源由 `tools/make_appicon.py` 从像素画源图生成，共三种格式，互为降级：

    appicon.ico   Windows 原生多尺寸（16~256px 共 9 档），标题栏 / 任务栏 /
                  Alt-Tab 由系统按 DPI 挑选最合适的一档，最清晰
    appicon.png   Tk 8.6+ 原生支持 PNG，iconphoto 跨平台首选
    appicon.gif   旧版 Tk 不支持 PNG 时的兜底

Windows 上 `.ico` 放在最后设置：后设置者覆盖前者，让系统拿到多尺寸图标，
而不是把单张位图交给系统缩放。任一资源缺失或加载失败都不会影响程序启动。

本模块不依赖项目内其他模块（日志实例由调用方注入），因此也可被打包入口、
托盘程序等独立复用。
"""

import logging
import os
import sys
import tkinter as tk

log = logging.getLogger(__name__)

ASSET_DIR = "assets"
ICO_NAME = "appicon.ico"
PNG_NAME = "appicon.png"
GIF_NAME = "appicon.gif"

# .ico 内应包含的尺寸档位，由 tools/make_appicon.py 生成、test.py 交叉校验
EXPECTED_ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# PhotoImage 必须保留引用：一旦被垃圾回收，窗口图标会同步消失
_keep_alive = []


def assets_dir():
    """图标目录；兼容 PyInstaller 单文件模式（资源解包到 sys._MEIPASS）。"""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, ASSET_DIR)


def _load_photo(path, logger):
    """读取图片为 PhotoImage；失败返回 None（旧版 Tk 不支持 PNG 属正常）。"""
    try:
        photo = tk.PhotoImage(file=path)
    except tk.TclError as exc:
        logger.debug("图标加载失败：%s（%s）", path, exc)
        return None
    _keep_alive.append(photo)
    return photo


def apply_icon(root, directory=None, logger=None):
    """把图标应用到窗口，返回生效的方式（如 "png+ico"），全部失败返回空串。

    必须在 Tk 根窗口创建之后、窗口显示之前调用，否则标题栏会先闪一下
    默认的 Tk 羽毛图标。

    logger 默认用本模块的日志实例；由 gui.py 传入项目日志实例，即可让消息
    并入 monitor.log 与界面日志面板。
    """
    logger = logger or log
    directory = directory or assets_dir()
    applied = []

    for name, label in ((PNG_NAME, "png"), (GIF_NAME, "gif")):
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        photo = _load_photo(path, logger)
        if photo is None:
            continue
        try:
            root.iconphoto(True, photo)     # True：同时作为后续 Toplevel 的默认图标
            applied.append(label)
            break
        except tk.TclError as exc:
            logger.debug("iconphoto 失败（%s）：%s", path, exc)

    ico = os.path.join(directory, ICO_NAME)
    if os.path.isfile(ico):
        try:
            root.iconbitmap(default=ico)    # 之后创建的 Toplevel（对话框等）
            root.iconbitmap(ico)            # 当前窗口
            applied.append("ico")
        except tk.TclError as exc:
            logger.debug("iconbitmap 失败（%s）：%s", ico, exc)

    if applied:
        logger.info("窗口图标已应用：%s", "+".join(applied))
    else:
        logger.warning("窗口图标未应用：%s 下无可用图标资源", directory)
    return "+".join(applied)

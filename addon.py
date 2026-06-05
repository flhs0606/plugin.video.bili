# -*- coding:utf-8 -*-
"""B站插件入口 — Kodi 每次导航时启动全新进程，生成目录列表后立即退出。

v0.4.0 在每个 CPythonInvoker 进程里起一个 daemon HTTP server
服务静态 MPD。原因：用户的 Kodi 部署上 xbmc.service 扩展点
并不可靠启动 (Kodi log 完全没有任何 [plugin.video.bili] service:
trace 行)；daemon thread 在 addon.py 进程里 bind 54321 是
fallback。inputstream.adaptive 在 Kodi 主进程发 HTTP 拉 MPD。
"""
import atexit
import os
import sys
import threading
import time
import traceback

import xbmc

from core import plugin

# 导入 routes 模块以注册所有路由（@plugin.route 装饰器在导入时自动注册）
import routes  # noqa: F401

# atexit 兜底：Kodi 进程退出时关掉所有 live danmaku 子线程，释放 socket。
# 新 CPythonInvoker 进程里 _instances 是空的，但本进程内的实例会被正常清理。
def _atexit_cleanup():
    try:
        mod = __import__('live.danmaku', fromlist=['stop_all_live_danmaku'])
        mod.stop_all_live_danmaku()
        # 释放所有 lock 文件（这些 lock 是本进程创建的，进程退时清掉）
        bp_mod = __import__('utils', fromlist=['get_temp_path'])
        bp = bp_mod.get_temp_path()
        if bp:
            import os as _os
            import glob as _glob
            for lock in _glob.glob(_os.path.join(bp, 'danmaku_*.lock')):
                try:
                    with open(lock, 'r', encoding='utf-8') as f:
                        first = f.read().split('\n', 1)[0]
                    if first == str(_os.getpid()):
                        _os.remove(lock)
                except Exception:
                    pass
    except Exception:
        pass

atexit.register(_atexit_cleanup)


_mpd_server_started = False


def _start_mpd_server_daemon():
    """在当前进程里启一个 daemon 线程跑静态 MPD HTTP server。

    端口 54321。返回 True 表示已 bind, False 表示失败。
    整个 plugin run 期间反复调用是 idempotent。
    """
    global _mpd_server_started
    if _mpd_server_started:
        return True
    try:
        from http_server import get_http_server
        from utils import getSetting
        port = int(getSetting('server_port') or 54321)
        httpd = get_http_server(port=port)
        if not httpd:
            # 端口已被占用 — 通常意味着 service.py 进程或
            # 前一个 CPythonInvoker 进程仍占着。MPD server
            # 反正已在那台机器上 listen, 当前导航仍能拉 MPD,
            # 不必本地起。降为 debug 而不是 warning。
            xbmc.log(
                '[plugin.video.bili] addon: MPD server :%s already '
                'in use — using existing listener' % port,
                xbmc.LOGDEBUG,
            )
            return False

        def _serve():
            xbmc.log(
                '[plugin.video.bili] addon: MPD server thread start',
                xbmc.LOGINFO,
            )
            try:
                while True:
                    httpd.handle_request()
            except Exception as e:
                xbmc.log(
                    '[plugin.video.bili] addon: MPD server thread error: %s'
                    % e, xbmc.LOGERROR,
                )
            finally:
                try:
                    httpd.server_close()
                except Exception:
                    pass
                xbmc.log(
                    '[plugin.video.bili] addon: MPD server thread end',
                    xbmc.LOGINFO,
                )

        t = threading.Thread(target=_serve, name='mpd-static-server',
                             daemon=True)
        t.start()
        xbmc.log(
            '[plugin.video.bili] addon: MPD server listening on 0.0.0.0:%s'
            % port, xbmc.LOGINFO,
        )
        _mpd_server_started = True
        return True
    except Exception:
        xbmc.log(
            '[plugin.video.bili] addon: MPD server FATAL\n%s'
            % traceback.format_exc(), xbmc.LOGERROR,
        )
        return False


_start_mpd_server_daemon()

# 注意：Kodi 每次导航都是全新 Python 进程，内存缓存不跨进程共享。
# clear_function_cache() 在新鲜进程中为 no-op，此处不调用。
# 缓存的持久化部分由 login/logout 路由负责刷新。

if __name__ == '__main__':
    plugin.run()

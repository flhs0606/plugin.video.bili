# -*- coding:utf-8 -*-
"""B站插件入口 — Kodi 每次导航时启动全新进程，生成目录列表后立即退出。

v0.4.0 在每个 CPythonInvoker 进程里起一个 daemon HTTP server
服务静态 MPD。原因：用户的 Kodi 部署上 xbmc.service 扩展点
并不可靠启动 (Kodi log 完全没有任何 [plugin.video.bili] service:
trace 行)；daemon thread 在 addon.py 进程里 bind 54321 是
fallback。inputstream.adaptive 在 Kodi 主进程发 HTTP 拉 MPD。
"""
import os
import sys
import threading
import time
import traceback

import xbmc

from core import plugin

# 导入 routes 模块以注册所有路由（@plugin.route 装饰器在导入时自动注册）
import routes  # noqa: F401


_MPD_SERVER_STARTED = False
_MPD_SERVER_LOCK = threading.Lock()


def _start_mpd_server_daemon():
    """在当前进程里启一个 daemon 线程跑静态 MPD HTTP server。

    端口 54321。返回 True 表示已 bind, False 表示失败。
    整个 plugin run 期间反复调用是 idempotent。
    """
    global _MPD_SERVER_STARTED
    with _MPD_SERVER_LOCK:
        if _MPD_SERVER_STARTED:
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
            _MPD_SERVER_STARTED = True
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

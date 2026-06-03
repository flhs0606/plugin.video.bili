# -*- coding:utf-8 -*-
"""B站插件入口 — Kodi 每次导航时启动全新进程，生成目录列表后立即退出。

v0.4.0 启动一个 daemon HTTP server (54321)，专门用于给 inputstream.adaptive
服务 MPD 静态文件。这绕过了 v0.3.0 依赖 xbmc.service 进程的方案 —
xbmc.service 在某些 Kodi 部署上启动不稳定 (Android / WebOS / CoreELEC
容器化), 我们改在每个 CPythonInvoker 进程里启 daemon, 监听 54321。
Kodi 主进程的 inputstream.adaptive 通过 localhost 连过来即可。
"""
import os
import sys
import threading
import traceback

import xbmc

from core import plugin

# 导入 routes 模块以注册所有路由（@plugin.route 装饰器在导入时自动注册）
import routes  # noqa: F401


def _start_mpd_server_daemon():
    """在当前进程里启一个 daemon 线程跑静态 MPD HTTP server。

    端口 54321。返回 True 表示已 bind, False 表示失败 (可能是上一
    个进程已 bind, OS 仍持有端口)。无论失败与否都注册 atexit 清理。
    """
    try:
        from http_server import get_http_server
        from utils import getSetting
        port = int(getSetting('server_port') or 54321)
        httpd = get_http_server(port=port)
        if not httpd:
            xbmc.log(
                '[plugin.video.bili] addon: MPD server bind FAILED on :%s'
                % port, xbmc.LOGWARNING,
            )
            return False

        def _serve():
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

        t = threading.Thread(target=_serve, name='mpd-static-server',
                             daemon=True)
        t.start()
        xbmc.log(
            '[plugin.video.bili] addon: MPD server listening on 0.0.0.0:%s'
            % port, xbmc.LOGINFO,
        )
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

# -*- coding: utf-8 -*-
"""Long-lived service: bind 54321 and serve the static MPD file.

In v0.4.0 this is the PRIMARY listener. The addon.py daemon-thread
fallback kicks in only when this service fails to bind (which
happens if service.py crashes or isn't started by Kodi).

Diagnostic philosophy: any failure in this file must surface in
xbmc.log. Service processes in Kodi are otherwise silent; if the
import chain breaks at startup, we have no way to know without
explicit try/except + log.
"""
import sys
import traceback


# ── Top-level diagnostic: import xbmc FIRST so we can log later ──
try:
    import xbmc
    xbmc.log('[plugin.video.bili] service: import xbmc OK', xbmc.LOGINFO)
except Exception:
    sys.stderr.write('plugin.video.bili service: FATAL cannot import xbmc\n')
    sys.stderr.write(traceback.format_exc() + '\n')
    sys.exit(1)

# ── Phase 1: xbmcvfs (needed by http_server) ──
try:
    import xbmcvfs
    xbmc.log('[plugin.video.bili] service: import xbmcvfs OK', xbmc.LOGINFO)
except Exception as e:
    xbmc.log('[plugin.video.bili] service: FATAL import xbmcvfs: %s\n%s' % (
        e, traceback.format_exc()), xbmc.LOGERROR)
    sys.exit(1)

# ── Phase 2: http_server ──
try:
    from http_server import get_http_server
    xbmc.log('[plugin.video.bili] service: import http_server OK', xbmc.LOGINFO)
except Exception as e:
    xbmc.log('[plugin.video.bili] service: FATAL import http_server: %s\n%s' % (
        e, traceback.format_exc()), xbmc.LOGERROR)
    sys.exit(1)

# Live danmaku is intentionally NOT imported here. service.py used
# to call stop_all_live_danmaku() at shutdown, but that requires
# importing live.danmaku which pulls in xbmcvfs + zlib + thread +
# WebSocket deps. None of those are needed for serving the MPD.
# The danmaku threads' atexit hook handles cleanup on their own.


def run():
    from utils import getSetting  # 推迟到 run() 再 import，启动时少解析一个模块
    try:
        port = int(getSetting('server_port') or 54321)
    except Exception as e:
        xbmc.log(
            '[plugin.video.bili] service: cannot read server_port: %s; '
            'falling back to 54321' % e, xbmc.LOGWARNING,
        )
        port = 54321

    try:
        httpd = get_http_server(port=port)
    except Exception as e:
        xbmc.log(
            '[plugin.video.bili] service: FATAL get_http_server: %s\n%s' % (
                e, traceback.format_exc(),
            ), xbmc.LOGERROR,
        )
        return

    if not httpd:
        xbmc.log(
            '[plugin.video.bili] service: bind FAILED on :%s — port held '
            'by another process' % port, xbmc.LOGERROR,
        )
        return

    # Monitor.onSettingsChanged() 在用户改本插件设置并点确定时触发。
    # Kodi 默认不会自动重跑 plugin 列表（用户回到菜单看到的是改之前的
    # 状态），这里主动 Container.Refresh 强制重新求值 function.* 开关。
    # 必须用子类覆盖 onSettingsChanged 钩子；Kodi 在 settings dialog
    # close 后从内部线程调一次，waitForAbort 不会阻塞它。
    class _SettingsMonitor(xbmc.Monitor):
        def onSettingsChanged(self):
            xbmc.log(
                '[plugin.video.bili] service: onSettingsChanged -> Container.Refresh',
                xbmc.LOGINFO,
            )
            xbmc.executebuiltin('Container.Refresh')

    monitor = _SettingsMonitor()
    xbmc.log(
        '[plugin.video.bili] service: MPD server listening on 0.0.0.0:%s' % port,
        xbmc.LOGINFO,
    )

    try:
        while not monitor.abortRequested():
            httpd.handle_request()
            if monitor.waitForAbort(0.5):
                break
    except Exception as e:
        xbmc.log(
            '[plugin.video.bili] service: serve loop error: %s\n%s' % (
                e, traceback.format_exc(),
            ), xbmc.LOGERROR,
        )
    finally:
        xbmc.log('[plugin.video.bili] service: shutting down', xbmc.LOGINFO)
        try:
            httpd.server_close()
        except Exception:
            pass


if __name__ == '__main__':
    run()

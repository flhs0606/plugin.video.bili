# -*- coding: utf-8 -*-
"""Long-lived service: host the static MPD HTTP server.

In v0.4.0 the server only serves `{cid}.mpd` from
`special://temp/plugin.video.bili/` (see http_server.py). On exit we
stop all live-danmaku WebSocket threads so Kodi shutdown is clean.
"""
import xbmc
from http_server import get_http_server
from live.danmaku import stop_all_live_danmaku


def run():
    from utils import getSetting  # local import; utils is fine in service
    port = getSetting('server_port') or 54321
    httpd = get_http_server(port=int(port))
    if not httpd:
        xbmc.log(
            '[plugin.video.bili] service: failed to bind 0.0.0.0:%s'
            % port, xbmc.LOGERROR,
        )
        return

    monitor = xbmc.Monitor()
    xbmc.log(
        '[plugin.video.bili] service: MPD server listening on 0.0.0.0:%s'
        % port, xbmc.LOGINFO,
    )

    try:
        while not monitor.abortRequested():
            # handle_request() is blocking with no timeout; pair with
            # waitForAbort(0.5) so the abort flag is checked ~twice per
            # second. The .5s ceiling is invisible to Kodi (HTTP requests
            # complete in milliseconds).
            httpd.handle_request()
            if monitor.waitForAbort(0.5):
                break
    finally:
        xbmc.log('[plugin.video.bili] service: shutting down', xbmc.LOGINFO)
        try:
            httpd.server_close()
        except Exception:
            pass
        stop_all_live_danmaku()


if __name__ == '__main__':
    run()

# -*- coding: utf-8 -*-
from monitor import BilibiliMonitor
from live_danmaku import stop_all_live_danmaku
import xbmc


def run():
    sleep_time = 3  # 提高退出响应速度（原10s太慢）
    monitor = BilibiliMonitor()

    monitor.remove_temp_dir()

    while not monitor.abortRequested():
        if monitor.waitForAbort(sleep_time):
            break

    # 退出前停止所有直播弹幕线程，避免 WebSocket 残留阻塞 Kodi
    xbmc.log('[plugin.video.bili] service shutting down, stopping live danmaku...', xbmc.LOGINFO)
    stop_all_live_danmaku()

    if monitor.httpd:
        monitor.shutdown_httpd()


if __name__ == '__main__':
    run()

# -*- coding: utf-8 -*-
"""MPD server: serve the static `{cid}.mpd` file that
inputstream.adaptive reads to discover B 站 segment URLs.

Single job: serve `special://temp/plugin.video.bili/*.mpd`. There is
no segment proxy (segments go directly from inputstream.adaptive to
B 站 CDN with `stream_headers=Referer=…`). Anything else returns 404.

Bind lifecycle is owned by the caller (service.py primary,
addon.py daemon-thread fallback). This module just provides the
request handler class and the bind helper.
"""
from http import server as BaseHTTPServer
import os
import re
import socket
import xbmcvfs


class BilibiliRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def __init__(self, request, client_address, server):
        self.addon_id = 'plugin.video.bili'
        try:
            self.base_path = xbmcvfs.translatePath(
                'special://temp/%s' % self.addon_id
            ).decode('utf-8')
        except AttributeError:
            self.base_path = xbmcvfs.translatePath(
                'special://temp/%s' % self.addon_id
            )
        self.base_path = os.path.realpath(self.base_path)
        self.chunk_size = 1024 * 64
        BaseHTTPServer.BaseHTTPRequestHandler.__init__(
            self, request, client_address, server,
        )

    def _safe_file_path(self, url_path):
        """Resolve a URL path to a local file under special://temp/<addon_id>.

        Returns the absolute file path or None if the path is unsafe or
        doesn't end in `.mpd`. The path-traversal guard follows the
        v0.3.0 implementation.
        """
        qpos = url_path.find('?')
        if qpos != -1:
            url_path = url_path[:qpos]
        if not url_path.endswith('.mpd'):
            return None
        safe = url_path.strip('/').strip('\\')
        parts = [p for p in safe.replace('\\', '/').split('/') if p and p != '..']
        safe = '/'.join(parts)
        file_path = os.path.join(self.base_path, safe)
        file_path = os.path.realpath(file_path)
        if (
            not file_path.startswith(self.base_path + os.sep)
            and file_path != self.base_path
        ):
            return None
        return file_path

    def _send_mpd(self, file_path):
        """公共：发送 MPD 文件内容（GET 用 stream，HEAD 用 header-only）。"""
        if not os.path.isfile(file_path):
            self.send_error(404, 'File Not Found')
            return
        size = os.path.getsize(file_path)
        self.send_response(200)
        # inputstream.adaptive needs the standard DASH MIME type
        # to auto-detect the manifest format. application/xml+dash
        # is non-standard and adaptive rejects it.
        self.send_header('Content-Type', 'application/dash+xml')
        self.send_header('Content-Length', size)
        self.end_headers()
        # GET 时 method 不是 HEAD 才写 body
        if self.command != 'HEAD':
            try:
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(self.chunk_size)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except IOError:
                pass

    def do_GET(self):
        file_path = self._safe_file_path(self.path)
        if not file_path:
            self.send_error(404 if not self.path.endswith('.mpd') else 403,
                            'Not Found' if not self.path.endswith('.mpd') else 'Forbidden')
            return
        self._send_mpd(file_path)

    def do_HEAD(self):
        # HEAD 走和 GET 一样的逻辑；_send_mpd 看 self.command 决定是否发 body
        self.do_GET()

    def log_message(self, format, *args):
        # Silence BaseHTTPServer's stderr logger; Kodi logs are sufficient.
        return


def get_mpd_server(address=None, port=None):
    """Bind and return a HTTPServer. Caller is responsible for serving.

    `port` defaults to 54321 (the historical default and Kodi addon
    setting default). The address defaults to 0.0.0.0. Returns None if
    the bind fails (port already in use).

    SO_REUSEADDR is enabled on the server class so that the OS lets
    us rebind a port that is still in TIME_WAIT from a recently
    dead CPythonInvoker process. Without this, every navigation
    after the first one logs 'bind FAILED' until the OS reclaims
    the port.
    """
    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', address or ''):
        address = '0.0.0.0'
    port = int(port) if port else 54321
    try:
        # Set the class attribute before instantiating so the
        # socketserver base picks it up during bind().
        BaseHTTPServer.HTTPServer.allow_reuse_address = True
        server = BaseHTTPServer.HTTPServer(
            (address, port), BilibiliRequestHandler,
        )
        return server
    except socket.error:
        return None

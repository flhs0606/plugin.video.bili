# -*- coding: utf-8 -*-
"""Local HTTP server: serve the static MPD file that inputstream.adaptive
reads to discover B 站 segment URLs.

In v0.4.0 this server has a single job: serve `{cid}.mpd` from
`special://temp/plugin.video.bili/`. There is no segment proxy
(segments go directly from inputstream.adaptive to B 站 CDN with
`stream_headers=Referer=…`). Anything else returns 404.
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

    def do_GET(self):
        if not self.path.endswith('.mpd'):
            self.send_error(404, 'Not Found')
            return
        file_path = self._safe_file_path(self.path)
        if not file_path:
            self.send_error(403, 'Forbidden')
            return
        try:
            with open(file_path, 'rb') as f:
                self.send_response(200)
                self.send_header('Content-Type', 'application/xml+dash')
                self.send_header('Content-Length', os.path.getsize(file_path))
                self.end_headers()
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except IOError:
            self.send_error(404, 'File Not Found')

    def do_HEAD(self):
        if not self.path.endswith('.mpd'):
            self.send_error(501, 'Not Implemented')
            return
        file_path = self._safe_file_path(self.path)
        if not file_path:
            self.send_error(403, 'Forbidden')
            return
        if not os.path.isfile(file_path):
            self.send_error(404, 'File Not Found')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/xml+dash')
        self.send_header('Content-Length', os.path.getsize(file_path))
        self.end_headers()

    def log_message(self, format, *args):
        # Silence BaseHTTPServer's stderr logger; Kodi logs are sufficient.
        return


def get_http_server(address=None, port=None):
    """Bind and return a HTTPServer. Caller is responsible for serving.

    `port` defaults to 54321 (the historical default and Kodi addon
    setting default). The address defaults to 0.0.0.0. Returns None if
    the bind fails (port already in use).
    """
    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', address or ''):
        address = '0.0.0.0'
    port = int(port) if port else 54321
    try:
        server = BaseHTTPServer.HTTPServer(
            (address, port), BilibiliRequestHandler,
        )
        return server
    except socket.error:
        return None

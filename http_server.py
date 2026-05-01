# -*- coding: utf-8 -*-
from http import server as BaseHTTPServer
import re
import socket
import os
import xbmc
import xbmcvfs


class BilibiliRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    def __init__(self, request, client_address, server):
        self.addon_id = 'plugin.video.bili'
        self.chunk_size = 1024 * 64
        try:
            self.base_path = xbmc.translatePath('special://temp/%s' % self.addon_id).decode('utf-8')
        except AttributeError:
            self.base_path = xbmc.translatePath('special://temp/%s' % self.addon_id)
        # 规范化为绝对路径，防止路径遍历
        self.base_path = os.path.realpath(self.base_path)
        BaseHTTPServer.BaseHTTPRequestHandler.__init__(self, request, client_address, server)

    def _safe_file_path(self, url_path):
        """将 URL 路径安全解析为本地文件路径，防止目录遍历攻击"""
        # 提取 .mpd 之前的路径部分
        if (pos := url_path.find('.mpd')) != -1:
            url_path = url_path[:pos + 4]
        # 去除首尾空白和路径分隔符
        safe = url_path.strip('/').strip('\\')
        # 移除 .. 组件
        parts = [p for p in safe.replace('\\', '/').split('/') if p and p != '..']
        safe = '/'.join(parts)
        file_path = os.path.join(self.base_path, safe)
        file_path = os.path.realpath(file_path)
        # 确保解析后的路径在 base_path 内
        if not file_path.startswith(self.base_path + os.sep) and file_path != self.base_path:
            return None
        return file_path

    def do_GET(self):
        if self.path.endswith('.mpd'):
            file_path = self._safe_file_path(self.path)
            if not file_path:
                self.send_error(403, 'Forbidden')
                return
            file_chunk = True
            try:
                with open(file_path, 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/xml+dash')
                    self.send_header('Content-Length', os.path.getsize(file_path))
                    self.end_headers()
                    while file_chunk:
                        file_chunk = f.read(self.chunk_size)
                        if file_chunk:
                            self.wfile.write(file_chunk)
            except IOError:
                response = 'File Not Found: |{proxy_path}| -> |{file_path}|'.format(proxy_path=self.path, file_path=file_path.encode('utf-8'))
                self.send_error(404, response)
        else:
            self.send_error(404, 'Not Found')

    def do_HEAD(self):
        if self.path.endswith('.mpd'):
            file_path = self._safe_file_path(self.path)
            if not file_path:
                self.send_error(403, 'Forbidden')
                return
            if not os.path.isfile(file_path):
                response = 'File Not Found: |{proxy_path}| -> |{file_path}|'.format(proxy_path=self.path, file_path=file_path.encode('utf-8'))
                self.send_error(404, response)
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/xml+dash')
                self.send_header('Content-Length', os.path.getsize(file_path))
                self.end_headers()
        else:
            self.send_error(501, 'Not Implemented')


def get_http_server(address=None, port=None):
    address = address if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', address) else '0.0.0.0'
    port = int(port) if port else 54321
    try:
        server = BaseHTTPServer.HTTPServer((address, port), BilibiliRequestHandler)
        return server
    except socket.error as e:
        return None

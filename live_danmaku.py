# -*- coding:utf-8 -*-
"""B站直播弹幕 ASS 叠加

流程：
1. WBI 签名请求 getDanmuInfo 获取 token + host_list
2. ws:// 连接弹幕服务器，发送认证包（含 token + buvid）
3. 实时接收 DANMU_MSG → 构造 danmaku2ass 格式 → ProcessComments 生成 ASS → setSubtitles 刷新
"""
import struct
import json
import time
import threading
import os
import io
import base64
import zlib
import socket

import xbmc

from utils import getSetting, make_dirs
from danmaku2ass import ProcessComments, CalculateLength

# ── WebSocket 工具 ────────────────────────────────────────────────────

def _ws_send(sock, data):
    """发送 WebSocket 二进制帧（client → server 需要 mask）"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    frame = bytearray([0x82])  # FIN + binary
    length = len(data)
    if length < 126:
        frame.append(0x80 | length)
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack('>H', length))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack('>Q', length))
    mask_key = os.urandom(4)
    frame.extend(mask_key)
    frame.extend(bytes(b ^ mask_key[i % 4] for i, b in enumerate(data)))
    sock.send(bytes(frame))


def _bili_packet(opcode, body=b''):
    """B站二进制协议包：16 字节头 + body"""
    if isinstance(body, str):
        body = body.encode('utf-8')
    return struct.pack('>IHHII', 16 + len(body), 16, 1, opcode, 1) + body


# ── 客户端 ────────────────────────────────────────────────────────────

class LiveDanmakuClient:
    def __init__(self, room_id, ass_path, uid=0, cookie='',
                 font_size=25, opacity=1.0, stay_time=8, display_area=1.0,
                 buvid=''):
        self.room_id      = int(room_id)
        self.ass_path     = ass_path
        self.uid          = int(uid) if uid else 0
        self._cookie      = cookie
        self._buvid       = buvid
        self.font_size    = float(font_size)
        self._opacity     = float(opacity)
        self.stay_time    = float(stay_time)
        self.display_area = float(display_area)

        self.running      = False
        self.sock         = None
        # danmaku2ass 格式: [(timeline, unix_ts, seq, text, pos, color, size_px, height, width), ...]
        # pos: 0=滚动, 1=底部居中, 2=顶部居中, 3=反向滚动
        self.danmaku_list = []
        self._seq         = 0
        self.lock         = threading.Lock()
        self._start_time  = 0

    def _get_token_wbi(self):
        """WBI 签名请求 getDanmuInfo"""
        try:
            from api import getWbiKeys, encWbi
            import requests
            params = encWbi({'id': str(self.room_id), 'type': '0'},
                            *getWbiKeys())
            full_url = 'https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo'
            h = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://live.bilibili.com/',
            }
            if self._cookie:
                h['Cookie'] = self._cookie
            r = requests.get(full_url, params=params, headers=h, timeout=10)
            data = r.json()
            xbmc.log('[live_danmaku] getDanmuInfo code=%s' % data['code'], xbmc.LOGINFO)
            if data['code'] == 0:
                return data['data']
        except Exception as e:
            xbmc.log('[live_danmaku] get_token_wbi: %s' % str(e), xbmc.LOGWARNING)
        return None

    def _connect(self, host, port):
        """ws:// TCP 直连"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(8)
            sock.connect((host, port))
            key = base64.b64encode(os.urandom(16)).decode()
            req = (f'GET /sub HTTP/1.1\r\nHost: {host}\r\n'
                   f'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                   f'Sec-WebSocket-Key: {key}\r\n'
                   f'Sec-WebSocket-Version: 13\r\n\r\n')
            sock.send(req.encode())
            resp = b''
            while b'\r\n\r\n' not in resp:
                chunk = sock.recv(4096)
                if not chunk:
                    sock.close()
                    return None
                resp += chunk
            if b'101' not in resp:
                sock.close()
                return None
            self.sock = sock
            xbmc.log('[live_danmaku] ws connected %s:%s' % (host, port), xbmc.LOGINFO)
            return sock
        except Exception as e:
            xbmc.log('[live_danmaku] ws connect: %s' % str(e), xbmc.LOGWARNING)
            return None

    def _send_auth(self, token):
        body = json.dumps({
            'uid':      self.uid,
            'roomid':   self.room_id,
            'protover': 2,
            'buvid':    self._buvid or '',
            'platform': 'web',
            'type':     2,
            'key':      token,
        })
        _ws_send(self.sock, _bili_packet(7, body))
        xbmc.log('[live_danmaku] auth sent', xbmc.LOGINFO)

    # ── 协议解析 → danmaku2ass 格式 ─────────────────────────────────────

    def _handle_message(self, body):
        """解析单条 B站 WebSocket 消息，构造 danmaku2ass 兼容的元组"""
        msg = json.loads(body.decode('utf-8', errors='replace'))
        if msg.get('cmd') != 'DANMU_MSG':
            return
        info = msg.get('info', [])
        if len(info) < 3:
            return
        meta = info[0]
        if not isinstance(meta, list) or len(meta) < 4:
            return

        # B站弹幕属性（与 danmaku2ass ReadCommentsBilibili 解析逻辑一致）
        mode     = int(meta[1]) if len(meta) > 1 else 1   # 1=滚动,4=底部,5=顶部
        fontsize = int(meta[2]) if len(meta) > 2 else 25   # 25=标准大小
        color    = int(meta[3]) if len(meta) > 3 else 0xffffff
        dm_ts    = int(meta[4]) if len(meta) > 4 else int(time.time())

        text = str(info[1]) if info[1] else ''
        if not text.strip():
            return

        # 映射到 danmaku2ass pos: 0=滚动, 1=底部, 2=顶部, 3=反向
        pos_map = {1: 0, 5: 1, 4: 2}
        pos = pos_map.get(mode)
        if pos is None:
            return

        # 像素尺寸（与 danmaku2ass 完全一致：size = 原始值 * 基准字号 / 25.0）
        size_px   = fontsize * self.font_size / 25.0
        height_px = (text.count('\n') + 1) * size_px
        width_px  = CalculateLength(text) * size_px

        timeline = time.time() - self._start_time
        self._seq += 1
        with self.lock:
            self.danmaku_list.append(
                (timeline, dm_ts, self._seq, text, pos, color, size_px, height_px, width_px))

    def _parse_binary(self, data):
        pos = 0
        while pos + 16 <= len(data):
            tl = struct.unpack_from('>I', data, pos)[0]
            hl = struct.unpack_from('>H', data, pos + 4)[0]
            pv = struct.unpack_from('>H', data, pos + 6)[0]
            op = struct.unpack_from('>I', data, pos + 8)[0]
            if tl < 16 or pos + tl > len(data):
                break
            body = data[pos + hl:pos + tl]
            pos += tl
            if pv == 2:
                try:
                    self._parse_binary(zlib.decompress(body))
                except zlib.error:
                    pass
                continue
            if op == 8:
                xbmc.log('[live_danmaku] auth OK (op=8)', xbmc.LOGINFO)
            elif op == 5:
                try:
                    self._handle_message(body)
                except Exception:
                    pass

    def _recv_loop(self):
        buf = b''
        if self.sock:
            self.sock.settimeout(1.0)  # 设置一次即可，避免循环内重复调用
        while self.running:
            sock = self.sock  # 快照引用，防止 stop() 置 None 后崩溃
            if not sock:
                break
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 2:
                    opcode = buf[0] & 0x0F
                    masked = (buf[1] & 0x80) != 0
                    plen   = buf[1] & 0x7F
                    offset = 2
                    if plen == 126:
                        if len(buf) < 4: break
                        plen = struct.unpack_from('>H', buf, offset)[0]
                        offset += 2
                    elif plen == 127:
                        if len(buf) < 10: break
                        plen = struct.unpack_from('>Q', buf, offset)[0]
                        offset += 8
                    if opcode == 0x8:
                        self.running = False
                        break
                    if masked:
                        if len(buf) < offset + 4 + plen: break
                        mk = buf[offset:offset + 4]
                        offset += 4
                        payload = bytes(buf[offset + i] ^ mk[i % 4] for i in range(plen))
                    else:
                        if len(buf) < offset + plen: break
                        payload = buf[offset:offset + plen]
                    offset += plen
                    buf = buf[offset:]
                    if opcode == 0x2:
                        self._parse_binary(payload)
                    elif opcode == 0x9:
                        try:
                            sock.send(bytes([0x8A, 0x00]))
                        except Exception:
                            pass
            except socket.timeout:
                continue
            except (OSError, ConnectionError, ConnectionResetError):
                break
            except Exception as e:
                xbmc.log('[live_danmaku] recv: %s' % str(e), xbmc.LOGWARNING)
                break

    def _run(self):
        # 1. WBI 签名获取 token
        info = self._get_token_wbi()
        if not info:
            xbmc.log('[live_danmaku] getDanmuInfo FAIL', xbmc.LOGERROR)
            return

        token = info.get('token', '')
        host_list = info.get('host_list', [])
        if not host_list or not token:
            xbmc.log('[live_danmaku] no host/token', xbmc.LOGERROR)
            return

        # 用最后一个 host（wiliwili 做法）
        host = host_list[-1].get('host', '')
        port = host_list[-1].get('ws_port', 2244)
        xbmc.log('[live_danmaku] using %s:%s' % (host, port), xbmc.LOGINFO)

        # 2. ws:// 连接
        if not self._connect(host, port):
            return

        # 3. 发送认证
        self._send_auth(token)

        # ★ 必须在启动任何依赖 _start_time 的子线程之前设置
        self._start_time = time.time()

        # 4. 心跳线程
        def _hb():
            while self.running:
                sock = self.sock  # 快照引用
                if not sock:
                    break
                time.sleep(30)
                if not self.running:
                    break
                sock = self.sock
                if sock:
                    try:
                        _ws_send(sock, _bili_packet(2, b'{}'))
                    except Exception:
                        pass
        threading.Thread(target=_hb, daemon=True).start()

        # 5. ASS 写入 + 刷新（交给 danmaku2ass）
        _MAX_LIST = 2000
        width  = 1920
        height = 540
        reserve_blank = int((1.0 - self.display_area) * height)

        def _writer():
            last_sync = 0
            live_marker = '/live/' + str(self.room_id)
            while self.running:
                time.sleep(1.5)
                if not self.running:
                    break
                with self.lock:
                    # 防止列表无限增长
                    if len(self.danmaku_list) > _MAX_LIST:
                        self.danmaku_list = self.danmaku_list[-_MAX_LIST:]
                    # 时间窗口：只取最近 stay_time+2 秒内的弹幕
                    # 更旧的已经离开屏幕，不再出现在 ASS 中
                    now_offset = time.time() - self._start_time
                    cutoff = now_offset - self.stay_time - 2
                    # 先取最后 _MAX_LIST 条避免全量遍历
                    pool = self.danmaku_list[-_MAX_LIST:]
                    snapshot = [c for c in pool if c[0] >= cutoff]
                    # 兜底：刚开播时窗口内不够，取最后 50 条
                    if len(snapshot) < 50:
                        snapshot = list(self.danmaku_list[-50:])
                    # 硬上限：最多 300 条送 danmaku2ass（性能）
                    snapshot = snapshot[-300:]

                if not snapshot:
                    continue

                # ★ 不 shift，直接传原始 timeline 给 ProcessComments
                # danmaku2ass 按 timeline 排序后做碰撞避免。
                # 注意：timeline < now_offset 的弹幕在 ASS 中的 Start 已
                # 落后于 Kodi 当前播放位置，Kodi 会从对应进度开始渲染
                # （即弹幕从屏幕中间或左边缘开始），这是 ASS 静态文件方案
                # 的固有折衷。直播场景下感知影响不大。

                buf = io.StringIO()
                ProcessComments(snapshot, buf, width, height,
                                reserve_blank,
                                'sans-serif', self.font_size, self._opacity,
                                self.stay_time, self.stay_time,
                                [], False, None)
                content = buf.getvalue()
                buf.close()

                tmp = self.ass_path + '.tmp'
                try:
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(content)
                    os.replace(tmp, self.ass_path)
                except Exception:
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass

                if not self.running:
                    break
                now = time.time()
                if now - last_sync < 8:
                    continue
                last_sync = now
                try:
                    p = xbmc.Player()
                    if p.isPlaying():
                        cur = xbmc.getInfoLabel('Player.Filenameandpath') or ''
                        if live_marker in cur:
                            p.setSubtitles(self.ass_path)
                except Exception:
                    pass
        threading.Thread(target=_writer, daemon=True).start()

        self._recv_loop()

    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()
        # 等待连接
        for _ in range(10):
            time.sleep(0.5)
            if not self.running:
                return False
            with self.lock:
                if self.danmaku_list:
                    return True
        return self.running

    def stop(self):
        self.running = False
        sock = self.sock
        self.sock = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass


# ── 便捷函数 ────────────────────────────────────────────────────────────

_instances = {}

def start_live_danmaku(room_id, uid=0, cookie=''):
    bp = xbmc.translatePath('special://temp/plugin.video.bili/')
    if not make_dirs(bp):
        return None, None
    path = os.path.join(bp, 'live_%s.ass' % room_id)

    # 获取 buvid
    buvid = ''
    try:
        from api import get_cookie_value
        buvid = get_cookie_value('buvid3')
    except Exception:
        pass

    # 占位 ASS（最小有效结构，无可见内容）
    ph = ('[Script Info]\n'
          '; Script generated by plugin.video.bili live danmaku\n'
          'ScriptType: v4.00+\n'
          'PlayResX: 1920\nPlayResY: 540\n'
          'Aspect Ratio: 1920:540\nCollisions: Normal\nWrapStyle: 2\n'
          'ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.601\n\n'
          '[V4+ Styles]\n'
          'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
          'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
          'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
          'Alignment, MarginL, MarginR, MarginV, Encoding\n'
          'Style: R2L,sans-serif,25,&H00FFFFFF,&H00FFFFFF,&H00000000,'
          '&H00000000,0,0,0,0,100,100,0.00,0.00,1,1,0,7,0,0,0,0\n\n'
          '[Events]\n'
          'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(ph)
    except Exception:
        pass

    key = str(room_id)
    if key in _instances:
        try:
            _instances[key].stop()
        except Exception:
            pass

    c = LiveDanmakuClient(
        room_id, path, uid, cookie,
        float(getSetting('font_size')),
        float(getSetting('opacity')),
        float(getSetting('danmaku_stay_time')),
        float(getSetting('display_area')),
        buvid)
    ok = c.start()
    _instances[key] = c
    if not ok:
        xbmc.log('[live_danmaku] start FAIL for room=%s' % room_id, xbmc.LOGWARNING)
    return path, c

def stop_live_danmaku(room_id):
    key = str(room_id)
    if key in _instances:
        try:
            _instances[key].stop()
        except Exception:
            pass
        del _instances[key]

def stop_all_live_danmaku():
    """停止所有正在运行的直播弹幕线程"""
    for key in list(_instances.keys()):
        stop_live_danmaku(key)

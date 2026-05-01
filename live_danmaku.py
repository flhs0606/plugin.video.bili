# -*- coding:utf-8 -*-
"""B站直播弹幕 ASS 叠加

流程：
1. WBI 签名请求 getDanmuInfo 获取 token + host_list
2. ws:// 连接弹幕服务器，发送认证包（含 token + buvid）
3. 实时接收 DANMU_MSG → 写入 ASS → setSubtitles 刷新
"""
import struct
import json
import time
import threading
import os
import base64
import zlib
import random
import socket

import xbmc

from utils import getSetting, make_dirs

# ── ASS 生成 ──────────────────────────────────────────────────────────

def _fmt_ass_time(seconds):
    s = max(seconds, 0)
    return '%d:%02d:%02d.%02d' % (int(s//3600), int(s%3600//60), int(s%60), int(s*100)%100)

def _write_ass(path, danmaku_list, stay_time, font_size, alpha, display_area):
    dh = int((1.0 - display_area) * 1080 / 2)
    lines = [
        '[Script Info]', 'Title: Bilibili Live', 'ScriptType: v4.00+',
        'PlayResX: 1920', 'PlayResY: 1080', 'WrapStyle: 2', '',
        '[V4+ Styles]',
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
        'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
        'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding',
        f'Style: R2L,sans-serif,{font_size},&H{alpha}FFFFFF,&H{alpha}FFFFFF,&H00000000,'
        f'&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,2,10,10,{dh},0',
        f'Style: TOP,sans-serif,{font_size},&H{alpha}FFFFFF,&H{alpha}FFFFFF,&H00000000,'
        f'&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,8,10,10,{dh},0',
        f'Style: BTM,sans-serif,{font_size},&H{alpha}FFFFFF,&H{alpha}FFFFFF,&H00000000,'
        f'&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,2,10,10,{dh},0',
        '', '[Events]',
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    ]
    used_y = set()
    for ts, text, mode in danmaku_list:
        safe = text.replace('{', '\\{').replace('}', '\\}')
        s = _fmt_ass_time(ts)
        e = _fmt_ass_time(ts + stay_time)
        if mode == 5:
            lines.append(f'Dialogue: 0,{s},{e},TOP,,10,10,{dh},,{safe}')
        elif mode == 4:
            lines.append(f'Dialogue: 0,{s},{e},BTM,,10,10,{dh},,{safe}')
        else:
            tr = int(ts * 10)
            for _ in range(20):
                y = random.randint(dh, 1080 - dh)
                if (tr, y) not in used_y:
                    used_y.add((tr, y))
                    break
            lines.append(f'Dialogue: 0,{s},{e},R2L,,10,10,{dh},,{{\\move(1920,{y},-500,{y})}}{safe}')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
    except Exception:
        pass


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
        self.font_size    = int(font_size)
        self._alpha       = '%02X' % (100 - int(float(opacity) * 100))
        self.stay_time    = int(stay_time)
        self.display_area = float(display_area)

        self.running      = False
        self.sock         = None
        self.danmaku_list = []
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
                    msg = json.loads(body.decode('utf-8', errors='replace'))
                    if msg.get('cmd') == 'DANMU_MSG':
                        info = msg.get('info', [])
                        if len(info) >= 3:
                            meta = info[0]
                            mode = meta[1] if len(meta) > 1 else 1
                            ts = time.time() - self._start_time
                            with self.lock:
                                self.danmaku_list.append((ts, info[1], mode))
                except Exception:
                    pass

    def _recv_loop(self):
        buf = b''
        while self.running:
            try:
                self.sock.settimeout(1.0)
                chunk = self.sock.recv(4096)
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
                            self.sock.send(bytes([0x8A, 0x00]))
                        except Exception:
                            pass
            except socket.timeout:
                continue
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

        # 4. 心跳 + ASS 写入
        def _hb():
            while self.running and self.sock:
                time.sleep(30)
                if self.sock:
                    try:
                        _ws_send(self.sock, _bili_packet(2, b'[object Object]'))
                    except Exception:
                        pass
        threading.Thread(target=_hb, daemon=True).start()

        # 5. ASS 写入 + 刷新
        def _writer():
            player = xbmc.Player()
            last_sync = 0
            last_count = 0
            while self.running:
                time.sleep(1.5)
                with self.lock:
                    count = len(self.danmaku_list)
                    if count == last_count:
                        continue
                    last_count = count
                    snapshot = list(self.danmaku_list[-600:])
                _write_ass(self.ass_path, snapshot, self.stay_time,
                           self.font_size, self._alpha, self.display_area)
                now = time.time()
                if now - last_sync >= 2:
                    last_sync = now
                    try:
                        if player.isPlaying():
                            player.setSubtitles(self.ass_path)
                    except Exception:
                        pass
        threading.Thread(target=_writer, daemon=True).start()

        self._start_time = time.time()
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
        if self.sock:
            try:
                self.sock.close()
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

    # 占位 ASS
    ph = ('[Script Info]\nTitle: Bilibili Live\nScriptType: v4.00+\n'
          'PlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n'
          '[V4+ Styles]\n'
          'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
          'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
          'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
          'Alignment, MarginL, MarginR, MarginV, Encoding\n'
          'Style: R2L,sans-serif,25,&H00FFFFFF,&H00FFFFFF,&H00000000,'
          '&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,2,10,10,0,0\n\n'
          '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, '
          'MarginV, Effect, Text\n'
          'Dialogue: 0,0:00:00.00,0:00:05.00,R2L,,10,10,0,,'
          '{\\move(1920,540,-500,540)}弹幕加载中...')
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

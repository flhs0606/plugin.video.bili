# v0.4.0 — Restore `inputstream.adaptive` as Only Playback Path

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace v0.3.0's broken local-proxy playback with v0.1.0's verified `inputstream.adaptive` + MPD-direct path; route live through `inputstream.adaptive manifest_type='hls'`; remove the segment proxy and its service process; keep the static MPD HTTP server.

**Architecture:**
- VOD: write MPD to `special://temp/plugin.video.bili/{cid}.mpd` → serve at `http://127.0.0.1:54321/{cid}.mpd` → `inputstream.adaptive` parses MPD → fetches B 站 CDN segments with `stream_headers='Referer=…'`. Per-Representation `<SegmentBase indexRange>` + `<Initialization range>` so adaptive splices init/media byte ranges. Multi-AS audio (AAC + Dolby + Hi-Res FLAC) preserved from v0.3.0.
- Live: prefer `master_url` (m3u8 from `http_hls` protocol); fall back to `urls[0]` (raw m4s). Single output: `inputstream.adaptive manifest_type='hls'` with `manifest_update_params='full'`.
- No segment proxy. The local HTTP server serves only the MPD file. The `xbmc.service` extension stays, hosting this server.

**Tech Stack:** Python 3 (Kodi 21's interpreter), `inputstream.adaptive` (optional dep, system-provided), `http.server.HTTPServer` (stdlib), `xbmcaddon`/`xbmcvfs`/`xbmc`/`xbmcgui` (Kodi Python API).

**Reference material:**
- v0.1.0 baseline (works): `E:\Project\plugin.video.bili-origin\`
- v0.3.0 current (broken): `e:\Project\plugin.video.bili\`
- Design spec: `docs/superpowers/specs/2026-06-03-restore-inputstream-adaptive-design.md`

**Non-negotiable invariants from the spec:**
- `inputstream.adaptive` is `optional="true"` in `addon.xml` (not hard-required).
- VOD `path` is the HTTP URL `http://127.0.0.1:{port}/{cid}.mpd`, not a `special://` or `file://` path.
- `stream_headers` / `manifest_headers` are exactly `Referer=https://www.bilibili.com` (no UA, no Cookie).
- Live prefers `master_url` (m3u8) over `urls[0]` (raw m4s).
- Live has no FLV pipe fallback.

---

## Task 1: Update `addon.xml` — make `inputstream.adaptive` optional

**Files:**
- Modify: `e:\Project\plugin.video.bili\addon.xml`

- [ ] **Step 1: Read current `addon.xml` to confirm structure**

The file currently has (relevant excerpt from `addon.xml:5-12`):
```xml
<requires>
  <import addon="xbmc.python" version="3.0.0"/>
  <import addon="script.module.requests" version="2.12.4"/>
  <import addon="script.module.qrcode" version="5.3"/>
</requires>
<extension point="xbmc.python.pluginsource" library="addon.py">
  <provides>video</provides>
</extension>
<extension point="xbmc.service" library="service.py"/>
```

`xbmc.service` STAYS (the static MPD server needs a host). We only add `inputstream.adaptive` as optional.

- [ ] **Step 2: Add `inputstream.adaptive` import**

Edit `addon.xml` so the `<requires>` block becomes:

```xml
<requires>
  <import addon="xbmc.python" version="3.0.0"/>
  <import addon="script.module.requests" version="2.12.4"/>
  <import addon="script.module.qrcode" version="5.3"/>
  <import addon="inputstream.adaptive" optional="true"/>
</requires>
```

- [ ] **Step 3: Bump version to 0.4.0**

Change the `version` attribute on the root `<addon>` element from `0.3.0` to `0.4.0`.

- [ ] **Step 4: Update `news` block**

Replace the `v0.3.0` news entry with a `v0.4.0` entry. Add it BEFORE the existing `v0.3.0` block. The full `news` block becomes:

```xml
<news>
v0.4.0
- Restore v0.1.0 VOD path: inputstream.adaptive + per-representation
  SegmentBase + Referer-only manifest_headers / stream_headers.
  BaseURL points at B 站 CDN directly (no segment proxy).
- Live routed through inputstream.adaptive (manifest_type='hls',
  manifest_update_params='full'); ffmpegdirect removed.
- Delete playback/proxy.py and monitor.py. http_server.py and
  service.py stay but are simplified — the local server only serves
  the static MPD file; segments are fetched directly by adaptive.
  inputstream.adaptive is now optional; menu guides the install.
v0.3.0
- 完全移除 inputstream.ffmpegdirect / inputstream.adaptive 依赖。
  改用 Kodi 内置 ffmpeg dash demuxer 直接播放 DASH MPD，
  分片请求走本地 Python 代理注入 Cookie/Referer/UA 认证头。
- MPD 用 BaseURL 单文件模式（ffmpeg dashdec 原生支持），
  保留多音轨 AdaptationSet + Dolby Vision/HDR10/HLG 标记。
- 直播 fmp4/ts 统一用 ffmpeg 管道直连 + reconnect 参数。
v0.2.0
- Runtime detection: checks if inputstream.ffmpegdirect has the bili-patches marker.
v0.1.0
- Initial release (DASH + live).
</news>
```

- [ ] **Step 5: Update `description` summary lines**

In the same `addon.xml`, change the two `<description>` lines so they no longer claim "no external inputstream addons required". Replace the existing `lang="en"` description with:

```xml
<description lang="en">Browse, search, and play Bilibili videos. Uses inputstream.adaptive for DASH MPD playback (Kodi ships this addon; install via the menu prompt if missing). Supports Dolby Vision, Dolby Atmos, Hi-Res FLAC, HDR10, HLG.</description>
```

And replace the `lang="zh_CN"` description with:

```xml
<description lang="zh_CN">浏览、搜索、播放哔哩哔哩视频。使用 inputstream.adaptive 播放 DASH MPD（Kodi 自带；首次使用按菜单提示安装）。支持杜比视界、杜比全景声、Hi-Res FLAC、HDR10、HLG。</description>
```

- [ ] **Step 6: Verify XML is well-formed**

Run: `python -c "import xml.etree.ElementTree as ET; ET.parse('e:/Project/plugin.video.bili/addon.xml'); print('OK')"`
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add addon.xml
git commit -m "feat: declare inputstream.adaptive optional, bump to 0.4.0"
```

---

## Task 2: Delete `playback/proxy.py`

**Files:**
- Delete: `e:\Project\plugin.video.bili\playback\proxy.py`

- [ ] **Step 1: Verify no callers in v0.3.0 code remain after later tasks**

The v0.3.0 callers are `routes/video.py:22` and `http_server.py:118`. Both will be removed in Tasks 5 and 6, so by the end of this plan no caller exists. We delete the file as part of the cascade in Task 8 ("Delete obsolete files"). For Task 2 we do a no-op placeholder commit so task ordering is explicit. Skip the deletion here; it is centralized in Task 8.

- [ ] **Step 2: Note the deletion target**

`playback/proxy.py` will be deleted in Task 8. Mark this task complete once Task 8 finishes.

- [ ] **Step 3: Commit (no-op placeholder)**

```bash
git commit --allow-empty -m "chore: track playback/proxy.py for deletion in Task 8"
```

---

## Task 3: Delete `playback/m3u8.py`

**Files:**
- Delete: `e:\Project\plugin.video.bili\playback\m3u8.py`

- [ ] **Step 1: Verify no callers**

Search result of `grep -rn "playback.m3u8\|from .m3u8\|from playback import.*m3u8" e:/Project/plugin.video.bili/`:
- `routes/video.py:23` — `from playback.m3u8 import write_m3u8_files`

This call is removed when `routes/video.py` is rewritten in Task 6. Centralized deletion happens in Task 8.

- [ ] **Step 2: Note the deletion target**

`playback/m3u8.py` will be deleted in Task 8.

- [ ] **Step 3: Commit (no-op placeholder)**

```bash
git commit --allow-empty -m "chore: track playback/m3u8.py for deletion in Task 8"
```

---

## Task 4: Delete `monitor.py`

**Files:**
- Delete: `e:\Project\plugin.video.bili\monitor.py`

- [ ] **Step 1: Verify no callers remain after Task 5**

`monitor.py` is imported by `service.py` (`from monitor import BilibiliMonitor`). `service.py` is rewritten in Task 5 to NOT import `monitor`. The `BilibiliMonitor` class itself is no longer needed because the static MPD server runs directly in `service.py` and the danmaku lifecycle is handled by `live.danmaku.stop_all_live_danmaku()` at shutdown. Centralized deletion in Task 8.

- [ ] **Step 2: Note the deletion target**

`monitor.py` will be deleted in Task 8.

- [ ] **Step 3: Commit (no-op placeholder)**

```bash
git commit --allow-empty -m "chore: track monitor.py for deletion in Task 8"
```

---

## Task 5: Rewrite `http_server.py` to serve only the static MPD file

**Files:**
- Modify: `e:\Project\plugin.video.bili\http_server.py` (full rewrite)

The new `http_server.py` exposes ONE endpoint: GET `/{cid}.mpd` → returns the MPD file from `special://temp/plugin.video.bili/{cid}.mpd` with `Content-Type: application/xml+dash`. Anything else returns 404. The path-traversal guard from v0.3.0 is preserved.

- [ ] **Step 1: Read current `http_server.py` for reference**

The current file is at `e:/Project/plugin.video.bili/http_server.py`. It has:
- `BilibiliRequestHandler` class with `do_GET` / `do_HEAD`
- A `/proxy/{id}.mp4` route that we are REMOVING
- A static `/{cid}.mpd` route that we are KEEPING
- `_safe_file_path` helper that we KEEP
- Lazy `_requests` import that we REMOVE

- [ ] **Step 2: Replace the file contents**

Overwrite `e:\Project\plugin.video.bili\http_server.py` with:

```python
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
```

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('e:/Project/plugin.video.bili/http_server.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add http_server.py
git commit -m "refactor: gut http_server.py to static MPD serving only"
```

---

## Task 6: Rewrite `service.py` to host the MPD server

**Files:**
- Modify: `e:\Project\plugin.video.bili\service.py` (full rewrite)

`service.py` becomes a thin long-lived process: bind 54321, loop on a Kodi `Monitor.waitForAbort`, handle one HTTP request per loop tick, stop live danmaku threads on exit.

- [ ] **Step 1: Read current `service.py` for reference**

Current file: `e:/Project/plugin.video.bili/service.py`. It uses `BilibiliMonitor` and `monitor.shutdown_httpd()`. After this task, neither exists.

- [ ] **Step 2: Replace the file contents**

Overwrite `e:\Project\plugin.video.bili\service.py` with:

```python
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
```

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('e:/Project/plugin.video.bili/service.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add service.py
git commit -m "refactor: simplify service.py to host static MPD server"
```

---

## Task 7: Verify `live/danmaku.py` exports `stop_all_live_danmaku`

**Files:**
- Read: `e:\Project\plugin.video.bili\live\danmaku.py`

The new `service.py` calls `stop_all_live_danmaku()`. Confirm the import works.

- [ ] **Step 1: Confirm symbol exists**

Run: `grep -nE "def stop_all_live_danmaku|^def stop_live_danmaku" e:/Project/plugin.video.bili/live/danmaku.py`
Expected: at least one match, ideally `def stop_all_live_danmaku():`.

- [ ] **Step 2: If only `stop_live_danmaku` (singular) exists, alias it**

If the grep finds `stop_live_danmaku(room_id)` but not `stop_all_live_danmaku`, add this at the end of `live/danmaku.py`:

```python


def stop_all_live_danmaku():
    """Stop every active live-danmaku client. Used at service shutdown
    and at the start of VOD navigation (per Kodi addon process model,
    the latter is a no-op in service mode but matters in the per-nav
    process)."""
    for room_id in list(_instances.keys()):
        try:
            _instances[room_id].stop()
        except Exception:
            pass
        _instances.pop(room_id, None)
```

Verify the existing `_instances` symbol name by reading the file. Adjust the body if `_instances` is named differently (the v0.3.0 implementation should have it; if not, locate the registry variable in `live/danmaku.py` and substitute).

- [ ] **Step 3: Commit (only if Step 2 added code)**

```bash
git add live/danmaku.py
git commit -m "feat: ensure stop_all_live_danmaku() exists for service shutdown"
```

---

## Task 8: Mark deletion targets as pending (placeholder)

**Files:**
- None (no-op commit)

We defer the actual file deletion to a later task because `playback/mpd.py`, `playback/__init__.py`, and `routes/video.py` still import from the modules to be deleted. The actual deletion happens after Tasks 9 and 10 have migrated those imports.

- [ ] **Step 1: Verify the cross-references that will block deletion**

Run: `grep -rnE "from playback\.proxy|from \.proxy|from playback\.m3u8|from \.m3u8|import monitor|from monitor" e:/Project/plugin.video.bili/ --include="*.py"`
Expected: matches in `playback/mpd.py:18` (imports `_proxy_register`), `playback/__init__.py:13` (re-exports), `routes/video.py:22-23` (imports `unregister_all`, `write_m3u8_files`). These will be cleared in Tasks 9 and 10.

- [ ] **Step 2: Note the deletion target**

`monitor.py`, `playback/proxy.py`, `playback/m3u8.py` will be deleted in a dedicated later task once Tasks 9 and 10 land.

- [ ] **Step 3: Commit (no-op placeholder)**

```bash
git commit --allow-empty -m "chore: track monitor.py, proxy.py, m3u8.py for deletion post-Task 10"
```

---

## Task 9: Rewrite `playback/mpd.py` — v0.1.0 video + v0.3.0 audio

**Files:**
- Modify: `e:\Project\plugin.video.bili\playback\mpd.py` (full rewrite)

The new `generate_mpd(dash)` has a single-arg signature matching v0.1.0. Video AdaptationSet uses `<SegmentBase indexRange>` + `<Initialization range>` per Representation. Audio is emitted as one `<AdaptationSet>` per `AudioTrack` (v0.3.0 multi-AS, AAC + Dolby + Hi-Res FLAC separate), with `lang="zh-Hans"`, `codecs` from `track.codec_mpd`, `<AudioChannelConfiguration>`, `<Role value="main"/>` on primary AAC, `<SupplementalProperty value="JOC"/>` on Dolby.

- [ ] **Step 1: Read v0.3.0 `mpd.py` and `audio.py` for reference**

Read both files to confirm the helpers we re-use:
- `playback/mpd.py` — current v0.3.0 implementation
- `playback/audio.py` — `AudioTrack` dataclass, `collect_audio_tracks`, `select_by_user_pref`, `PREF_HIGH`

The v0.3.0 `_build_audio_as(track)` helper (currently in `playback/mpd.py:198-241`) is the body we KEEP. We do NOT need to copy it from origin — it already exists in the v0.3.0 file and is correct.

- [ ] **Step 2: Write the new `playback/mpd.py`**

Overwrite `e:\Project\plugin.video.bili\playback\mpd.py` with:

```python
# -*- coding:utf-8 -*-
"""DASH MPD generation: B 站 playurl DASH → standard MPEG-DASH MPD.

v0.4.0 structure:
  - Video: v0.1.0 — per Representation, <SegmentBase indexRange="…"> +
    <Initialization range="…"/>. inputstream.adaptive uses these to
    splice init/media byte ranges precisely when Range-fetching from
    B 站 CDN.
  - Audio: v0.3.0 — one <AdaptationSet> per AudioTrack. AAC + Dolby +
    Hi-Res FLAC each get their own AS, with lang="zh-Hans",
    <AudioChannelConfiguration>, <Role value="main"/> on the primary
    AAC, and <SupplementalProperty value="JOC"/> on Dolby.

BaseURL points at B 站 CDN directly. inputstream.adaptive fetches
segments with `stream_headers=Referer=https://www.bilibili.com` —
no local proxy is involved.
"""
from xml.sax.saxutils import escape as _xml_escape

from core import xbmc
from .resolution import choose_resolution
from .audio import (
    collect_audio_tracks, select_by_user_pref, PREF_HIGH,
)


# ── 工具函数 ────────────────────────────────────────────────────────────

def _duration_to_iso8601(seconds) -> str:
    if not seconds:
        return 'PT0S'
    s = float(seconds)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - h * 3600 - m * 60
    if h:
        return 'PT%dH%dM%.3fS' % (h, m, sec)
    if m:
        return 'PT%dM%.3fS' % (m, sec)
    return 'PT%.3fS' % sec


def _amp(s: str) -> str:
    return s.replace('&', '&amp;')


# ── 视频 AdaptationSet (v0.1.0 形态) ────────────────────────────────────

def _build_video_as(videos: list) -> list:
    """Single video AS; one Representation per quality. Each Representation
    has its own <SegmentBase indexRange> + <Initialization range>."""
    if not videos:
        return []
    lines = [
        '\t\t<AdaptationSet mimeType="video/mp4" '
        'startWithSAP="1" segmentAlignment="true" '
        'scanType="progressive">\n',
    ]
    for v in videos:
        attrs = ['id="%s"' % v.get('id', '')]
        if 'bandwidth' in v:
            attrs.append('bandwidth="%d"' % v['bandwidth'])
        if 'codecs' in v:
            attrs.append('codecs="%s"' % v['codecs'])
        if 'frameRate' in v:
            attrs.append('frameRate="%s"' % v['frameRate'])
        if 'height' in v:
            attrs.append('height="%d"' % v['height'])
        if 'width' in v:
            attrs.append('width="%d"' % v['width'])
        lines.append('\t\t\t<Representation %s>\n' % ' '.join(attrs))
        lines.append(
            '\t\t\t\t<BaseURL>%s</BaseURL>\n'
            % _amp(v.get('baseUrl', ''))
        )
        for bu in v.get('backup_url') or []:
            lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' % _amp(bu))
        sb = v.get('SegmentBase') or {}
        if 'indexRange' in sb and 'Initialization' in sb:
            lines.append(
                '\t\t\t\t<SegmentBase indexRange="%s">\n' % sb['indexRange']
            )
            lines.append(
                '\t\t\t\t\t<Initialization range="%s">'
                '</Initialization>\n' % sb['Initialization']
            )
            lines.append('\t\t\t\t</SegmentBase>\n')
        lines.append('\t\t\t</Representation>\n')
    lines.append('\t\t</AdaptationSet>\n')
    return lines


# ── 音频 AdaptationSet (v0.3.0 形态，多 AS) ─────────────────────────────

def _audio_channel_cfg(track) -> str:
    scheme = 'urn:mpeg:mpegB:cicp:ChannelConfiguration'
    if track.channels and track.channels > 0:
        v = track.channels
    elif track.kind == 'dolby':
        v = 7
    else:
        v = 2
    return (
        '<AudioChannelConfiguration schemeIdUri="%s" value="%d"/>'
    ) % (scheme, v)


def _build_audio_as(track) -> list:
    codec_attr = track.codec_mpd
    channel_cfg = _audio_channel_cfg(track)
    label = _xml_escape(track.label or str(track.id))

    lines = [
        '\t\t<AdaptationSet contentType="audio" mimeType="audio/mp4" '
        'segmentAlignment="true" startWithSAP="1" '
        'lang="zh-Hans" '
        'codecs="%s" '
        'label="%s">\n' % (codec_attr, label),
    ]
    lines.append('\t\t\t%s\n' % channel_cfg)
    if track.kind == 'aac' and track.id == PREF_HIGH:
        lines.append(
            '\t\t\t<Role schemeIdUri="urn:mpeg:dash:role:2011" '
            'value="main"/>\n',
        )
    if track.kind == 'dolby':
        lines.append(
            '\t\t\t<SupplementalProperty '
            'schemeIdUri="tag:dolby.com,2018:dash:EC3_ExtensionType:2018" '
            'value="JOC"/>\n',
        )

    attrs = [
        'id="%d"' % track.id,
        'bandwidth="%d"' % track.bandwidth,
    ]
    if track.audio_sampling_rate:
        attrs.append('audioSamplingRate="%d"' % track.audio_sampling_rate)
    lines.append('\t\t\t<Representation %s>\n' % ' '.join(attrs))

    lines.append(
        '\t\t\t\t<BaseURL>%s</BaseURL>\n' % _amp(track.base_url)
    )
    for bu in track.backup_url or []:
        lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' % _amp(bu))
    sb = track.segment_base or {}
    if 'indexRange' in sb and 'Initialization' in sb:
        lines.append(
            '\t\t\t\t<SegmentBase indexRange="%s">\n' % sb['indexRange']
        )
        lines.append(
            '\t\t\t\t\t<Initialization range="%s">'
            '</Initialization>\n' % sb['Initialization']
        )
        lines.append('\t\t\t\t</SegmentBase>\n')
    lines.append('\t\t\t</Representation>\n')
    lines.append('\t\t</AdaptationSet>\n')
    return lines


# ── 顶层 MPD ────────────────────────────────────────────────────────────

def generate_mpd(dash: dict) -> str:
    """Build the MPD XML.

    Single-arg signature matching v0.1.0 (the proxy-related
    cookie/ua/port params from v0.3.0 are gone — BaseURL is CDN-direct).
    """
    videos = choose_resolution(dash)
    audio_tracks = select_by_user_pref(collect_audio_tracks(dash))

    duration_iso = _duration_to_iso8601(dash.get('duration', 0))
    minbuf_ms = dash.get('min_buffer_time') or dash.get('minBufferTime') or 1.5
    minbuf_iso = _duration_to_iso8601(minbuf_ms)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
        'type="static" '
        'mediaPresentationDuration="%s" '
        'minBufferTime="%s">\n' % (duration_iso, minbuf_iso),
        '\t<Period>\n',
    ]
    lines.extend(_build_video_as(videos))
    for t in audio_tracks:
        lines.extend(_build_audio_as(t))
    lines.append('\t</Period>\n</MPD>\n')
    return ''.join(lines)
```

- [ ] **Step 3: Verify `playback/audio.py` still exports the needed symbols**

Run: `grep -nE "^def collect_audio_tracks|^def select_by_user_pref|^PREF_HIGH|^class AudioTrack" e:/Project/plugin.video.bili/playback/audio.py`
Expected: at least 4 matches.

- [ ] **Step 4: Syntax check**

Run: `python -c "import ast; ast.parse(open('e:/Project/plugin.video.bili/playback/mpd.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add playback/mpd.py
git commit -m "refactor: restore v0.1.0 video SegmentBase, keep v0.3.0 multi-AS audio"
```

---

## Task 10: Update `routes/video.py` — HTTP path + 4-prop `set_resolved_url`

**Files:**
- Modify: `e:\Project\plugin.video.bili\routes\video.py`

This is the heart of the VOD change. The new `video()` function:
1. Calls B 站 playurl API (existing logic preserved).
2. If `dash` returned: writes MPD via `generate_mpd(dash)`, writes to `special://temp/plugin.video.bili/{cid}.mpd`, and calls `set_resolved_url` with the HTTP URL + 4 inputstream props.
3. `audio_only` branch: pipe single audio track with `Referer=…`.
4. `durl` branch: pipe single MP4 with `Referer=…`.

- [ ] **Step 1: Read current `routes/video.py`**

The current file is the v0.3.0 implementation. We are replacing the VOD branch (the part after the diagnostic logging) and the imports of `m3u8` / `proxy`.

- [ ] **Step 2: Apply the new imports and the new VOD branch**

Edit `e:\Project\plugin.video.bili\routes\video.py`. The full new file is below; replace the entire file with it.

```python
# -*- coding:utf-8 -*-
"""点播播放：番剧分集 / 视频分P / 实际播放（DASH MPD → inputstream.adaptive）。

v0.4.0 流程：
  1. 调 B 站 playurl API (fnval=4048) 获取 DASH 数据
  2. playback/mpd.generate_mpd(dash) 生成 MPD XML
     - Video: per-Representation <SegmentBase indexRange> +
       <Initialization range> (v0.1.0 形态，让 adaptive 精准拼 Range)
     - Audio: 多 AdaptationSet (AAC / Dolby / Hi-Res FLAC, v0.3.0 形态)
     - BaseURL = B 站 CDN 直链（不过代理）
  3. MPD 写 special://temp/plugin.video.bili/{cid}.mpd
  4. plugin.set_resolved_url 把 http://127.0.0.1:{port}/{cid}.mpd 喂给
     inputstream.adaptive，4 个 properties：
       inputstream.adaptive.manifest_type     = 'mpd'
       inputstream.adaptive.manifest_headers  = 'Referer=https://www.bilibili.com'
       inputstream.adaptive.stream_headers    = 'Referer=https://www.bilibili.com'
       inputstream                              = 'inputstream.adaptive'
"""
import os

from core import plugin, xbmc, xbmcvfs
from utils import getSetting, get_temp_path, make_dirs, tag
from api import get_api_data, raw_get_api_data, get_cookie
from live import stop_all_live_danmaku
from playback import (
    generate_mpd, generate_ass, report_history,
)


# wiliwili FNVAL
_WILIWILI_FNVAL = 4048

_BILI_REFERER = 'Referer=https://www.bilibili.com'


def _try_wiliwili_playurl(bvid, cid, qn, fnval=None):
    if fnval is None:
        fnval = _WILIWILI_FNVAL
    params = {
        'bvid': bvid, 'cid': str(cid),
        'gaia_source': 'view-card', 'from_client': 'BROWSER',
        'is_main_page': 'false', 'need_fragment': 'false',
        'isGaiaAvoided': 'true', 'voice_balance': '1',
        'web_location': '1315873', 'qn': str(qn),
        'fourk': '1', 'fnval': str(fnval), 'fnver': '0',
    }
    try:
        from api import getWbiKeys, encWbi
        img_key, sub_key = getWbiKeys()
        params = encWbi(params, img_key, sub_key)
    except Exception as e:
        xbmc.log('[wiliwili-playurl] WBI sign failed: %s' % e, xbmc.LOGWARNING)

    for path in ('/x/player/wbi/playurl', '/x/web-interface/playurl'):
        res = raw_get_api_data(path, data=params)
        if res.get('code') == 0 and (res.get('data') or res.get('result')):
            xbmc.log('[wiliwili-playurl] success via %s' % path, xbmc.LOGINFO)
            return path, res
        xbmc.log(
            '[wiliwili-playurl] %s failed code=%s msg=%s' % (
                path, res.get('code'), res.get('message', ''),
            ),
            xbmc.LOGWARNING,
        )
    return None, res


def _media_id_to_season(media_id) -> int:
    res = get_api_data('/pgc/review/user', {'media_id': media_id})
    if res['code'] == 0:
        return res['result']['media']['season_id']
    return 0


@plugin.route('/bangumi/<type>/<id>/')
def bangumi(type, id):
    items = []
    if type == 'media_id':
        type = 'season_id'
        id = _media_id_to_season(id)
    res = get_api_data('/pgc/view/web/season', {type: id})
    if res['code'] != 0:
        return items
    for episode in res['result']['episodes']:
        if episode['badge']:
            label = tag('【' + episode['badge'] + '】', 'pink') + episode['share_copy']
        else:
            label = episode['share_copy']
        context_menu = [(
            '仅播放音频',
            f"PlayMedia({plugin.url_for('video', id=episode['bvid'], cid=episode['cid'], ispgc='true', audio_only='true', title=episode['share_copy'])})",
        )]
        items.append({
            'label': label,
            'path': plugin.url_for('video', id=episode['bvid'], cid=episode['cid'],
                                   ispgc='true', audio_only='false',
                                   title=episode['share_copy']),
            'is_playable': True,
            'icon': episode['cover'],
            'thumbnail': episode['cover'],
            'context_menu': context_menu,
            'info': {
                'mediatype': 'video', 'title': episode['share_copy'],
                'duration': episode['duration'] / 1000,
                'plot': f"{episode['share_copy']}\n{episode['bvid']}\nep{episode['ep_id']}",
            },
            'info_type': 'video',
        })
    return items


@plugin.route('/videopages/<id>/')
def videopages(id):
    videos = []
    res = get_api_data('/x/web-interface/view', {'bvid': id})
    data = res['data']
    if res['code'] != 0:
        return videos
    for item in data['pages']:
        pic = item.get('first_frame') or data['pic']
        context_menu = [(
            '仅播放音频',
            f"PlayMedia({plugin.url_for('video', id=data['bvid'], cid=item['cid'], ispgc='false', audio_only='true', title=item['part'])})",
        )]
        videos.append({
            'label': item['part'],
            'path': plugin.url_for('video', id=data['bvid'], cid=item['cid'],
                                   ispgc='false', audio_only='false',
                                   title=item['part']),
            'is_playable': True,
            'icon': pic, 'thumbnail': pic,
            'context_menu': context_menu,
            'info': {
                'mediatype': 'video', 'title': item['part'],
                'duration': item['duration'],
            },
            'info_type': 'video',
        })
    return videos


# ── 实际播放入口 ─────────────────────────────────────────────────────

@plugin.route('/video/<id>/<cid>/<ispgc>/<audio_only>/<title>/')
def video(id, cid, ispgc, audio_only, title):
    stop_all_live_danmaku()

    cid = str(cid)
    ispgc = ispgc == 'true'
    audio_only = audio_only == 'true'
    video_url = ''

    if cid == '0':
        res = get_api_data('/x/web-interface/view', {'bvid': id})
        if res['code'] != 0:
            return
        data = res['data']
        cid = data['pages'][0]['cid']
        if 'redirect_url' in data and 'bangumi/play/ep' in data['redirect_url']:
            ispgc = True
        else:
            ispgc = False

    if ispgc:
        url = '/pgc/player/web/playurl'
    else:
        url = None

    qn = getSetting('video_resolution')
    enable_dash = getSetting('enable_dash')
    fnval = _WILIWILI_FNVAL if enable_dash == 'true' else 1

    if ispgc:
        params = {'bvid': id, 'cid': cid, 'qn': qn, 'fnval': fnval,
                  'fnver': 0, 'fourk': 1}
        res = raw_get_api_data(url, data=params)
        if res.get('code') != 0:
            return
    else:
        wiliwili_url, res = _try_wiliwili_playurl(id, cid, qn, fnval=fnval)
        if wiliwili_url is None:
            xbmc.log('[video] wiliwili failed, fallback /x/player/playurl', xbmc.LOGWARNING)
            params = {'bvid': id, 'cid': cid, 'qn': qn, 'fnval': fnval,
                      'fnver': 0, 'fourk': 1,
                      'from_client': 'BROWSER', 'isGaiaAvoided': 'true',
                      'web_location': '1315873', 'need_fragment': 'false'}
            res = raw_get_api_data('/x/player/playurl', data=params)
            if res.get('code') != 0:
                return
            url = '/x/player/playurl'
        else:
            url = wiliwili_url

    data = res['result'] if ispgc else res['data']
    port = getSetting('server_port') or '54321'

    # 1) audio_only: 单音轨 pipe 直连（不走 adaptive）
    if 'dash' in data and audio_only:
        from playback import collect_audio_tracks, select_by_user_pref
        tracks = select_by_user_pref(collect_audio_tracks(data['dash']))
        if not tracks:
            return
        t = tracks[0]
        video_url = {
            'label': title,
            'path': '%s|%s' % (t.base_url, _BILI_REFERER),
            'is_playable': True,
        }
        plugin.set_resolved_url(video_url)
        return

    # 2) DASH: 写 MPD → set_resolved_url 喂 inputstream.adaptive
    if 'dash' in data:
        basepath = get_temp_path()
        if not basepath or not make_dirs(basepath):
            return

        try:
            mpd_text = generate_mpd(data['dash'])
        except Exception as e:
            xbmc.log('[video] generate_mpd failed: %s' % e, xbmc.LOGERROR)
            return

        mpd_path = os.path.join(basepath, '%s.mpd' % cid)
        try:
            with xbmcvfs.File(mpd_path, 'w') as f:
                success = f.write(mpd_text)
            if not success:
                xbmc.log('[video] MPD write failed: %s' % mpd_path, xbmc.LOGERROR)
                return
        except Exception as e:
            xbmc.log('[video] MPD write error: %s' % e, xbmc.LOGERROR)
            return

        mpd_url = 'http://127.0.0.1:%s/%s.mpd' % (port, cid)
        xbmc.log('[video] MPD written: %s → %s' % (mpd_path, mpd_url), xbmc.LOGINFO)
        video_url = {
            'path': mpd_url,
            'is_playable': True,
            'properties': {
                'inputstream': 'inputstream.adaptive',
                'inputstream.adaptive.manifest_type': 'mpd',
                'inputstream.adaptive.manifest_headers': _BILI_REFERER,
                'inputstream.adaptive.stream_headers': _BILI_REFERER,
            },
        }

    elif 'durl' in data:
        durl_url = data['durl'][0]['url']
        if durl_url:
            video_url = {
                'path': '%s|%s' % (durl_url, _BILI_REFERER),
                'is_playable': True,
            }
    else:
        video_url = ''

    if not video_url:
        return

    ass = None
    if getSetting('enable_danmaku') == 'true':
        ass = generate_ass(cid)
    if getSetting('report_history') == 'true':
        report_history(id, cid)

    plugin.set_resolved_url(video_url, subtitles=ass)
```

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('e:/Project/plugin.video.bili/routes/video.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Verify no `m3u8` / `proxy` references remain**

Run: `grep -nE "m3u8|proxy" e:/Project/plugin.video.bili/routes/video.py`
Expected: NO matches.

- [ ] **Step 5: Commit**

```bash
git add routes/video.py
git commit -m "refactor: VOD path uses inputstream.adaptive + static MPD URL"
```

---

## Task 11: Update `routes/live.py` — master_url priority + adaptive HLS

**Files:**
- Modify: `e:\Project\plugin.video.bili\routes\live.py`

The new `live(id)` function:
1. Tries `format=1` (fmp4 only) at the multi-QN ladder.
2. On no result, tries `format=0,1,2` (all formats) at the same ladder.
3. `choose_live_resolution(streams)` returns a `best` dict with `urls` and `master_url`.
4. **Prefer `master_url`** (m3u8) as `path`. Fall back to `urls[0]` (raw m4s) only if `master_url` is empty.
5. Emit ONE `set_resolved_url` with `inputstream.adaptive manifest_type='hls'`, `manifest_update_params='full'`, both Referer headers.
6. If `best` is FLV-only (no fmp4 at all), notify + return; no FLV pipe fallback.

- [ ] **Step 1: Read current `routes/live.py:332-410` for context**

Current file ends with two branches: FLV pipe + fmp4/ts pipe. Both are replaced.

- [ ] **Step 2: Replace the `live` function body**

Open `e:\Project\plugin.video.bili\routes\live.py` in your editor. Locate the function `def live(id):` (around line 333). Replace the **entire body** of the function (everything after the `def live(id):` line up to and including the last `plugin.set_resolved_url(...)` call) with the following:

```python
def _fetch(stream_qn, fmt_filter):
    params = (
        'room_id={}&no_playurl=0&mask=1&qn={}&platform=web'
        '&protocol=0,1&format={}&codec=0,1,2'
        '&dolby=5&ptype=8&panorama=1'
    ).format(id, stream_qn, fmt_filter)
    r = fetch_url(
        'https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo?' + params
    )
    if r['code'] != 0 or not r.get('data', {}).get('playurl_info'):
        return None
    return r['data']['playurl_info']['playurl']['stream']


def live(id):
    """Adaptive HLS only. Force fmp4 (format=1) at multi-QN levels;
    on no fmp4, retry with all formats. Prefer master_url (m3u8 from
    http_hls) over urls[0] (raw m4s) as inputstream.adaptive's path.
    """
    qn = getSetting('live_resolution')

    # ── 强制 fmp4 (format=1) 多 QN 降级 ──
    streams = None
    for try_qn in (qn, 400, 250, 150, 80):
        streams = _fetch(try_qn, '1')
        if streams:
            break
    # ── 无 fmp4 → 回退所有 format (0,1,2) ──
    if not streams:
        for try_qn in (qn, 400, 250, 150, 80):
            streams = _fetch(try_qn, '0,1,2')
            if streams:
                break
    if not streams:
        xbmc.log('[live] no playurl for room_id=%s' % id, xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'plugin.video.bili', '无法获取直播流 (room_id=%s)' % id,
            xbmcgui.NOTIFICATION_ERROR, 3000,
        )
        return

    best = choose_live_resolution(streams)
    if not best:
        xbmc.log('[live] no codec room_id=%s' % id, xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'plugin.video.bili', '该房间无可用编码 (room_id=%s)' % id,
            xbmcgui.NOTIFICATION_ERROR, 3000,
        )
        return

    master_url = best.get('master_url', '') or ''
    urls = best.get('urls', []) or []
    fmt_name = best.get('format_name', '')
    codec_name = best.get('codec_name', '')

    # 优先 master_url (m3u8 from http_hls)；回退 urls[0] (raw m4s)
    chosen = master_url or (urls[0] if urls else '')
    if not chosen:
        xbmc.log('[live] no url room_id=%s' % id, xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            'plugin.video.bili', '直播流 URL 为空 (room_id=%s)' % id,
            xbmc.NOTIFICATION_ERROR, 3000,
        )
        return

    xbmc.log(
        '[live] %s/%s room_id=%s master=%s url=%s' % (
            fmt_name, codec_name, id,
            'yes' if master_url else 'no',
            chosen[:80],
        ),
        xbmc.LOGINFO,
    )

    # ── 直播弹幕 ──
    live_ass = None
    if getSetting('enable_live_danmaku') == 'true':
        from api import get_uid, get_cookie
        uid = get_uid()
        cookie = get_cookie()
        live_ass, _ = start_live_danmaku(id, uid, cookie)

    # ── inputstream.adaptive 唯一输出 ──
    plugin.set_resolved_url({
        'path': chosen,
        'is_playable': True,
        'is_live': True,
        'properties': {
            'inputstream': 'inputstream.adaptive',
            'inputstream.adaptive.manifest_type': 'hls',
            'inputstream.adaptive.manifest_update_params': 'full',
            'inputstream.adaptive.manifest_headers': _BILI_REFERER,
            'inputstream.adaptive.stream_headers': _BILI_REFERER,
        },
    }, subtitles=live_ass)
```

- [ ] **Step 3: Add the `_BILI_REFERER` constant at module level**

At the top of `e:\Project\plugin.video.bili\routes\live.py` (above the `_LIVE_AREAS` dict or anywhere near the top), add:

```python
_BILI_REFERER = 'Referer=https://www.bilibili.com'
```

- [ ] **Step 4: Remove the unused `_HLS_ALLOWED_EXTS` and `_ensure_hls_ext` helpers**

These v0.3.0 helpers are no longer called. Delete them entirely (find the `def _ensure_hls_ext(url: str) -> str:` block and the `_HLS_ALLOWED_EXTS` tuple above it; remove both).

- [ ] **Step 5: Remove the unused `urlparse` import if no other caller**

Run: `grep -n "urlparse" e:/Project/plugin.video.bili/routes/live.py`
Expected: After Step 4, NO matches. If so, remove `urlparse` from the `from urllib.parse import urlencode, urlparse` line.

- [ ] **Step 6: Add the `xbmcgui` import if not already imported**

Run: `grep -nE "^from core import|^import xbmcgui" e:/Project/plugin.video.bili/routes/live.py`
Expected: `from core import plugin, xbmc, xbmcvfs, xbmcgui, xbmcaddon` is already at the top (it is in v0.3.0). If not, add `xbmcgui` to the import line.

- [ ] **Step 7: Syntax check**

Run: `python -c "import ast; ast.parse(open('e:/Project/plugin.video.bili/routes/live.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add routes/live.py
git commit -m "refactor: live path uses inputstream.adaptive manifest_type=hls"
```

---

## Task 12: Add `inputstream.adaptive` install-prompt to `routes/menu.py:index()`

**Files:**
- Modify: `e:\Project\plugin.video.bili\routes\menu.py`

The `index()` function in `routes/menu.py` (which is the actual location — note: v0.1.0 had this in `routes.py`, but v0.3.0 split it into `routes/menu.py`) currently calls `get_categories()` and returns the menu items. We need to insert the install-prompt block BEFORE the function returns.

- [ ] **Step 1: Read `routes/menu.py` to locate `index()`**

The function is the one decorated with `@plugin.route('/')`.

- [ ] **Step 2: Verify the function body**

Current shape:
```python
@plugin.route('/')
def index():
    items = []
    categories = update_categories()
    for category in categories:
        if getSetting('function.' + category['name']) == 'true':
            ...
            items.append({...})
    return items
```

- [ ] **Step 3: Add the install-prompt block right before `return items`**

Insert this block immediately before the `return items` line of `index()`:

```python
    if (getSetting('enable_dash') == 'true'
            and not xbmc.getCondVisibility('System.HasAddon(inputstream.adaptive)')):
        if xbmcgui.Dialog().yesno(
            '安装插件',
            '使用 dash 功能需要安装 inputstream.adaptive 插件，是否安装？',
            '取消', '确认',
        ):
            xbmc.executebuiltin('InstallAddon(inputstream.adaptive)')
        else:
            if xbmcgui.Dialog().yesno(
                '取消安装', '不使用 dash 请到设置中关闭',
                '取消', '确认',
            ):
                plugin.open_settings()
```

- [ ] **Step 4: Verify `xbmcgui` and `xbmc` are imported in this file**

Run: `grep -nE "^from core import|^import xbmc" e:/Project/plugin.video.bili/routes/menu.py`
Expected: `xbmc` and `xbmcgui` are imported (v0.3.0 has them).

- [ ] **Step 5: Syntax check**

Run: `python -c "import ast; ast.parse(open('e:/Project/plugin.video.bili/routes/menu.py').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add routes/menu.py
git commit -m "feat: prompt to install inputstream.adaptive when missing"
```

---

## Task 13: Add new strings to `resources/language/.../strings.po`

**Files:**
- Modify: `e:\Project\plugin.video.bili\resources\language\resource.language.zh_cn\strings.po`
- Modify: `e:\Project\plugin.video.bili\resources\language\resource.language.en_gb\strings.po`

We add three new strings:
- `30140` — "使用 dash 功能需要安装 inputstream.adaptive 插件，是否安装？" / "DASH playback requires inputstream.adaptive. Install?"
- `30141` — "不使用 dash 请到设置中关闭" / "Disable DASH in settings to dismiss this prompt."
- `30142` — "无法获取直播流" / "Unable to fetch live stream"

(The "取消" / "确认" / "安装插件" / "取消安装" strings are already 30140-class, but v0.3.0 had them as raw literals. To stay close to v0.1.0 and reduce risk, we keep them as raw literals in `routes/menu.py` for v0.4.0 and only localize the new live-failure notification.)

- [ ] **Step 1: Read the existing `strings.po` files**

Open both files and confirm they have the `msgid "" / msgstr ""` header at the top. If they do not, add one.

- [ ] **Step 2: Add the new strings to the Chinese `strings.po`**

Append to `e:\Project\plugin.video.bili\resources\language\resource.language.zh_cn\strings.po`:

```po
msgid "30142"
msgstr "无法获取直播流"
```

- [ ] **Step 3: Add the new strings to the English `strings.po`**

Append to `e:\Project\plugin.video.bili\resources\language\resource.language.en_gb\strings.po`:

```po
msgid "30142"
msgstr "Unable to fetch live stream"
```

(We deliberately do NOT localize the install-prompt strings in v0.4.0. They use raw literals matching v0.1.0, which is a known-good UX. Localization is a follow-up.)

- [ ] **Step 4: Verify the new msgid is present in both files**

Run: `grep -n "30142" e:/Project/plugin.video.bili/resources/language/resource.language.*/strings.po`
Expected: 2 matches, one per language file.

- [ ] **Step 5: Commit**

```bash
git add resources/language/
git commit -m "feat: add 30142 string for live-stream fetch failure"
```

---

## Task 14: Update `CLAUDE.md` to reflect v0.4.0 architecture

**Files:**
- Modify: `e:\Project\plugin.video.bili\CLAUDE.md`

The project instructions document describes the v0.3.0 architecture. We update the four v0.3.0-specific claims so future agents see the v0.4.0 state.

- [ ] **Step 1: Update the build/deps line (line 17)**

In `CLAUDE.md`, locate the line:
```
- External Python deps come from Kodi add-ons, **not pip**: `script.module.requests` (≥2.12.4), `script.module.qrcode` (≥5.3). **No inputstream addons required** — Kodi built-in ffmpeg handles DASH MPD natively.
```

Replace it with:
```
- External Python deps come from Kodi add-ons, **not pip**: `script.module.requests` (≥2.12.4), `script.module.qrcode` (≥5.3). **inputstream.adaptive** is declared `optional="true"` in `addon.xml`; Kodi 21 ships it, and `routes/menu.py:index()` prompts the user to install it if missing. v0.1.0-style MPD (per-Representation `<SegmentBase indexRange>` + `<Initialization range>`) + B 站 CDN BaseURL directly + `stream_headers='Referer=https://www.bilibili.com'`.
```

- [ ] **Step 2: Update the routes/video.py annotation (line 63)**

In `CLAUDE.md`, locate:
```
  - [routes/video.py](routes/video.py) — `bangumi`, `videopages`, `video` (3 routes; MPD 生成 + 本地代理分片, 无 inputstream 依赖)
```

Replace it with:
```
  - [routes/video.py](routes/video.py) — `bangumi`, `videopages`, `video` (3 routes; MPD 生成 → `inputstream.adaptive` + 4 prop; segments fetch B 站 CDN with `stream_headers=Referer=…`)
```

- [ ] **Step 3: Replace the VOD flow block (lines 80-92)**

The current block is the v0.3.0 ffmpeg dashdemux flow. Replace it with the v0.4.0 adaptive flow:

```
### Request flow (VOD DASH MPD playback — 默认路径)

```
1. Kodi → addon.py → plugin.run() → routes/video.py:video(id, cid, ...)
2. 调用 B 站 playurl API (fnval=4048) 获取 DASH 数据
3. playback/mpd.generate_mpd(dash) 生成 MPD XML
   - 视频: 每个 Representation 包含 <SegmentBase indexRange="…"> +
     <Initialization range="…"/> (v0.1.0 形态)
   - 音频: 多 <AdaptationSet> (AAC + Dolby + Hi-Res FLAC, v0.3.0 形态)
   - BaseURL = B 站 CDN 直链 (不过代理)
4. MPD 写盘到 special://temp/plugin.video.bili/{cid}.mpd
5. plugin.set_resolved_url({
     path: "http://127.0.0.1:{port}/{cid}.mpd",
     properties: {
       'inputstream': 'inputstream.adaptive',
       'inputstream.adaptive.manifest_type': 'mpd',
       'inputstream.adaptive.manifest_headers': 'Referer=https://www.bilibili.com',
       'inputstream.adaptive.stream_headers': 'Referer=https://www.bilibili.com',
     },
   })
6. inputstream.adaptive 通过 HTTP 读 MPD → 解析 → 拼 Range 拉 B 站 CDN segments
7. inputstream.adaptive 内置 demuxer 解析 fmp4 → 解码播放
```

- [ ] **Step 4: Update the live playback flow block**

Find the existing "Request flow (live playback)" block and replace its body. The new block:

```
### Request flow (live playback)

1. routes/live.py:live(id)
2. getRoomPlayInfo with `format=1` (fmp4 only) at multi-QN levels
3. 若全部 QN 都无 fmp4，回退 `format=0,1,2` 多 QN 重试
4. playback.live.choose_live_resolution(streams) → best (含 master_url / urls[0])
5. **优先 master_url (m3u8 from http_hls)**；否则 urls[0] (裸 m4s URL)
6. plugin.set_resolved_url({
     path: <chosen url>,
     is_playable: True,
     is_live: True,
     properties: {
       'inputstream': 'inputstream.adaptive',
       'inputstream.adaptive.manifest_type': 'hls',
       'inputstream.adaptive.manifest_update_params': 'full',
       'inputstream.adaptive.manifest_headers': 'Referer=https://www.bilibili.com',
       'inputstream.adaptive.stream_headers': 'Referer=https://www.bilibili.com',
     },
   })
7. Live danmaku: live/danmaku.py:start_live_danmaku() 在 set_resolved_url 之前启动，
   生成外挂 .ass 通过 subtitles=live_ass 传 Kodi 播放器
```

- [ ] **Step 5: Verify CLAUDE.md no longer has v0.3.0 claims**

Run: `grep -nE "No inputstream addons required|本地代理分片|不设置 inputstream 属性|ffmpeg dash demuxer" e:/Project/plugin.video.bili/CLAUDE.md`
Expected: NO matches.

- [ ] **Step 6: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to describe v0.4.0 architecture"
```

---

## Task 15: Final integration smoke test in Kodi 21

**Files:**
- Read: `e:\Project\plugin.video.bili\addon.xml` (verify final state)
- Run Kodi 21 with the plugin installed

There is no automated test suite. This task is the manual smoke verification per spec §7.

- [ ] **Step 1: Re-parse all Python files**

Run: `python -c "import ast, os; [ast.parse(open(os.path.join(r,f)).read()) for r,_,fs in os.walk('e:/Project/plugin.video.bili') for f in fs if f.endswith('.py')]; print('all .py parse OK')"`
Expected: `all .py parse OK`

- [ ] **Step 2: Verify deleted files are gone**

Run: `ls e:/Project/plugin.video.bili/monitor.py e:/Project/plugin.video.bili/playback/proxy.py e:/Project/plugin.video.bili/playback/m3u8.py 2>&1`
Expected: all three report "No such file or directory".

- [ ] **Step 3: Verify `addon.xml` is well-formed and version is 0.4.0**

Run: `python -c "import xml.etree.ElementTree as ET; t = ET.parse('e:/Project/plugin.video.bili/addon.xml'); print('version:', t.getroot().get('version'))"`
Expected: `version: 0.4.0`

- [ ] **Step 4: Verify `inputstream.adaptive` is in requires**

Run: `grep -nE 'inputstream\.adaptive' e:/Project/plugin.video.bili/addon.xml`
Expected: at least one match, and the line should include `optional="true"`.

- [ ] **Step 5: Manual Kodi smoke test (VOD AVC)**

Zip the plugin root: `plugin.video.bili-0.4.0.zip` at the repo root. Install in Kodi 21. Play a normal AVC 1080P video. Expect: plays, MPD is requested from `http://127.0.0.1:54321/{cid}.mpd` (visible in `xbmc.log`).

- [ ] **Step 6: Manual Kodi smoke test (VOD Hi-Res FLAC)**

Play a Hi-Res FLAC B 站 video. Expect: plays, audio track menu shows multiple options (AAC + Dolby + Hi-Res FLAC), selecting Hi-Res FLAC plays lossless audio.

- [ ] **Step 7: Manual Kodi smoke test (VOD Dolby Vision)**

Play a Dolby Vision title. Expect: plays; system reports `Dolby Vision` in the OSD.

- [ ] **Step 8: Manual Kodi smoke test (Live)**

Open a live room. Expect: plays via `inputstream.adaptive manifest_type='hls'`. `xbmc.log` should show `master=yes` or `master=no` depending on the room.

- [ ] **Step 9: Manual smoke test (Seek)**

Drag the seek bar mid-video. Expect: <1s rebuffer, no error dialog.

- [ ] **Step 10: Manual smoke test (First-run install prompt)**

On a Kodi install WITHOUT `inputstream.adaptive` (use a portable Kodi install or temporarily disable it). Open the plugin. Expect: a `yesno` dialog asking "使用 dash 功能需要安装 inputstream.adaptive 插件，是否安装？".

- [ ] **Step 11: Commit any final fixes**

If any smoke test fails, fix the relevant code and commit. Repeat the affected test.

- [ ] **Step 12: Tag the release**

```bash
git tag v0.4.0
git log --oneline -1
```

Expected last log line: shows the v0.4.0 set of changes. Tag `v0.4.0` is now on the latest commit.

---

## Self-Review (after writing)

1. **Spec coverage:**
   - §2 Goal #1 (VOD MPD with SegmentBase + manifest_headers + stream_headers) — Task 9 (`mpd.py`) + Task 10 (`routes/video.py`)
   - §2 Goal #2 (multi-AS audio preserved) — Task 9
   - §2 Goal #3 (live adaptive HLS with master_url priority) — Task 11
   - §2 Goal #4 (delete segment proxy) — Tasks 2, 3, 4, 5, 6, 8
   - §2 Goal #5 (inputstream.adaptive optional + menu install prompt) — Tasks 1, 12
   - §3 Non-goals — no `use_ffmpegdirect_fallback` setting, no HTTPS, no toggles, no automatic version detection → not implemented (correct)
   - §4.1 VOD flow — Tasks 5, 6, 9, 10
   - §4.2 Live flow — Task 11
   - §4.3 durl path — preserved in Task 10
   - §5.1 addon.xml — Task 1
   - §5.2 mpd.py — Task 9
   - §5.3 m3u8.py delete — Task 8
   - §5.4 proxy.py delete — Task 8
   - §5.5 http_server.py — Task 5
   - §5.6 service.py — Task 6
   - §5.7 monitor.py delete — Task 8
   - §5.8 routes/video.py — Task 10
   - §5.9 routes/live.py — Task 11
   - §5.10 menu install-prompt — Task 12
   - §5.11 plugin_compat.py unchanged — not implemented (correct)
   - §5.12 settings.xml unchanged — not implemented (correct)
   - §5.13 strings.po — Task 13
   - §6 Error handling — Tasks 10, 11
   - §7 Testing — Task 15
   - §9 Risk register — not implemented as code; risks are documented in spec and the relevant mitigations are in the relevant tasks (multi-QN fallback in Task 11, m3u8 preference in Task 11, master_url empty fallback in Task 11)

2. **Placeholder scan:** No "TBD" / "TODO" / "implement later" / "fill in" / "similar to" / "appropriate" placeholders. All code is shown in full.

3. **Type/signature consistency:**
   - `generate_mpd(dash)` — single-arg, matches v0.1.0 (Task 9 caller is Task 10).
   - `build_manifest_headers` was mentioned in an earlier spec draft but is NOT used in the final spec — correctly absent.
   - `_BILI_REFERER` constant — added in Task 11, used in `routes/live.py`. Different from `routes/video.py`'s `_BILI_REFERER` (also added in Task 10). Both are scoped to their respective files; no cross-module collision.
   - `stop_all_live_danmaku()` — confirmed in Task 7, used in Task 6 (`service.py`) and Task 10 (`routes/video.py`).
   - `getSetting('server_port')` — read in both Task 6 (service) and Task 10 (routes/video), same default `54321`.

4. **Order of execution:** Tasks must be done in order. Task 8 is now a placeholder (the actual deletion moved to Task 16). Task 9 must precede Task 10 (mpd.py's new signature is what routes/video.py calls). Task 11 (routes/live.py) is independent of Task 10 but depends on `live.danmaku.start_live_danmaku` import being available. Task 16 (final deletion) must come after Task 10 (and Task 9) since they migrate the proxy/m3u8 imports.

---

## Task 16: Delete obsolete files (deferred from Task 8)

**Files:**
- Delete: `e:\Project\plugin.video.bili\monitor.py`
- Delete: `e:\Project\plugin.video.bili\playback\proxy.py`
- Delete: `e:\Project\plugin.video.bili\playback\m3u8.py`

After Tasks 9 and 10, no file imports `monitor`, `proxy`, or `m3u8`. Safe to delete.

- [ ] **Step 1: Verify no remaining references**

Run: `grep -rnE "from monitor|import monitor|from playback\.proxy|from \.proxy|from playback\.m3u8|from \.m3u8" e:/Project/plugin.video.bili/ --include="*.py"`
Expected: NO matches.

- [ ] **Step 2: Delete the three files**

```bash
git rm e:/Project/plugin.video.bili/monitor.py
git rm e:/Project/plugin.video.bili/playback/proxy.py
git rm e:/Project/plugin.video.bili/playback/m3u8.py
```

- [ ] **Step 3: Verify the plugin still loads (smoke check)**

Run: `PYTHONIOENCODING=utf-8 python -c "import ast, os; [ast.parse(open(os.path.join(r,f), encoding='utf-8').read()) for r,_,fs in os.walk('e:/Project/plugin.video.bili') for f in fs if f.endswith('.py')]; print('all .py parse OK')"`
Expected: `all .py parse OK`

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: delete monitor.py, playback/proxy.py, playback/m3u8.py"
```

---

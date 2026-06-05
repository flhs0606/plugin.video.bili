# CLAUDE.md

哔哩哔哩 Kodi 21 插件。点播走 DASH MPD → `inputstream.adaptive`；直播走 ffmpeg pipe。

- Plugin ID: `plugin.video.bili`；Author: Mephis；Version: 0.4.0
- v0.4.0 移除本地 CDN 代理：segments 由 `inputstream.adaptive` 直拉 B 站 CDN（带 `Referer`）。

## 运行环境

- 纯 Python，跑在 Kodi 21 内嵌解释器。打包 = zip 仓库根目录。
- 无 build/test/lint、无 launch config。不可从 IDE 跑。
- 依赖是 Kodi 扩展（`script.module.requests`、`script.module.qrcode`），不走 pip。`inputstream.adaptive` 标为 optional，缺失时 `routes/menu.py:index()` 提示安装。
- 每次导航是新进程。跨请求状态走 `plugin.get_storage(...)`。
- 手动测试：在 Kodi UI 里跑。

## 顶层架构

3 个 Kodi 扩展点：

1. `xbmc.python.pluginsource` → [addon.py](addon.py)：一次性目录列表。`import routes` 触发 `@plugin.route` 注册 → `_start_mpd_server_daemon()`（daemon 线程 fallback，service 不可靠时用）→ `plugin.run()`。
2. `xbmc.service` → [service.py](service.py)：v0.4.0 主 listener。`0.0.0.0:54321`（`server_port` 可覆盖），`xbmc.Monitor().waitForAbort()` 循环。
3. Live 弹幕生命周期：[addon.py](addon.py) 的 `atexit` 钩子兜底调 `stop_all_live_danmaku()` + 清 lock 文件；daemon 线程自管。`service.py` 故意不 import `live.danmaku`。

### 包结构

| 目录 | 职责 |
|---|---|
| [api/](api/) | HTTP 层：`http.py`（`get_api_data`/`fetch_url`）、`wbi.py`（WBI 签名）、`cookie.py` |
| [playback/](playback/) | 点播播放：`mpd.py`（MPD 生成）、`audio.py`（音轨）、`resolution.py`、`live.py`（直播选流）、`ass.py`（VOD 弹幕 ASS）、`item.py`、`history.py` |
| [live/](live/) | `LiveDanmakuClient`（WS + Bili 二进制协议 → `.ass`，atomic 写盘） |
| [routes/](routes/) | 9 个子模块，URL handlers。共享工具在 [routes/_helpers.py](routes/_helpers.py) |
| 根 | [addon.py](addon.py)、[core.py](core.py)、[plugin_compat.py](plugin_compat.py)（xbmcswift2 兼容层）、[http_server.py](http_server.py)、[utils.py](utils.py)、[danmaku2ass.py](danmaku2ass.py)（vendored） |

`plugin_compat.Plugin` 提供 `route` / `url_for` / `get_storage` / `cached`（TTL=分钟） / `open_settings` / `set_resolved_url` / `run`。其他模块统一 `from core import plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon`。

## 播放主链路

**点播（DASH）**：`routes/video.py:video` → B 站 playurl (`fnval=4048`) → `playback/mpd.generate_mpd`（视频 `<SegmentBase indexRange>` + 音频多 AS：AAC / Dolby / Hi-Res FLAC）→ 写 `special://temp/plugin.video.bili/{cid}.mpd` → `set_resolved_url` 指向 `http://127.0.0.1:{port}/{cid}.mpd` + 4 个 `inputstream.adaptive` props → player 直拉 B 站 CDN。

**点播（Durl 回退）**：`{mp4_url}|Referer=...`（仅 Referer）。

**直播**：`routes/live.py:live` → 两次串行 `getRoomPlayInfo`（`qn=user + format=1` 优先 → `qn=80 + format=0,1,2` → `protocol=0`）→ `playback/live.choose_live_resolution`（codec hevc>avc × modern>flv × qn 高优先）→ 优先 `master_url`、回退 `urls[0]` → ffmpeg pipe `{url}|Referer=...&User-Agent=...&Origin=...&reconnect=1&reconnect_streamed=1&reconnect_delay_max=5`。**不**喂 `inputstream.adaptive`。

## 约定与硬约束

- 中文代码；docstring 可用英文。
- 调 `api.bilibili.com` 一律 `api.get_api_data(path, data=, *, raw=False)`（CDN playurl 用 `raw=True`）。其他 URL 走 `api.fetch_url(url, *, raw=False)`。仅 `live/danmaku.py:_get_token_wbi` 与二维码端点手写请求。
- 缓存：账号/短 TTL 走 `@plugin.cached(TTL=分钟)`。改登录态必须同时调 `api.clear_cookie_cache()` + `plugin.clear_function_cache()`。
- 路径经 `xbmc.translatePath`（polyfill 到 `xbmcvfs.translatePath`）。`special://profile/addon_data/<id>/.storage/`（持久 JSON）、`special://temp/plugin.video.bili/`（MPD/ASS/QR）。
- 直播 listitem：`IsPlayable=True` + `is_live=True`（触发 `IsLiveStream`/`IsLive` + `setMediaType('video')`）。**不要设 `inputstreamaddon`**（Kodi 21 弃用）。
- 后台线程：`LiveDanmakuClient._writer/_hb/_recv_loop` + `plugin_compat._wait_and_set_subtitles` 是 daemon；`addon.py` 的 `atexit` 兜底。`self.danmaku_list` 读写一律在 `with self.lock:` 内。
- WS 协议：`live/danmaku.py:_ws_send` 客户端→服务端帧 mask，服务端→客户端帧 unmask。`protover==2` payload zlib 解压（递归）。
- WBI：`/x/space/wbi/...` 与 `/x/web-interface/wbi/search/*` 必须 `encWbi(params, *getWbiKeys())`；`getWbiKeys()` 30 分钟缓存。
- 菜单 gating：每个大类有 `function.<name>` 布尔开关（settings.xml + `routes/menu.py:index()`）。`_categories()` 的 `name` 与 setting id **逐字符一致**。
- 静态数据手改：`routes/live.py:_LIVE_AREAS`（~190 行）、`routes/home.py:_DYNAMIC_REGIONS`（~42 行）。
- 跨包 import：`api/`、`playback/`、`live/`、`routes/` 内 sibling 用相对 import；顶层用绝对 import。
- 日志：`xbmc.log('[<module>] ...', xbmc.LOGINFO|LOGWARNING|LOGERROR)`，前缀如 `[plugin.video.bili]`、`[api.wbi]`、`[live.danmaku]`。

## 常见任务

- **新顶层菜单**：3 处同步 —— settings.xml `function_setting` 加行、`routes/menu.py:_categories()` 加条目、对应 `routes/*.py` 加 `@plugin.route` handler。label 走 `strings.po` + `localize(id)`。
- **新 B 站 API**：从 `api` 包导入；WBI 端点加签名；非 CDN 响应套 `@plugin.cached(TTL=...)`。
- **新直播源**：照 `routes/live.py:live` —— `is_playable=True` + `is_live=True` + 单次 `set_resolved_url`（ffmpeg pipe）。URL 优先 `master_url`、回退 `urls[0]`。
- **调弹幕**：group 2 改配置；`live/danmaku.py:start_live_danmaku` 读设置。`_writer` 循环 `time.sleep(3)` 是 ASS 刷新间隔；`setSubtitles` 9 秒节拍，别缩短到 3s（视觉会闪）。
- **改版本号**：只改 [addon.xml](addon.xml) 的 `version`，**别动 `id`**（是 storage 目录 key）。
- **发版**：仓库根目录打成 `plugin.video.bili-<version>.zip`（Kodi 按结构安装）；确认 `addon.xml` 在 zip 根。

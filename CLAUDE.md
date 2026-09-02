# CLAUDE.md

哔哩哔哩 Kodi 21 插件。点播走 DASH MPD → `inputstream.adaptive`；直播走 ffmpeg pipe。
无需我明确要求，当我需要库或API文档、生成代码、设置或配置步骤时，始终使用Context7。

- Plugin ID: `plugin.video.bili`；Author: Mephis；Version: 0.7.0
- v0.4.0 移除本地 CDN 代理：segments 由 `inputstream.adaptive` 直拉 B 站 CDN（带 `Referer`）。
- v0.6.0 删除 `enable_dash` 设置项。点播自动按 `inputstream.adaptive` 是否安装切换 DASH (fnval=4048) / 非 DASH (fnval=1)。
- v0.7.0 直播 60 分钟断流修复：`service.py` 新增 `/live-m3u8/<room_id>` 实时转发端点，ffmpeg 每次 refresh 都从 CDN 拿当下最新 sliding window；`storage/live_refresh_state.json` 里的 `m3u8_url` 由 daemon 每 50 分钟调 `getRoomPlayInfo` 续命。**直播现在强依赖 service.py**：addon.py daemon fallback 进程下 storage 为空，live 会 503。

## 运行环境

- 纯 Python，跑在 Kodi 21 内嵌解释器。打包 = zip 仓库根目录；无 build/test/lint。
- 依赖是 Kodi 扩展（`script.module.requests`、`script.module.qrcode`），不走 pip。`inputstream.adaptive` 标为 optional，缺失时 `routes/menu.py:index()` 提示安装。
- 每次导航是新进程。跨请求状态走 `plugin.get_storage(...)`。

## 顶层架构

3 个 Kodi 扩展点：

1. `xbmc.python.pluginsource` → [addon.py](addon.py)：`import routes` 触发 `@plugin.route` 注册 → `_start_mpd_server_daemon()`（daemon 线程 fallback，service 不可靠时用 — 但 v0.7.0+ 直播依赖 service.py，fallback 下 live 会 503）→ `plugin.run()`。
2. `xbmc.service` → [service.py](service.py)：v0.4.0 主 listener，bind `0.0.0.0:54321`（`server_port` 可覆盖），`xbmc.Monitor().waitForAbort()` 循环。同时承载两个端点：
  - `/{cid}.mpd` → 静态 DASH MPD 文件（点播，给 `inputstream.adaptive` 读）。
  - `/live-m3u8/{room_id}` → v0.7.0+ 直播实时转发（每次 fetch CDN m3u8 给 ffmpeg）。daemon 线程（`_LiveRefreshDaemon`）每 50 分钟调 `getRoomPlayInfo` 更新 storage 里的 `m3u8_url`（避开 B 站 CDN 的 ~1-2 小时 TRID 过期）。
3. Live 弹幕生命周期：[addon.py](addon.py) 的 `atexit` 钩子兜底调 `stop_all_live_danmaku()` + 清 lock 文件。`service.py` 故意不 import `live.danmaku`。

### 包结构

| 目录 | 职责 |
|---|---|
| [api/](api/) | HTTP 层：`http.py`（`get_api_data`/`fetch_url`，全局 `requests.Session`）、`wbi.py`（WBI 签名）、`cookie.py` |
| [playback/](playback/) | 点播播放：`mpd.py`（MPD 生成）、`mpd_server.py`（静态 MPD 服务 + v0.7.0+ `/live-m3u8/<room_id>` 实时转发端点）、`audio.py` / `resolution.py` / `live.py` / `ass.py` / `item.py` / `history.py` |
| [live/](live/) | `LiveDanmakuClient`（WS + Bili 二进制协议 → `.ass`，atomic 写盘） |
| [routes/](routes/) | 9 个子模块，URL handlers。共享工具在 [routes/_helpers.py](routes/_helpers.py) |
| [subtitle/](subtitle/) | `danmaku2ass.py`（XML → ASS），被 `playback/ass.py` 与 `live/danmaku.py` 共用 |
| 根 | [addon.py](addon.py)、[core.py](core.py)（重导出层）、[plugin.py](plugin.py)、[service.py](service.py)、[utils.py](utils.py)（8 类职责速查表见文件顶部） |

所有模块统一 `from core import plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon`（`core.py` 重导出，保持 import 兼容）。

## 播放主链路

**点播（DASH）**：`routes/video.py:video` → B 站 playurl (`fnval=4048`) → `playback/mpd.generate_mpd`（视频 `<SegmentBase indexRange>` + 音频多 AS：AAC / Dolby / Hi-Res FLAC）→ 写 `special://temp/plugin.video.bili/{cid}.mpd` → `set_resolved_url` 指向 `http://127.0.0.1:{port}/{cid}.mpd` + 4 个 `inputstream.adaptive` props → player 直拉 B 站 CDN。

**点播（Durl 回退）**：`{mp4_url}|Referer=...`（仅 Referer）。

**直播**（v0.7.0+）：`routes/live.py:live` → 两次串行 `getRoomPlayInfo`（`qn=user + format=1` 优先 → `qn=80 + format=0,1,2` → `protocol=0`）→ `playback/live.choose_live_resolution`（codec hevc>avc × modern>flv × qn 高优先）→ 优先 `master_url`、回退 `urls[0]` →
- **m3u8 路径**（`format_name != 'flv'`）：写入 `storage/live_refresh_state.json[id] = {'m3u8_url': chosen}`（用 `plugin.write_storage`，**不**用 `get_storage` —— 跨进程 cache 不可见）→ `set_resolved_url` 指向 `http://127.0.0.1:54321/live-m3u8/<id>|Referer=...&...&reconnect=1&...`。service.py 每次 ffmpeg 来拉都实时转发 CDN m3u8 → 改写相对路径为绝对路径 → 返回给 ffmpeg。ffmpeg 拉 segment 直接走 CDN 绝对 URL。**不**喂 `inputstream.adaptive`。
- **flv 路径**：直接喂 CDN URL（ffmpeg pipe + reconnect）。

## 约定与硬约束

- 中文代码；docstring 可用英文。
- 调 `api.bilibili.com` 一律 `api.get_api_data(path, data=, *, raw=False)`（CDN playurl 用 `raw=True`）。其他 URL 走 `api.fetch_url(url, *, raw=False)`。仅 `live/danmaku.py:_get_token_wbi` 与二维码端点手写请求。
- 缓存：账号/短 TTL 走 `@plugin.cached(TTL=分钟)`。改登录态必须同时调 `api.clear_cookie_cache()` + `plugin.clear_function_cache()`。
- 路径经 `xbmc.translatePath`（polyfill 到 `xbmcvfs.translatePath`）。`special://profile/addon_data/<id>/.storage/`（持久 JSON）、`special://temp/plugin.video.bili/`（MPD/ASS/QR）。
- 直播 listitem：`IsPlayable=True` + `is_live=True`（触发 `IsLiveStream`/`IsLive` + `setMediaType('video')`）。**不要设 `inputstreamaddon`**（Kodi 21 弃用）。
- 后台线程：`LiveDanmakuClient._writer/_hb/_recv_loop` + `plugin._wait_and_set_subtitles` 是 daemon；`addon.py` 的 `atexit` 兜底。`self.danmaku_list` 读写一律在 `with self.lock:` 内。
- WS 协议：`live/danmaku.py:_ws_send` 客户端→服务端帧 mask，服务端→客户端帧 unmask。`protover==2` payload zlib 解压（递归）。
- WBI：`encWbi(params, *getWbiKeys())`；`getWbiKeys()` 30 分钟缓存。
- 菜单 gating：每个大类有 `function.<name>` 布尔开关（settings.xml + `routes/menu.py:index()`）。`_categories()` 的 `name` 与 setting id **逐字符一致**。
- 静态数据手改：`routes/live.py:_LIVE_AREAS`、`routes/home.py:_DYNAMIC_REGIONS`。
- 跨包 import：`api/`、`playback/`、`live/`、`routes/` 内 sibling 用相对 import；顶层用绝对 import。
- 日志：`xbmc.log('[<module>] ...', xbmc.LOGDEBUG|LOGINFO|LOGWARNING|LOGERROR)`。DEBUG 用于高频诊断(每 API 调用 / 每播放一次),INFO 用于一次性阶段事件(WS 握手 / 选流结果),WARNING/ERROR 留给真异常。前缀如 `[plugin.video.bili]`、`[api.wbi]`、`[live.danmaku]`。

## 常见任务

- **新顶层菜单**：3 处同步 —— settings.xml `function_setting` 加行、`routes/menu.py:_categories()` 加条目、对应 `routes/*.py` 加 `@plugin.route` handler。label 走 `strings.po` + `localize(id)`。
- **新 B 站 API**：从 `api` 包导入；WBI 端点加签名；非 CDN 响应套 `@plugin.cached(TTL=...)`。
- **新直播源**：照 `routes/live.py:live` —— `is_playable=True` + `is_live=True`。m3u8 路径必须 `plugin.write_storage('live_refresh_state', {id: {'m3u8_url': ...}})`，然后 `set_resolved_url` 喂 `http://127.0.0.1:54321/live-m3u8/<id>`；flv 路径直接喂 CDN URL。URL 优先 `master_url`、回退 `urls[0]`。
- **调弹幕**：group 2 改配置；`live/danmaku.py:start_live_danmaku` 读设置。`_writer` 循环 `time.sleep(3)` 是 ASS 刷新间隔；`setSubtitles` 9 秒节拍，别缩短到 3s（视觉会闪）。
- **改版本号**：只改 [addon.xml](addon.xml) 的 `version`，**别动 `id`**（是 storage 目录 key）。
- **发版**：仓库根目录打成 `plugin.video.bili-<version>.zip`（Kodi 按结构安装）；确认 `addon.xml` 在 zip 根。

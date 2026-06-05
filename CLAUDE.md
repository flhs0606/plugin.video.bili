# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# 哔哩哔哩 Kodi 插件 (plugin.video.bili)

A Kodi 21 video add-on for Bilibili, refactored from `chen310/plugin.video.bilibili`. Provides browse (home feed, rankings, dynamics, search, history, favorites, watchlater, user space, collections/series, subscriptions), playback (VOD via DASH MPD fed to inputstream.adaptive; live fmp4/HLS via adaptive HLS), and QR-code / cookie login. v0.4.0 dropped the v0.3.0 local CDN proxy — segments now go directly from inputstream.adaptive to B 站 CDN with `stream_headers=Referer=…`.

Plugin ID: `plugin.video.bili`. Author: Mephis. Version: 0.4.0.

## Build / Install / Develop

There is **no Python build system, no `requirements.txt`, no test runner, no linter, no formatter** in this repo. The plugin is plain Python that runs **inside Kodi's embedded interpreter**, so:

- The deliverable is the repo root itself (zip it and "Install from zip" in Kodi) — see [README.md](README.md) for the master-zip URL.
- Python compatibility target: Kodi 21's Python 3 (declared as `xbmc.python` 3.0.0 in [addon.xml](addon.xml)). Do not use stdlib features newer than what Kodi 21 ships.
- External Python deps come from Kodi add-ons, **not pip**: `script.module.requests` (≥2.12.4), `script.module.qrcode` (≥5.3). **inputstream.adaptive** is declared `optional="true"` in `addon.xml`; Kodi 21 ships it, and `routes/menu.py:index()` prompts the user to install it if missing. v0.1.0-style MPD (per-Representation `<SegmentBase indexRange>` + `<Initialization range>`) + B 站 CDN BaseURL directly + `stream_headers=Referer=https://www.bilibili.com` (set in `routes/video.py:video` via `BILI_REFERER` from `api.http`).
- Manually test by copying the folder into Kodi's `addons/` (or installing the zip), enabling it, and exercising routes in the UI. There is no headless test harness; Kodi is the runtime.
- `.vscode/settings.json` only sets `python-envs.defaultEnvManager` — there is no launch config or task. Do not expect to "run" the plugin from the IDE; it must be invoked through Kodi.
- Kodi spawns a **fresh Python process for every navigation** (see [addon.py](addon.py) header comment), so in-memory module state does not survive between requests. Any persistence must go through `plugin.get_storage(...)`.

## Architecture

### Process model & entry points (3 Kodi extension points)

Kodi invokes these extension points declared in [addon.xml](addon.xml):

1. **`xbmc.python.pluginsource` → [addon.py](addon.py)**: one-shot directory lister. Imports [routes](routes/__init__.py) (which imports all route submodules — each `@plugin.route(...)` handler registers at import time), then calls `plugin.run()` once and exits.
2. **`xbmc.service` → [service.py](service.py)**: long-running service. Binds a static MPD HTTP server on `0.0.0.0:54321` (overridable via `server_port` setting) and loops on `xbmc.Monitor().waitForAbort()`. Stops live-danmaku WebSocket threads on shutdown.
3. The `xbmc.Monitor` instance also owns the **live danmaku lifecycle** at shutdown (calls `stop_all_live_danmaku()` from [live/danmaku.py](live/danmaku.py) before tearing down the HTTP server).

### Module map

Code is organized into four packages; the remaining modules live at the addon root.

**Packages (按功能分目录)**
- [api/](api/) — Bilibili HTTP layer split by concern:
  - [api/wbi.py](api/wbi.py) — WBI signing: `encWbi` (immutable, never mutates input dict), cached `getWbiKeys` via `@plugin.cached(TTL=30)`, with fallback keys on API failure.
  - [api/cookie.py](api/cookie.py) — Cookie management: `get_cookie` with 60s TTL cache, `_ensure_buvid3` to avoid CDN 403 by auto-generating `buvid3` (and explicitly `account.sync()`-ing the storage), regex-cached `get_cookie_value`, `get_uid`.
  - [api/http.py](api/http.py) — HTTP request helpers: two public functions — `fetch_url(url, *, raw=False)` and `get_api_data(path, data=None, *, raw=False)`. `raw=True` skips the 1-minute in-memory cache (used for CDN playurl responses that must always be fresh). Both delegate to internal `_get_url` / `_get_api` with `@plugin.cached(TTL=1)` wrapping. `build_headers(cookie=None)` and `post_data(url, data)` cover non-`api.bilibili.com` hosts. `BILI_REFERER` is the centralized `'Referer=https://www.bilibili.com'` constant. Default `User-Agent` is `'wiliwili'` (custom UA is required to unlock Atmos/Hi-Res audio — modern Chrome UA gets the web path which is downgraded to AAC only). Referer `https://www.bilibili.com`. 10s `requests` timeout.
  - [api/__init__.py](api/__init__.py) — re-exports the public API so `from api import get_api_data` works.
- [playback/](playback/) — VOD playback helpers:
  - [playback/mpd.py](playback/mpd.py) — `generate_mpd(dash)`: 生成标准 MPEG-DASH MPD。视频 AS: 每个 Representation 包含 `<SegmentBase indexRange>` + `<Initialization range>` (v0.1.0 形态, 让 inputstream.adaptive 拼 Range)。音频: 多 `<AdaptationSet>` (AAC + Dolby + Hi-Res FLAC, v0.3.0 形态)。BaseURL 写 B 站 CDN 直链, 不过代理。
  - [playback/item.py](playback/item.py) — `get_video_item` (B 站 item dict → Kodi listitem dict, multi-P detection via `videos`/`page`/`count`), `parse_plot` (builds the on-hover description).
  - [playback/resolution.py](playback/resolution.py) — `choose_resolution` (点播视频 Representation 过滤).
  - [playback/audio.py](playback/audio.py) — `AudioTrack` dataclass, `collect_audio_tracks` (合并 `dash.audio` / `dash.dolby.audio` / `dash.flac.audio` 三处, 通过 `codecs` 字符串 `infer_kind` 判 `aac` / `dolby` / `flac`), `select_audio_tracks` (按 5 档偏好 + 降级链, 同 id 内 FLAC/Atmos 总是包含到 MPD 让 player 自选). Constants: `PREF_ATMOS=30250` (B 站 E-AC-3 JOC), `PREF_HIRES=30251` (FLAC), `PREF_HIGH=30280`, `PREF_MED=30232`, `PREF_LOW=30216`.
  - [playback/ass.py](playback/ass.py) — `generate_ass(cid)`: downloads `https://comment.bilibili.com/{cid}.xml`, runs `danmaku2ass.Danmaku2ASS`.
  - [playback/history.py](playback/history.py) — `report_history(bvid, cid)`: POSTs heartbeat with `bili_jct` csrf.
  - [playback/live.py](playback/live.py) — `choose_live_resolution(streams)`: 直播流按 codec (hevc 优先) × format (modern fmp4/hls 优先) × qn (高优先) 选最佳. AV1 桶预留但 B 站目前没推.
  - [playback/__init__.py](playback/__init__.py) — re-exports.
- [live/](live/) — 直播弹幕：
  - [live/danmaku.py](live/danmaku.py) — `LiveDanmakuClient`: WBI-signed `getDanmuInfo` → WebSocket handshake (manual frame codec, no `websockets` dep) → binary Bili packet parsing (zlib-decompress for `protover=2`) → `danmaku2ass.ProcessComments` → atomic `.ass` rewrite (`.tmp` + `os.replace`) every ~1.5s. Module-level `_instances` dict keyed by `room_id`; `start_live_danmaku` / `stop_live_danmaku` / `stop_all_live_danmaku` are the public API.
- [routes/](routes/) — Kodi URL handlers split by feature (9 files). All submodules are auto-registered by [routes/__init__.py](routes/__init__.py) which does `from . import auth, menu, ...`. Shared helpers in [routes/_helpers.py](routes/_helpers.py): `append_next_page`, `up_context_menu`, `live_status_label`, `format_up_plot`.
  - [routes/auth.py](routes/auth.py) — login / logout / QR / Cookie login / load from file / cache cleanup (4 routes; `check_login` is the only "diagnose" route after `debug_cookie` was removed)
  - [routes/menu.py](routes/menu.py) — `index`, `move_up/down`, `default_menus`, `open_settings` (5 routes)
  - [routes/popular.py](routes/popular.py) — popular / ranking / weekly / related (6 routes)
  - [routes/user.py](routes/user.py) — `space_videos`, `followings`, `followers`, `user`, `user_live_room` (5 routes)
  - [routes/collections.py](routes/collections.py) — seasons/series, favorites, watchlater, history (9 routes)
  - [routes/search.py](routes/search.py) — search / history / clear / delete (5 routes)
  - [routes/home.py](routes/home.py) — `home`, `dynamic_list`, `dynamic`, `web_dynamic` (4 routes)
  - [routes/video.py](routes/video.py) — `bangumi`, `videopages`, `video` (3 routes; MPD 生成 → `inputstream.adaptive` + 4 prop; segments fetch B 站 CDN with `stream_headers=BILI_REFERER`)
  - [routes/live.py](routes/live.py) — `live_areas`, `live_area`, `following_live`, `live` (4 routes; `_LIVE_AREAS` static data inline. Note: routes are snake_case but the `function.<name>` setting id in `settings.xml` is `function.followingLive` to preserve the user's persisted toggle.)

**Top-level**
- [addon.py](addon.py) — entry point (`from core import plugin; import routes; plugin.run()`).
- [core.py](core.py) — Constructs the singleton `plugin = Plugin()` after importing from `plugin_compat`. The `xbmc.translatePath = xbmcvfs.translatePath` polyfill lives in [plugin_compat.py](plugin_compat.py) (not in `core.py`).
- [plugin_compat.py](plugin_compat.py) — `xbmcswift2`-compatible `Plugin` class built on Kodi 21 native APIs. Implements `route`, `url_for`, `get_storage` (JSON-backed `_Storage` with TTL), `cached` (per-call TTL decorator, TTL is in **minutes**), `open_settings`, `set_resolved_url` (with background thread that waits for `xbmc.Player().isPlaying()` before calling `setSubtitles` — required in Kodi 21), and `run` (URL → view-function dispatch). All other modules import `from core import plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon`.
- [http_server.py](http_server.py) — `http.server.HTTPServer` bound to `0.0.0.0:54321` (overridable via `server_port` setting). ONE endpoint: serves `.mpd` files from `special://temp/plugin.video.bili/`. Path-traversal guard (`_safe_file_path`). Shared `_send_mpd` helper for GET (streams body) and HEAD (headers only).
- [utils.py](utils.py) — Stateless formatting/text helpers (no `plugin` dep beyond re-exporting `xbmc*`). Includes `tag()` for `[COLOR]…[/COLOR]` markup, `convert_number` (万/亿), `format_stat` (stat dict → labeled string, dedup by Chinese label), `make_dirs` (tries `xbmcvfs.mkdirs` then `os.makedirs`, returns `None`), `get_temp_path` (`special://temp/plugin.video.bili/`, also ensures dir), `remove_dir` (xbmcvfs → shutil fallback; replaces the v0.3.0 `safe_remove_dir` which returned a bool). `localize` / `getSetting` cache the `xbmcaddon.Addon()` instance at module level.
- [danmaku2ass.py](danmaku2ass.py) — Vendored GPLv3 from [m13253/danmaku2ass](https://github.com/m13253/danmaku2ass). Do not edit unless re-syncing upstream.
- [resources/settings.xml](resources/settings.xml) — Kodi settings schema. The `function.<name>` toggles gate the corresponding category in [routes/menu.py](routes/menu.py) `index()`. `network_request_cache`, `enable_dash`, `enable_danmaku`, `enable_live_danmaku`, `video_resolution`/`video_encoding`, `live_resolution`/`live_video_encoding`, `audio_quality` (5 档 Atmos/Hi-Res/AAC), `font_size`/`opacity`/`danmaku_stay_time`/`display_area`, `report_history`, `server_port` are all consumed via `getSetting(...)` in various modules.
- [resources/language/](resources/language/) — `strings.po` files for `en_gb` and `zh_cn`. String IDs are referenced in `settings.xml` (`label="30001"` etc.) and consumed via `xbmcaddon.Addon().getLocalizedString(id)` in `utils.localize()`.

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

### Request flow (Durl legacy — 回退路径)

1. B 站返回 `data['durl']` 时，直接用 pipe 语法 `{mp4_url}|Referer=...&User-Agent=...&Cookie=...`
2. Kodi 内置 ffmpeg 自动打开 HTTP 流并解析 MP4

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

### URL routing

`plugin_compat.UrlRule` converts `<param>` to named regex groups. **The last param in a rule is greedy** (matches `[^/]+` → `.+`) so paths like video titles or search keywords containing `/` work. Use `plugin.url_for(endpoint, **kwargs)` to build URLs; unknown kwargs become query string.

## Conventions & gotchas

- **All comments and identifiers are Chinese** (Simplified). Match the existing style for new code; English is fine for docstrings if you prefer, but the surrounding code is Chinese.
- **All HTTP requests to `api.bilibili.com` MUST use `api.get_api_data(path, data=…, *, raw=False)`** — they inject the standard `User-Agent`, `Referer`, and `Cookie` headers and apply the 10s timeout. Pass `raw=True` for CDN playurl responses that must always be fresh. `api.fetch_url(url, *, raw=False)` is the equivalent for arbitrary URLs (used by live streams). Only `live/danmaku.py:LiveDanmakuClient._get_token_wbi` and the QR-code endpoints hand-roll requests (intentional, different host).
- **Cache discipline**: anything user-account-related or with a short TTL uses `@plugin.cached(TTL=…)` (TTL is in minutes). CDN playurl responses in [routes/video.py:video](routes/video.py) deliberately bypass cache via `get_api_data(..., raw=True)`. When changing login state, call `api.clear_cookie_cache()` AND `plugin.clear_function_cache()` together — both are needed (see [routes/auth.py:cookie_login](routes/auth.py) / `qrcode_login` / `logout`).
- **Cross-process state**: Kodi's per-navigation process model means `plugin.clear_function_cache()` is a no-op in `addon.py` (intentional, see comment). It is meaningful only inside the long-lived `service.py` process.
- **Path handling**: always go through `xbmc.translatePath(...)` (polyfilled to `xbmcvfs.translatePath`) — never hardcode filesystem paths. Special roots used: `special://profile/addon_data/<addon_id>/.storage/` (per-addon JSON), `special://temp/plugin.video.bili/` (per-session MPD/ASS/QR PNG).
- **Live stream playback**: must set both `IsPlayable` and `is_live`; `is_live` triggers `IsLiveStream`/`IsLive` props and a `VideoInfoTag.setMediaType('video')` call. Do **not** set `inputstreamaddon` (deprecated in Kodi 21).
- **Background threads**: `live.danmaku.LiveDanmakuClient._writer/_hb/_recv_loop` and `plugin_compat._wait_and_set_subtitles` are daemon threads. The danmaku client's `stop()` calls `join(2.0)` on every thread to ensure clean shutdown; `addon.py` registers an `atexit` hook that calls `stop_all_live_danmaku()` as a backstop. Reads of `self.danmaku_list` from any thread must go through the `_snapshot_danmaku()` method (acquires the lock and returns a copy in one step).
- **WebSocket protocol**: `live/danmaku.py:_ws_send` masks client→server frames (required by RFC 6455). Server→client frames here are unmasked (Bili servers don't mask).
- **Bili binary packet header** (parsed in `live/danmaku.py:_parse_binary`): 16-byte header `[total_len(4), header_len(2), protover(2), opcode(4), seq(4)]`. `protover==2` payloads are zlib-compressed and must be decompressed recursively.
- **API path → URL**: `api/http.py:build_api_url` always prepends `https://api.bilibili.com`. WBI endpoints (`/x/space/wbi/arc/search`, `/x/web-interface/wbi/search/*`) require `encWbi(params, *getWbiKeys())` — call `getWbiKeys()` once per request (it has its own 30-min cache).
- **Settings gating**: menu categories are gated by `function.<name>` booleans ([routes/menu.py:58](routes/menu.py#L58)). New top-level menu items need a row in [resources/settings.xml](resources/settings.xml) `function_setting` category AND an entry in [routes/menu.py:_categories()](routes/menu.py#L8).
- **Lists of static data**: `_LIVE_AREAS` (~600 lines) is in [routes/live.py](routes/live.py); `_DYNAMIC_REGIONS` (~40 lines) is in [routes/home.py](routes/home.py). Update by hand; there's no script.
- **Logging**: use `xbmc.log('[<module>] ...', xbmc.LOGINFO|LOGWARNING|LOGERROR)` — keep a short prefix for grep-ability. This plugin uses prefixes like `[plugin.video.bili]`, `[api.wbi]`, `[live.danmaku]`, `[playback.ass]`.
- **`xbmcaddon.Addon()` calls**: in long-running contexts (service), prefer caching the addon handle; `xbmcaddon.Addon()` per-call is fine in short-lived addon.py invocations.
- **No CI, no tests, no type checker**: keep patches small and self-contained. Run a manual smoke test in Kodi after touching routing, danmaku, or playback paths.
- **New menu items**: a new top-level entry needs THREE places: (1) [resources/settings.xml](resources/settings.xml) `function_setting` `<setting id="function.X">` row, (2) entry in [routes/menu.py:_categories()](routes/menu.py#L8), (3) the `@plugin.route(...)` handler in the appropriate [routes/](routes/) submodule. Settings labels are pulled from `strings.po` by `localize(id)`.
- **Cross-package imports**: when adding a new module under `api/`, `playback/`, `live/`, or `routes/`, sibling imports use relative form (`from .module import x`); top-level imports (e.g. `from utils import ...`, `from core import ...`) are absolute. The addon root is on `sys.path` so absolute imports work.

## Common tasks

- **Add a new top-level menu**: add row to [resources/settings.xml](resources/settings.xml) `function_setting`, add entry in `_categories()` in [routes/menu.py](routes/menu.py), add `@plugin.route` handler in the appropriate `routes/` submodule. **Important**: the `name` field in `_categories()` must match the `function.<name>` setting id in `settings.xml` character-for-character (this is what `menu.py:index()` looks up via `getSetting('function.' + category['name'])`). The route function name and URL pattern can use snake_case independently. Localize the label.
- **Add a new Bilibili API call**: import from `api` (`get_api_data`, `encWbi`, `getWbiKeys`, `get_cookie_value`, `get_uid`); use WBI signing for `/x/space/wbi/...` and `/x/web-interface/wbi/...`; cache non-CDN responses with `@plugin.cached(TTL=…)`.
- **Add a new live stream source**: follow [routes/live.py:live](routes/live.py) — set `is_playable=True`, `is_live=True`. v0.4.0 emits ONE `set_resolved_url` with `inputstream.adaptive manifest_type='hls'`, `manifest_update_params='full'`, and `BILI_REFERER` for both manifest/stream headers. The chosen URL is `master_url` (m3u8 from http_hls) if present, else `urls[0]` (raw m4s) — both go through Kodi's ffmpeg pipe. `choose_live_resolution` picks by codec (hevc preferred) × format (modern fmp4/hls preferred) × qn.
- **Adjust danmaku behavior**: settings in [resources/settings.xml](resources/settings.xml) group 2; consumed in [live/danmaku.py:start_live_danmaku](live/danmaku.py) via `getSetting`. The `_writer` loop in [live/danmaku.py](live/danmaku.py) controls timing — `time.sleep(1.5)` is the refresh interval; do not lower it without testing perf.
- **Bump version**: edit `version` in [addon.xml](addon.xml) and the addon `id` (which is the storage dir key) — don't change the id without a data migration plan.
- **Ship a release**: zip the repo root as `plugin.video.bili-<version>.zip` (Kodi installs by structure, not by name); confirm `addon.xml` is at the root of the zip.

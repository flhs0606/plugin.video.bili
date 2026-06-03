# Restore `inputstream.adaptive` as the only playback path

**Date**: 2026-06-03
**Status**: Design proposal (pre-implementation)
**Target version**: 0.4.0

## 1. Background & motivation

v0.3.0 removed all `inputstream.*` addons in favor of Kodi's built-in ffmpeg
dashdemuxer. The local HTTP proxy (`/proxy/{id}.mp4`) is the only
authentication-header injection point, and Kodi's ffmpeg is the only
MPEG-DASH consumer.

User feedback: **"离开 inputstream.adaptive 一直播放失败"** — under v0.3.0
the playback path is broken in practice. Symptoms reported span every video
class (AVC / HEVC / DV / Hi-Res FLAC) and both VOD and live. The user wants
`inputstream.adaptive` re-introduced as the **only** playback path, replacing
the ffmpeg-direct route. `inputstream.ffmpegdirect` is to be removed
permanently (no fallback path).

## 2. Goals

1. Restore `inputstream.adaptive` as the **sole** playback path for VOD and
   live. Both modes go through `ListItem` `inputstream.*` properties.
2. Keep the local HTTP proxy (`http_server.py` + `playback/proxy.py`) as the
   **authentication-header injection layer** — inputstream.adaptive fetches
   segments through `/proxy/{id}.mp4` instead of B 站 CDN directly. This
   isolates Cookie / Referer / User-Agent management in one place.
3. Delete `playback/m3u8.py` — no longer needed (inputstream.adaptive consumes
   MPD directly).
4. Delete all `ffmpeg pipe` paths for DASH playback (MPD). One exception: see
   §4.4 (`durl` legacy path), which is a Kodi-native MP4 demuxer route that
   happens to use the same `url|headers` syntax; it does **not** depend on
   ffmpegdirect and is out of scope for removal.
5. Declare `inputstream.adaptive` as a hard dependency in `addon.xml`.

## 3. Non-goals

- No automatic version detection / downgrade to `ffmpegdirect` if
  `inputstream.adaptive` is missing. The addon just refuses to start.
- No HTTPS on the local proxy (127.0.0.1 HTTP is acceptable).
- No UI toggles for the playback engine — `enable_dash` only decides MPD vs
  `durl` (DASH vs legacy MP4).

## 4. Architecture

### 4.1 Point of contact

`plugin_compat._dict_to_li` already forwards `properties` to
`ListItem.setProperty`. We need every key starting with `inputstream.` to
land on the ListItem verbatim. **Current code already does this** (see
`plugin_compat.py:179`). The change is at the *caller* — `routes/video.py`
and `routes/live.py` must populate these properties.

### 4.2 VOD request flow

```
addon.py (one-shot)
  └─ routes/video.py:video(id, cid, ...)
     ├─ /x/player/wbi/playurl  →  data['dash']
     ├─ playback/mpd.generate_mpd()   →  MPD XML
     │     BaseURL = http://127.0.0.1:PORT/proxy/{seg_id}.mp4
     │     (existing _build_video_as / _build_audio_as / _video_color_props
     │      are kept; only ffmpeg-CLI-only hacks and `use_proxy=False`
     │      branch are removed)
     ├─ write to special://temp/plugin.video.bili/v.{cid}.mpd
     ├─ playback.proxy.unregister_all()
     └─ plugin.set_resolved_url({
            path:   "http://127.0.0.1:{port}/v.{cid}.mpd",
            properties: {
                'inputstream': 'inputstream.adaptive',
                'inputstream.adaptive.manifest_type': 'mpd',
                'inputstream.adaptive.manifest_headers':
                    'User-Agent=...&Referer=...&Cookie=...',  # see §5.2
                'inputstream.adaptive.original_mediatype': 'video',
                'inputstream.adaptive.stream_selection_type': 'manual-osd',
            },
            is_playable: True,
        }, subtitles=ass)

service.py (long-lived)
  └─ http_server.py
     ├─ GET /v.{cid}.mpd        → serve the on-disk MPD file
     └─ GET /proxy/{id}.mp4     → lookup(seg_id) → inject headers →
                                    stream B 站 CDN
```

inputstream.adaptive's responsibilities: MPD parse, segment Range
construction, decoder selection, HDR/DV signaling. Our plugin's
responsibilities: fetch B 站 playurl, build the MPD XML, write the MPD file,
populate `inputstream.*` properties on the ListItem.

### 4.3 Live request flow

```
addon.py (one-shot)
  └─ routes/live.py:live(id)
     ├─ getRoomPlayInfo (with multi-QN fallback) → playurl_info
     ├─ playback.live.choose_live_resolution()
     ├─ pick first fmp4 URL (format=1, codec=avc preferred)
     └─ plugin.set_resolved_url({
            path:   "<fmp4_cdn_url>",
            properties: {
                'inputstream': 'inputstream.adaptive',
                'inputstream.adaptive.manifest_type': 'hls',
                'inputstream.adaptive.manifest_update_params': 'full',
                'inputstream.adaptive.manifest_headers':
                    'User-Agent=...&Referer=...&Cookie=...',
            },
            is_playable: True,
            is_live:    True,
        }, subtitles=live_ass)
```

Notes:
- `inputstream.adaptive` consumes the fmp4 manifest as HLS and refreshes it
  via `manifest_update_params=full` (live mode). FLV is no longer a target
  codec — if B 站 only returns FLV for a room, we discard and request
  fmp4 explicitly (`format=1`).
- Live danmaku (live/danmaku.py) is unchanged — still produces an external
  `.ass` and passes it via `subtitles=live_ass`.

### 4.4 `durl` legacy path

If the B 站 API returns `data['durl']` (no `data['dash']`) — the legacy
non-segmented MP4 stream, used for some older 1080P titles — we keep a
**direct pipe** route:

```python
plugin.set_resolved_url({
    'path': f'{durl_url}|{hdr}',
    'is_playable': True,
}, subtitles=ass)
```

This is **not** inputstream.adaptive (adaptive does not consume a single
durl URL) and **not** inputstream.ffmpegdirect (we never re-introduce that
addon). Kodi 21's built-in ffmpeg MP4 demuxer consumes the pipe directly.
The `durl` URL is rare in 2026 — modern B 站 playurl almost always
returns `dash` — but a single non-DASH fallback remains so the addon does
not blank-screen on legacy titles.

## 5. Module changes

### 5.1 `addon.xml`

Add hard dependency:

```xml
<import addon="inputstream.adaptive" version="21.5.0"/>
```

`news` block:

```
v0.4.0
- Restore inputstream.adaptive as the only playback path for VOD and live.
  Hard dependency on inputstream.adaptive ≥ 21.5.0.
- Reintroduce manifest_headers on ListItem for B 站 CDN authentication
  fallback (still goes through local proxy by default).
- Remove playback/m3u8.py (no longer needed) and all ffmpeg pipe paths.
```

### 5.2 `playback/mpd.py` — rewrite

Drop the `use_proxy=False` ffmpeg-CLI branch (no caller uses it). Default
`use_proxy=True`. Keep the proxy/BaseURL construction. Strip the dashdemux
hacks (every comment about `ffmpeg dashdec`). Add a new public function
`build_manifest_headers(cookie, ua, referer) -> str` that returns the
`User-Agent=...&Referer=...&Cookie=...` string for the
`inputstream.adaptive.manifest_headers` ListItem property. The string is
`&`-delimited key=value pairs with each value **URL-encoded** via
`urllib.parse.quote(value, safe='')` — this is required because the User-
Agent contains spaces, colons, slashes, and parentheses that would break
naive `&` splitting. `routes/video.py` and `routes/live.py` both call this
helper, keeping the proxy-header format and the manifest_headers format in
sync (mitigates the risk in §9).

### 5.3 `playback/m3u8.py` — delete

`routes/video.py` will no longer import from this module. Git history
preserves the file if a rollback is ever needed.

### 5.4 `routes/video.py`

- Remove `from playback.m3u8 import write_m3u8_files` and the entire
  m3u8-build branch in `video()`.
- Replace with: write MPD via `generate_mpd(dash, cookie, ua, port,
  use_proxy=True)` → write file → `set_resolved_url` with inputstream props.
- `audio_only` branch: keep the `dash` path (fmp4 single-track pipe still
  works; this is non-adaptive audio-only, not a ffmpegdirect dependency).

### 5.5 `routes/live.py`

- Remove `_ensure_hls_ext` (no ffmpeg HLS demuxer involved anymore).
- Remove the FLV branch and the fmp4/ts pipe branch.
- Single output: inputstream.adaptive `manifest_type='hls'` for fmp4.
- If `_fetch(...)` returns streams that contain **only** FLV format entries
  (i.e. `choose_live_resolution` would return `format_name='flv'`), do a
  re-fetch in `routes/live.py` with `format=1` (fmp4 only) before giving
  up. If the re-fetch still returns no fmp4, log + abort with a Kodi
  notification — do not fall back to FLV pipe.

### 5.6 `playback/proxy.py` — unchanged

Still required: inputstream.adaptive's segment requests go through
`/proxy/{id}.mp4`. `unregister_all()` is invoked at the start of every
`video()` call (before the new MPD is written) so stale seg_ids from a
previous play don't leak in.

### 5.7 `http_server.py` — minor

- Verify `Accept-Ranges: bytes` is present in every `/proxy/{id}.mp4`
  response branch (the existing code emits it unconditionally, but confirm
  in the final review that it is not stripped for HEAD/206 paths).
  inputstream.adaptive uses Range heavily; the upstream CDN already sends
  it but we make sure it survives our header pass-through.
- Keep the path-traversal guard (`_safe_file_path`).

### 5.8 `plugin_compat.py` — no change

`setProperty('inputstream.adaptive.*', value)` already works through the
existing `properties` dict iteration in `_dict_to_li` (lines 175–180).

### 5.9 `resources/settings.xml` — minor

- `enable_dash` keeps its current meaning: when true the plugin calls
  playurl with `fnval=4048` and consumes `data['dash']` (MPD via
  inputstream.adaptive); when false it calls with `fnval=1` and the API
  may return `data['durl']` (legacy MP4 pipe, see §4.4) or low-quality
  DASH — either way we feed it through the matching path.
- Add a help string on the `enable_dash` setting pointing to the
  `inputstream.adaptive` install location (Kodi 21 ships it in the
  official repo).

### 5.10 `resources/language/.../strings.po`

Add localized string for the help text and a notice when
`inputstream.adaptive` is detected missing at startup.

## 6. Error handling

| Failure | Detection | Behavior |
|---|---|---|
| `inputstream.adaptive` not installed | `xbmcaddon.Addon('inputstream.adaptive')` raises | `xbmcgui.Dialog().ok(...)` at first `set_resolved_url`, then `return` |
| B 站 playurl API 403 / 404 | `res.get('code') != 0` | log + return (existing behavior) |
| HTTP proxy port 54321 busy | `socket.error` on `HTTPServer(...)` | log + Kodi-level error dialog; user can change `server_port` |
| `durl` empty | `data['durl'][0]['url']` falsy | return without `set_resolved_url` |
| Live fmp4 unavailable | all `choose_live_resolution` candidates are `flv` | log + `Dialog().notification`; do not fall back to FLV pipe |

## 7. Testing

The plugin has no automated test suite. Manual smoke tests in Kodi 21:

1. **VOD AVC** — play a normal AVC video (e.g. 1080P). Expect: plays,
   bitrate adapts on seek.
2. **VOD HEVC** — pick a HEVC-encoded B 站 video. Expect: plays.
3. **VOD DV** — pick a Dolby Vision title. Expect: plays, system reports
   `Dolby Vision` in the OSD / display info.
4. **VOD HDR10 / HLG** — expect: plays, system reports the matching HDR
   transfer.
5. **VOD Hi-Res FLAC** — expect: plays, audio track selectable, FLAC
   bit-depth shown in codec info.
6. **Live** — open a room. Expect: plays, danmaku overlay visible, no
   rebuffer on tabbing out / back.
7. **Seek** — drag the seek bar mid-video. Expect: <1s rebuffer, no error
   dialog, no `/proxy/... 404` log lines.
8. **Cold restart** — quit Kodi, reopen, play a video. Expect: no stale
   `seg_id` files in temp dir (proxy is in-memory; the file is on disk only
   inside service process lifetime).

## 8. Scope guard (YAGNI)

Explicitly **out of scope** for this design:

- Automatic version detection of `inputstream.adaptive` — we just declare a
  hard minimum.
- A `use_ffmpegdirect_fallback` setting — user already chose to drop the
  fallback. The flag would invite drift back into the v0.2 mess.
- HTTPS on the local proxy — 127.0.0.1 HTTP is fine; Kodi inputstream does
  not require TLS for loopback.
- Live HLS m3u8 wrapper generation — inputstream.adaptive can consume the
  raw fmp4 manifest as HLS directly.
- Dynamic B 站 CDN domain pools — the API returns the current hosts; no
  manual host list.

## 9. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| inputstream.adaptive fmp4 live manifest handling differs across versions | medium | live broken | Pin minimum version (21.5.0) in addon.xml |
| MPD manifest_headers string syntax differs from proxy-header format | low | seg fetch 403 | Use a single helper `build_manifest_headers()` so both call sites stay in sync |
| `unregister_all()` racing with adaptive's in-flight Range requests | low | seek 404 | Keep seg_id file until service shutdown (already the behavior in `lookup`) |
| `durl` direct pipe relies on Kodi 21's MP4 demuxer (no inputstream) | low | non-DASH fallback broken | Document the limitation; durl is rare for B 站 in 2026 |

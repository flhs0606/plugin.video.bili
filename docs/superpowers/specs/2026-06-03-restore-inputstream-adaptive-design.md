# Restore `inputstream.adaptive` (VOD) — drop the local HTTP proxy

**Date**: 2026-06-03
**Status**: Design proposal (pre-implementation)
**Target version**: 0.4.0
**Supersedes**: v0.3.0 (the failed "no-inputstream" build)

## 1. Background & motivation

The v0.3.0 release removed all `inputstream.*` addons in favor of Kodi's
built-in ffmpeg dashdemuxer, with a local HTTP proxy (`/proxy/{id}.mp4`)
as the only authentication-header injection layer. This design does not
work in practice — every video class (AVC / HEVC / DV / Hi-Res FLAC) and
both VOD and live fail in some way.

A reference of the **known-good v0.1.0 build** is preserved at
`E:\Project\plugin.video.bili-origin\`. That build uses
`inputstream.adaptive` directly against B 站 CDN URLs (no segment
proxy), with two small header properties (`manifest_headers` and
`stream_headers`) for Referer, and a `<SegmentBase indexRange=…>` block
in the MPD so the adaptive demuxer can splice init/media ranges
precisely.

This design **restores the v0.1.0 VOD architecture verbatim** and keeps
the v0.3.0-style `manifest_type='hls'` live path (with `ffmpegdirect`
*removed*). The segment-proxy half of the local HTTP server is deleted;
the static-file MPD half is kept (inputstream.adaptive reads the MPD
through it; segments go straight to B 站 CDN with `stream_headers`).
The `xbmc.service` extension point stays but the BilibiliMonitor /
restart_httpd machinery is removed.

## 2. Goals

1. VOD playback goes through `inputstream.adaptive` with
   `manifest_type='mpd'`, `manifest_headers=Referer=…`, and
   `stream_headers=Referer=…` (referer-only, matching v0.1.0).
2. MPD is generated with **per-Representation `<SegmentBase indexRange>`
   + `<Initialization range>`** (v0.1.0 structure) so inputstream.adaptive
   can do precise byte-range splices. BaseURL is the B 站 CDN URL
   directly; no local proxy is involved.
3. Live playback goes through `inputstream.adaptive` with
   `manifest_type='hls'`, `manifest_update_params='full'`, and the same
   Referer-only header pair. We force `format=1` (fmp4) at the playurl
   API — FLV is no longer consumed.
4. The local HTTP proxy and its proxy registry are **deleted**. The
   `xbmc.service` extension stays (it now hosts a tiny static-file
   MPD server, see §5.5/§5.6); the proxy half of that server is gone.
5. `inputstream.adaptive` is declared as an **optional** dependency
   (`optional="true"`); the menu `index()` detects the addon and prompts
   the user to install it (v0.1.0 behaviour). v0.3.0's "hard dependency"
   decision is reverted.

## 3. Non-goals

- No fallback to `inputstream.ffmpegdirect` (v0.1.0 had it for live
  fmp4/ts; v0.4.0 routes live through `inputstream.adaptive` instead and
  ffmpegdirect is gone).
- No ffmpeg pipe paths (`url|headers`, `reconnect=…`) for live streams.
- No local proxy / port binding / 54321 server.
- No HTTPS on a proxy (we have no proxy).
- No UI toggles for the playback engine.

## 4. Architecture

### 4.1 VOD request flow

```
addon.py (one-shot)
  └─ routes/video.py:video(id, cid, ...)
     ├─ /x/player/wbi/playurl  →  data['dash']
     ├─ playback/mpd.generate_mpd(dash)  →  MPD XML
     │     BaseURL = <B站 CDN baseUrl>   (direct, no proxy)
     │     each Representation:
     │       <SegmentBase indexRange="…">
     │         <Initialization range="…"/>
     │       </SegmentBase>
     │     audio AdaptationSet has lang="und"
     ├─ write to special://temp/plugin.video.bili/{cid}.mpd
     └─ plugin.set_resolved_url({
            path: 'http://127.0.0.1:54321/{cid}.mpd',  # served by a static-file-only http server
            properties: {
                'inputstream': 'inputstream.adaptive',
                'inputstream.adaptive.manifest_type': 'mpd',
                'inputstream.adaptive.manifest_headers':
                    'Referer=https://www.bilibili.com',
                'inputstream.adaptive.stream_headers':
                    'Referer=https://www.bilibili.com',
            },
            is_playable: True,
        }, subtitles=ass)
```

> The local HTTP server is **static-file only** in v0.4.0 (see §5.5). It
> serves `{cid}.mpd` from `special://temp/plugin.video.bili/` and nothing
> else. No `/proxy/...` endpoint exists. BaseURL inside the MPD is the
> B 站 CDN URL directly — inputstream.adaptive fetches segments with its
> own `stream_headers` (Referer), without any local hop.

### 4.2 Live request flow

```
addon.py (one-shot)
  └─ routes/live.py:live(id)
     ├─ getRoomPlayInfo → playurl_info
     │   (format filter forces fmp4: format=1; multi-QN fallback)
     ├─ playback.live.choose_live_resolution()  → best fmp4 stream
     └─ plugin.set_resolved_url({
            path:   <fmp4 cdn url>,
            properties: {
                'inputstream': 'inputstream.adaptive',
                'inputstream.adaptive.manifest_type': 'hls',
                'inputstream.adaptive.manifest_update_params': 'full',
                'inputstream.adaptive.manifest_headers':
                    'Referer=https://www.bilibili.com',
                'inputstream.adaptive.stream_headers':
                    'Referer=https://www.bilibili.com',
            },
            is_playable: True,
            is_live:    True,
        }, subtitles=live_ass)
```

FLV is **not** consumed. If the playurl API returns only FLV entries
after the multi-QN fallback, the route re-fetches with
`format=0,1,2` (all formats); if even that has no fmp4, log + abort
with a Kodi notification.

### 4.3 `durl` legacy path

Unchanged from v0.3.0. `data['durl']` triggers
`path = durl_url|Referer=https://www.bilibili.com`. Not inputstream.
Not ffmpegdirect. Kodi's built-in ffmpeg MP4 demuxer handles it.

## 5. Module changes

### 5.1 `addon.xml`

```xml
<requires>
  <import addon="xbmc.python" version="3.0.0"/>
  <import addon="script.module.requests" version="2.12.4"/>
  <import addon="script.module.qrcode" version="5.3"/>
  <import addon="inputstream.adaptive" optional="true"/>
</requires>
<extension point="xbmc.python.pluginsource" library="addon.py">
  <provides>video</provides>
</extension>
<extension point="xbmc.service" library="service.py"/>
```

`xbmc.service` stays — it still hosts the static-file MPD server
(§5.5/§5.6). The proxy half of that server is gone but the long-lived
process is required to bind 54321.

`news` block:

```
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
```

### 5.2 `playback/mpd.py` — restore v0.1.0 structure

This is the v0.1.0 implementation, recovered verbatim from
`E:\Project\plugin.video.bili-origin\video_utils.py:253-307`. It is the
exact code that was verified to play; the v0.3.0 rewrite that dropped
`<SegmentBase>` is reverted. The function signature is restored to
`generate_mpd(dash)` (one argument) — the v0.3.0 cookie/ua/port params
go away because BaseURL no longer points at a proxy.

```python
def generate_mpd(dash):
    videos = choose_resolution(dash['video'])
    audios = sorted(dash['audio'], key=lambda x: x.get('id', 0), reverse=True)

    mpd_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" '
        'type="static" mediaPresentationDuration="PT',
        str(dash['duration']),
        'S" minBufferTime="PT', str(dash['minBufferTime']), 'S">\n',
        '\t<Period>\n',
    ]

    def _build_adaptation_set(items, mime_type, extra_attrs=''):
        lines = ['\t\t<AdaptationSet mimeType="%s" startWithSAP="1" '
                 'segmentAlignment="true"%s>\n' % (mime_type, extra_attrs)]
        for item in items:
            base_url = item['baseUrl'].replace('&', '&amp;')
            attrs = []
            for k in ('bandwidth', 'codecs', 'frameRate', 'height',
                      'width', 'id', 'audioSamplingRate'):
                if k in item:
                    attrs.append('%s="%s"' % (k, item[k]))
            lines.append('\t\t\t<Representation %s>\n' % ' '.join(attrs))
            lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' % base_url)
            for bu in item.get('backup_url', []) or []:
                lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' %
                             bu.replace('&', '&amp;'))
            lines.append('\t\t\t\t<SegmentBase indexRange="%s">\n'
                         % item['SegmentBase']['indexRange'])
            lines.append('\t\t\t\t\t<Initialization range="%s">'
                         '</Initialization>\n'
                         % item['SegmentBase']['Initialization'])
            lines.append('\t\t\t\t</SegmentBase>\n')
            lines.append('\t\t\t</Representation>\n')
        lines.append('\t\t</AdaptationSet>\n')
        return lines

    mpd_lines.extend(_build_adaptation_set(
        videos, 'video/mp4', ' scanType="progressive"'))
    mpd_lines.extend(_build_adaptation_set(
        audios, 'audio/mp4', ' lang="und"'))
    mpd_lines.append('\t</Period>\n</MPD>\n')
    return ''.join(mpd_lines)
```

**Note on v0.3.0 enhancements that we are dropping on purpose**:
- HDR10 / DV / HLG `<EssentialProperty>` and `<SupplementalProperty>`
  blocks (`_video_color_props`) — v0.1.0 did not have these and the user
  reports v0.1.0 plays DV / HDR correctly. The CICP signaling in
  inputstream.adaptive comes from the Representation `codecs` and
  container metadata, not from MPD `<EssentialProperty>`. We can
  re-introduce them in a follow-up spec if needed.
- Multi-adaptation-set audio grouping (AAC / Dolby / FLAC) — v0.1.0 had
  one combined `audios` list, sorted by id. v0.3.0 separated them; we
  revert to the simpler v0.1.0 grouping.

`routes/video.py` is the **only** caller of `generate_mpd()`. The
helper that built the manifest_headers string is no longer needed (the
string is constant `'Referer=https://www.bilibili.com'` and lives in
`routes/video.py`).

### 5.3 `playback/m3u8.py` — delete

Not imported by anyone in v0.4.0.

### 5.4 `playback/proxy.py` — delete

No callers in v0.4.0.

### 5.5 `http_server.py` — replace with a static-file-only MPD server

`http_server.py` is **kept** but gutted: it serves only `.mpd` files
from `special://temp/plugin.video.bili/`. The `/proxy/...` route and the
`BilibiliRequestHandler._proxy_cdn` are deleted. The `playback.proxy`
lookup is no longer needed. The `requests` lazy import goes away. The
path-traversal guard (`_safe_file_path`) is kept.

This is needed because `inputstream.adaptive` in Kodi 21 reads the
manifest URL via Kodi's HTTP client, which has known issues opening
`file://` or `special://` URLs through inputstream's parser. The proven
shape — `http://127.0.0.1:54321/{cid}.mpd` — requires a tiny local
HTTP endpoint. We keep that endpoint **without** keeping the segment
proxy. Risk: `inputstream.adaptive` may, in some Kodi 21 builds, accept
`file://` directly. If a future smoke test confirms that, this server
can be deleted too; the spec notes it as a follow-up but does not
defer it.

The host process is `service.py` (kept, see §5.6).

### 5.6 `service.py` — keep, but simplify

`service.py` stays because the static-file MPD server in §5.5 still
needs a host process. The proxy-related work (BilibiliMonitor,
restart_httpd, shutdown_httpd) is removed. The lifecycle is just:

```python
from xbmc import Monitor
from live.danmaku import stop_all_live_danmaku
from http_server import get_http_server

def run():
    httpd = get_http_server(port=54321)
    monitor = Monitor()
    if httpd:
        while not monitor.abortRequested:
            httpd.handle_request()  # non-blocking serve
            if monitor.waitForAbort(0.5):
                break
        httpd.server_close()
    stop_all_live_danmaku()
```

### 5.7 `monitor.py` — delete

The BilibiliMonitor class exists only to host the proxy (`shutdown_httpd`,
`restart_httpd`, `remove_temp_dir`). With §5.5's server now living in
`service.py` directly, `monitor.py` has no callers.

### 5.8 `routes/video.py`

The VOD path becomes a near-clone of v0.1.0 `routes.py:video()`:
- Use `generate_mpd(dash)` (one-arg, restored signature).
- Write MPD to `special://temp/plugin.video.bili/{cid}.mpd`.
- `path` is the **local MPD file path**:
  `xbmcvfs.translatePath('special://temp/plugin.video.bili/{cid}.mpd')`.
  inputstream.adaptive opens it directly via Kodi's VFS — no HTTP hop,
  no `127.0.0.1` URL.
- `properties` carries the four `inputstream.*` keys from §4.1.
- `audio_only` branch unchanged: `path = audio_url|Referer=…`.
- `durl` branch unchanged: `path = durl_url|Referer=…`.

### 5.9 `routes/live.py`

- Remove `_ensure_hls_ext`.
- Force `format=1` (fmp4) in the initial `_fetch`; on failure fall back
  to `format=0,1,2`; on no fmp4 found, log + notify + return.
- Single output: inputstream.adaptive `manifest_type='hls'` with
  `manifest_update_params='full'` and the two Referer headers.

### 5.10 `routes/menu.py:index()` — add the install-prompt

Reintroduce the v0.1.0 detection block before returning items:

```python
if (getSetting('enable_dash') == 'true'
        and not xbmc.getCondVisibility('System.HasAddon(inputstream.adaptive)')):
    if xbmcgui.Dialog().yesno('安装插件', '使用 dash 功能需要安装 '
                              'inputstream.adaptive 插件，是否安装？'):
        xbmc.executebuiltin('InstallAddon(inputstream.adaptive)')
    elif xbmcgui.Dialog().yesno('取消安装', '不使用 dash 请到设置中关闭'):
        plugin.open_settings()
```

### 5.11 `plugin_compat.py` — no change

`_dict_to_li` already forwards `properties` starting with `inputstream.`
verbatim, and `set_resolved_url` reads the same `properties` dict. The
existing code at `plugin_compat.py:174-180` is sufficient.

### 5.12 `resources/settings.xml` — no change

`enable_dash` keeps its current meaning: when true, fetch DASH and
generate MPD; when false, fall back to durl. The
`function.inputstream_adaptive` setting does **not** exist (v0.4.0
uses Kodi's own addon dependency resolution).

### 5.13 `resources/language/.../strings.po` — minor

Add localized strings for the install prompt and the "no fmp4"
notification, in both `en_gb` and `zh_cn`.

## 6. Error handling

| Failure | Detection | Behavior |
|---|---|---|
| `inputstream.adaptive` not installed | menu's `System.HasAddon` returns false | `Dialog().yesno` → `InstallAddon(inputstream.adaptive)` or open settings (v0.1.0 UX) |
| B 站 playurl API 403 / 404 | `res.get('code') != 0` | log + return (existing behavior) |
| MPD write to `special://temp` fails | `xbmcvfs.File.write` returns false | log + return (existing v0.1.0 behavior) |
| `durl` empty | `data['durl'][0]['url']` falsy | return without `set_resolved_url` |
| Live fmp4 unavailable (all streams are FLV) | `choose_live_resolution` returns FLV or None | `Dialog().notification` + log + return; no fallback path |

## 7. Testing

Manual smoke tests in Kodi 21 (no automated test suite):

1. **VOD AVC 1080P** — play a normal AVC video. Expect: plays; MPD
   generated under `special://temp/plugin.video.bili/{cid}.mpd`; log
   shows `inputstream.adaptive` is the consumer.
2. **VOD HEVC** — play a HEVC title. Expect: plays.
3. **VOD DV** — play a Dolby Vision title. Expect: plays; system
   reports `Dolby Vision` in the OSD.
4. **VOD HDR10 / HLG** — expect: plays, matching HDR transfer reported.
5. **VOD Hi-Res FLAC** — expect: plays, FLAC track selectable in OSD.
6. **Live** — open a room. Expect: plays via
   `inputstream.adaptive manifest_type='hls'`; danmaku overlay visible.
7. **Seek** — drag the seek bar mid-video. Expect: <1s rebuffer; no
   error dialog. (v0.1.0's behavior is what we are matching.)
8. **First-run** — fresh install of plugin without
   `inputstream.adaptive`. Open plugin → menu prompts to install. After
   install, video plays.
9. **No `service.py` proxy cleanup** — verify in Kodi logs that the
   plugin no longer hosts the `/proxy/...` endpoint. MPD files under
   `special://temp/plugin.video.bili/{cid}.mpd` are overwritten on the
   next play; we accept accumulation as in v0.1.0. (The user can clear
   the `enable_dash` setting or the addon cache from the settings
   panel; the existing `remove_cache_files` route already handles this.)

## 8. Scope guard (YAGNI)

Explicitly **out of scope**:

- Reintroducing `inputstream.ffmpegdirect` (live goes through adaptive
  only).
- Reintroducing ffmpeg pipe paths for live FLV.
- Reintroducing HDR10 / DV / FLAC audio AdaptationSet grouping (we
  revert to v0.1.0's simpler `audios` list).
- Automatic version detection of `inputstream.adaptive` — the user
  manually installs when prompted, or `addon.xml`'s `optional="true"`
  keeps Kodi happy if they never use DASH.
- HTTPS on the (deleted) proxy.
- Dynamic B 站 CDN domain pools.

## 9. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| inputstream.adaptive version too old to read v0.1.0-style MPD | low | no playback | User's Kodi 21 ships adaptive ≥ 21.5.0 (Kodi 21 system addon); optional dep declaration matches the official repo |
| B 站 CDN requires more than Referer for non-logged-in users | medium | no playback for anonymous | Already a known issue in v0.1.0; user reports v0.1.0 was working with their login, so this matches v0.1.0 behavior |
| Live `format=1` (fmp4) not always available for all rooms | medium | live breaks for some rooms | The `format=0,1,2` fallback is preserved; on no fmp4 the route fails loudly with a notification, instead of silently falling back to a broken path |
| Special://temp files accumulate | low | disk usage | The temp dir already accumulates; v0.1.0 did not clean it. Not a regression. |

## 10. Files to delete (summary)

- `playback/m3u8.py`
- `playback/proxy.py`
- `monitor.py`

## 11. Files to modify (summary)

- `addon.xml` — drop `xbmc.service` is **kept** (still needed for the
  tiny MPD server); `inputstream.adaptive` becomes optional
- `playback/mpd.py` — restore v0.1.0 `generate_mpd(dash)` body
- `routes/video.py` — restore v0.1.0 MPD write + 4-prop set_resolved_url
- `routes/live.py` — force fmp4, single adaptive path
- `routes/menu.py:index()` — re-add install-prompt block
- `http_server.py` — gut: only MPD static serving remains
- `service.py` — keep, but remove `monitor.shutdown_httpd` and the
  BilibiliMonitor class; service now only loops on a Monitor for
  `waitForAbort` and runs `stop_all_live_danmaku` on shutdown
- `resources/language/.../strings.po` — install prompt text

## 12. Files untouched

- `addon.py`
- `core.py`
- `api/*`
- `live/danmaku.py`
- `playback/{ass,item,resolution,audio,history,live,__init__}.py`
  — note `m3u8.py` and `proxy.py` are deleted (§10)
- `plugin_compat.py`
- `utils.py`
- `danmaku2ass.py`
- `resources/settings.xml` (no new settings)
- `resources/language/.../strings.po` (only message strings added)

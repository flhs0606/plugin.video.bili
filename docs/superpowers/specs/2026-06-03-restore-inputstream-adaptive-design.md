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
   API — FLV is no longer consumed. When the API returns
   `master_url` (http_hls protocol m3u8) we use that as `path`
   preferentially; only fall back to a raw `urls[0]` m4s URL when
   `master_url` is empty. See §4.2 / §5.9 for the failure modes this
   is meant to address.
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
     │     the chosen dict has BOTH:
     │       urls[0]      → "host + base_url + extra" (a single m4s URL)
     │       master_url   → http_hls protocol's m3u8 (only some rooms)
     ├─ pick the input: master_url if present, else urls[0]
     └─ plugin.set_resolved_url({
            path:   <chosen url>,
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

The first iteration: `format=1` (fmp4 only) at multi-QN levels. On no
fmp4 found, re-fetch with `format=0,1,2` (all formats) and the
multi-QN ladder again. If even that yields no fmp4, log + abort with a
Kodi notification.

**Why this is hard** (mitigations in §5.9 / §9):

- B 站 returns **single m4s URLs** (not m3u8 playlists) when
  `protocol=0,1` is requested. inputstream.adaptive's
  `manifest_type='hls'` is a *strong* declaration: it expects an m3u8.
  When fed a raw m4s URL, adaptive historically has trouble —
  this is the failure mode the user wants v0.4.0 to fix.
- When `protocol` includes `http_hls`, B 站 returns a real m3u8 in
  `master_url`; this works with adaptive. We prefer it.
- `manifest_update_params='full'` re-fetches the manifest periodically.
  Raw m4s URLs have query-string signatures that expire; the refresh
  must produce a new signed URL each cycle. This is the B 站-specific
  reason live with adaptive is harder than the same call against a
  normal CDN.
- `stream_headers='Referer=…'` is enough for the VOD CDN. For live
  m4s URLs, B 站's CDN generally accepts Referer but may 403 on the
  *first* Range request (segment URL with no Range). adaptive retries
  with Range automatically; if it doesn't, we add a probe.

FLV is **not** consumed in v0.4.0. v0.1.0 used `inputstream.ffmpegdirect`
for fmp4 and ffmpeg pipe for FLV; v0.4.0 consolidates on
inputstream.adaptive only.

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

### 5.2 `playback/mpd.py` — restore v0.1.0 structure (with v0.3.0 audio)

The VOD `generate_mpd(dash)` body is rebuilt by:

1. **Video AdaptationSet** (v0.1.0 exact): per Representation, output
   `<SegmentBase indexRange="…">` with `<Initialization range="…"/>`
   and a single `<BaseURL>…</BaseURL>`. This is what inputstream.adaptive
   uses to splice init/media byte ranges precisely against B 站 CDN.
2. **Audio AdaptationSets** (v0.3.0, NOT v0.1.0): one AS per
   `AudioTrack`, with `lang="zh-Hans"`, `codecs` from
   `track.codec_mpd`, `<AudioChannelConfiguration>`, and either
   `<Role value="main"/>` (primary AAC) or
   `<SupplementalProperty value="JOC"/>` (Dolby). Per-Representation
   `<SegmentBase>` is kept.

The signature stays `generate_mpd(dash)` (one argument, matching
v0.1.0). The v0.3.0 `cookie/ua/port/use_proxy` parameters are removed —
BaseURL is the B 站 CDN URL directly, so no auth-header context needs
to be threaded through.

Pseudocode shape:

```python
def generate_mpd(dash):
    videos = choose_resolution(dash['video'])
    audio_tracks = select_by_user_pref(collect_audio_tracks(dash))

    # … MPD/Period envelope …

    # Video: v0.1.0 single-AS shape, per-Representation SegmentBase
    lines.append('\t\t<AdaptationSet mimeType="video/mp4" '
                 'startWithSAP="1" segmentAlignment="true" '
                 'scanType="progressive">\n')
    for v in videos:
        attrs = ['id="%s"' % v['id'], 'codecs="%s"' % v.get('codecs', ''),
                 'bandwidth="%d"' % v.get('bandwidth', 0)]
        if 'width' in v:    attrs.append('width="%d"' % v['width'])
        if 'height' in v:   attrs.append('height="%d"' % v['height'])
        if 'frameRate' in v: attrs.append('frameRate="%s"' % v['frameRate'])
        lines.append('\t\t\t<Representation %s>\n' % ' '.join(attrs))
        lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' %
                     v['baseUrl'].replace('&', '&amp;'))
        for bu in v.get('backup_url', []) or []:
            lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' %
                         bu.replace('&', '&amp;'))
        sb = v['SegmentBase']
        lines.append('\t\t\t\t<SegmentBase indexRange="%s">\n' % sb['indexRange'])
        lines.append('\t\t\t\t\t<Initialization range="%s">'
                     '</Initialization>\n' % sb['Initialization'])
        lines.append('\t\t\t\t</SegmentBase>\n')
        lines.append('\t\t\t</Representation>\n')
    lines.append('\t\t</AdaptationSet>\n')

    # Audio: v0.3.0 multi-AS shape (one per AudioTrack)
    for t in audio_tracks:
        lines.extend(_build_audio_as(t))

    lines.append('\t</Period>\n</MPD>\n')
    return ''.join(lines)
```

`_build_audio_as(track)` is the v0.3.0 helper — its body is unchanged.
`collect_audio_tracks`, `select_by_user_pref`, `AudioTrack`, and
`_infer_kind` (codecs-based dispatch) are all reused from
`playback/audio.py`.

`routes/video.py` is the only caller of `generate_mpd()`.

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
- `path` is the **HTTP URL served by the static MPD server**:
  `f'http://127.0.0.1:{port}/{cid}.mpd'` (port = `getSetting('server_port')`,
  default 54321). The static server (§5.5) serves it. v0.1.0 used
  the same URL shape; v0.4.0 keeps it because inputstream.adaptive in
  Kodi 21 reads the manifest URL via Kodi's HTTP client, which is the
  proven path. The `xbmcvfs.translatePath(...)` local-file path is
  rejected here — adaptive cannot be assumed to read `special://`
  paths reliably in all Kodi 21 builds.
- `properties` carries the four `inputstream.*` keys from §4.1.
- `audio_only` branch unchanged: `path = audio_url|Referer=…`.
- `durl` branch unchanged: `path = durl_url|Referer=…`.

### 5.9 `routes/live.py`

- Remove `_ensure_hls_ext`.
- The playurl request: initial loop tries `format=1` (fmp4 only) at
  QN levels `(live_resolution, 400, 250, 150, 80)`. On no result,
  re-fetch with `format=0,1,2` at the same QN ladder. This recovers
  rooms that only return FLV at the user's preferred QN but fmp4 at a
  lower QN.
- After `choose_live_resolution(streams)` returns a `best` dict:
  1. If `best['master_url']` is non-empty, **use master_url** as
     `path`. master_url is the m3u8 that the http_hls protocol returns,
     and it is the easiest input for inputstream.adaptive.
  2. Otherwise use `best['urls'][0]` (a raw m4s URL).
- Single output: inputstream.adaptive `manifest_type='hls'` with
  `manifest_update_params='full'` and the two Referer headers.
- If `best` is still FLV-only (no fmp4), log + `Dialog().notification`
  + return. **No FLV pipe fallback** (v0.4.0 commits to adaptive only).
- `playback.live.choose_live_resolution` does **not** change: it still
  returns the same dict shape with `urls`, `master_url`, `format_name`,
  `codec_name`, `current_qn`. We just consume `master_url` first.

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
   `inputstream.adaptive manifest_type='hls'`. Test both:
   - A room whose `master_url` is populated (http_hls room): adaptive
     reads the m3u8; logs should show the m3u8 URL as `path`.
   - A room whose `master_url` is empty (only `urls[0]` returned):
     adaptive reads a single m4s URL; expect the player to Range-fetch
     the file. This is the v0.4.0's risky path — confirm or fail.
   - Danmaku overlay (`live/danmaku.py`) should still appear.
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
| Live `format=1` (fmp4) not always available for all rooms | medium | live breaks for some rooms | `format=0,1,2` fallback preserves availability at lower QN; `master_url` (m3u8) is preferred when present |
| Live raw m4s URL (no m3u8 wrapper) confuses `manifest_type='hls'` | medium-high | live never starts | `master_url` from http_hls is the safe path; for raw m4s rooms we feed the URL directly to adaptive and expect it to Range-fetch the file as a single segment. Log the format chosen so we can diagnose in Kodi logs |
| Live m4s URL signature expires during playback | high (B 站 m4s URLs are short-lived) | live dies after a few minutes | `manifest_update_params='full'` re-fetches the manifest on Kodi's refresh cadence; if the re-fetched URL 403s, we let adaptive give up and the user re-enters the room. Long-term fix would be a re-auth hook, out of scope for v0.4.0 |
| Live muxed A/V (B 站 fmp4 has audio inside the same m4s) confuses adaptive | medium | no audio | B 站 fmp4 is a single muxed track; adaptive's HLS demuxer should not need an explicit AS for it. If it does, follow-up spec adds an audio AdaptationSet |
| Special://temp files accumulate | low | disk usage | The temp dir already accumulates; v0.1.0 did not clean it. Not a regression |

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

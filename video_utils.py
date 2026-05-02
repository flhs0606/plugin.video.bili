# -*- coding:utf-8 -*-
"""视频处理工具：列表项构建、Plot、分辨率选择、MPD 生成、弹幕 ASS 生成"""
import os
import json
import requests

from core import plugin, xbmc, xbmcvfs, xbmcgui
from utils import (
    tag, parts_tag, convert_number, timestamp_to_date, clear_text,
    format_stat, parse_duration, get_temp_path, make_dirs, getSetting,
    notify, notify_error
)
from api import get_api_data, get_cookie_value, post_data, fetch_url
from danmaku2ass import Danmaku2ASS
from xml.sax.saxutils import escape as xml_escape


# ── 视频列表项构建 ──────────────────────────────────────────────────────

def _flat_get(item, *keys, default=None):
    """从 item 中按优先级取第一个存在的扁平字段值"""
    for key in keys:
        if key in item:
            return item[key]
    return default


def _extract_uname_mid(item):
    """从各 API 格式中提取 UP 主名称和 mid"""
    for container in ('upper', 'owner'):
        if container in item:
            return item[container].get('name', ''), item[container].get('mid', 0)
    uname = _flat_get(item, 'author', 'author_name', default='')
    mid = _flat_get(item, 'mid', 'uid', 'author_mid', default=0)
    return uname, mid


def get_video_item(item):
    if item.get('attr', 0) != 0:
        return

    # 多 P 标记
    multi_key = ''
    for key in ('videos', 'page', 'count'):
        if key in item and isinstance(item[key], int):
            multi_key = key
            break

    uname, mid = _extract_uname_mid(item)
    pic = _flat_get(item, 'pic', 'cover', 'face', default='')
    bvid = _flat_get(item, 'bvid', default='')
    if not bvid and 'history' in item:
        bvid = item['history'].get('bvid', '')
    title = _flat_get(item, 'title', default='')
    cid = _flat_get(item, 'cid', default=0)
    if not cid:
        if 'ugc' in item:
            cid = item['ugc'].get('first_cid', 0)
        elif 'history' in item:
            cid = item['history'].get('cid', 0)

    # 时长提取
    duration = 0
    for key in ('duration', 'length'):
        if key in item:
            val = item[key]
            duration = val if isinstance(val, int) else parse_duration(val)
            break
    else:
        if 'duration_text' in item:
            duration = parse_duration(item['duration_text'])

    plot = parse_plot(item)
    if uname:
        label = f"{uname} - {title}"
    else:
        label = title
    context_menu = []
    if uname and mid:
        context_menu.append((f"转到UP: {uname}", f"Container.Update({plugin.url_for('user', id=mid)})"))
    context_menu.append(("查看推荐视频", f"Container.Update({plugin.url_for('related_videos', id=bvid)})"))
    if (not multi_key) or item[multi_key] == 1:
        context_menu.append(("仅播放音频", f"PlayMedia({plugin.url_for('video', id=bvid, cid=cid, ispgc='false', audio_only='true', title=title)})"))
        video = {
            'label': label,
            'path': plugin.url_for('video', id=bvid, cid=cid, ispgc='false', audio_only='false', title=title),
            'is_playable': True,
            'icon': pic,
            'thumbnail': pic,
            'context_menu': context_menu,
            'info': {
                'mediatype': 'video',
                'title': title,
                'duration': duration,
                'plot': plot
            },
            'info_type': 'video'
        }
    elif item[multi_key] > 1:
        video = {
            'label': parts_tag(item[multi_key]) + label,
            'path': plugin.url_for('videopages', id=bvid),
            'icon': pic,
            'thumbnail': pic,
            'context_menu': context_menu,
            'info': {
                'plot': plot
            }
        }
    else:
        return
    return video


def parse_plot(item):
    plot = ''
    if 'upper' in item:
        plot += f"UP: {item['upper']['name']}\tID: {item['upper']['mid']}\n"
    elif 'owner' in item:
        plot += f"UP: {item['owner']['name']}\tID: {item['owner']['mid']}\n"
    elif 'author' in item:
        plot += f"UP: {item['author']}"
        if 'mid' in item:
            plot += f'\tID: {item["mid"]}'
        plot += '\n'

    if 'bvid' in item:
        plot += f"{item['bvid']}\n"

    if 'pubdate' in item:
        plot += f"{timestamp_to_date(item['pubdate'])}\n"

    if 'copyright' in item and str(item['copyright']) == '1':
        plot += '未经作者授权禁止转载\n'

    state = format_stat(item)

    if state:
        plot += f"{state[:-3]}\n"
    plot += '\n'

    if 'achievement' in item and item['achievement']:
        plot += f"{tag(item['achievement'], 'orange')}\n\n"
    if 'rcmd_reason' in item and isinstance(item['rcmd_reason'], str) and item['rcmd_reason']:
        plot += f"推荐理由：{item['rcmd_reason']}\n\n"
    if 'desc' in item and item['desc']:
        plot += f"简介: {item['desc']}"
    elif 'description' in item and item['description']:
        plot += f"简介: {item['description']}"

    return plot


# ── 分辨率 / 编码选择 ───────────────────────────────────────────────────

def _filter_by_preference(items, current_value, key='id'):
    """按偏好值降级筛选：优先选匹配 current_value 的，否则选最高可用的"""
    filtered = []
    max_val = 0
    for item in items:
        if item[key] > current_value:
            continue
        if item[key] == current_value:
            filtered.append(item)
        else:
            if (not filtered) or item[key] == max_val:
                filtered.append(item)
                max_val = item[key]
            else:
                break
    if not filtered:
        min_val = items[-1][key]
        filtered = [item for item in items if item[key] == min_val]
    return filtered


def choose_resolution(videos):
    videos = sorted(videos, key=lambda x: (x['id'], x['codecid']), reverse=True)
    current_id = int(getSetting('video_resolution'))
    current_codecid = int(getSetting('video_encoding'))

    filtered_videos = _filter_by_preference(videos, current_id, 'id')
    return _filter_by_preference(filtered_videos, current_codecid, 'codecid')


def choose_live_resolution(streams):
    """从 B站直播 API 返回的流中选择最佳编码，返回 dict:
      - urls, format_name, codec_name, current_qn, master_url
    """
    if not streams:
        return None

    encoding = getSetting('live_video_encoding')
    prefer_hevc = (encoding == '12')

    def _codes(lst):
        return ', '.join('%s(qn=%s)' % (c['codec_name'], c['current_qn']) for c in lst)

    # 全局 master_url（http_hls 协议才有，http_stream 没有）
    global_master_url = ''
    for s in streams:
        if s.get('master_url'):
            global_master_url = s['master_url']
            break

    # 按 (FLV/非FLV) × (AVC/HEVC) 分类
    flv_avc, flv_hevc, other_avc, other_hevc = [], [], [], []

    for stream in streams:
        for fmt in stream.get('format', []):
            is_flv = (fmt['format_name'] == 'flv')
            for codec in fmt['codec']:
                live = {
                    'format_name': fmt['format_name'],
                    'codec_name': codec['codec_name'],
                    'current_qn': int(codec['current_qn']),
                    'urls': [info['host'] + codec['base_url'] + info['extra']
                             for info in codec['url_info']],
                    'master_url': global_master_url,
                }
                if is_flv:
                    if live['codec_name'] == 'avc':
                        flv_avc.append(live)
                    elif live['codec_name'] == 'hevc':
                        flv_hevc.append(live)
                else:
                    if live['codec_name'] == 'avc':
                        other_avc.append(live)
                    elif live['codec_name'] == 'hevc':
                        other_hevc.append(live)

    def pick(lst):
        return max(lst, key=lambda x: x['current_qn']) if lst else None

    xbmc.log('[live] available: flv_avc=[%s] flv_hevc=[%s] avc=[%s] hevc=[%s]' % (
        _codes(flv_avc), _codes(flv_hevc), _codes(other_avc), _codes(other_hevc)),
        xbmc.LOGINFO)

    if prefer_hevc:
        best = pick(flv_avc) or pick(flv_hevc) or pick(other_hevc) or pick(other_avc)
    else:
        best = pick(flv_avc) or pick(other_avc) or pick(flv_hevc) or pick(other_hevc)

    if not best:
        return None

    xbmc.log('[live] selected: %s/%s qn=%s' % (best['format_name'], best['codec_name'], best['current_qn']), xbmc.LOGINFO)
    return best


# ── MPD 生成 ────────────────────────────────────────────────────────────

def generate_mpd(dash):
    videos = choose_resolution(dash['video'])
    audios = sorted(dash['audio'], key=lambda x: x.get('id', 0), reverse=True)

    mpd_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>\n',
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static" mediaPresentationDuration="PT', str(dash['duration']), 'S" minBufferTime="PT', str(dash['minBufferTime']), 'S">\n',
        '\t<Period>\n'
    ]

    def _build_adaptation_set(items, mime_type, extra_attrs=''):
        """统一构造 AdaptationSet + Representation，消除 video/audio 重复"""
        lines = ['\t\t<AdaptationSet mimeType="%s" startWithSAP="1" segmentAlignment="true"%s>\n'
                 % (mime_type, extra_attrs)]
        for item in items:
            base_url = item['baseUrl'].replace('&', '&amp;')
            attrs = []
            if 'bandwidth' in item:
                attrs.append('bandwidth="%s"' % item['bandwidth'])
            if 'codecs' in item:
                attrs.append('codecs="%s"' % item['codecs'])
            if 'frameRate' in item:
                attrs.append('frameRate="%s"' % item['frameRate'])
            if 'height' in item:
                attrs.append('height="%s"' % item['height'])
            if 'width' in item:
                attrs.append('width="%s"' % item['width'])
            if 'id' in item:
                attrs.append('id="%s"' % item['id'])
            if 'audioSamplingRate' in item:
                attrs.append('audioSamplingRate="%s"' % item['audioSamplingRate'])
            lines.append('\t\t\t<Representation %s>\n' % ' '.join(attrs))
            lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' % base_url)
            for bu in item.get('backup_url', []) or []:
                lines.append('\t\t\t\t<BaseURL>%s</BaseURL>\n' % bu.replace('&', '&amp;'))
            lines.append('\t\t\t\t<SegmentBase indexRange="%s">\n'
                         % item['SegmentBase']['indexRange'])
            lines.append('\t\t\t\t\t<Initialization range="%s"></Initialization>\n'
                         % item['SegmentBase']['Initialization'])
            lines.append('\t\t\t\t</SegmentBase>\n')
            lines.append('\t\t\t</Representation>\n')
        lines.append('\t\t</AdaptationSet>\n')
        return lines

    # video
    mpd_lines.extend(_build_adaptation_set(
        videos, 'video/mp4', ' scanType="progressive"'))

    # audio（按质量降序排列，inputstream.adaptive 会优先选择第一个）
    mpd_lines.extend(_build_adaptation_set(
        audios, 'audio/mp4', ' lang="und"'))

    mpd_lines.append('\t</Period>\n</MPD>\n')

    return ''.join(mpd_lines)


def generate_ass(cid):
    basepath = xbmc.translatePath('special://temp/plugin.video.bili/')
    if not make_dirs(basepath):
        return
    xmlfile = os.path.join(basepath, str(cid) + '.xml')
    assfile = os.path.join(basepath, str(cid) + '.ass')
    if xbmcvfs.exists(assfile):
        return assfile
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3071.115 Safari/537.36'
    }

    try:
        res = requests.get(f'https://comment.bilibili.com/{cid}.xml', headers=headers, timeout=10)
        res.encoding = 'utf-8'
        content = res.text
    except Exception as e:
        xbmc.log('[plugin.video.bili] generate_ass failed to fetch danmaku: %s' % str(e), xbmc.LOGWARNING)
        return
    with xbmcvfs.File(xmlfile, 'w') as f:
        success = f.write(content)
    if not success:
        return
    font_size = float(getSetting('font_size'))
    text_opacity = float(getSetting('opacity'))
    duration = float(getSetting('danmaku_stay_time'))
    width = 1920
    height = 540
    reserve_blank = int((1.0 - float(getSetting('display_area'))) * height)
    Danmaku2ASS(xmlfile, 'autodetect' , assfile, width, height, reserve_blank=reserve_blank,font_size=font_size, text_opacity=text_opacity,duration_marquee=duration,duration_still=duration)
    if xbmcvfs.exists(assfile):
        return assfile


# ── 历史记录上报 ────────────────────────────────────────────────────────

def report_history(bvid, cid):
    data = {
        'bvid': bvid,
        'cid': cid,
        'csrf': get_cookie_value('bili_jct')
    }
    res = post_data('https://api.bilibili.com/x/click-interface/web/heartbeat', data)
    if res.get('code') != 0:
        xbmc.log('[plugin.video.bili] report_history failed: %s' % res.get('message', ''), xbmc.LOGWARNING)
    return res

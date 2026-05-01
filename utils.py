# -*- coding:utf-8 -*-
"""显示、格式化、文件系统等工具函数（无 plugin 依赖）"""
import sys
import os
import locale
import shutil
from datetime import datetime

from core import xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon


def tag(info, color='red'):
    return f'[COLOR {color}]{info}[/COLOR]'


def parts_tag(p):
    return tag(f'【{p}P】', 'red')


def convert_number(num):
    if isinstance(num, str):
        return num
    if num < 10000:
        return str(num)
    if num < 99999500:
        result = round(num / 10000, 1)
        return str(result) + "万"
    else:
        result = round(num / 100000000, 1)
        return str(result) + "亿"


def timestamp_to_date(timestamp):
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime('%Y.%m.%d %H:%M:%S')


def notify(title, msg, t=1500):
    xbmcgui.Dialog().notification(title, msg, xbmcgui.NOTIFICATION_INFO, t, False)


def notify_error(res):
    message = res.get('message', '未知错误')
    notify('提示', f'{res.get("code", -1)}: {message}')


def localize(id):
    return xbmcaddon.Addon().getLocalizedString(id)


def getSetting(name):
    return xbmcplugin.getSetting(int(sys.argv[1]), name)


def clear_text(text):
    return text.replace('<em class=\"keyword\">', '').replace('</em>', '')


# 播放统计字段映射：(字段名, 中文标签)，按优先级排列；同标签只取第一个匹配
_STAT_KEY_MAP = [
    ('view', '播放'), ('play', '播放'), ('like', '点赞'), ('likes', '点赞'),
    ('coin', '投币'), ('favorite', '收藏'), ('collect', '收藏'),
    ('reply', '评论'), ('comment', '评论'), ('danmaku', '弹幕'),
    ('share', '分享'),
]


def format_stat(item):
    """从B站API返回的item中提取格式化后的播放统计字符串"""
    state = ''
    seen_labels = set()
    if 'stat' in item:
        stat = item['stat']
        for key, label in _STAT_KEY_MAP:
            if key in stat and label not in seen_labels:
                state += f"{convert_number(stat[key])}{label} · "
                seen_labels.add(label)
    elif 'cnt_info' in item:
        stat = item['cnt_info']
        for key, label in _STAT_KEY_MAP:
            if key in stat and label not in seen_labels:
                state += f"{convert_number(stat[key])}{label} · "
                seen_labels.add(label)
    else:
        if 'play' in item and isinstance(item['play'], int):
            state += f"{convert_number(item['play'])}播放 · "
        if 'comment' in item and isinstance(item['comment'], int):
            state += f"{convert_number(item['comment'])}评论 · "
    return state


def parse_duration(duration_text):
    parts = duration_text.split(':')
    duration = 0
    for part in parts:
        duration = duration * 60 + int(part)
    return duration


def make_dirs(path):
    if not path.endswith('/'):
        path = ''.join([path, '/'])
    path = xbmc.translatePath(path)
    if not xbmcvfs.exists(path):
        try:
            _ = xbmcvfs.mkdirs(path)
        except Exception as e:
            xbmc.log('[plugin.video.bili] mkdirs via xbmcvfs failed: %s' % str(e), xbmc.LOGWARNING)
        if not xbmcvfs.exists(path):
            try:
                os.makedirs(path)
            except Exception as e:
                xbmc.log('[plugin.video.bili] mkdirs via os failed: %s' % str(e), xbmc.LOGWARNING)
        return xbmcvfs.exists(path)

    return True


def get_temp_path():
    temppath = xbmc.translatePath('special://temp/plugin.video.bili/')
    if not make_dirs(temppath):
        return
    return temppath


def safe_remove_dir(path, log_prefix='[plugin.video.bili]'):
    """安全删除目录：先尝试 xbmcvfs.rmdir，失败则回退 shutil.rmtree"""
    if not os.path.isdir(path):
        return True
    try:
        xbmcvfs.rmdir(path, force=True)
    except Exception as e:
        xbmc.log(f'{log_prefix} xbmcvfs.rmdir failed: {e}', xbmc.LOGWARNING)
    if os.path.isdir(path):
        try:
            shutil.rmtree(path)
        except Exception as e:
            xbmc.log(f'{log_prefix} shutil.rmtree failed: {e}', xbmc.LOGWARNING)
    return not os.path.isdir(path)

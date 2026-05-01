# -*- coding:utf-8 -*-
"""B站 API 网络层：Cookie、WBI 签名、HTTP 请求封装"""
import sys
import os
import re
import json
import time
from urllib.parse import urlencode
from functools import reduce
from hashlib import md5
import requests

from core import plugin, xbmc, xbmcplugin
from utils import getSetting, notify


# ── WBI 签名常量 ────────────────────────────────────────────────────────

mixinKeyEncTab = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52
]


def getMixinKey(orig: str):
    '对 imgKey 和 subKey 进行字符顺序打乱编码'
    return reduce(lambda s, i: s + orig[i], mixinKeyEncTab, '')[:32]


def encWbi(params: dict, img_key: str, sub_key: str):
    '为请求参数进行 wbi 签名'
    mixin_key = getMixinKey(img_key + sub_key)
    curr_time = round(time.time())
    params['wts'] = curr_time                                   # 添加 wts 字段
    params = dict(sorted(params.items()))                       # 按照 key 重排参数
    # 过滤 value 中的 "!'()*" 字符
    params = {
        k : ''.join(filter(lambda chr: chr not in "!'()*", str(v)))
        for k, v 
        in params.items()
    }
    query = urlencode(params)                      # 序列化参数
    wbi_sign = md5((query + mixin_key).encode()).hexdigest()    # 计算 w_rid
    params['w_rid'] = wbi_sign
    return params


def getWbiKeys():
    '获取最新的 img_key 和 sub_key'
    json_content = get_api_data('/x/web-interface/nav')
    img_url: str = json_content['data']['wbi_img']['img_url']
    sub_url: str = json_content['data']['wbi_img']['sub_url']
    img_key = img_url.rsplit('/', 1)[1].split('.')[0]
    sub_key = sub_url.rsplit('/', 1)[1].split('.')[0]
    return img_key, sub_key


# ── Cookie 管理 ─────────────────────────────────────────────────────────

_cookie_re_cache = {}

def _ensure_buvid3(cookie):
    """确保 cookie 中存在 buvid3，不存在则从持久化存储中读取或生成"""
    if 'buvid3=' in cookie:
        return cookie
    # 尝试复用已生成并持久化的 buvid3
    account = plugin.get_storage('account')
    saved_buvid3 = account.get('_buvid3', '')
    if not saved_buvid3:
        import uuid
        saved_buvid3 = uuid.uuid4().hex + uuid.uuid4().hex
        account['_buvid3'] = saved_buvid3
    prefix = f'buvid3={saved_buvid3}; '
    return prefix + cookie if cookie else prefix.rstrip('; ')


def get_cookie():
    account = plugin.get_storage('account')
    cookie = account.get('cookie', '')
    # 始终确保 buvid3 存在，避免 CDN 403（参考 wiliwili 做法）
    cookie = _ensure_buvid3(cookie)
    return cookie


def get_cookie_value(key):
    cookie = get_cookie()
    if not cookie:
        return ''
    # 精确匹配 cookie 键名，避免部分匹配（如 DedeUserID 匹配到 DedeUserID__ckMd5）
    if key not in _cookie_re_cache:
        _cookie_re_cache[key] = re.compile(r'(?:^|;\s*)' + re.escape(key) + r'=([^;]*)')
    m = _cookie_re_cache[key].search(cookie)
    return m.group(1) if m else ''


def get_uid():
    return get_cookie_value('DedeUserID') or '0'


# ── HTTP 请求层 ─────────────────────────────────────────────────────────

_USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3071.115 Safari/537.36'
_REFERER = 'https://www.bilibili.com'


def _build_headers():
    headers = {
        'User-Agent': _USER_AGENT,
        'Referer': _REFERER,
    }
    cookie = get_cookie()
    if cookie:
        headers['Cookie'] = cookie
    return headers


def post_data(url, data):
    headers = _build_headers()
    try:
        res = requests.post(url, data=data, headers=headers).json()
    except Exception as e:
        res = {'code': -1, 'message': '网络错误'}
    return res


def raw_fetch_url(url):
    xbmc.log('url_get: ' + url)
    headers = _build_headers()
    try:
        res = requests.get(url, headers=headers).json()
    except Exception as e:
        res = {'code': -1, 'message': '网络错误'}
    return res


@plugin.cached(TTL=1)
def cached_fetch_url(url):
    return raw_fetch_url(url)


def fetch_url(url):
    if getSetting('network_request_cache') == 'true':
        return cached_fetch_url(url)
    else:
        return raw_fetch_url(url)


def raw_get_api_data(url, data={}):
    url = f'https://api.bilibili.com{url}'
    if data:
        url += '?' + urlencode(data)
    return raw_fetch_url(url)


def cached_get_api_data(url, data={}):
    url = f'https://api.bilibili.com{url}'
    if data:
        url += '?' + urlencode(data)
    return cached_fetch_url(url)


def get_api_data(url, data={}):
    if getSetting('network_request_cache') == 'true':
        return cached_get_api_data(url, data)
    else:
        return raw_get_api_data(url, data)

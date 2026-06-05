# -*- coding:utf-8 -*-
"""HTTP 请求封装：UA/Referer/Cookie 注入、超时、缓存开关。

关键发现（参考 https://github.com/xfangfang/wiliwili/blob/yoga/wiliwili/include/api/bilibili/util/http.hpp）：
  wiliwili 用的是裸 `User-Agent: wiliwili`，没有任何 sec-ch-* / Accept 头。
  但它能拿到 Atmos / Hi-Res / Dolby Vision，我们用现代 Chrome 反而拿不到。
  说明 B 站 server 对"现代浏览器 UA"和"非标客户端 UA"的策略不同：
    - 现代 Chrome UA 走 web 路径，被限制只回 AAC
    - 自定义 UA 走"特殊客户端"路径，放开所有格式

所以本插件**必须用 wiliwili 风格的 UA**，不能用真实浏览器 UA。
"""
from urllib.parse import urlencode

import requests

from core import plugin, xbmc
from utils import getSetting


# 故意用 wiliwili 自己的标识，绕开 B 站的"web 客户端降级到 AAC"策略
_USER_AGENT = 'wiliwili'
_REFERER = 'https://www.bilibili.com'
_REQUEST_TIMEOUT = 10  # 所有 requests 调用统一超时（秒），避免 Kodi 退出时残留

# 喂给 inputstream.adaptive 的 stream_headers / manifest_headers 字符串
BILI_REFERER = 'Referer=https://www.bilibili.com'


def build_headers(cookie: str = None) -> dict:
    """wiliwili 风格的最小请求头集合（UA + Referer + Origin）。

    关键决策：不发 sec-ch-* / Accept / Accept-Language。B 站 server
    会按这些 header 判断走 web 路径还是 special-client 路径，
    走 web 路径会被强制降级到只回 AAC。
    """
    h = {
        'User-Agent': _USER_AGENT,
        'Referer': _REFERER,
        'Origin': 'https://www.bilibili.com',
    }
    if cookie:
        h['Cookie'] = cookie
    return h


def _resolve_cookie() -> str:
    """延迟 import 避免 api.http ↔ api.cookie 循环。"""
    from api.cookie import get_cookie
    return get_cookie()


def post_data(url: str, data: dict) -> dict:
    headers = build_headers(_resolve_cookie())
    try:
        res = requests.post(url, data=data, headers=headers, timeout=_REQUEST_TIMEOUT).json()
    except Exception as e:
        xbmc.log('[api.http] post_data error: %s: %s url=%s' % (
            type(e).__name__, str(e), url), xbmc.LOGWARNING)
        res = {'code': -1, 'message': '网络错误: %s' % type(e).__name__}
    return res


def _get_url(url: str) -> dict:
    """fetch_url 的实际 GET 实现（无缓存）。"""
    xbmc.log('url_get: ' + url)
    headers = build_headers(_resolve_cookie())
    try:
        return requests.get(url, headers=headers, timeout=_REQUEST_TIMEOUT).json()
    except Exception as e:
        xbmc.log(
            '[api.http] fetch_url error: %s: %s url=%s' % (
                type(e).__name__, str(e), url,
            ),
            xbmc.LOGWARNING,
        )
        return {'code': -1, 'message': '网络错误: %s' % type(e).__name__}


_cached_url = plugin.cached(TTL=1)(_get_url)


def fetch_url(url: str, *, raw: bool = False) -> dict:
    """GET 任意 URL。`raw=True` 跳过 1 分钟内存缓存（CDN 临时签名等场景）。"""
    if raw or getSetting('network_request_cache') != 'true':
        return _get_url(url)
    return _cached_url(url)


def build_api_url(path: str, data: dict = None) -> str:
    url = f'https://api.bilibili.com{path}'
    if data:
        url += '?' + urlencode(data)
    return url


def _get_api(path: str, data=None) -> dict:
    return _get_url(build_api_url(path, data))


_cached_api = plugin.cached(TTL=1)(_get_api)


def get_api_data(path: str, data: dict = None, *, raw: bool = False) -> dict:
    """B 站 api.bilibili.com 接口入口。`raw=True` 跳过 1 分钟缓存。"""
    if raw or getSetting('network_request_cache') != 'true':
        return _get_api(path, data)
    return _cached_api(path, data)

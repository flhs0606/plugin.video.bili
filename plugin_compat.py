"""
plugin_compat.py - Kodi 21 原生兼容层，替换 xbmcswift2

提供与原 xbmcswift2 Plugin 类兼容的接口，底层使用 Kodi 21 原生 Python API。
导出: Plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon

用法: from plugin_compat import Plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon
"""
import sys
import os
import re
import json
import time
import threading
from functools import wraps
from datetime import timedelta
from urllib.parse import urlencode as _urlencode

# Kodi 原生模块
import xbmc
import xbmcplugin
import xbmcvfs
import xbmcgui
import xbmcaddon

try:
    xbmc.translatePath = xbmcvfs.translatePath
except AttributeError:
    pass


# ═══════════════════════════════════════════════════════════════════════════
# URL 路由
# ═══════════════════════════════════════════════════════════════════════════

class NotFoundException(Exception):
    pass


class UrlRule:
    def __init__(self, url_rule, view_func, name, options=None):
        self.url_rule = url_rule
        self.view_func = view_func
        self.name = name
        self.options = options or {}
        rule = url_rule if url_rule == '/' else url_rule.rstrip('/') + '/?'
        self._regex = re.compile('^' + rule.replace('<', '(?P<').replace('>', '>[^/]+?)') + '$')
        self._format = url_rule.replace('<', '{').replace('>', '}')
        self._params = re.findall(r'\<(.+?)\>', url_rule)

    def match(self, path):
        m = self._regex.match(path)
        if m:
            return self.view_func, m.groupdict()
        raise NotFoundException()

    def make_path(self, items):
        fmt = {}
        query = {}
        for k, v in items.items():
            if k in self._params:
                fmt[k] = str(v)
            else:
                query[k] = str(v)
        path = self._format.format(**fmt)
        if query:
            path += '?' + _urlencode(query)
        return path


# ═══════════════════════════════════════════════════════════════════════════
# JSON 持久化存储
# ═══════════════════════════════════════════════════════════════════════════

class _Storage(dict):
    def __init__(self, filepath, ttl=None):
        super().__init__()
        self._filepath = filepath
        self._ttl = ttl
        self._dirty = False
        self._load()

    def _load(self):
        if os.path.isfile(self._filepath):
            try:
                with open(self._filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if self._ttl and '_ts' in data:
                        ts = data.pop('_ts')
                        if timedelta(seconds=time.time() - ts) > self._ttl:
                            data = {}
                    self.update(data)
            except (json.JSONDecodeError, IOError):
                pass

    def sync(self):
        if self._dirty:
            os.makedirs(os.path.dirname(self._filepath), exist_ok=True)
            with open(self._filepath, 'w', encoding='utf-8') as f:
                json.dump(dict(self), f, ensure_ascii=False)
            self._dirty = False

    def close(self):
        self.sync()

    def clear(self):
        super().clear()
        self._dirty = True
        self.sync()

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self._dirty = True

    def __delitem__(self, k):
        super().__delitem__(k)
        self._dirty = True

    def __del__(self):
        try:
            self.sync()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


# ═══════════════════════════════════════════════════════════════════════════
# dict -> xbmcgui.ListItem 转换
# ═══════════════════════════════════════════════════════════════════════════

def _dict_to_li(item, set_played=False):
    label = item.get('label', '')
    path = item.get('path', '')
    li = xbmcgui.ListItem(label=label, label2=item.get('label2', ''), path=path)
    
    icon = item.get('icon', '')
    thumb = item.get('thumbnail', icon)
    if icon or thumb:
        li.setArt({'icon': icon, 'thumb': thumb})
    
    info = item.get('info', {})
    if info:
        li.setInfo(item.get('info_type', 'video'), info)
    
    props = item.get('properties', {})
    for k, v in props.items():
        li.setProperty(k, str(v))
    
    if item.get('is_playable'):
        li.setProperty('IsPlayable', 'true')
    
    # 直播流：Kodi 21 需要设置 IsLiveStream 防止提前终止
    # 注意：不要设置 inputstreamaddon，Kodi 21 中该属性已弃用
    if item.get('is_live'):
        li.setProperty('IsLiveStream', 'true')
        li.setProperty('IsLive', 'true')
        # Kodi 21 推荐使用 VideoInfoTag 设置直播属性
        try:
            tag = li.getVideoInfoTag()
            tag.setMediaType('video')
        except Exception:
            pass
    
    cm = item.get('context_menu', [])
    if cm:
        li.addContextMenuItems([(l, a) for l, a in cm], replaceItems=True)
    
    return li, path


# ═══════════════════════════════════════════════════════════════════════════
# Plugin 兼容类
# ═══════════════════════════════════════════════════════════════════════════

class Plugin:
    def __init__(self, name=None, addon_id=None):
        self._addon = xbmcaddon.Addon()
        self._addon_id = self._addon.getAddonInfo('id')
        self._name = name or self._addon.getAddonInfo('name')
        self._routes = []
        self._view_functions = {}
        self._end_of_directory = False
        self._added_items = []
        self._storage_path = xbmcvfs.translatePath(
            'special://profile/addon_data/%s/.storage/' % self._addon_id)
        os.makedirs(self._storage_path, exist_ok=True)
        self._unsynced_storages = {}

    # ── route / url_for ──────────────────────────────────────────────────

    def route(self, url_rule, name=None, options=None):
        def decorator(f):
            view_name = name or f.__name__
            rule = UrlRule(url_rule, f, view_name, options)
            self._view_functions[view_name] = rule
            self._routes.append(rule)
            return f
        return decorator

    def url_for(self, endpoint, **items):
        rule = self._view_functions.get(endpoint)
        if not rule:
            for r in self._view_functions.values():
                if r.view_func is endpoint:
                    rule = r
                    break
        if not rule:
            raise NotFoundException("%s doesn't match any known patterns." % endpoint)
        return 'plugin://%s%s' % (self._addon_id, rule.make_path(items))

    # ── storage ──────────────────────────────────────────────────────────

    def get_storage(self, name='main', file_format='pickle', TTL=None):
        filename = os.path.join(self._storage_path, name + '.json')
        if filename in self._unsynced_storages:
            return self._unsynced_storages[filename]
        ttl = timedelta(minutes=TTL) if TTL else None
        s = _Storage(filename, ttl=ttl)
        self._unsynced_storages[filename] = s
        return s

    # ── cached ───────────────────────────────────────────────────────────

    _fcache_name = '.functions'

    def cached(self, TTL=10):
        """缓存装饰器，TTL 单位为分钟，默认 10 分钟。每个缓存条目独立计时过期"""
        ttl_seconds = TTL * 60

        def decorating_function(function):
            storage = self.get_storage(self._fcache_name)

            @wraps(function)
            def wrapper(*args, **kwargs):
                key = function.__name__ + '|' + '|'.join(str(a) for a in args)
                if kwargs:
                    key += '|__KW__|' + '|'.join(
                        '%s=%s' % (k, v) for k, v in sorted(kwargs.items()))
                entry = storage.get(key)
                if entry is not None:
                    # entry 格式: [timestamp, result]；兼容旧格式（裸值）
                    if isinstance(entry, list) and len(entry) == 2:
                        if time.time() - entry[0] < ttl_seconds:
                            return entry[1]
                    else:
                        # 旧格式无时间戳，视为已过期，删除后重新请求
                        del storage[key]
                result = function(*args, **kwargs)
                storage[key] = [time.time(), result]
                storage.sync()
                return result
            return wrapper
        return decorating_function

    def clear_function_cache(self):
        try:
            self.get_storage(self._fcache_name).clear()
        except Exception:
            pass

    # ── open_settings ────────────────────────────────────────────────────

    def open_settings(self):
        self._addon.openSettings()

    # ── finish / add_items ───────────────────────────────────────────────

    def _add_items(self, items):
        handle = int(sys.argv[1])
        tuples = []
        for item in items:
            if not item:
                continue
            li, path = _dict_to_li(item)
            is_folder = not item.get('is_playable', False)
            tuples.append((path, li, is_folder))
        xbmcplugin.addDirectoryItems(handle, tuples, len(tuples))
        self._added_items.extend(items)

    def finish(self, items=None, sort_methods=None, succeeded=True,
               update_listing=False, cache_to_disc=True):
        if items:
            self._add_items(items)
        self._end_of_directory = True
        handle = int(sys.argv[1])
        xbmcplugin.endOfDirectory(handle, succeeded, update_listing, cache_to_disc)
        return self._added_items

    # ── set_resolved_url ─────────────────────────────────────────────────

    def set_resolved_url(self, item=None, subtitles=None):
        handle = int(sys.argv[1])
        self._end_of_directory = True

        if item is None:
            li = xbmcgui.ListItem()
            xbmcplugin.setResolvedUrl(handle, False, li)
            return [li]

        if isinstance(item, str):
            item = {'path': item}

        li, _ = _dict_to_li(item)

        succeeded = bool(item.get('path', ''))
        xbmcplugin.setResolvedUrl(handle, succeeded, li)

        if subtitles:
            # Kodi 21: setSubtitles 必须在播放开始后调用，等待 player 就绪
            t = threading.Thread(target=self._wait_and_set_subtitles,
                                 args=(subtitles,), daemon=True)
            t.start()

        return [li]

    def _wait_and_set_subtitles(self, subtitles):
        """等待播放器启动后设置外挂字幕"""
        player = xbmc.Player()
        for _ in range(30):
            if player.isPlaying():
                break
            time.sleep(1)
        if player.isPlaying():
            xbmc.log('[plugin_compat] setting subtitles: %s' % subtitles, xbmc.LOGINFO)
            player.setSubtitles(subtitles)
        else:
            xbmc.log('[plugin_compat] player not started, subtitles skipped', xbmc.LOGWARNING)

    # ── run ──────────────────────────────────────────────────────────────

    def run(self):
        argv = sys.argv
        handle = int(argv[1]) if len(argv) > 1 else -1
        url = argv[0]
        query = argv[2] if len(argv) > 2 else ''

        plugin_prefix = 'plugin://' + self._addon_id
        if url.startswith(plugin_prefix):
            path = url[len(plugin_prefix):]
        else:
            path = url
        if not path.startswith('/'):
            path = '/' + path
        if query:
            path += ('?' + query) if '?' not in path else '&' + query

        for rule in self._routes:
            try:
                view_func, params = rule.match(path)
            except NotFoundException:
                continue

            result = view_func(**params)

            if handle >= 0 and not self._end_of_directory:
                if result is not None:
                    result = self.finish(result)
                else:
                    handle_int = int(sys.argv[1])
                    xbmcplugin.endOfDirectory(handle_int, False)

            for s in self._unsynced_storages.values():
                try:
                    s.close()
                except Exception:
                    pass
            return result

        xbmc.log('[plugin_compat] No route for: %s' % path, xbmc.LOGERROR)
        if handle >= 0:
            xbmcplugin.endOfDirectory(handle, False)
        return None

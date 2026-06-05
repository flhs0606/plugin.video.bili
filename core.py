# -*- coding:utf-8 -*-
"""插件核心实例 — 被所有模块导入的共享 Plugin 对象

`xbmc.translatePath = xbmcvfs.translatePath` polyfill 在 plugin_compat.py
里做，core.py 自身就 import plugin_compat，所以删掉这里的重复版本。
"""
from plugin_compat import Plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon

plugin = Plugin()

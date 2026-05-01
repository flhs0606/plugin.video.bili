# -*- coding:utf-8 -*-
"""插件核心实例 — 被所有模块导入的共享 Plugin 对象"""
from plugin_compat import Plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon

try:
    xbmc.translatePath = xbmcvfs.translatePath
except AttributeError:
    pass

plugin = Plugin()

# -*- coding:utf-8 -*-
"""B站插件入口 — Kodi 启动时由 addon.xml 指定的 library"""
from core import plugin

# 导入 routes 模块以注册所有路由（@plugin.route 装饰器在导入时自动注册）
import routes  # noqa: F401

# 启动时清除过期/失效的缓存（CDN URL 有时效性）
plugin.clear_function_cache()

if __name__ == '__main__':
    plugin.run()

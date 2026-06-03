# -*- coding:utf-8 -*-
"""B站插件入口 — Kodi 每次导航时启动全新进程，生成目录列表后立即退出。

v0.4.0 不再起本地 HTTP server：inputstream.adaptive 在 Kodi 21 接受
file:// 绝对路径直接读 MPD 盘上文件（v0.1.0 时未验证过这个能力，
xbmc.service + http_server 那一套是过度设计；现在用 file:// 路径
直接给 adaptive, 完全跳过 HTTP 栈)。service.py 仍然保留以备
将来需要其他长驻任务时使用。
"""
from core import plugin

# 导入 routes 模块以注册所有路由（@plugin.route 装饰器在导入时自动注册）
import routes  # noqa: F401

# 注意：Kodi 每次导航都是全新 Python 进程，内存缓存不跨进程共享。
# clear_function_cache() 在新鲜进程中为 no-op，此处不调用。
# 缓存的持久化部分由 login/logout 路由负责刷新。

if __name__ == '__main__':
    plugin.run()

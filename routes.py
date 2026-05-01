# -*- coding:utf-8 -*-
"""所有 Kodi 路由处理函数"""
import os
import json
import time
import shutil
from urllib.parse import urlencode

import requests

from core import plugin, xbmc, xbmcplugin, xbmcvfs, xbmcgui, xbmcaddon
from utils import (
    tag, parts_tag, convert_number, timestamp_to_date, notify, notify_error,
    localize, getSetting, clear_text, format_stat, parse_duration,
    make_dirs, get_temp_path, safe_remove_dir
)
from api import (
    get_cookie, get_cookie_value, get_uid,
    raw_fetch_url, fetch_url, raw_get_api_data, cached_get_api_data, get_api_data,
    getWbiKeys, encWbi, post_data
)
from video_utils import (
    get_video_item, parse_plot, choose_live_resolution,
    generate_mpd, generate_ass, report_history
)
from live_danmaku import start_live_danmaku


# ═══════════════════════════════════════════════════════════════════════════
# 缓存 / 登录 / 工具路由
# ═══════════════════════════════════════════════════════════════════════════

@plugin.route('/remove_cache_files/')
def remove_cache_files():
    addon_id = 'plugin.video.bili'
    try:
        path = xbmc.translatePath(f'special://temp/{addon_id}').decode('utf-8')
    except (AttributeError, UnicodeDecodeError):
        path = xbmc.translatePath(f'special://temp/{addon_id}')

    if safe_remove_dir(path):
        xbmcgui.Dialog().ok('提示', '清除成功')
        return True
    else:
        xbmcgui.Dialog().ok('提示', '清除失败')
        return False


@plugin.route('/check_login/')
def check_login():
    if not get_cookie():
        xbmcgui.Dialog().ok('提示', '账号未登录')
        return
    res = raw_get_api_data('/x/web-interface/nav/stat')
    if res['code'] == 0:
        xbmcgui.Dialog().ok('提示', '登录成功')
    elif res['code'] == -101:
        xbmcgui.Dialog().ok('提示', '账号未登录')
    else:
        xbmcgui.Dialog().ok('提示', res.get('message', '未知错误'))


@plugin.route('/logout/')
def logout():
    account = plugin.get_storage('account')
    account['cookie'] = ''
    plugin.clear_function_cache()
    xbmcgui.Dialog().ok('提示', '退出成功')


@plugin.route('/cookie_login/')
def cookie_login():
    keyboard = xbmc.Keyboard('', '请输入 Cookie')
    keyboard.doModal()
    if (keyboard.isConfirmed()):
        cookie = keyboard.getText().strip()
        if not cookie:
            return
    else:
        return
    account = plugin.get_storage('account')
    account['cookie'] = cookie
    plugin.clear_function_cache()
    xbmcgui.Dialog().ok('提示', 'Cookie 设置成功')


@plugin.route('/qrcode_login/')
def qrcode_login():
    temp_path = get_temp_path()
    if not temp_path:
        notify('提示', '无法创建文件夹')
        return
    temp_path = os.path.join(temp_path, 'login.png')
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3071.115 Safari/537.36',
        }
        res = requests.get('https://passport.bilibili.com/x/passport-login/web/qrcode/generate', headers=headers).json()
    except Exception:
        notify('提示', '二维码获取失败')
        return
    if res['code'] != 0:
        notify_error(res)

    login_path = res['data']['url']
    key = res['data']['qrcode_key']
    try:
        import qrcode
    except Exception as e:
        xbmc.log('[plugin.video.bili] qrcode import failed: %s' % str(e), xbmc.LOGERROR)
        notify('提示', '缺少依赖：请安装 script.module.qrcode 插件')
        return
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=20
    )
    qr.add_data(login_path)
    qr.make(fit=True)
    img = qr.make_image()
    img.save(temp_path)
    xbmc.executebuiltin('ShowPicture(%s)' % temp_path)
    polling_login_status(key)


def polling_login_status(key):
    session = requests.Session()
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3071.115 Safari/537.36',
    }
    for i in range(50):
        try:
            response = session.get(f'https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={key}', headers=headers)
            check_result = response.json()
        except Exception:
            time.sleep(3)
            continue
        if check_result['code'] != 0:
            xbmc.executebuiltin('Action(Back)')
            return
        if check_result['data']['code'] == 0:
            account = plugin.get_storage('account')
            cookies = session.cookies
            cookies = ' '.join([cookie.name + '=' + cookie.value + ';' for cookie in cookies])
            xbmc.log('set-cookie: ' + cookies)
            account['cookie'] = cookies
            plugin.clear_function_cache()
            xbmcgui.Dialog().ok('提示', '登录成功')
            xbmc.executebuiltin('Action(Back)')
            return
        elif check_result['data']['code'] == 86038:
            notify('提示', '二维码已失效')
            xbmc.executebuiltin('Action(Back)')
            return
        time.sleep(3)
    xbmc.executebuiltin('Action(Back)')


# ═══════════════════════════════════════════════════════════════════════════
# 菜单管理
# ═══════════════════════════════════════════════════════════════════════════

def get_categories():
    uid = get_uid()
    categories = [
        {'name': 'home', 'id': 30101, 'path': plugin.url_for('home', page=1)},
        {'name': 'dynamic_list', 'id': 30102, 'path': plugin.url_for('dynamic_list')},
        {'name': 'ranking_list', 'id': 30103, 'path': plugin.url_for('ranking_list')},
        {'name': 'popular_weekly', 'id': 30114, 'path': plugin.url_for('popular_weekly')},
        {'name': 'popular_history', 'id': 30115, 'path': plugin.url_for('popular_history')},
        {'name': 'live_areas', 'id': 30104, 'path': plugin.url_for('live_areas', level=1, id=0)},
        {'name': 'followingLive', 'id': 30105, 'path': plugin.url_for('followingLive', page=1)},
        {'name': 'my_collection', 'id': 30106, 'path': plugin.url_for('my_collection')},
        {'name': 'web_dynamic', 'id': 30107, 'path': plugin.url_for('web_dynamic', page=1, offset=0)},
        {'name': 'followings', 'id': 30108, 'path': plugin.url_for('followings', id=uid, page=1)},
        {'name': 'followers', 'id': 30109, 'path': plugin.url_for('followers', id=uid, page=1)},
        {'name': 'watchlater', 'id': 30110, 'path': plugin.url_for('watchlater')},
        {'name': 'history', 'id': 30111, 'path': plugin.url_for('history', time=0)},
        {'name': 'space_videos', 'id': 30112, 'path': plugin.url_for('space_videos', id=uid, page=1)},
        {'name': 'my', 'id': 30117, 'path': plugin.url_for('user', id=uid)},
        {'name': 'search_list', 'id': 30113, 'path': plugin.url_for('search_list')},
        {'name': 'open_settings', 'id': 30116, 'path': plugin.url_for('open_settings')},
    ]
    return categories


def update_categories():
    categories = get_categories()
    data = plugin.get_storage('data')
    sorted_categories = data.get('categories')
    if not sorted_categories:
        sorted_categories = categories
        return categories

    kv = dict()
    for category in categories:
        kv[category['id']] = category

    visited = []
    new_categories = []
    for category in sorted_categories:
        if category['id'] in kv:
            visited.append(category['id'])
            new_categories.append(kv[category['id']])
    for id in kv:
        if id not in visited:
            new_categories.append(kv[id])
    data['categories'] = new_categories
    return new_categories


@plugin.route('/')
def index():
    items = []
    categories = update_categories()

    for category in categories:
        if getSetting('function.' + category['name']) == 'true':
            context_menu = [
                ('上移菜单项', 'RunPlugin(%s)' % plugin.url_for('move_up', name=category['name'])),
                ('下移菜单项', 'RunPlugin(%s)' % plugin.url_for('move_down', name=category['name'])),
                ('恢复默认菜单顺序', 'RunPlugin(%s)' % plugin.url_for('default_menus')),
            ]
            items.append({
                'label': localize(category['id']),
                'path': category['path'],
                'context_menu': context_menu,
            })
    if getSetting('enable_dash') == 'true' and not xbmc.getCondVisibility('System.HasAddon(inputstream.adaptive)'):
        result = xbmcgui.Dialog().yesno('安装插件', '使用 dash 功能需要安装 inputstream.adaptive 插件，是否安装？', '取消', '确认')
        if result:
            xbmc.executebuiltin('InstallAddon(inputstream.adaptive)')
        else:
            result = xbmcgui.Dialog().yesno('取消安装', '不使用 dash 请到设置中关闭', '取消', '确认')
            if result:
                plugin.open_settings()
    return items


@plugin.route('/move_up/<name>/')
def move_up(name):
    data = plugin.get_storage('data')
    categories = data['categories']
    index = next((i for i, item in enumerate(categories) if item['name'] == name), None)
    if index is not None and index > 0:
        categories[index], categories[index-1] = categories[index-1], categories[index]
    xbmc.executebuiltin('Container.Refresh')


@plugin.route('/move_down/<name>/')
def move_down(name):
    data = plugin.get_storage('data')
    categories = data['categories']
    index = next((i for i, item in enumerate(categories) if item['name'] == name), None)
    if index is not None and index < len(categories)-1:
        categories[index], categories[index+1] = categories[index+1], categories[index]
    xbmc.executebuiltin('Container.Refresh')


@plugin.route('/default_menus/')
def default_menus():
    data = plugin.get_storage('data')
    data['categories'] = get_categories()
    xbmc.executebuiltin('Container.Refresh')


@plugin.route('/open_settings/')
def open_settings():
    plugin.open_settings()


# ═══════════════════════════════════════════════════════════════════════════
# 热门 / 排行
# ═══════════════════════════════════════════════════════════════════════════

@plugin.route('/popular_history/')
def popular_history():
    videos = []
    res = get_api_data('/x/web-interface/popular/precious')
    if res['code'] != 0:
        return videos
    list = res['data']['list']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    return videos


@plugin.route('/popular_weekly/')
def popular_weekly():
    categories = []
    res = get_api_data('/x/web-interface/popular/series/list')
    if res['code'] != 0:
        return categories
    list = res['data']['list']
    for item in list:
        categories.append({
            'label': f"{item['name']} {item['subject']}",
            'path':plugin.url_for('weekly', number = item['number']),
        })
    return categories


@plugin.route('/weekly/<number>/')
def weekly(number):
    videos = []
    res = get_api_data('/x/web-interface/popular/series/one', {'number': number})
    if res['code'] != 0:
        return videos
    list = res['data']['list']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    return videos


@plugin.route('/ranking_list/')
def ranking_list():
    rankings = [['全站', 0], ['国创相关', 168], ['动画', 1], ['音乐', 3], ['舞蹈', 129], ['游戏', 4], ['知识', 36], ['科技', 188], ['运动', 234], ['汽车', 223], ['生活', 160], ['美食', 211], ['动物圈', 217], ['鬼畜', 119], ['时尚', 155], ['娱乐', 5], ['影视', 181]]
    return [{
        'label': r[0],
        'path': plugin.url_for('ranking', id=r[1])
    } for r in rankings]


@plugin.route('/ranking/<id>/')
def ranking(id):
    res = get_api_data('/x/web-interface/ranking/v2', {'rid': id})
    videos = []
    if (res['code'] != 0):
        return videos
    list = res['data']['list']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    return videos


@plugin.route('/related_videos/<id>/')
def related_videos(id):
    videos = []
    res = get_api_data('/x/web-interface/archive/related', {'bvid': id})
    if res['code'] != 0:
        notify_error(res)
        return videos
    list = res['data']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    return videos


# ═══════════════════════════════════════════════════════════════════════════
# 用户空间
# ═══════════════════════════════════════════════════════════════════════════

@plugin.route('/space_videos/<id>/<page>/')
def space_videos(id, page):
    videos = []
    if id == '0':
        notify('提示', '未登录')
        return videos
    ps = 50
    img_key, sub_key = getWbiKeys()
    data = encWbi(
        params = {
            'mid': id,
            'ps': ps,
            'pn': page,
            'order': 'pubdate',
            'tid': 0,
            'keyword': '',
            'platform': 'web'
        },
        img_key=img_key,
        sub_key=sub_key
    )
    res = get_api_data('/x/space/wbi/arc/search', data)
    if res['code'] != 0:
        notify_error(res)
        return videos

    list = res['data']['list']['vlist']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    if int(page) * ps < res['data']['page']['count']:
        videos.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('space_videos', id=id, page=int(page) + 1),
        })
    return videos


def _relation_list(api_path, route_name, id, page):
    users = []
    if id == '0':
        notify('提示', '未登录')
        return users
    ps = 50
    data = {
        'vmid': id,
        'ps': ps,
        'pn': page,
        'order': 'desc',
        'order_type': 'attention'
    }
    res = get_api_data(api_path, data)
    if res['code'] != 0:
        notify_error(res)
        return users
    list = res['data']['list']
    for item in list:
        if item['vip']['vipType'] == 0:
            uname = item['uname']
        else:
            uname = tag(item['uname'], 'pink')
        plot = f"UP: {item['uname']}\tID: {item['mid']}\n\n"
        if item['official_verify']['desc']:
            plot += tag(item['official_verify']['desc'], 'orange') + '\n'
        plot += '\n'
        if item['sign']:
            plot += f"签名: {item['sign']}"
        user = {
            'label': uname,
            'path': plugin.url_for('user', id=item['mid']),
            'icon': item['face'],
            'thumbnail': item['face'],
            'info': {
                'plot': plot
            },
        }
        users.append(user)
    if int(page) * 50 < res['data']['total']:
        users.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for(route_name, id=id, page=int(page) + 1),
        })
    return users


@plugin.route('/followings/<id>/<page>/')
def followings(id, page):
    return _relation_list('/x/relation/followings', 'followings', id, page)


@plugin.route('/followers/<id>/<page>/')
def followers(id, page):
    return _relation_list('/x/relation/followers', 'followers', id, page)


@plugin.route('/user/<id>/')
def user(id):
    return [
        {
            'label': '投稿的视频',
            'path': plugin.url_for('space_videos', id=id, page=1),
        },
        {
            'label': '直播间',
            'path': plugin.url_for('user_live_room', uid=id),
        },
        {
            'label': '合集和列表',
            'path': plugin.url_for('seasons_series', uid=id, page=1),
        },
        {
            'label': '关注列表',
            'path': plugin.url_for('followings', id=id, page=1),
        },
        {
            'label': '粉丝列表',
            'path': plugin.url_for('followers', id=get_uid(), page=1),
        },
        {
            'label': 'TA的订阅',
            'path': plugin.url_for('his_subscription', id=id),
        },
    ]


@plugin.route('/user_live_room/<uid>/')
def user_live_room(uid):
    res = get_api_data('/x/space/wbi/acc/info', {'mid': uid})
    if res['code'] != 0:
        return []
    item = res['data']
    if not item['live_room']:
        notify('提示', '直播间不存在')
        return []
    plot = f"UP: {item['name']}\tID: {item['mid']}\n房间号: {item['live_room']['roomid']}\n{item['live_room']['watched_show']['text_large']}"
    if item['live_room']['liveStatus'] == 1:
        label = f"{tag('【直播中】', 'red')}{item['name']} - {item['live_room']['title']}"
    else:
        label = f"{tag('【未直播】', 'grey')}{item['name']} - {item['live_room']['title']}"
    context_menu = [
        (f"转到UP: {item['name']}", f"Container.Update({plugin.url_for('user', id=item['mid'])})")
    ]
    return [{
        'label': label,
        'path': plugin.url_for('live', id=item['live_room']['roomid']),
        'is_playable': True,
        'icon': item["live_room"]["cover"],
        'thumbnail': item["live_room"]["cover"],
        'context_menu': context_menu,
        'info': {
            'mediatype': 'video',
            'title': item['live_room']['title'],
            'plot': plot
        }
    }]


# ═══════════════════════════════════════════════════════════════════════════
# 合集 / 系列 / 订阅
# ═══════════════════════════════════════════════════════════════════════════

@plugin.route('/seasons_series/<uid>/<page>/')
def seasons_series(uid, page):
    collections = []
    ps = 20
    data = {
        'mid': uid,
        'page_num': page,
        'page_size': ps
    }
    res = get_api_data('/x/polymer/web-space/seasons_series_list', data)
    if res['code'] != 0:
        notify_error(res)
        return collections
    list = res['data']['items_lists']['seasons_list']
    for item in list:
        collections.append({
            'label': item['meta']['name'],
            'path': plugin.url_for('seasons_and_series_detail', uid=uid, id=item['meta']['season_id'], type='season', page=1),
            'icon': item['meta']['cover'],
            'thumbnail': item['meta']['cover']
        })
    list = res['data']['items_lists']['series_list']
    for item in list:
        collections.append({
            'label': item['meta']['name'],
            'path': plugin.url_for('seasons_and_series_detail', uid=uid, id=item['meta']['series_id'], type='series', page=1),
            'icon': item['meta']['cover'],
            'thumbnail': item['meta']['cover']
        })
    if res['data']['items_lists']['page']['page_num'] * res['data']['items_lists']['page']['page_size'] < res['data']['items_lists']['page']['total']:
        collections.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('seasons_series', uid=uid, page=int(page)+1)
        })
    return collections


@plugin.route('/seasons_and_series_detail/<uid>/<id>/<type>/<page>/')
def seasons_and_series_detail(id, uid, type, page):
    videos = []
    ps = 100
    if type == 'season':
        url = '/x/polymer/space/seasons_archives_list'
        data = {
            'mid': uid,
            'season_id': id,
            'sort_reverse': False,
            'page_size': ps,
            'page_num': page
        }
    else:
        url = '/x/series/archives'
        data = {
            'mid': uid,
            'series_id': id,
            'sort': 'desc',
            'ps': ps,
            'pn': page
        }
    res = get_api_data(url, data)
    if res['code'] != 0:
        return videos
    list = res['data']['archives']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    if type == 'season':
        if res['data']['page']['page_num'] * res['data']['page']['page_size'] < res['data']['page']['total']:
            videos.append({
                'label': tag('下一页', 'yellow'),
                'path': plugin.url_for('seasons_and_series_detail', uid=uid, id=id, type=type, page=int(page)+1)
            })
    else:
        if res['data']['page']['num'] * res['data']['page']['size'] < res['data']['page']['total']:
            videos.append({
                'label': tag('下一页', 'yellow'),
                'path': plugin.url_for('seasons_and_series_detail', uid=uid, id=id, type=type, page=int(page)+1)
            })
    return videos


@plugin.route('/his_subscription/<id>/')
def his_subscription(id):
    return [
        {
            'label': '追番',
            'path': plugin.url_for('fav_series', uid=id, type=1)
        },
        {
            'label': '追剧',
            'path': plugin.url_for('fav_series', uid=id, type=2)
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# 搜索
# ═══════════════════════════════════════════════════════════════════════════

@plugin.route('/search_list/')
def search_list():
    kv = {
        'all': '综合搜索',
        'video': '视频搜索',
        'media_bangumi': '番剧搜索',
        'media_ft': '影视搜索',
        'live': '直播搜索',
        'bili_user': '用户搜索',
    }
    items = []
    for key in kv:
        items.append({
            'label': kv[key],
            'path': plugin.url_for('search', type=key, page=1)
        })
    items.append({
        'label': '清除搜索历史',
        'path': plugin.url_for('clear_search_history')
    })
    data = plugin.get_storage('data')
    search_history = data.get('search_history', [])
    for item in search_history:
        context_menu = [
            ('删除该搜索历史', f"RunPlugin({plugin.url_for('delete_keyword', type=item['type'], keyword=item['keyword'])})"),
        ]
        items.append({
            'label': f"[B]{tag(item['keyword'], 'pink')}[/B]{tag('(' + kv[item['type']] + ')', 'grey')}",
            'path': plugin.url_for('search_by_keyword', type=item['type'], keyword=item['keyword'], page=1),
            'context_menu': context_menu,
        })
    return items


@plugin.route('/delete_keyword/<type>/<keyword>/')
def delete_keyword(type, keyword):
    data = plugin.get_storage('data')
    search_history = data['search_history']
    for item in search_history:
        if item['keyword'] == keyword and item['type'] == type:
            search_history.remove(item)
            xbmc.executebuiltin('Container.Refresh')
            return


def add_keyword(type, keyword):
    data = plugin.get_storage('data')
    if 'search_history' not in data:
        data['search_history'] = []
    search_history = data['search_history']
    for item in search_history:
        if item["type"] == type and item["keyword"] == keyword:
            search_history.remove(item)
            search_history.insert(0, item)
            return
    search_history.insert(0, {"type": type, "keyword": keyword})


@plugin.route('/clear_search_history/')
def clear_search_history():
    data = plugin.get_storage('data')
    if 'search_history' in data:
        data['search_history'] = []
        xbmc.executebuiltin('Container.Refresh')


def get_search_list(list):
    videos = []
    for item in list:
        if item['type'] == 'video':
            item['title'] = clear_text(item['title'])
            video = get_video_item(item)
        elif item['type'] == 'media_bangumi' or item['type'] == 'media_ft':
            if item['type'] == 'media_bangumi':
                cv_type = '声优'
            else:
                cv_type = '出演'
            plot = f"{tag(clear_text(item['title']), 'pink')} {item['index_show']}\n\n"
            plot += f"地区: {item['areas']}\n"
            plot += cv_type + ': ' + clear_text(item['cv']).replace('\n', '/') + '\n'
            plot += item['staff'] + '\n'
            plot += '\n'
            plot += item['desc']
            video = {
                'label': tag('【' + item['season_type_name'] + '】', 'pink') + clear_text(item['title']),
                'path': plugin.url_for('bangumi', type='season_id' ,id=item['season_id']),
                'icon': item['cover'],
                'thumbnail': item['cover'],
                'info': {
                    'plot': plot
                }
            }
        elif item['type'] == 'bili_user':
            plot = f"UP: {item['uname']}\tLV{item['level']}\n"
            plot += f"ID: {item['mid']}\n"
            plot += f"粉丝: {convert_number(item['fans'])}\n\n"
            plot += f"签名: {item['usign']}\n"
            video = {
                'label': f"{tag('【用户】')}{item['uname']}",
                'path': plugin.url_for('user', id=item['mid']),
                'icon': item['upic'],
                'thumbnail': item['upic'],
                'info': {
                    'plot': plot
                }
            }
        else:
            continue
        videos.append(video)
    return videos


@plugin.route('/search/<type>/<page>/')
def search(type, page):
    videos = []
    keyboard = xbmc.Keyboard('', '请输入搜索内容')
    keyboard.doModal()
    if (keyboard.isConfirmed()):
        keyword = keyboard.getText()
    else:
        return videos

    if not keyword.strip():
        return videos
    add_keyword(type, keyword)
    return search_by_keyword(type, keyword, page)


def _build_live_search_item(item, has_title=True):
    """构建直播搜索结果列表项，has_title 表示 item 是否包含 title 字段"""
    uname = clear_text(item['uname'])
    plot = f"UP: {uname}\tID: {item['uid']}\n房间号: {item['roomid']}\n\n"
    context_menu = [
        (f"转到UP: {uname}", f"Container.Update({plugin.url_for('user', id=item['uid'])})")
    ]
    if has_title:
        title = clear_text(item['title'])
        title_display = item['title'].replace('<em class=\"keyword\">', '[COLOR pink]').replace('</em>', '[/COLOR]')
        if item['live_status'] == 1:
            label = tag('【直播中】', 'red') + item['uname'] + ' - ' + title_display
        else:
            label = tag('【未直播】', 'grey') + item['uname'] + ' - ' + title_display
    else:
        title = uname
        name_display = item['uname'].replace('<em class=\"keyword\">', '[COLOR pink]').replace('</em>', '[/COLOR]')
        if item['live_status'] == 1:
            label = tag('【直播中】', 'red') + name_display
        else:
            label = tag('【未直播】', 'grey') + name_display
    return {
        'label': label,
        'path': plugin.url_for('live', id=item['roomid']),
        'is_playable': True,
        'icon': item['uface'],
        'thumbnail': item['uface'],
        'context_menu': context_menu,
        'info': {
            'mediatype': 'video',
            'title': title,
            'plot': plot,
        },
        'info_type': 'video'
    }


@plugin.route('/search_by_keyword/<type>/<keyword>/<page>/')
def search_by_keyword(type, keyword, page):
    videos = []
    data = {
        'page': page,
        'page_size': 50,
        'platform': 'pc',
        'keyword': keyword,
    }

    if type == 'all':
        url = '/x/web-interface/wbi/search/all/v2'
    else:
        url = '/x/web-interface/wbi/search/type'
        data['search_type'] = type

    # 搜索类 API 强制要求 WBI 签名
    img_key, sub_key = getWbiKeys()
    data = encWbi(data, img_key, sub_key)
    res = get_api_data(url, data)
    if res['code'] != 0:
        return videos
    if 'result' not in res['data']:
        return videos
    list = res['data']['result']
    if type == 'all':
        for result in list:
            if result['result_type'] in ['video', 'media_bangumi', 'media_ft', 'bili_user']:
                videos.extend(get_search_list(result['data']))
    else:
        if type == 'live':
            for item in res['data']['result']['live_user']:
                videos.append(_build_live_search_item(item, has_title=False))
            for item in res['data']['result']['live_room']:
                videos.append(_build_live_search_item(item, has_title=True))
        else:
            videos.extend(get_search_list(list))
    if res['data']['page'] < res['data']['numPages']:
        videos.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('search_by_keyword', type=type, keyword=keyword , page=int(page)+1)
        })
    return videos


# ═══════════════════════════════════════════════════════════════════════════
# 直播
# ═══════════════════════════════════════════════════════════════════════════

# B站直播分区数据（静态快照，如需更新请同步 B站 API）
_LIVE_AREAS = {'2': {'id': '2', 'name': '网游', 'list': [{'id': '86', 'name': '英雄联盟'}, {'id': '92', 'name': 'DOTA2'}, {'id': '89', 'name': 'CS:GO'}, {'id': '240', 'name': 'APEX英雄'}, {'id': '666', 'name': '永劫无间'}, {'id': '88', 'name': '穿越火线'}, {'id': '87', 'name': '守望先锋'}, {'id': '80', 'name': '吃鸡行动'}, {'id': '252', 'name': '逃离塔科夫'}, {'id': '695', 'name': '传奇'}, {'id': '78', 'name': 'DNF'}, {'id': '575', 'name': '生死狙击2'}, {'id': '599', 'name': '洛奇英雄传'}, {'id': '102', 'name': '最终幻想14'}, {'id': '249', 'name': '星际战甲'}, {'id': '710', 'name': '梦三国'}, {'id': '690', 'name': '英魂之刃'}, {'id': '82', 'name': '剑网3'}, {'id': '691', 'name': '铁甲雄兵'}, {'id': '300', 'name': '封印者'}, {'id': '653', 'name': '新天龙八部'}, {'id': '667', 'name': '赛尔号'}, {'id': '668', 'name': '造梦西游'}, {'id': '669', 'name': '洛克王国'}, {'id': '670', 'name': '问道'}, {'id': '654', 'name': '诛仙世界'}, {'id': '652', 'name': '大话西游'}, {'id': '683', 'name': '奇迹MU'}, {'id': '684', 'name': '永恒之塔'}, {'id': '685', 'name': 'QQ三国'}, {'id': '677', 'name': '人间地狱'}, {'id': '329', 'name': 'VALORANT'}, {'id': '686', 'name': '彩虹岛'}, {'id': '663', 'name': '洛奇'}, {'id': '664', 'name': '跑跑卡丁车'}, {'id': '658', 'name': '星际公民'}, {'id': '659', 'name': 'Squad战术小队'}, {'id': '629', 'name': '反恐精英Online'}, {'id': '648', 'name': '风暴奇侠'}, {'id': '642', 'name': '装甲战争'}, {'id': '590', 'name': '失落的方舟'}, {'id': '639', 'name': '阿尔比恩'}, {'id': '600', 'name': '猎杀对决'}, {'id': '472', 'name': 'CFHD '}, {'id': '650', 'name': '骑士精神2'}, {'id': '680', 'name': '超击突破'}, {'id': '634', 'name': '武装突袭'}, {'id': '84', 'name': '300英雄'}, {'id': '91', 'name': '炉石传说'}, {'id': '499', 'name': '剑网3缘起'}, {'id': '649', 'name': '街头篮球'}, {'id': '601', 'name': '综合射击'}, {'id': '505', 'name': '剑灵'}, {'id': '651', 'name': '艾尔之光'}, {'id': '632', 'name': '黑色沙漠'}, {'id': '596', 'name': ' 天涯明月刀'}, {'id': '519', 'name': '超激斗梦境'}, {'id': '574', 'name': '冒险岛'}, {'id': '487', 'name': '逆战'}, {'id': '181', 'name': '魔兽争霸3'}, {'id': '610', 'name': 'QQ飞车'}, {'id': '83', 'name': '魔兽世界'}, {'id': '388', 'name': 'FIFA ONLINE 4'}, {'id': '581', 'name': 'NBA2KOL2'}, {'id': '318', 'name': '使命召唤:战区'}, {'id': '656', 'name': 'VRChat'}, {'id': '115', 'name': '坦克世界'}, {'id': '248', 'name': '战舰世界'}, {'id': '316', 'name': '战争雷霆'}, {'id': '383', 'name': '战意'}, {'id': '114', 'name': '风暴英雄'}, {'id': '93', 'name': '星际争霸2'}, {'id': '239', 'name': '刀塔自走 棋'}, {'id': '164', 'name': '堡垒之夜'}, {'id': '251', 'name': '枪神纪'}, {'id': '81', 'name': '三国杀'}, {'id': '112', 'name': '龙之谷'}, {'id': '173', 'name': '古剑奇谭OL'}, {'id': '176', 'name': '幻想全明星'}, {'id': '288', 'name': '怀旧网游'}, {'id': '298', 'name': '新游前瞻'}, {'id': '331', 'name': '星战前夜：晨曦'}, {'id': '350', 'name': '梦幻西游端游'}, {'id': '551', 'name': '流放之路'}, {'id': '633', 'name': 'FPS沙盒'}, {'id': '459', 'name': '永恒轮回'}, {'id': '607', 'name': '激战2'}, {'id': '107', 'name': '其他网游'}]}, '3': {'id': '3', 'name': '手游', 'list': [{'id': '35', 'name': '王者荣耀'}, {'id': '256', 'name': '和平精英'}, {'id': '395', 'name': 'LOL手游'}, {'id': '321', 'name': '原神'}, {'id': '163', 'name': '第五人格'}, {'id': '255', 'name': '明日方舟'}, {'id': '474', 'name': '哈利波特：魔法觉醒 '}, {'id': '550', 'name': '幻塔'}, {'id': '514', 'name': '金铲铲之战'}, {'id': '506', 'name': 'APEX手游'}, {'id': '598', 'name': '深空之眼'}, {'id': '675', 'name': '无期迷途'}, {'id': '687', 'name': '光遇'}, {'id': '717', 'name': '跃迁旅人'}, {'id': '725', 'name': '环形战争'}, {'id': '689', 'name': '香肠派对'}, {'id': '645', 'name': '猫之城'}, {'id': '644', 'name': '玛娜希斯回响'}, {'id': '386', 'name': '使命召唤手游'}, {'id': '615', 'name': '黑色沙漠手游'}, {'id': '40', 'name': '崩坏3'}, {'id': '407', 'name': '游戏王：决斗链接'}, {'id': '303', 'name': '游戏王'}, {'id': '724', 'name': 'JJ斗地主'}, {'id': '571', 'name': '蛋仔派对'}, {'id': '36', 'name': '阴阳师'}, {'id': '719', 'name': '欢乐斗地主'}, {'id': '718', 'name': '空之要塞：启航'}, {'id': '292', 'name': '火影忍者手游'}, {'id': '37', 'name': 'Fate/GO'}, {'id': '354', 'name': '综合棋牌'}, {'id': '154', 'name': 'QQ飞车手游'}, {'id': '140', 'name': '决战！平安京'}, {'id': '41', 'name': '狼人杀'}, {'id': '352', 'name': '三国杀移动版'}, {'id': '113', 'name': '碧蓝航线'}, {'id': '156', 'name': '影之诗'}, {'id': '189', 'name': '明日之后'}, {'id': '50', 'name': '部落冲突: 皇室战争'}, {'id': '661', 'name': '奥比岛手游'}, {'id': '704', 'name': '盾之勇者成名录：浪潮'}, {'id': '214', 'name': '雀姬'}, {'id': '330', 'name': ' 公主连结Re:Dive'}, {'id': '343', 'name': 'DNF手游'}, {'id': '641', 'name': 'FIFA足球世界'}, {'id': '258', 'name': 'BanG Dream'}, {'id': '469', 'name': '荒野乱斗'}, {'id': '333', 'name': 'CF手游'}, {'id': '293', 'name': '战双帕弥什'}, {'id': '389', 'name': '天涯明月刀手游'}, {'id': '42', 'name': '解密 游戏'}, {'id': '576', 'name': '恋爱养成游戏'}, {'id': '492', 'name': '暗黑破坏神：不朽'}, {'id': '502', 'name': '暗区突围'}, {'id': '265', 'name': '跑 跑卡丁车手游'}, {'id': '212', 'name': '非人学园'}, {'id': '286', 'name': '百闻牌'}, {'id': '269', 'name': '猫和老鼠手游'}, {'id': '442', 'name': '坎公 骑冠剑'}, {'id': '203', 'name': '忍者必须死3'}, {'id': '342', 'name': '梦幻西游手游'}, {'id': '504', 'name': '航海王热血航线'}, {'id': '39', 'name': ' 少女前线'}, {'id': '688', 'name': '300大作战'}, {'id': '525', 'name': '少女前线：云图计划'}, {'id': '478', 'name': '漫威超级战争'}, {'id': '464', 'name': '摩尔庄园手游'}, {'id': '493', 'name': '宝可梦大集结'}, {'id': '473', 'name': '小动物之星'}, {'id': '448', 'name': '天地劫：幽城再临'}, {'id': '511', 'name': '漫威对决'}, {'id': '538', 'name': ' 东方归言录'}, {'id': '178', 'name': '梦幻模拟战'}, {'id': '643', 'name': '时空猎人3'}, {'id': '613', 'name': '重返帝国'}, {'id': '679', 'name': '休闲小游戏'}, {'id': '98', 'name': '其他手游'}, {'id': '274', 'name': '新游评测'}]}, '6': {'id': '6', 'name': '单机游戏', 'list': [{'id': '236', 'name': '主机游戏'}, {'id': '579', 'name': '战神'}, {'id': '216', 'name': '我的世界'}, {'id': '726', 'name': '大多数'}, {'id': '283', 'name': '独立游戏'}, {'id': '237', 'name': '怀旧游戏'}, {'id': '460', 'name': '弹幕互动玩法'}, {'id': '722', 'name': '互动派对'}, {'id': '276', 'name': '恐怖游戏'}, {'id': '693', 'name': '红色警戒2'}, {'id': '570', 'name': '策略游戏'}, {'id': '723', 'name': '战锤40K:暗潮'}, {'id': '707', 'name': '禁闭求生'}, {'id': '694', 'name': '斯普拉遁3'}, {'id': '700', 'name': '卧龙：苍天陨落'}, {'id': '282', 'name': '使命召唤19'}, {'id': '665', 'name': '异度神剑'}, {'id': '555', 'name': '艾尔登法环'}, {'id': '636', 'name': '聚会游戏'}, {'id': '716', 'name': '哥谭骑士'}, {'id': '277', 'name': '命运2'}, {'id': '630', 'name': '沙石镇时光'}, {'id': '591', 'name': 'Dread Hunger'}, {'id': '721', 'name': '生化危机'}, {'id': '714', 'name': '失落 迷城：群星的诅咒'}, {'id': '597', 'name': '战地风云'}, {'id': '720', 'name': '宝可梦集换式卡牌游戏'}, {'id': '612', 'name': '幽灵线：东京'}, {'id': '357', 'name': '糖豆人'}, {'id': '586', 'name': '消逝的光芒2'}, {'id': '245', 'name': '只狼'}, {'id': '578', 'name': '怪物猎人'}, {'id': '218', 'name': ' 饥荒'}, {'id': '228', 'name': '精灵宝可梦'}, {'id': '708', 'name': 'FIFA23'}, {'id': '582', 'name': '暖雪'}, {'id': '594', 'name': '全面战争：战锤3'}, {'id': '580', 'name': '彩虹六号：异种'}, {'id': '302', 'name': 'FORZA 极限竞速'}, {'id': '362', 'name': 'NBA2K'}, {'id': '548', 'name': '帝国时代4'}, {'id': '559', 'name': '光环：无限'}, {'id': '537', 'name': '孤岛惊魂6'}, {'id': '309', 'name': '植物大战僵尸'}, {'id': '540', 'name': '仙剑奇侠传七'}, {'id': '223', 'name': '灵魂筹码'}, {'id': '433', 'name': '格斗游戏'}, {'id': '226', 'name': '荒野大镖客2'}, {'id': '426', 'name': '重生细胞'}, {'id': '227', 'name': '刺客信条'}, {'id': '387', 'name': '恐鬼症'}, {'id': '219', 'name': '以撒'}, {'id': '446', 'name': '双人成行'}, {'id': '295', 'name': '方 舟'}, {'id': '313', 'name': '仁王2'}, {'id': '244', 'name': '鬼泣5'}, {'id': '727', 'name': '黑白莫比乌斯 岁月的代价'}, {'id': '364', 'name': '枪火重生'}, {'id': '341', 'name': '盗贼之海'}, {'id': '507', 'name': '胡闹厨房'}, {'id': '500', 'name': '体育游戏'}, {'id': '439', 'name': '恐惧之间'}, {'id': '308', 'name': '塞尔达'}, {'id': '261', 'name': '马力欧制造2'}, {'id': '243', 'name': '全境封锁2'}, {'id': '326', 'name': '骑马与砍杀'}, {'id': '270', 'name': '人类一败涂地'}, {'id': '424', 'name': '鬼谷八荒'}, {'id': '273', 'name': '无主之地3'}, {'id': '220', 'name': '辐射76'}, {'id': '257', 'name': '全面战争'}, {'id': '463', 'name': '亿万僵尸'}, {'id': '535', 'name': '暗黑破坏神2'}, {'id': '583', 'name': '文字游戏'}, {'id': '592', 'name': '恋爱模 拟游戏'}, {'id': '593', 'name': '泰拉瑞亚'}, {'id': '441', 'name': '雨中冒险2'}, {'id': '678', 'name': '游戏速通'}, {'id': '681', 'name': '摔角城大乱斗'}, {'id': '692', 'name': '勇敢的哈克'}, {'id': '698', 'name': ' 审判系列'}, {'id': '728', 'name': '蜀山：初章'}, {'id': '235', 'name': '其他单机'}]}, '1': {'id': '1', 'name': '娱乐', 'list': [{'id': '21', 'name': '视频唱见'}, {'id': '530', 'name': '萌宅领域'}, {'id': '145', 'name': '视频聊天'}, {'id': '207', 'name': '舞见'}, {'id': '706', 'name': '情感'}, {'id': '123', 'name': '户外'}, {'id': '399', 'name': '日常'}]}, '5': {'id': '5', 'name': '电台', 'list': [{'id': '190', 'name': '唱见电台'}, {'id': '192', 'name': '聊天电台'}, {'id': '193', 'name': '配音'}]}, '9': {'id': '9', 'name': '虚拟主播', 'list': [{'id': '371', 'name': '虚拟主播'}, {'id': '697', 'name': '3D虚拟主播'}]}, '10': {'id': '10', 'name': '生活', 'list': [{'id': '646', 'name': '生活分享'}, {'id': '628', 'name': '运动'}, {'id': '624', 'name': '搞笑'}, {'id': '627', 'name': '手工绘画'}, {'id': '369', 'name': '萌宠'}, {'id': '367', 'name': '美食'}, {'id': '378', 'name': '时尚'}, {'id': '33', 'name': '影音馆'}]}, '11': {'id': '11', 'name': '知识', 'list': [{'id': '376', 'name': '社科法律心理'}, {'id': '702', 'name': '人文历史'}, {'id': '372', 'name': '校园学习'}, {'id': '377', 'name': '职场·技能'}, {'id': '375', 'name': ' 科技'}, {'id': '701', 'name': '科学科普'}]}, '13': {'id': '13', 'name': '赛事', 'list': [{'id': '561', 'name': '游戏赛事'}, {'id': '562', 'name': '体育赛事'}, {'id': '563', 'name': '赛事综合'}]}}


@plugin.route('/live_areas/<level>/<id>/')
def live_areas(level, id):
    areas = _LIVE_AREAS
    if level == '1':
        return [{
            'label': areas[area_id]['name'],
            'path': plugin.url_for('live_areas', level=2, id=area_id),
        } for area_id in areas]

    childran_areas = areas[id]['list']
    items = [{
        'label': areas[id]['name'],
        'path': plugin.url_for('live_area', pid=id, id=0, page=1),
    }]
    items.extend([{
        'label': area['name'],
        'path': plugin.url_for('live_area', pid=id, id=area['id'], page=1),
    } for area in childran_areas])
    return items


@plugin.route('/live_area/<pid>/<id>/<page>/')
def live_area(pid, id, page):
    lives = []
    page_size = 30
    data = {
        'platform': 'web',
        'parent_area_id': pid,
        'area_id': id,
        'page': page,
        'page_size': page_size
    }
    res = fetch_url('https://api.live.bilibili.com/room/v3/area/getRoomList?' + urlencode(data))
    if res['code'] != 0:
        return lives
    list = res['data']['list']
    for item in list:
        plot = f"UP: {item['uname']}\tID: {item['uid']}\n房间号: {item['roomid']}\n\n"
        if item['verify']['desc']:
            plot += tag(item['verify']['desc'], 'orange') + '\n\n'
        plot += item['title']
        context_menu = [
            (f"转到UP: {item['uname']}", f"Container.Update({plugin.url_for('user', id=item['uid'])})")
        ]
        live = {
            'label': item['uname'] + ' - ' + item['title'],
            'path': plugin.url_for('live', id=item['roomid']),
            'is_playable': True,
            'icon': item['cover'],
            'thumbnail': item['cover'],
            'context_menu': context_menu,
            'info': {
                'mediatype': 'video',
                'title': item['title'],
                'plot': plot,
            },
            'info_type': 'video'
        }
        lives.append(live)
    if page_size * int(page) < res['data']['count']:
        lives.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('live_area', pid=pid, id=id, page=int(page)+1)
        })
    return lives


@plugin.route('/followingLive/<page>/')
def followingLive(page):
    page = int(page)
    items = []
    if get_uid() == '0':
        notify('提示', '未登录')
        return items
    res = fetch_url(f'https://api.live.bilibili.com/xlive/web-ucenter/user/following?page={page}&page_size=10&platform=web')
    if res['code'] != 0:
        notify_error(res)
        return items
    list = res['data']['list']
    for live in list:
        # B站关注API可能返回 room_id（真实房间号）和 roomid（短号），优先使用 room_id
        room_id = live.get('room_id', live.get('roomid', 0))
        if live['live_status'] == 1:
            label = tag('【直播中】 ', 'red')
        else:
            label = tag('【未开播】 ', 'grey')
        label += live['uname'] + ' - ' +  live['title']
        context_menu = [
            (f"转到UP: {live['uname']}", f"Container.Update({plugin.url_for('user', id=live['uid'])})")
        ]
        item = {
            'label': label,
            'path': plugin.url_for('live', id=room_id),
            'is_playable': True if live['live_status'] == 1 else False,
            'icon': live['face'],
            'thumbnail': live['face'],
            'context_menu': context_menu,
            'info': {
                'mediatype': 'video',
                'title': live['title'],
                'plot': f"UP: {live['uname']}\tID: {live['uid']}\n房间号: {room_id}\n\n{live['title']}",
            },
            'info_type': 'video'
        }
        items.append(item)
    if page < res['data']['totalPage']:
        items.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('followingLive', page=page + 1)
        })
    return items


@plugin.route('/live/<id>/')
def live(id):
    """FLV 最稳，优先 FLV-only API；无 FLV 则降级 fmp4 + ffmpegdirect"""
    qn = getSetting('live_resolution')
    ref = 'https://www.bilibili.com'
    ua  = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    hdr = 'Referer=%s&User-Agent=%s&Origin=%s' % (ref, ua, ref)

    def _fetch(stream_qn, fmt_filter):
        params = (
            'room_id={}&no_playurl=0&mask=1&qn={}&platform=web'
            '&protocol=0,1&format={}&codec=0,1,2'
            '&dolby=5&ptype=8&panorama=1'
        ).format(id, stream_qn, fmt_filter)
        r = fetch_url('https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo?' + params)
        if r['code'] != 0 or not r.get('data', {}).get('playurl_info'):
            return None
        return r['data']['playurl_info']['playurl']['stream']

    # ── 强制 FLV：多 QN 降级尝试 ──
    streams = None
    for try_qn in (qn, 400, 250, 150, 80):
        streams = _fetch(try_qn, '0')
        if streams:
            break
    # ── 无 FLV → 回退全部格式 ──
    if not streams:
        streams = _fetch(qn, '0,1,2')
    if not streams:
        xbmc.log('[live] no playurl for room_id=%s' % id, xbmc.LOGERROR)
        return

    best = choose_live_resolution(streams)
    if not best:
        xbmc.log('[live] no codec room_id=%s' % id, xbmc.LOGERROR)
        return

    urls       = best.get('urls', [])
    master_url = best.get('master_url', '')
    fmt_name   = best.get('format_name', '')
    codec_name = best.get('codec_name', '')

    chosen = urls[0] if urls else (master_url or None)
    if not chosen:
        xbmc.log('[live] no url room_id=%s' % id, xbmc.LOGERROR)
        return

    xbmc.log('[live] %s/%s room_id=%s' % (fmt_name, codec_name, id), xbmc.LOGINFO)

    # ── 直播弹幕 ──
    live_ass = None
    if getSetting('enable_live_danmaku') == 'true':
        uid = get_uid()
        cookie = get_cookie()
        live_ass, _ = start_live_danmaku(id, uid, cookie)

    # FLV → ffmpeg 管道直连（稳定）
    if fmt_name == 'flv':
        plugin.set_resolved_url({
            'path': '%s|%s' % (chosen, hdr),
            'is_playable': True,
            'is_live': True,
        }, subtitles=live_ass)
        return

    # fmp4/ts → inputstream.ffmpegdirect（原生 ffmpeg + reconnect）
    has_ffdg = xbmc.getCondVisibility('System.HasAddon(inputstream.ffmpegdirect)')
    if has_ffdg:
        xbmc.log('[live] ffmpegdirect for room_id=%s' % id, xbmc.LOGINFO)
        plugin.set_resolved_url({
            'path': chosen,
            'is_playable': True,
            'is_live': True,
            'properties': {
                'inputstream': 'inputstream.ffmpegdirect',
                'inputstream.ffmpegdirect.is_realtime_stream': 'true',
                'inputstream.ffmpegdirect.manifest_type': 'hls',
                'inputstream.ffmpegdirect.stream_headers': hdr,
                'inputstream.ffmpegdirect.reconnect': '1',
                'inputstream.ffmpegdirect.reconnect_streamed': '1',
                'inputstream.ffmpegdirect.reconnect_delay_max': '5',
            }
        }, subtitles=live_ass)
        return

    # 回退 ffmpeg 管道
    xbmc.log('[live] ffmpeg pipe fallback for room_id=%s' % id, xbmc.LOGWARNING)
    plugin.set_resolved_url({
        'path': '%s|%s' % (chosen, hdr),
        'is_playable': True,
        'is_live': True,
    }, subtitles=live_ass)


# ═══════════════════════════════════════════════════════════════════════════
# 收藏 / 稍后再看 / 历史
# ═══════════════════════════════════════════════════════════════════════════

@plugin.route('/my_collection/')
def my_collection():
    uid= get_uid()
    if uid == '0':
        notify('提示', '未登录')
        return []
    items = [
        {
            'label': '我的收藏夹',
            'path': plugin.url_for('favlist_list', uid=uid),
        },
        {
            'label': '追番',
            'path': plugin.url_for('fav_series', uid=uid, type=1)
        },
        {
            'label': '追剧',
            'path': plugin.url_for('fav_series', uid=uid, type=2)
        },
    ]
    return items


@plugin.route('/fav_series/<uid>/<type>/')
def fav_series(uid, type):
    videos = []
    if uid == '0':
        return videos

    res = get_api_data('/x/space/bangumi/follow/list', {'vmid': uid, 'type': type})
    if res['code'] != 0:
        return videos

    list = res['data']['list']
    for item in list:
        label = item['title']
        if item['season_type_name']:
            label = tag('【' + item['season_type_name'] + '】', 'pink') + label
        plot = f"{tag(item['title'], 'pink')}\t{item['new_ep']['index_show']}\n"
        if item['publish']['release_date_show']:
            plot += f"发行时间: {item['publish']['release_date_show']}\n"
        if item['styles']:
            plot += f"类型: {tag(' '.join(item['styles']), 'blue')}\n"
        if item['areas']:
            plot += f"地区: {' '.join( [area['name'] for area in item['areas']])}\n"
        state = format_stat(item)
        if state:
            plot += f"{state[:-3]}\n"
        plot += f"\n{item['summary']}"
        video = {
            'label': label,
            'path': plugin.url_for('bangumi', type='season_id' ,id=item['season_id']),
            'icon': item['cover'],
            'thumbnail': item['cover'],
            'info': {
                'plot': plot
            }
        }
        videos.append(video)
    return videos


@plugin.route('/favlist_list/<uid>/')
def favlist_list(uid):
    videos = []
    if uid == '0':
        return videos

    res = get_api_data('/x/v3/fav/folder/created/list-all', {'up_mid': uid})

    if res['code'] != 0:
        return videos

    list = res['data']['list']
    for item in list:
        video = {
            'label': item['title'],
            'path': plugin.url_for('favlist', id=item['id'], page=1)
        }
        videos.append(video)
    return videos


@plugin.route('/favlist/<id>/<page>/')
def favlist(id, page):
    videos = []
    data = {
        'media_id': id,
        'ps': 20,
        'pn': page,
        'keyword': '',
        'order': 'mtime',
        'tid': '0'
    }
    res = get_api_data('/x/v3/fav/resource/list', data)
    if res['code'] != 0:
        return videos
    list = res['data']['medias']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    if res['data']['has_more']:
        videos.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('favlist', id=id, page=int(page)+1)
        })
    return videos


@plugin.route('/watchlater/')
def watchlater():
    videos = []
    url = '/x/v2/history/toview'
    res = get_api_data(url)

    if res['code'] != 0:
        notify_error(res)
        return videos
    list = res['data']['list']
    for item in list:
        video = get_video_item(item)
        if video:
            videos.append(video)
    return videos


@plugin.route('/history/<time>/')
def history(time):
    videos = []
    url = '/x/web-interface/history/cursor'
    data = {
        'view_at': time,
        'ps': 20,
    }
    res = raw_get_api_data(url, data)
    if res['code'] != 0:
        notify_error(res)
        return videos
    list = res['data']['list']
    for item in list:
        if item['videos'] >=1:
            video = get_video_item(item)
            if not video:
                continue
        else:
            if item['history']['business'] == 'live':
                if item['live_status'] == 1:
                    label = tag('【直播中】 ', 'red')
                else:
                    label = tag('【未开播】 ', 'grey')
                label += item['author_name'] + ' - ' +  item['title']
                context_menu = [
                    (f"转到UP: {item['author_name']}", f"Container.Update({plugin.url_for('user', id=item['author_mid'])})")
                ]
                video = {
                    'label': label,
                    'path': plugin.url_for('live', id=item['kid']),
                    'is_playable': True,
                    'icon': item['cover'],
                    'thumbnail': item['cover'],
                    'context_menu': context_menu,
                    'info': {
                        'mediatype': 'video',
                        'title': item['title'],
                    },
                    'info_type': 'video'
                }
            elif item['history']['business'] == 'pgc':
                if item['badge']:
                    label = tag('【' + item['badge'] + '】', 'pink') + item['title']
                else:
                    label = item['title']
                if 'show_title' in item and item['show_title']:
                    label += '\n' + tag(item['show_title'], 'grey')
                video = {
                    'label': label,
                    'path': plugin.url_for('bangumi', type='ep_id', id=item['history']['epid']),
                    'icon': item['cover'],
                    'thumbnail': item['cover'],
                    'info_type': 'video'
                }
            else:
                continue
        videos.append(video)
    videos.append({
        'label': tag('下一页', 'yellow'),
        'path': plugin.url_for('history', time=res['data']['cursor']['view_at'])
    })
    return videos


# ═══════════════════════════════════════════════════════════════════════════
# 首页推荐 / 动态
# ═══════════════════════════════════════════════════════════════════════════

@plugin.route('/home/<page>/')
def home(page):
    videos = []
    page = int(page)
    url = '/x/web-interface/index/top/feed/rcmd'
    data = {
        'y_num': 3,
        'fresh_type': 4,
        'feed_version': 'V8',
        'fresh_idx_1h': page,
        'fetch_row': 3 * page + 1,
        'fresh_idx': page,
        'brush': page,
        'homepage_ver': 1,
        'ps': 12,
        'last_y_num': 4,
        'outside_trigger': ''
    }
    res = get_api_data(url, data)

    if res['code'] != 0:
        return videos

    list = res['data']['item']
    for item in list:
        if not item['bvid']:
            continue
        if 'live.bilibili.com' in item['uri']:
            if (item['room_info']['live_status'] == 1):
                label = tag('【直播中】', 'red') + item['owner']['name'] + ' - ' + item['title']
            else:
                label = tag('【未直播】', 'grey') + item['owner']['name'] + ' - ' + item['title']
            plot = f"UP: {item['owner']['name']}\tID: {item['owner']['mid']}\n房间号: {item['room_info']['room_id']}\n{item['watched_show']['text_large']}\n分区: {item['area']['area_name']}"
            context_menu = [
                (f"转到UP: {item['owner']['name']}", f"Container.Update({plugin.url_for('user', id=item['owner']['mid'])})")
            ]
            video = {
                'label': label,
                'path': plugin.url_for('live', id=item['url'].split('/')[-1]),
                'is_playable': True,
                'icon': item['pic'],
                'thumbnail': item['pic'],
                'context_menu': context_menu,
                'info': {
                    'plot': plot
                }
            }
        else:
            video = get_video_item(item)
            if not video:
                continue
        videos.append(video)
    videos.append({
        'label': tag('下一页', 'yellow'),
        'path': plugin.url_for('home', page=page+1)
    })
    return videos


# B站动态分区数据（静态快照）
_DYNAMIC_REGIONS = [['番剧', 13], ['- 连载动画', 33], ['- 完结动画', 32], ['- 资讯', 51], ['- 官方延伸', 152], ['电影', 23], ['国创', 167], ['- 国产动画', 153], ['- 国产原创相关', 168], ['- 布袋戏', 169], ['- 动态漫·广播剧', 195], ['- 资讯', 51], ['电视剧', 11], ['纪录片', 177], ['动画', 1], ['- MAD·AMV', 24], ['- MMD·3D', 25], ['- 短片·手书·配音', 47], ['- 手办·模玩', 210], ['- 特摄', 86], ['- 动漫杂谈', 253], ['- 综合', 27], ['游戏', 4], ['- 单机游戏', 17], ['- 电 子竞技', 171], ['- 手机游戏', 172], ['- 网络游戏', 65], ['- 桌游棋牌', 173], ['- GMV', 121], ['- 音游', 136], ['- Mugen', 19], ['鬼畜', 119], ['- 鬼畜 调教', 22], ['- 音MAD', 26], ['- 人力VOCALOID', 126], ['- 鬼畜剧场', 216], ['- 教程演示', 127], ['音乐', 3], ['- 原创音乐', 28], ['- 翻唱', 31], ['- 演奏', 59], ['- VOCALOID·UTAU', 30], ['- 音乐现场', 29], ['- MV', 193], ['- 乐评盘点', 243], ['- 音乐教学', 244], ['- 音乐综合', 130], ['舞蹈', 129], ['- 宅舞', 20], ['- 街舞', 198], ['- 明星舞蹈', 199], ['- 中国舞', 200], ['- 舞蹈综合', 154], ['- 舞蹈教程', 156], ['影视', 181], ['- 影视杂谈', 182], ['- 影视剪辑', 183], ['- 小剧场', 85], ['- 预告·资讯', 184], ['娱乐', 5], ['- 综艺', 71], ['- 娱乐杂谈', 241], ['- 粉丝创作', 242], ['- 明星综合', 137], ['知识', 36], ['- 科学科普', 201], ['- 社科·法律·心理', 124], ['- 人文历史', 228], ['- 财经商业', 207], ['- 校园学习', 208], ['- 职业职场', 209], ['- 设计·创意', 229], ['- 野生技能协会', 122], ['科技', 188], ['- 数码', 95], ['- 软件应用', 230], ['- 计算机技术', 231], ['- 科工机械', 232], ['资讯', 51], ['- 热点', 203], ['- 环球', 204], ['- 社会', 205], ['- 综合', 27], ['美食', 211], ['- 美食制作', 76], ['- 美食侦探', 212], ['- 美食测评', 213], ['- 田 园美食', 214], ['- 美食记录', 215], ['生活', 160], ['- 搞笑', 138], ['- 亲子', 254], ['- 出行', 250], ['- 三农', 251], ['- 家居房产', 239], ['- 手工', 161], ['- 绘画', 162], ['- 日常', 21], ['汽车', 223], ['- 赛车', 245], ['- 改装玩车', 246], ['- 新能源车', 246], ['- 房车', 248], ['- 摩托车', 240], ['- 购车攻略', 227], ['- 汽车生活', 176], ['时尚', 155], ['- 美妆护肤', 157], ['- 仿妆cos', 252], ['- 穿搭', 158], ['- 时尚潮流', 159], ['运动', 234], ['- 篮球', 235], ['- 足球', 249], ['- 健身', 164], ['- 竞技体育', 236], ['- 运动文化', 237], ['- 运动综合', 238], ['动物圈', 217], ['- 喵星人', 218], ['- 汪星人', 219], ['- 小宠异宠', 222], ['- 野生动物', 221], ['- 动物二创', 220], ['- 动物综合', 75], ['搞笑', 138], ['单机游戏', 17]]


@plugin.route('/dynamic_list/')
def dynamic_list():
    items = []
    for d in _DYNAMIC_REGIONS:
        if d[0].startswith('- '):
            continue
        items.append({
            'label':d[0],
            'path': plugin.url_for('dynamic', id=d[1], page=1)
        })
    return items


@plugin.route('/dynamic/<id>/<page>/')
def dynamic(id, page):
    videos = []
    ps = 50
    res = get_api_data('/x/web-interface/dynamic/region', {'pn':page, 'ps':ps, 'rid':id})
    if res['code'] != 0:
        return videos
    list = res['data']['archives']
    for item in list:
        if 'redirect_url' in item and 'www.bilibili.com/bangumi/play' in item['redirect_url']:
            plot = parse_plot(item)
            bangumi_id = item['redirect_url'].split('/')[-1].split('?')[0]
            if bangumi_id.startswith('ep'):
                type = 'ep_id'
            else:
                type = 'season_id'
            bangumi_id = bangumi_id[2:]
            video = {
                'label': tag('【' + item['tname'] +  '】', 'pink') + item['title'],
                'path': plugin.url_for('bangumi', type=type, id=bangumi_id),
                'icon': item['pic'],
                'thumbnail': item['pic'],
                'info': {
                    'plot': plot
                },
                'info_type': 'video'
            }
        else:
            video = get_video_item(item)
            if not video:
                continue
        videos.append(video)
    if int(page) * ps < res['data']['page']['count']:
        videos.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('dynamic', id=id, page=int(page) + 1)
        })
    return videos


@plugin.route('/web_dynamic/<page>/<offset>/')
def web_dynamic(page, offset):
    videos = []
    url = '/x/polymer/web-dynamic/v1/feed/all'
    data = {
        'timezone_offset': -480,
        'type': 'all',
        'page': page
    }
    if page != '1':
        data['offset'] = offset
    res = get_api_data(url, data)
    if res['code'] != 0:
        return videos
    list = res['data']['items']
    offset = res['data']['offset']
    for d in list:
        major = d['modules']['module_dynamic']['major']
        if not major:
            continue
        author = d['modules']['module_author']['name']
        mid = d['modules']['module_author']['mid']
        if 'archive' in major:
            item = major['archive']
            item['author'] = author
            item['mid'] = mid
            video = get_video_item(item)
        elif 'live_rcmd' in major:
            content = major['live_rcmd']['content']
            item = json.loads(content)
            if item['live_play_info']['live_status'] == 1:
                label = tag('【直播中】', 'red') + author + ' - ' + item['live_play_info']['title']
            else:
                label = tag('【未直播】', 'grey') + author + ' - ' + item['live_play_info']['title']
            plot = f"UP: {author}\tID: {mid}\n房间号: {item['live_play_info']['room_id']}\n{item['live_play_info']['watched_show']['text_large']}\n"
            plot += f"分区: {tag(item['live_play_info']['parent_area_name'], 'blue')} {tag(item['live_play_info']['area_name'], 'blue')}"
            context_menu = [
                (f"转到UP: {author}", f"Container.Update({plugin.url_for('user', id=mid)})")
            ]
            video = {
                'label': label,
                'path': plugin.url_for('live', id=item["live_play_info"]["room_id"]),
                'is_playable': True,
                'icon': item["live_play_info"]["cover"],
                'thumbnail': item["live_play_info"]["cover"],
                'context_menu': context_menu,
                'info': {
                    'mediatype': 'video',
                    'title': item['live_play_info']['title'],
                    'plot': plot
                },
                'info_type': 'video',
            }
        else:
            continue
        videos.append(video)
    if res['data']['has_more']:
        videos.append({
            'label': tag('下一页', 'yellow'),
            'path': plugin.url_for('web_dynamic', page=int(page)+1, offset=offset)
        })
    return videos


# ═══════════════════════════════════════════════════════════════════════════
# 番剧 / 视频播放
# ═══════════════════════════════════════════════════════════════════════════

def md2ss(id):
    res = get_api_data('/pgc/review/user', {'media_id': id})
    if res['code'] == 0:
        return res['result']['media']['season_id']
    return 0


@plugin.route('/bangumi/<type>/<id>/')
def bangumi(type, id):
    items = []
    if type == 'media_id':
        type = 'season_id'
        id = md2ss(id)
    res = get_api_data('/pgc/view/web/season', {type: id})
    if res['code'] != 0:
        return items
    episodes = res['result']['episodes']
    for episode in episodes:
        label = ''
        if episode['badge']:
            label = tag('【' + episode['badge'] + '】', 'pink') + episode['share_copy']
        else:
            label = episode['share_copy']
        context_menu = []
        context_menu.append(("仅播放音频", f"PlayMedia({plugin.url_for('video', id=episode['bvid'], cid=episode['cid'], ispgc='true', audio_only='true', title=episode['share_copy'])})"))
        item = {
            'label': label,
            'path': plugin.url_for('video', id=episode['bvid'], cid=episode['cid'], ispgc='true', audio_only='false', title=episode['share_copy']),
            'is_playable': True,
            'icon': episode['cover'],
            'thumbnail': episode['cover'],
            'context_menu': context_menu,
            'info': {
                'mediatype': 'video',
                'title': episode['share_copy'],
                'duration': episode['duration'] / 1000,
                'plot': f"{episode['share_copy']}\n{episode['bvid']}\nep{episode['ep_id']}",
            },
            'info_type': 'video',
        }
        items.append(item)
    return items


@plugin.route('/videopages/<id>/')
def videopages(id):
    videos = []
    res = get_api_data('/x/web-interface/view', {'bvid': id})
    data = res['data']
    if res['code'] != 0:
        return videos
    for item in data['pages']:
        if 'first_frame' in item and item['first_frame']:
            pic = item['first_frame']
        else:
            pic = data['pic']
        context_menu = []
        context_menu.append(("仅播放音频", f"PlayMedia({plugin.url_for('video', id=data['bvid'], cid=item['cid'], ispgc='false', audio_only='true', title=item['part'])})"))
        video = {
            'label': item['part'],
            'path': plugin.url_for('video', id=data['bvid'], cid=item['cid'], ispgc='false', audio_only='false', title=item['part']),
            'is_playable': True,
            'icon': pic,
            'thumbnail': pic,
            'context_menu': context_menu,
            'info': {
                'mediatype': 'video',
                'title': item['part'],
                'duration': item['duration']
            },
            'info_type': 'video',
        }
        videos.append(video)
    return videos


@plugin.route('/video/<id>/<cid>/<ispgc>/<audio_only>/<title>/')
def video(id, cid, ispgc, audio_only, title):
    cid = str(cid)  # 统一转为字符串，避免类型混用导致路径拼接或API调用异常
    ispgc = ispgc == 'true'
    audio_only = audio_only == 'true'
    video_url = ''
    enable_dash = getSetting('enable_dash') == 'true'
    if cid == '0':
        res = get_api_data('/x/web-interface/view', {'bvid': id})

        data = res['data']
        if res['code'] != 0:
            return

        cid = data['pages'][0]['cid']
        if 'redirect_url' in data and 'bangumi/play/ep' in data['redirect_url']:
            ispgc = True
        else:
            ispgc = False

    if ispgc:
        url = '/pgc/player/web/playurl'
    else:
        url = '/x/player/playurl'

    qn = getSetting('video_resolution')

    if enable_dash or audio_only:
        params = {
            'bvid': id,
            'cid': cid,
            'qn': qn,
            'fnval': 4048,
            'fourk': 1
        }
    else:
        params = {
            'bvid': id,
            'cid': cid,
            'qn': qn,
            'fnval': 128,
            'fourk': 1
        }

    # CDN URL 有时效性，必须跳过缓存直接请求
    res = raw_get_api_data(url, data=params)

    if res['code'] != 0:
        return
    if ispgc:
        data = res['result']
    else:
        data = res['data']

    if 'dash' in data:
        if audio_only:
            # 音频优先级排序（与 generate_mpd 一致）
            audio_list = sorted(data['dash']['audio'], key=lambda x: x.get('id', 0), reverse=True)
            video_url = audio_list[0]['baseUrl'] + '|Referer=https://www.bilibili.com'
            video_url = {
                'label': title,
                'path': video_url
            }
            plugin.set_resolved_url(video_url)
            return
        else:
            mpd = generate_mpd(data['dash'])
            success = None
            basepath = 'special://temp/plugin.video.bili/'
            if not make_dirs(basepath):
                return
            filepath = '{}{}.mpd'.format(basepath, cid)
            with xbmcvfs.File(filepath, 'w') as mpd_file:
                success = mpd_file.write(mpd)
            if not success:
                return
            ip_address = '127.0.0.1'
            port = getSetting('server_port')
            video_url = {
                'path': 'http://{}:{}/{}.mpd'.format(ip_address, port, cid),
                'properties': {
                    'inputstream': 'inputstream.adaptive',
                    'inputstream.adaptive.manifest_type': 'mpd',
                    'inputstream.adaptive.manifest_headers': 'Referer=https://www.bilibili.com',
                    'inputstream.adaptive.stream_headers': 'Referer=https://www.bilibili.com'
                }
            }
    elif 'durl' in data:
        video_url = data['durl'][0]['url']
        if video_url:
            video_url += '|Referer=https://www.bilibili.com'
    else:
        video_url = ''

    if video_url and getSetting('enable_danmaku') == 'true':
        ass = generate_ass(cid)
        if ass:
            player = xbmc.Player()
            if player.isPlaying():
                player.stop()
            if video_url and (getSetting('report_history') == 'true'):
                report_history(id, cid)
            plugin.set_resolved_url(video_url, ass)
            return
    if video_url and (getSetting('report_history') == 'true'):
        report_history(id, cid)
    plugin.set_resolved_url(video_url)

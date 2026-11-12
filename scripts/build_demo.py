#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 demo.html：内联 demo.json + holders.json + dividend.json

用法：
    python3 scripts/build_demo.py            # 默认：Chart.js 走 CDN（文件小 205 KB）
    python3 scripts/build_demo.py --offline  # 离线版：Chart.js 也内联（断网也能看）

⚠ 为什么要这个开关：页面现在唯一的外部依赖就是 Chart.js CDN。
   CDN 打不开时，文字全在、图全空 —— 手机上尤其常见（地铁、弱网、CDN 被拦）。
   「加到手机主屏」不要求离线；但「断网也能看」要求。
"""
import json, os, sys, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
VENDOR = os.path.join(BASE, 'scripts', 'vendor', 'chart.umd.js')
CHART_CDN = 'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js'

OFFLINE = '--offline' in sys.argv

tpl = open(os.path.join(BASE, 'demo.html'), encoding='utf-8').read()
demo = json.load(open(os.path.join(DATA, 'demo.json'), encoding='utf-8'))
# 个股：直接内联原始数据，让前端自由选两期对比
# （预先把「差异最大的一对」算死会隐藏信息：汇金投资四期都是 8 只，
#   任何一对都看不出变化，硬算只会得到一张全是"不变"的表）
hold = json.load(open(os.path.join(DATA, 'holders.json'), encoding='utf-8'))
divi = json.load(open(os.path.join(DATA, 'dividend.json'), encoding='utf-8'))

# 压体积
for r in demo['rows']:
    r['price'] = [None if p is None else round(p, 3) for p in r['price']]
for r in divi['rows']:
    r['price'] = [None if p is None else round(p, 3) for p in r['price']]
    r['dps'] = [None if p is None else round(p, 4) for p in r['dps']]
for h in hold['holders']:
    h.pop('latest', None)   # 前端自己从 by_period 取最新期

html = (tpl.replace('__DEMO__', json.dumps(demo, ensure_ascii=False, separators=(',', ':')))
           .replace('__STOCK__', json.dumps(hold, ensure_ascii=False, separators=(',', ':')))
           .replace('__DIVI__', json.dumps(divi, ensure_ascii=False, separators=(',', ':'))))


def local_chartjs():
    """取本地 Chart.js；没有就下载一次并存进 scripts/vendor/（下次不再联网）。"""
    if os.path.exists(VENDOR):
        return open(VENDOR, encoding='utf-8').read()
    print(f'  本地没有 Chart.js，正在下载… {CHART_CDN}')
    os.makedirs(os.path.dirname(VENDOR), exist_ok=True)
    with urllib.request.urlopen(CHART_CDN, timeout=30) as r:
        code = r.read().decode('utf-8')
    open(VENDOR, 'w', encoding='utf-8').write(code)
    print(f'  已缓存到 {VENDOR} ({len(code)/1024:.0f} KB)')
    return code


if OFFLINE:
    # 把 <script src="...chart.umd.js"></script> 换成内联 <script>…</script>
    # ⚠ 用完整标签匹配、不用正则替换 src 属性：标签写法和属性顺序改一次就会漏。
    tag_cdn = f'<script src="{CHART_CDN}"></script>'
    if tag_cdn not in html:
        raise SystemExit('✗ 模板里找不到 Chart.js 的 script 标签，--offline 无从替换。'
                         '请检查 demo.html 里 chart.umd.js 的写法是否变了。')
    code = local_chartjs()
    html = html.replace(tag_cdn, '<script>' + code + '</script>')
    assert CHART_CDN not in html, 'CDN 引用仍在'

out = os.path.join(BASE, 'demo_site.html')
open(out, 'w', encoding='utf-8').write(html)
print(f'写入 {out}  ({len(html)/1024:.0f} KB){"  [离线版]" if OFFLINE else "  [CDN 版]"}')
for k in ('__DEMO__', '__STOCK__', '__DIVI__'):
    assert k not in html, f'{k} 未替换'
print('占位符已全部替换')

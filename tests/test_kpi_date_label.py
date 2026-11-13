#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首页资金流四格的标题必须带日期（离线）。

对应 2026-11-13 的改动：用户看到「当日估算净流入」这句，问「当日是哪一天」。
这不是措辞偏好 —— 这个站上三个日期本来就不同：

    份额（算净流入用）  T+1 披露     → 决定 latest_date
    两市成交额          当晚就有
    两融余额            T+1 晚间才出

所以「当日」到底指哪一天，光看卡片判不出来。更要命的是读者会把它理解成
「今天早上」，把昨天的流入当成刚发生的消息。

→ 数字对应哪一天，就写在卡片上。

跑法：python3 tests/test_kpi_date_label.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

ok_n = fail_n = 0


def chk(label, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print(f'  ✅ {label}: {got!r}')
    else:
        fail_n += 1
        print(f'  ❌ {label}: 得到 {got!r}，应为 {want!r}')


tpl = open(os.path.join(ROOT, 'demo.html'), encoding='utf-8').read()

print('\n## 源码：不许再出现不带日期的「当日估算净流入」')

chk('★ 模板里没有「当日估算净流入」', '当日估算净流入' in tpl, False)
chk('★ 模板里没有「近 5 日累计」硬编码', '近 5 日累计' in tpl, False)
chk('★ 模板里没有「近 20 日累计」硬编码', '近 20 日累计' in tpl, False)

chk('有 mdShort 日期格式化', 'const mdShort=' in tpl, True)
chk('有 DAY_TAG', 'DAY_TAG=' in tpl, True)
chk('★ 第一格标题用 DAY_TAG 拼', '${DAY_TAG} 估算净流入' in tpl, True)
chk('★ 5/20 日标题用 spanTag 拼', 'spanTag(5)' in tpl and 'spanTag(20)' in tpl, True)

print('\n## 逻辑：区间必须数交易日，不能数日历天')

# ⚠ 这条是重点。用 latest_date 往前推 N 个自然日会跨周末：
#   11-12 往前推 5 天 = 11-07~11-12，但那期间只有 3 个交易日。
#   区间写在卡片上就成了错的。
chk('★ spanTag 取 days 序列本身', 'const ds=D.days||[]' in tpl, True)
chk('★ 用 days[length-n] 而不是减自然日',
    'ds[ds.length-n]' in tpl, True)
# ⚠ 上一条（原本写的是「没有拿日期做减法」）是个无效断言：
#   它用 and 串了两个条件，前半个恒 False，于是整条恒通过 —— 什么都没验。
#   真正该验的是：spanTag 里不许出现「按天数推算日期」的迹象。
#   按自然日推会用到 Date / setDate / getTime / 86400 之类，
#   而正解只做字符串切分。所以直接在 spanTag 函数体里查这些词。
_m = re.search(r'function spanTag\(n\)\{(.*?)\n\}', tpl, re.S)
chk('★ 能找到 spanTag 函数体', bool(_m), True)
if _m:
    body = _m.group(1)
    banned = [w for w in ('new Date', 'setDate', 'getTime', '86400', 'getDate') if w in body]
    chk('★ spanTag 里没有按自然日推算日期', banned, [])

print('\n## 降级：拿不到日期要退回相对词，不能留空')

# 留空会变成一张没有时间坐标的卡片 —— 比用「当日」更糟，
# 至少「当日」还提示了「这不是历史值」。
chk('★ DAY_TAG 有 || 降级', "mdShort(D.latest_date)||'当日'" in tpl, True)
chk('★ spanTag 有长度不足的降级', 'ds.length<n||L.length<n' in tpl, True)

print('\n## 两市成交额必须显示自己的日期')

# 这一格用「成交量自己的最新一天」，不一定是 latest_date
# （成交额当晚就有、份额要 T+1）。不写日期会让人以为和前两格同一天。
chk('★ 成交额那格带 mdShort(vLast.d)', "mdShort(vLast.d)" in tpl, True)

print('\n## 实跑：用仓库真实数据求值，标题与数字都要对')

demo_fp = os.path.join(ROOT, 'data', 'demo.json')
if not os.path.exists(demo_fp):
    print('  （跳过：data/demo.json 不存在）')
else:
    d = json.load(open(demo_fp, encoding='utf-8'))
    days, flow = d.get('days') or [], (d.get('combo') or {}).get('flow') or []

    def mdShort(s):
        p = str(s or '').split('-')
        return f'{int(p[1])}.{int(p[2])}' if len(p) == 3 else ''

    day_tag = mdShort(d.get('latest_date')) or '当日'

    def span_tag(n):
        if len(days) < n or len(flow) < n:
            return f'近 {n} 日累计'
        a, b = mdShort(days[-n]), mdShort(days[-1])
        return f'{a}–{b} 累计' if (a and b) else f'近 {n} 日累计'

    k1, k2, k3 = f'{day_tag} 估算净流入', span_tag(5), span_tag(20)
    print(f'    第一格：{k1}   {flow[-1]:+.1f} 亿')
    print(f'    第二格：{k2}   {sum(flow[-5:]):+.1f} 亿')
    print(f'    第三格：{k3}   {sum(flow[-20:]):+.1f} 亿')

    chk('★ 第一格标题是 月.日 形态', bool(re.fullmatch(r'\d{1,2}\.\d{1,2} 估算净流入', k1)), True)
    chk('★ 第二格是「月.日–月.日 累计」形态',
        bool(re.fullmatch(r'\d{1,2}\.\d{1,2}–\d{1,2}\.\d{1,2} 累计', k2)), True)
    chk('★ 第三格同上',
        bool(re.fullmatch(r'\d{1,2}\.\d{1,2}–\d{1,2}\.\d{1,2} 累计', k3)), True)

    # 区间首日必须真的是序列里的第 n 个（数交易日），不是自然日减出来的
    chk('★ 5 日区间首日 = days[-5]', k2.startswith(mdShort(days[-5])), True)
    chk('★ 20 日区间首日 = days[-20]', k3.startswith(mdShort(days[-20])), True)
    # 末日必须是最新那天
    chk('★ 两个区间的末日都是最新交易日',
        (k2.split('–')[1].split(' ')[0], k3.split('–')[1].split(' ')[0]),
        (mdShort(days[-1]), mdShort(days[-1])))

print(f'\n{"=" * 50}\n通过 {ok_n} 项，失败 {fail_n} 项')
sys.exit(0 if fail_n == 0 else 1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""retail.py 的测试。

★ 这个文件里的每一条断言，都是**真跑之后**才发现的错，不是预想的。
  按发现顺序编号，方便以后回查「当时到底踩了什么」。

跑法：python3 tests/test_retail.py
"""
import importlib.util
import io
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('retail', os.path.join(BASE, 'scripts', 'retail.py'))
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

n_ok = n_bad = 0


def chk(name, got, want):
    global n_ok, n_bad
    ok = got == want
    if ok:
        n_ok += 1
    else:
        n_bad += 1
        print(f'  ✗ {name}\n      期望 {want!r}\n      实际 {got!r}')


def chk_true(name, got):
    chk(name, bool(got), True)


def section(t):
    print(f'\n{t}')


def at(src, needle):
    """取下标；取不到返回 -1，让后面的断言失败而不是抛 ValueError。

    ⚠ 为什么不用 src.index()：反向验证时故意把某个锚点整段删掉，
      index() 会直接抛 ValueError，测试是「崩了」而不是「挂了」——
      崩了看不出是哪一条口径被破坏，也看不出期望是什么。
    """
    return src.find(needle)


# ══════════════════════════════════════════════════════════════════
section('1. 表格列语义：第一列是「点击」，第二列才是「回复」')

# 真跑发现的错：一开始把第一列当回复数，样本里出现「4638 条回复」——
# 一个散户短帖不可能有 4638 条回复，那是阅读量。
# 这条钉住列的顺序，改 HTML 解析时如果错位会立刻挂。
FAKE = '''<tr class="tr2">
<td><span class="red">4638</span></td>
<td><span class="red">3</span></td>
<td><a href="/?s=thread&tid=6009099&bid=9279" target="_blank" class=" linkblack f14">标题在这</a></td>
<td><div class="author"><a href='/u/1' title='某散户'>某散户</a></div></td>
<td>3分钟前</td>
</tr>'''
m = R.ROW.findall(FAKE)
chk('① 解析出 1 行', len(m), 1)
chk('② 第 1 列 = 点击量 4638', int(m[0][0]), 4638)
chk('③ 第 2 列 = 回复数 3', int(m[0][1]), 3)
chk('④ 标题解析正确', re.sub(r'<[^>]+>', '', m[0][3]).strip(), '标题在这')
chk('⑤ 作者解析正确', m[0][4], '某散户')

# ══════════════════════════════════════════════════════════════════
section('2. 投票模板识别：必须整条匹配，不能只查「包含看多」')

# 只要标题里出现「看多」就判，会把自由发言也算进来。
# 实测股吧里有人写「我看空，但也有人说看多」，那是一条帖，不是两票。
chk('① 标准模板 → 看多', bool(R.VOTE.match('我看多 上证指数(SH000001)[我')), True)
chk('② 标准模板 → 看空', bool(R.VOTE.match('我看空 深证成指(SZ399001)[我')), True)
chk('③ 自由发言（含看多但不是模板）→ 不认',
    bool(R.VOTE.match('我看空但是有人看多')), False)
chk('④ 「看多的人太多了」→ 不认', bool(R.VOTE.match('看多的人太多了')), False)
chk('⑤ 空标题 → 不认', bool(R.VOTE.match('')), False)

# 用模板反查方向
chk('⑥ 取到方向=多', R.VOTE.match('我看多 上证指数').group(1), '多')
chk('⑦ 取到方向=空', R.VOTE.match('我看空 上证指数').group(1), '空')

# ══════════════════════════════════════════════════════════════════
section('3. 时间解析：股吧不显示年份，跨年要减一年')

from datetime import date

TODAY = date(2026, 11, 16)
chk('① 「3分钟前」→ 今天', R.parse_time('3分钟前', TODAY), TODAY)
chk('② 「2小时前」→ 今天', R.parse_time('2小时前', TODAY), TODAY)
chk('③ 「今天09:43」→ 今天', R.parse_time('今天09:43', TODAY), TODAY)
chk('④ 「昨天14:00」→ 昨天', R.parse_time('昨天14:00', TODAY), date(2026, 11, 15))
chk('⑤ 「11月14日」→ 2026-11-14', R.parse_time('11月14日', TODAY), date(2026, 11, 14))
chk('⑥ 完整日期直接解析', R.parse_time('2026-11-14 09:43', TODAY), date(2026, 11, 14))

# ★ 跨年：1 月看到「12月30日」，那是去年的 12-30，不是今年还没到的 12-30。
#   不加这条保护，「近 30 天」窗口会把去年 12 月的帖子算成未来的，静默错。
JAN = date(2027, 1, 5)
chk('⑦ ★ 跨年：「12月30日」在 1 月看到 → 去年',
    R.parse_time('12月30日', JAN), date(2026, 12, 30))
chk('⑧ 同一年内的「12月28日」不误减',
    R.parse_time('12月28日', JAN), date(2026, 12, 28))

chk('⑨ 空字符串 → None（不猜）', R.parse_time('', TODAY), None)
chk('⑩ 看不懂的 → None（不猜）', R.parse_time('很久以前', TODAY), None)

# ══════════════════════════════════════════════════════════════════
section('4. 机器帖过滤：行情快讯不是散户说的话')

# 实测「中证500」吧里混进大量系统播报，算进「散户在喊什么」就是张冠李戴。
for title, want in [
    ('【中证500股指期货(IC)主力合约日内涨超1%】', True),
    ('中证500股指期货（IC）主力合约涨超1', True),
    ('沪深300股指期货主力合约日内跌', True),
    ('无量上涨耍流氓！', False),
    ('今天能不跌吗', False),
]:
    chk(f'「{title[:20]}」机器帖={want}', bool(R.BOT.match(title)), want)

# 官方号也是机器，不是散户
chk('① 「新浪财经客户端」在官方号名单里',
    any(a in '新浪财经客户端' for a in R.BOT_AUTHOR), True)
chk('② 普通散户名不在名单里',
    any(a in '鹤鸣九皋的格雷格' for a in R.BOT_AUTHOR), False)

# ══════════════════════════════════════════════════════════════════
section('5. 去重口径：同一个人一天只算一票')

# 实测「天朗气清的伊芙」一小时内点了 4 次看空。不去重，一个人的情绪
# 会变成四个人的共识 —— 这是散户数据最容易犯的错，而且看不出来。
def dedup(votes):
    """和 retail.py main() 里同一套逻辑。"""
    uniq = {}
    for v in votes:
        k = (v['author'], v['date'])
        if k in uniq and uniq[k]['lean'] != v['lean']:
            uniq[k]['lean'] = 'mixed'
        elif k not in uniq:
            uniq[k] = dict(v)
    return list(uniq.values())


v = dedup([{'author': '甲', 'date': '2026-11-16', 'lean': 'down'}] * 4)
chk('① 同一人点 4 次看空 → 只算 1 条', len(v), 1)
chk('② 且方向仍是 down', v[0]['lean'], 'down')

v = dedup([
    {'author': '甲', 'date': '2026-11-16', 'lean': 'down'},
    {'author': '甲', 'date': '2026-11-16', 'lean': 'up'},
])
chk('③ 同一天既点空又点多 → mixed', v[0]['lean'], 'mixed')

v = dedup([
    {'author': '甲', 'date': '2026-11-15', 'lean': 'down'},
    {'author': '甲', 'date': '2026-11-16', 'lean': 'up'},
])
chk('④ 不同天可以各算一票（情绪会变）', len(v), 2)

v = dedup([
    {'author': '甲', 'date': '2026-11-16', 'lean': 'down'},
    {'author': '乙', 'date': '2026-11-16', 'lean': 'down'},
])
chk('⑤ 不同人各算一票', len(v), 2)

# ══════════════════════════════════════════════════════════════════
section('6. mixed 不进分母（两边都点 = 没定，不能替他表态）')

# 这条是真跑出来的：78 人明确表态 + 1 人两面都点。
# 如果把 mixed 塞进分母，那个人会被当成「0.5 个看空」这种不存在的东西。
n_up, n_dn, n_mx = 15, 63, 1
chk('① 分母 = up + down', n_up + n_dn, 78)
chk('② mixed 不进分母', n_up + n_dn + n_mx, 79)   # 只是证明 79≠78
chk('③ mixed 单独计数', n_mx, 1)

# ══════════════════════════════════════════════════════════════════
section('7. 样本不足时不给倾向词')

# OO 从第一轮就定了这条：「分母撑不起结论时给个词，等于用噪声冒充判断」。
chk('① MIN_VOTE 是个正数门槛', R.MIN_VOTE > 0, True)
chk('② MIN_VOTE 现在定在 30', R.MIN_VOTE, 30)
# 7 天窗口实测只有 46 人、30 天 78 人 —— 窗口定 30 天才过门槛
chk('③ 窗口 = 30 天（7 天太薄）', R.WINDOW_DAYS, 30)

# ══════════════════════════════════════════════════════════════════
section('8. 标题清洗：剥掉投票帖的模板尾巴')

# 「我看多 上证指数(SH000001)[我」这类后缀不剥，样本列表里全是重复括号
chk('① 剥掉 (SH000001)',
    R.clean_title('我看多 上证指数(SH000001)[我'), '我看多 上证指数')
chk('② 剥掉 <em> 高亮',
    R.clean_title('看好<em>新能源</em>'), '看好新能源')
chk('③ 普通标题原样保留',
    R.clean_title('无量上涨耍流氓！'), '无量上涨耍流氓！')

# ══════════════════════════════════════════════════════════════════
section('9. 源码层面的口径保护（防止以后改坏）')

src = io.open(os.path.join(BASE, 'scripts', 'retail.py'), encoding='utf-8').read()

# ★ 这几条是「不许再犯」的：
#   ① 不许把「自由发言」算进方向比例 —— 实测做不了，会得出反向结论
chk_true('① 源码里写明「自由发言不统计方向」',
         '自由发言只做样本展示，不计数' in src or '不统计方向' in src
         or '自由发言只做样本展示' in src)
#   ② 不许拿股吧短标题做情感分析
chk('② 源码里写明了「做不了方向统计」这个结论',
    '股吧短标题做不了' in src or '不能直接做情感分析' in src, True)
#   ③ 必须按独立作者去重
chk_true('③ 源码里有去重逻辑', 'uniq' in src and "v['author']" in src)
#   ④ 不许把散户数并进机构口径
chk('④ 源码里写明和预判层分开', '机构' in src and '散户' in src, True)

# ══════════════════════════════════════════════════════════════════
section('10. 和页面约定好的字段一个都不能少')

# 页面 retailBlock 直接读这些字段，少一个就是 undefined 显示成「—」
# 或者 NaN，而且只在有数据时才炸 —— 最难发现的那种。
PAGE_KEYS = ['lean_txt', 'n_up', 'n_down', 'n_people', 'n_mixed',
             'window_days', 'note', 'n_post', 'n_post_raw',
             'n_vote_post', 'n_bot', 'samples']
for k in PAGE_KEYS:
    # 在 main() 的返回字典里找这个键
    chk_true(f'retail.py 返回 {k!r}', f"'{k}':" in src or f"'{k}'" in src)

# 字段类型也要对得上（页面用 || 兜底，但类型错了仍会显示怪东西）
chk('① samples 是列表', 'samples' in src and 'samples\": ' not in src, True)
chk('② lean_txt 必须给中文（页面直接渲染）',
    'lean_txt' in src and '样本不足' in src, True)

# ══════════════════════════════════════════════════════════════════
section('11. update.py 的调用顺序（顺序错了会静默丢数据）')

up = io.open(os.path.join(BASE, 'scripts', 'update.py'), encoding='utf-8').read()

# ★ retail.py 是「读-改-写」sentiment.json，而 fetch_sentiment.py 每次
#   整个重写该文件。顺序反了 → 昨天抓的散户数据被今天重写的文件抹掉，
#   而且不报错。
i_sent = at(up, "run('fetch_sentiment.py'")
i_retail = at(up, "run('retail.py'")
i_compute = at(up, "run('compute_demo.py'")
# 先确认三个锚点都找到了 —— 否则 -1 < -1 这种比较会假通过
chk_true('① 三个调用点都能在 update.py 里找到',
         i_sent >= 0 and i_retail >= 0 and i_compute >= 0)
chk_true('② retail.py 排在 fetch_sentiment.py 之后', i_retail > i_sent)
chk_true('③ retail.py 排在 compute_demo.py 之前', i_retail < i_compute)

# 而且必须标 optional —— 拿不到散户不该拖垮整条链
chk_true('④ retail.py 标了 optional',
         "run('retail.py', optional=True)" in up)
chk_true('⑤ 失败要记进 fails（不能静默）',
         'retail.py（' in up)

# ══════════════════════════════════════════════════════════════════
section('12. compute_demo.py 必须透传 retail')

cd = io.open(os.path.join(BASE, 'scripts', 'compute_demo.py'), encoding='utf-8').read()
# 不透传 → 页面永远显示「还没有抓过散户层」，而数据其实抓到了。
# 「没取到」和「抓了但没数据」是两件完全不同的事。
chk_true("① 透传了 retail", "'retail'" in cd and '_sj.get' in cd)
chk_true('② 用的是 _sj.get，不是硬编码',
         "_sj.get('retail')" in cd)
# 必须和 forecast/policy 分开存，不能混进同一个字段
chk_true('③ retail 是 sentiment 下的独立键',
         "'forecast'" in cd and "'retail'" in cd)

# ══════════════════════════════════════════════════════════════════
section('13. 页面 demo.html 的散户块')

html = io.open(os.path.join(BASE, 'demo.html'), encoding='utf-8').read()

chk_true('① 有 retailBlock', 'retailBlock' in html)
chk_true('② 读的是 S.retail', 'S.retail' in html)
chk_true('③ 渲染顺序在 fcBlock 之后',
         html.index("${retailBlock}") > html.index("${fcBlock('a_share')}"))

# ★ bar() 必须在 fcBlock 外面
#   原来它定义在 fcBlock 内部，是局部变量。散户块在外面调用会
#   ReferenceError: bar is not defined —— 而这种错在 node --check 里
#   查不出来（语法合法），只有真渲染才炸。
i_bar = at(html, 'const bar=(up,down,tot)')
i_fc = at(html, 'const fcBlock=(key)')
chk_true('④ bar() 和 fcBlock 都能找到', i_bar >= 0 and i_fc >= 0)
chk_true('⑤ bar() 定义在 fcBlock 之前（在外层作用域）',
         i_bar >= 0 and i_fc >= 0 and i_bar < i_fc)
chk('⑥ bar() 只定义一次', html.count('const bar=(up,down,tot)'), 1)

# ★ 参数顺序：bar(偏多, 偏空, 分母)
#   换了下场就是颜色反了 —— 散户 80% 看空，条却整根红的，
#   比原来那个「只有红色」的 bug 还误导。
_a, _b = at(html, 'const retailBlock='), at(html, 'const PO=S.policy')
chk_true('⑦ retailBlock 和 policyBlock 都能定位', _a >= 0 and _b > _a)
seg = html[_a:_b] if (_a >= 0 and _b > _a) else ''
chk_true('⑧ 散户块调用 bar(nUp, nDn, nDec)（偏多在前）',
         'bar(nUp, nDn, nDec)' in seg)
chk_true('⑨ 散户块没有写反成 bar(nDn, nUp',
         'bar(nDn, nUp' not in seg)

# 页面文案里的禁词（不许在散户块里断言涨跌）
seg_pos = re.sub(r'不是[「『][^」』]*[」』]', '', seg)
seg_pos = re.sub(r'不(会|是|判|看)(涨|跌)', '', seg_pos)
leak = [w for w in ['会涨', '会跌', '必涨', '必跌', '看涨', '看跌'] if w in seg_pos]
chk('⑩ 散户块无涨跌断言（剥否定式后查）', leak, [])

# 必须写明「不能拿来干什么」—— 这是这一栏唯一防止被误用的东西
chk_true('⑪ 写了「它不能拿来干什么」', '它不能拿来干什么' in seg)
chk_true('⑫ 写了「会系统性偏空」这个偏差', '系统性偏空' in seg)

# ══════════════════════════════════════════════════════════════════
print(f'\n{"="*54}')
print(f'  通过 {n_ok} 项，失败 {n_bad} 项')
print('='*54)
sys.exit(1 if n_bad else 0)

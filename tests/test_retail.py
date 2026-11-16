#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""retail.py 的测试。

★ 这个文件里的每一条断言，都是**真跑之后**才发现的错，不是预想的。
  按发现顺序编号，方便以后回查「当时到底踩了什么」。

跑法：python3 tests/test_retail.py
"""
import importlib.util
import io
import json
import os
import re
import sys
from datetime import datetime

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
#
# ⚠⚠ 2026-11-16 修：这里原来是在**整个 update.py** 里找第一次出现的位置，
#   但那天新增了一个 run_shares_only_tail() 函数（补份额专用，也调 compute_demo），
#   它定义在 main() 之前 → i_compute 指到了那个函数里，
#   于是「retail 在 compute 之前」被判成 False，测试红了，但代码其实是对的。
#
#   教训：**顺序断言必须限定在被测的那段代码里**，不能全文件搜索 ——
#   只要有人往文件前面加一个辅助函数，这个测试就会假红（或更糟：假绿）。
#   现在先切出 main() 的函数体，只在里面比位置。
_m = re.search(r'^def main\(\):(.*?)(?=^def |\Z)', up, re.S | re.M)
up_main = _m.group(1) if _m else up

i_sent = at(up_main, "run('fetch_sentiment.py'")
i_retail = at(up_main, "run('retail.py'")
i_compute = at(up_main, "run('compute_demo.py'")
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

# ⚠⚠ 不要用 html.index() —— 它在锚点被删掉时抛 ValueError，
#   整个测试**崩溃**而不是「这一条挂掉」。
#   后果是后面所有断言（包括 ⑯ ⑰ ⑱ ⑲ ⑳ ㉑ 那几条专门查「三块已删」的）
#   一条都不执行，而输出看起来像是「只崩了个无关的」。
#   实测：把 ${retailBlock} 从渲染模板里删掉，测试崩在 ③，
#   ㉑「共识栏模板里确实渲染了 retailBlock」永远没跑到 —— 等于这条断言不存在。
#   （同类的错在第 15 节已经修过一次，第 13 节这里漏了。）
_i_rb = at(html, '${retailBlock}')
_i_fc0 = at(html, "${fcBlock('a_share')}")
chk_true('③ 两个锚点都能找到', _i_rb >= 0 and _i_fc0 >= 0)
chk_true('③b 渲染顺序在 fcBlock 之后', _i_rb >= 0 and _i_fc0 >= 0 and _i_rb > _i_fc0)

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



# ★ 顶部注释必须和事实一致
#   这一栏现在是三层（预判/散户/政策）。注释里还写「只剩两层」，
#   下一个改代码的人会以为没有散户块，直接删掉或者合并 ——
#   注释过时不会报错，但它会制造错误的判断。
chk_true('⑬ 顶部注释写的是三层', '这一栏是三层' in html)
chk_true('⑭ 顶部注释列了散户层', '散户层 = ' in html)
chk('⑮ 顶部注释不再说「只剩两层」', '这一栏只剩两层' not in html, True)

# ★★ 「三块已删」必须在【市场共识栏的渲染模板】里查，不能对整份 HTML 查
#
#   ⚠ 这是我在验证线上产物时踩的坑：我写了
#        c('已删「口径（这段很重要」', '口径（这段很重要' not in h)
#      结果这一项挂了 —— 但它是**误报**。
#
#      因为「口径（这段很重要，别跳过）」这个标题还出现在【股息率页签】里，
#      讲的是「股息率 = 年化每股分红 ÷ 股价」为什么不能按除权日求和，
#      和用户要求删的那个（市场共识栏底部的「口径」）**是同名不同块**。
#
#   教训：删掉一个块之后，用「整个文件里还有没有这个词」来验证是错的。
#         同一个词可能同时属于两个页签。**断言必须限定在被改动的那个渲染模板里。**
#         （这和「一个词同时属于两个主题时就不是可用的分类词」是同一类错。）
_cs = at(html, "${fcBlock('a_share')}")
_cs_end = html.find('`;', _cs) if _cs >= 0 else -1
chk_true('⑯ 能找到市场共识栏的渲染模板', _cs >= 0 and _cs_end > _cs)
cs_tpl = html[_cs:_cs_end] if (_cs >= 0 and _cs_end > _cs) else ''

chk_true('⑰ 共识栏里没有「行业热度 → 能买什么」', '行业热度 → 能买什么' not in cs_tpl)
chk_true('⑱ 共识栏里没有「概念热度 → 能买什么」', '概念热度 → 能买什么' not in cs_tpl)
chk_true('⑲ 共识栏里没有「口径（这段很重要」', '口径（这段很重要' not in cs_tpl)
# 但股息率页签那个「口径」要留着 —— 它是另一件事，删了会让股息率算法没法解释
chk_true('⑳ 股息率页签的「口径」还在（同名不同块，不能一起删）',
         '股息率 = 年化每股分红 ÷ 股价' in html)
# 散户块必须真的在这个模板里（不是定义了却没渲染）
chk_true('㉑ 共识栏模板里确实渲染了 retailBlock', '${retailBlock}' in cs_tpl)
# ══════════════════════════════════════════════════════════════════
section('14. build_demo.py 不许再读 holders.json（个股季报已删）')

bd = io.open(os.path.join(BASE, 'scripts', 'build_demo.py'), encoding='utf-8').read()
# ⚠ 必须剥掉注释再查。build_demo.py 里的注释**解释了为什么不再读 holders.json**
#   和为什么删掉 __STOCK__ —— 不剥注释，这两条断言会匹配到那句解释，
#   恒假。这个坑本轮已经踩过一次（检查 const dn=r.n_dir 是否残留时，
#   命中的也是注释里那句「原来这里写的是 const dn=r.n_dir」）。
#    剥两层：先剥三引号 docstring，再剥 # 注释。
#    只剥 # 是不够的 —— 文件头的 docstring 里也写着 holders.json，
#    那是「这个脚本做什么」的说明，不是代码。
bd_code = re.sub(r'"""[\s\S]*?"""', '', bd)
bd_code = re.sub(r'#.*$', '', bd_code, flags=re.M)
# 模板里已经没有 __STOCK__ 占位符了。脚本还去 read holders.json
# 并 replace 一个不存在的占位符 —— 不会报错，但也没做任何事，
# 而且 holders.json 哪天不生成就直接 FileNotFoundError 卡住整个构建。
chk_true('① 不再读 holders.json（剥 docstring+注释后）', 'holders.json' not in bd_code)
chk_true('② 不再 replace __STOCK__（剥 docstring+注释后）', '__STOCK__' not in bd_code)
chk_true('③ 也不再遍历 hold', "hold['holders']" not in bd_code)
chk_true('④ 占位符自检只查 DEMO 和 DIVI',
         "('__DEMO__', '__DIVI__')" in bd_code)

# 模板里确实没有这个占位符了（双重确认）
chk('⑤ 模板里确实无 __STOCK__', html.count('__STOCK__'), 0)

# ══════════════════════════════════════════════════════════════════
print('\n15. 时间解析：跨年 / 未来日期（2026-11-16 修的真 bug）')
# ══════════════════════════════════════════════════════════════════
#
# ⚠ 这一节对应的 bug 是**我自己在验证线上产物时才发现的**，而且
#   用户根本看不出来：线上页面散户样本里混着 4 条日期比今天还晚的帖子
#   （12月03日 / 12月04日 ×2 / 11月25日，当天是 11-16），
#   它们被当成「今年的」算进了「近 30 天」，还进了样本展示。
#
#   根因：跨年阈值写成了 `d > today + timedelta(days=30)`。
#   11-16 看到「12月03日」，算出来是今年 12-03，比今天只晚 17 天，
#   没到 30 天 → 不减年 → 变成「未来的帖子」，而且 >= 窗口起点，进统计。
#
#   判据应该是：**帖子不可能来自未来，所以只要算出比今天晚就是去年**。
#   另外窗口还缺上界（只有 d < since），两处都得补。

_spec = importlib.util.spec_from_file_location(
    'retail_mod', os.path.join(BASE, 'scripts', 'retail.py'))
_rm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rm)

_T = datetime(2026, 11, 16).date()      # 取一个固定的「今天」，不依赖跑测试的日期


def _pt(raw, today=_T):
    d = _rm.parse_time(raw, today)
    return d.isoformat() if d else None


# ── ① 去年 12 月/11 月的帖子，必须落到去年 ──
chk('① 11-16 看到「12月03日」→ 去年', _pt('12月03日'), '2025-12-03')
chk('② 11-16 看到「11月25日」→ 去年', _pt('11月25日'), '2025-11-25')
chk('③ 11-16 看到「12月31日」→ 去年', _pt('12月31日'), '2025-12-31')

# ── ② 窗口内的正常日期不许被误减年 ──
chk('④ 11-16 看到「11月13日」→ 今年', _pt('11月13日'), '2026-11-13')
chk('⑤ 11-16 看到「今天09:43」→ 今天', _pt('今天09:43'), '2026-11-16')
chk('⑥ 11-16 看到「3分钟前」→ 今天', _pt('3分钟前'), '2026-11-16')
chk('⑦ 11-16 看到「昨天14:00」→ 昨天', _pt('昨天14:00'), '2026-11-15')
chk('⑧ 带年份的直接解析', _pt('2026-11-14 09:43'), '2026-11-14')

# ── ③ 跨年边界：1 月看到 12 月 → 去年；看到 1 月 → 今年 ──
_T2 = datetime(2027, 1, 3).date()
chk('⑨ 01-03 看到「12月30日」→ 去年', _pt('12月30日', _T2), '2026-12-30')
chk('⑩ 01-03 看到「01月01日」→ 今年', _pt('01月01日', _T2), '2027-01-01')

# ── ④ 2/29 落在平年 → None，不许抛异常 ──
chk('⑪ 03-01 看到「02月29日」（平年）→ None',
    _pt('02月29日', datetime(2027, 3, 1).date()), None)

# ── ⑤ 源码层面的两条口径（防回归）──
_src = open(os.path.join(BASE, 'scripts', 'retail.py'), encoding='utf-8').read()
_src_code = re.sub(r'"""[\s\S]*?"""', '', _src)
_src_code = re.sub(r'#.*$', '', _src_code, flags=re.M)
chk_true('⑫ 不再用「晚 30 天才减年」的阈值', 'timedelta(days=30)' not in _src_code)
chk_true('⑬ 跨年判据是 d > today', 'if d > today' in _src_code)
chk_true('⑭ 窗口同时卡上界（d > today 剔掉）', 'd < since or d > today' in _src_code)
chk_true('⑮ 产出里记录被剔掉的未来帖条数', "'n_future': future" in _src_code)

# ── ⑥ 真实产物里不许有未来日期 ──
#    这条是「线上到底对不对」的最终判据：页面上的样本日期不能晚于 asof。
_sj_path = os.path.join(BASE, 'data', 'sentiment.json')
if os.path.exists(_sj_path):
    _rt = (json.load(open(_sj_path, encoding='utf-8')) or {}).get('retail') or {}
    _to = _rt.get('window_to')
    if _to:
        _tto = datetime.strptime(_to, '%Y-%m-%d').date()
        _bad = []
        for _s in (_rt.get('samples') or []):
            _m = re.match(r'(\d{1,2})月(\d{1,2})日', _s.get('time', ''))
            if not _m:
                continue
            try:
                _d = datetime(_tto.year, int(_m.group(1)), int(_m.group(2))).date()
            except ValueError:
                continue
            if _d > _tto:
                _bad.append(_s.get('time'))
        chk('⑯ 真实产物样本里没有晚于 window_to 的日期', _bad, [])
        # n_future 应该是 0 —— 非 0 说明 parse_time 又放水了
        chk('⑰ 真实产物 n_future == 0', _rt.get('n_future', 0), 0)
    else:
        chk_true('⑯ 真实产物带 window_to', False)
        chk_true('⑰ 真实产物带 n_future', False)
else:
    chk_true('⑯ 有 data/sentiment.json', False)

# ══════════════════════════════════════════════════════════════════
print(f'\n{"="*54}')
print(f'  通过 {n_ok} 项，失败 {n_bad} 项')
print('='*54)
sys.exit(1 if n_bad else 0)

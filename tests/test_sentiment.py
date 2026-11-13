#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""市场共识层测试。

这个文件要防的不是「代码报错」，而是**看起来对、实际说错话**。
共识/判断这一层没有事实层那种「份额涨了就是涨了」的硬校验，
所以它更容易在没人注意的时候安静地说错。

覆盖四类风险：
  ① 共识抽取抽出主语或碎片（「创业板指」「业板指跌」）
  ② 分位在样本不足时编一个数出来
  ③ 判断逻辑在三票矛盾时硬给方向
  ④ 情绪词表不对称（只收「跌」不收「涨」→ 永远悲观）

跑法：python3 tests/test_sentiment.py
"""
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import fetch_sentiment as S      # noqa: E402

_ok = 0
_bad = []


def chk(name, got, want):
    global _ok
    if got == want:
        _ok += 1
    else:
        _bad.append(f'{name}\n     得到 {got!r}\n     期望 {want!r}')


def chk_true(name, cond):
    chk(name, bool(cond), True)


def section(t):
    print(f'\n── {t} ──')


# ═══════════════════ 1. 共识抽取：不能抽主语，不能抽碎片 ═══════════════════

section('1. 共识抽取')

_news = [
    [{'title': '收评：创业板指高开低走跌3.32% 算力硬件股集体下挫', 'src': '新华财经'},
     {'title': '收盘｜创业板指跌3.32% 银行板块逆势拉升', 'src': '第一财经'},
     {'title': '收评：创业板指跌3.32% AI短剧、猪肉等概念板块活跃', 'src': '证券时报'},
     {'title': 'A股收盘｜创业板指跌3.32% 银行板块逆势拉升', 'src': '一财'},
     {'title': '收评：创业板指高开低走跌3.32%，算力硬件概念集体下挫', 'src': '每经'}],
]
_cons = S.consensus_of(_news, min_src=2)

_texts = [c['text'] for c in _cons]
chk('★ 不抽出纯主语「创业板指」', '创业板指' in _texts, False)
chk('★ 不抽出碎片「业板指跌」',
    any('业板指' in t for t in _texts), False)
chk('★ 抽出了主题「算力硬件」', '算力硬件' in _texts, True)

_hw = [c for c in _cons if c['text'] == '算力硬件']
chk('★★ 算力硬件归到「跌」方向', _hw[0]['dir'] if _hw else None, 'down')
chk('★★ 算力硬件的来源数合并了 2 家（不同写法归并成功）',
    _hw[0]['sources'] if _hw else 0, 2)

_bk = [c for c in _cons if c['text'] == '银行']
chk('★★ 银行归到「涨」方向（同一句里「跌」没污染它）',
    _bk[0]['dir'] if _bk else None, 'up')

# 例句必须带出去 —— 算法会错，人得能复核
chk('★ 每条共识都带 examples', all(c.get('examples') for c in _cons), True)

# min_src 必须真的生效
_low = S.consensus_of(_news, min_src=99)
chk('★ min_src 卡住时返回空', len(_low), 0)


# ═══════════════════ 2. 主题识别：方向词窗口 ═══════════════════

section('2. 主题与方向')

chk('★ 「银行拉升」识别为 up',
    dict(S._topic_hits('银行板块逆势拉升'))['银行'], 'up')
chk('★ 「半导体领跌」识别为 down（主题键名是「芯片半导体」）',
    dict(S._topic_hits('半导体方向领跌'))['芯片半导体'], 'down')
chk('★ 无方向词 → flat',
    dict(S._topic_hits('银行板块'))['银行'], 'flat')

# ⚠ 这是最容易错的一条：两个主题、两个方向，在同一句里。
#   实测错法一：取整句多数票 → 银行被「跌」污染成 down
#   实测错法二：对称窗口 ±12 字 → 银行被判成 mix
#   实测错法三：head 取主题前 2 字 → 「影视板块」借到上一句的「收跌」
_mix = dict(S._topic_hits('银行板块逆势拉升 创业板指跌3.32% 算力硬件下挫'))
chk('★★ 同一句里银行=up（不被后面的「跌」污染）', _mix.get('银行'), 'up')
chk('★★ 同一句里算力硬件=down', _mix.get('算力硬件'), 'down')

# 这条是 head 越界的活证据：影视的真实谓语是「大涨」，前面的「收跌」是上一句的
_pv = dict(S._topic_hits('A股三大指数收跌 影视板块逆市大涨 银行板块走强'))
chk('★★★ 影视=up（不借上一句的「收跌」）', _pv.get('影视传媒'), 'up')
chk('★★ 银行=up', _pv.get('银行'), 'up')

# 「回调」必须在下跌词表里
chk('★★ 「医药回调」=down', dict(S._topic_hits('白酒板块领涨，医药回调')).get('医药医疗'), 'down')

# 词表对称：两边数量不能差太多，否则整体偏向一边
chk('★★ 涨跌词表数量相近（差 <= 3）',
    abs(len(S.DIR_UP) - len(S.DIR_DOWN)) <= 3, True)


# ═══════════════════ 3. 分位：样本不足必须返回 None ═══════════════════

section('3. 分位诚实性')

_hist3 = [{'d': f'2026-11-{d:02d}', 'top_heat': 100 + d} for d in (1, 2, 3)]
p, n = S.percentile_of(_hist3, 'top_heat', 102)
chk('★★ 样本 3 天（<20）→ 分位必须是 None', p, None)
chk('★ 但同时要说清只有几天', n, 3)

_hist25 = [{'d': f'2026-10-{d:02d}', 'top_heat': 100 + d} for d in range(1, 26)]
p, n = S.percentile_of(_hist25, 'top_heat', 112)
chk('★ 样本 25 天 → 有分位', p is not None, True)
chk('★ 25 天里 ≤112 的有 12 个 → 48%', p, 48)
chk('★ 分母是历史天数', n, 25)

# 空值不能当 0 混进去
p2, n2 = S.percentile_of(_hist25, 'not_exist', 5)
chk('★ 该字段全空 → None', p2, None)
chk('★ 该字段全空 → n=0', n2, 0)

# 值本身为 None 时不能算
p3, _ = S.percentile_of(_hist25 + [{'d': 'x', 'top_heat': None}], 'top_heat', None)
chk('★ 当前值为 None → 分位 None', p3, None)


# ═══════════════════ 4. 情绪词表：必须对称 ═══════════════════

section('4. 情绪词表对称性')

chk('★ 看多词 >= 10 个', len(S.BULL) >= 10, True)
chk('★ 看空词 >= 10 个', len(S.BEAR) >= 10, True)
# 每一边都必须有「核心单字」
chk('★ 看多有「涨」', '涨' in S.BULL, True)
chk('★ 看空有「跌」', '跌' in S.BEAR, True)
chk('★ 看多有「涨停」', '涨停' in S.BULL, True)
chk('★ 看空有「跌停」', '跌停' in S.BEAR, True)

_m = S.mood_of(['今天涨停', '今天跌停', '没什么感觉', '大涨', '大跌'])
chk('★ 涨停算看多', _m['bull'], 2)
chk('★ 跌停算看空', _m['bear'], 2)
chk('★ 无词算中性', _m['flat'], 1)
chk('★ 分母是全部标题', _m['n'], 5)

_m0 = S.mood_of([])
chk('★ 空输入 → pct 是 None 不是 0', _m0['bull_pct'], None)


# ═══════════════════ 5. 判断：三票矛盾时不能给方向 ═══════════════════

section('5. 判断逻辑')

_vals_low = [{'name': '创业板', 'pe_pct': 6}, {'name': '恒生科技', 'pe_pct': 7}]
_vals_high = [{'name': '中证红利', 'pe_pct': 87}, {'name': '上证50', 'pe_pct': 85}]

# ⚠ 2026-11-13 起判断从「三票」变「四票」：加了「当天大盘涨跌」。
#   所以这里必须同时给 n_up/n_down（第一票用家数，不再用 dir 计数）
#   和 index_chg（第四票）。只改断言不改数据，会得到「看起来过了其实没测」。
_cons_up = [{'text': 'AI', 'sources': 5, 'dir': 'up', 'up': 5, 'down': 0,
             'n_up': 5, 'n_down': 0, 'n_flat': 0},
            {'text': '银行', 'sources': 4, 'dir': 'up', 'up': 4, 'down': 0,
             'n_up': 4, 'n_down': 0, 'n_flat': 0}]
_cons_dn = [{'text': '算力', 'sources': 5, 'dir': 'down', 'up': 0, 'down': 5,
             'n_up': 0, 'n_down': 5, 'n_flat': 0}]
_mood_bull = {'guba': {'n': 50, 'bull': 20, 'bear': 3, 'flat': 27,
                       'bull_pct': 40.0, 'bear_pct': 6.0}}
_mood_bear = {'guba': {'n': 50, 'bull': 2, 'bear': 20, 'flat': 28,
                       'bull_pct': 4.0, 'bear_pct': 40.0}}

# 四票全正：媒体偏涨 + 散户偏多 + 低估值 + 大盘涨
j1 = S.pick_judgment(_cons_up, _mood_bull, _vals_low, {}, index_chg=1.5)
chk('★★ 四票全正 → 一致偏强', j1['stance'], '一致偏强')

# 四票全负：媒体偏跌 + 散户偏空 + 高估值 + 大盘跌
j2 = S.pick_judgment(_cons_dn, _mood_bear, _vals_high, {}, index_chg=-2.0)
chk('★★ 四票全负 → 一致偏弱', j2['stance'], '一致偏弱')

# 关键：一正一负一中性 → 必须给方向，不能模糊
j3 = S.pick_judgment(_cons_up, _mood_bear, _vals_low, {}, index_chg=1.5)
chk('★★★ 三正一负 → 偏强（不是「分化」也不是「一致」）', j3['stance'], '偏强')

j4 = S.pick_judgment(_cons_up, _mood_bear, _vals_high, {}, index_chg=-1.0)
chk('★★★ 一正两负 → 偏弱', j4['stance'], '偏弱')

# ★★★ 第四票的核心用途：板块说涨、大盘在跌 → 必须指出这个矛盾
j7 = S.pick_judgment(_cons_up, _mood_bull, _vals_low, {}, index_chg=-3.32)
chk('★★★ 板块偏涨但大盘跌 3.32% → 反面证据必须点出这个背离',
    any('对着走' in a or '背离' in a for a in j7.get('anti') or []), True)
chk('★★★ 大盘跌时，第四票投负',
    any(v['name'] == '当天大盘涨跌' and v['v'] < 0 for v in j7.get('votes') or []), True)

# 大盘小幅波动（±0.3% 内）应算中性，不能因为 0.1% 就判方向
j8 = S.pick_judgment(_cons_up, _mood_bull, _vals_low, {}, index_chg=0.15)
chk('★★ 大盘 ±0.3% 内算中性', 
    any(v['name'] == '当天大盘涨跌' and v['v'] == 0 for v in j8.get('votes') or []), True)

# 大盘数据缺失时，那一票不出现（而不是当 0 票凑数）
j9 = S.pick_judgment(_cons_up, _mood_bull, _vals_low, {}, index_chg=None)
chk('★★ 大盘取不到时那一票直接不出现',
    any(v['name'] == '当天大盘涨跌' for v in j9.get('votes') or []), False)

# 全中性
_cons_flat = [{'text': 'X', 'sources': 3, 'dir': 'flat', 'up': 0, 'down': 0,
               'n_up': 0, 'n_down': 0, 'n_flat': 3}]
j5 = S.pick_judgment(_cons_flat, {'guba': {'n': 0}}, [], {})
chk('★ 无有效票 → 分化', j5['stance'], '分化')

# ★★ 缺票必须写进结论，不能被静默吞掉
chk('★★★ 股吧没抓到 → 必须记进 missing',
    any('股吧' in m for m in j5.get('missing') or []), True)
chk('★★★ 缺票要出现在一句话结论里（不许只藏在数据里）',
    '注意' in (j5.get('line') or ''), True)

# 完全没数据
j6 = S.pick_judgment([], {}, [], {})
chk('★ 全空 → 样本不足', j6['stance'], '样本不足')
chk('★ 全空时 evidence 为空', j6['evidence'], [])

# 四票必须能查到（可复核）
chk('★★ 判断带 votes 明细（四票）', len(j1.get('votes') or []), 4)
chk('★★ 每票写明理由', all(v.get('why') for v in j1['votes']), True)

# 反面证据：只要有高估值，就必须同时给「高分位不等于会跌」
chk('★★ 高估值时必须给反面证据',
    any('不等于' in a for a in j2.get('anti') or []), True)

# 免责声明必须存在
chk('★★★ 判断必须带「不是买卖建议」', '不是买卖建议' in (j1.get('note') or ''), True)
chk('★ 免责里写了「只动新增」', '只动新增' in (j1.get('note') or ''), True)

# ★★★ 结论措辞不许写死票数（票数改过一次，写死就会对不上）
chk('★★★ 一句话里不写死「三个维度」', '三个维度' in (j1.get('line') or ''), False)


# ═══════════════════ 6. 代码层回归（防止以后改回去） ═══════════════════

section('6. 代码层回归')

_src_path = os.path.join(BASE, 'scripts', 'fetch_sentiment.py')
_src = open(_src_path, encoding='utf-8').read()


def code_lines(s):
    """剥掉注释和 docstring，只看真正会跑的代码。

    ⚠ 上一轮（test_lag_baseline）踩过的坑：注释里写了「原来用 n-gram」
      会被算成「还在用 n-gram」。
    ⚠ 这里还多一层：**docstring 也要剥**。本文件顶部那段设计说明里
      明写着「不能写成『应该买 X』」—— 那是在禁止这件事，
      扫全文会把它自己当成违规。只剥 `#` 是不够的，
      因为那段话在 `\\"\\"\\"…\\"\\"\\"` 里面，`#` 剥不到它。
    """
    # 先去掉所有三引号块（含单引号和双引号两种）
    s = re.sub(r'""".*?"""', '', s, flags=re.S)
    s = re.sub(r"'''.*?'''", '', s, flags=re.S)
    out = []
    for ln in s.split('\n'):
        t = ln.split('#')[0]
        if t.strip():
            out.append(t)
    return out


_code = '\n'.join(code_lines(_src))

chk('★ 不再用 n-gram 滑窗（for size in (4,5,6）', 'for size in (4, 5, 6' in _code, False)
chk('★ 用主题表 TOPIC_LEX', 'TOPIC_LEX' in _code, True)
chk('★ 方向词分正负两组', 'DIR_UP' in _code and 'DIR_DOWN' in _code, True)
chk('★ 分位有最小样本门槛', 'MIN_SAMPLE_DAYS' in _code, True)
chk('★ 分位不足返回 None', 'return None, n' in _code, True)

# 判断函数必须同时产出 evidence 和 anti
chk('★ 判断同时给证据与反面证据',
    "'evidence': ev" in _code and "'anti': anti" in _code, True)

# 不得出现「建议买入」这类指令性表述
# ⚠ 必须剥注释再查。文件开头明明白白写着「不能写成『应该买 X』」——
#   那条注释本身是在**禁止**这件事，扫全文会把它当成违规。
#   这和 test_lag_baseline 里踩过的坑是同一个：查代码前必须剥注释。
_banned = ['建议买入', '建议卖出', '应该买', '应该卖', '推荐买入', '推荐卖出']
chk('★★ 代码里没有指令性文案（已剥注释）',
    [b for b in _banned if b in _code], [])

# 抖音相关：热榜抓取不能假装能拿财经
chk('★ 没有偷偷加抖音搜索（已知需登录）',
    'douyin.com/aweme/v1/web/search' in _src, False)


# ═══════════════════ 7. 2026-11-13 五个缺陷的回归 ═══════════════════
# 这一节全部来自当天实测暴露的真问题。每一条都对应页面上曾经错过的数字。

section('7. 分母一致性（曾把 15 票报成 21 票）')

# 构造：同一主题 4 家提到，其中 3 家说跌、1 家没方向
_arts_cov = [
    {'title': '算力硬件股集体下挫', 'src': 'A社'},
    {'title': '算力硬件概念领跌', 'src': 'B社'},
    {'title': '算力硬件方向走低', 'src': 'C社'},
    {'title': '算力硬件公司发布公告', 'src': 'D社'},   # 提到但无方向
]
_cov = S.consensus_of([_arts_cov], min_src=1)
_c0 = _cov[0]
chk('★★★ 分项之和必须等于总量（说涨+说跌+没方向 == 提到）',
    (_c0['n_up'] + _c0['n_down'] + _c0['n_flat']), _c0['sources'])
chk('★★ 没说方向的那家要单独计数，不能并进「在跌」',
    _c0['n_flat'], 1)
chk('★★ dir 的分母只算有方向的（3 家说跌 / 4 家提到）',
    (_c0['n_down'], _c0['sources']), (3, 4))

section('8. 同一家媒体发正反两篇（曾造成分项和 > 总量）')

_arts_two_way = [
    {'title': '金价失守3900美元', 'src': 'X社'},
    {'title': '法兴唱多黄金，金价有望反弹', 'src': 'X社'},   # 同一家，反向
    {'title': '现货黄金跌破3870', 'src': 'Y社'},
]
_tw = S.consensus_of([_arts_two_way], min_src=1)
_t0 = [r for r in _tw if r['text'] == '黄金'][0]
chk('★★★ 同一家发过涨也发过跌 → 仍只算 1 家，不重复计入两个方向',
    (_t0['n_up'] + _t0['n_down'] + _t0['n_flat']), _t0['sources'])
chk('★★ 该媒体按篇数多的那边归（跌 1 篇 vs 涨 1 篇 → 打平算没方向或跌，但不能两边都算）',
    _t0['n_up'] + _t0['n_down'] <= _t0['sources'], True)

section('9. 主题重叠（曾让一条标题投两票）')

_ov = S._topic_hits('A股市场震荡调整 AI短剧概念再度走强')
_ov_topics = [t for t, d in _ov]
chk('★★★ 「AI短剧」只归一个主题，不再同时算 AI应用 + 人工智能',
    len(_ov_topics), len(set(_ov_topics)))
chk('★★ 不能一个标题里出现两个语义包含的主题同时命中',
    not ('AI应用' in _ov_topics and '人工智能' in _ov_topics), True)

# 但不同位置上的两个主题必须都保留
_ov2 = S._topic_hits('银行板块逆市拉升 算力硬件下挫')
chk('★★★ 不同位置的银行和算力硬件都要保留（不能过度合并）',
    ('银行' in [t for t, d in _ov2]) and ('算力硬件' in [t for t, d in _ov2]), True)
chk('★★ 且两者方向相反：银行涨、算力跌',
    dict(_ov2).get('银行'), 'up')
chk('★★ 算力硬件=down', dict(_ov2).get('算力硬件'), 'down')

section('10. 价格下行词（「失守」曾被判没方向）')

chk('★★★ 「现货黄金失守3870美元」= down（不再判 flat）',
    dict(S._topic_hits('现货黄金失守3870美元/盎司')).get('黄金'), 'down')
chk('★★ 「跌破」= down', dict(S._topic_hits('金价跌破4000美元')).get('黄金'), 'down')
chk('★★ 「击穿」= down', dict(S._topic_hits('现货黄金击穿支撑位')).get('黄金'), 'down')
chk('★★ 对称补的「突破」= up', dict(S._topic_hits('金价突破历史高位')).get('黄金'), 'up')
chk('★★★ 涨跌词表数量差 <= 3（不对称会让页面永远偏空）',
    abs(len(S.DIR_UP) - len(S.DIR_DOWN)) <= 3, True)

section('11. 主题词覆盖率（第一票的置信度）')

_covr = S._topic_coverage({
    'a_share': [{'title': '创业板指跌3%'},            # 无主题
                {'title': '算力硬件下挫'},             # 有主题
                {'title': '沪指跌0.56%'},              # 无主题
                {'title': '银行板块拉升'}],            # 有主题
    'gold': [],
})
chk('★★ 覆盖率要算出来（2/4 = 50%）', _covr['a_share']['pct'], 50)
chk('★★ 空话题的 pct 必须是 None，不能是 0', _covr['gold']['pct'], None)
chk('★★ 覆盖率低是「不能单独当结论」，不是「结论错」',
    '不能单独' in S._topic_coverage.__doc__, True)

section('12. 大盘涨跌那一票（板块说涨、大盘跌 3% 的矛盾）')

chk('★★★ fetch_index_chg 存在且用腾讯（东财 push2his 实测掐断）',
    'qt.gtimg.cn' in _code, True)
chk('★★ 不再依赖 push2his 拿指数',
    'push2his' in _code.split('def fetch_index_chg')[1].split('\n\n')[0]
    if 'def fetch_index_chg' in _code else False, False)


# fetch_status 上报必须覆盖每个源
for k in ('valuations', 'hot_stock', 'hot_plate', 'guba', 'toutiao_feed'):
    chk(f'★ 体检上报了 {k}', f"report('{k}'" in _src, True)


# ═══════════════════ 7. 真实数据快照（跑过才有） ═══════════════════

section('7. 产出文件自检')

_fp = os.path.join(BASE, 'data', 'sentiment.json')
if os.path.exists(_fp):
    d = json.load(open(_fp, encoding='utf-8'))
    chk('★ 有 date', bool(d.get('date')), True)
    chk('★ 估值非空', len(d.get('valuations') or []) > 0, True)
    chk('★ 热股 100 只', len(d.get('hot_stocks') or []), 100)
    chk('★ 板块有行业和概念', sorted((d.get('plates') or {}).keys()),
        ['concept', 'industry'])
    chk('★ 板块带 ETF 代码',
        any(x.get('etf_code') for x in (d.get('plates') or {}).get('industry') or []), True)
    chk('★ 有判断', bool((d.get('judgment') or {}).get('stance')), True)
    chk('★ 判断的 note 在', '不是买卖建议' in ((d.get('judgment') or {}).get('note') or ''), True)
    chk('★ news 有 a_share 和 gold', sorted((d.get('news') or {}).keys()),
        ['a_share', 'gold'])
    chk('★ 股吧全是散户帖（无 media）',
        all(x.get('kind') == 'post' for x in d.get('guba') or []), True)
    chk('★ 有 history 数组', isinstance(d.get('history'), list), True)
else:
    chk('★ sentiment.json 存在（先跑 fetch_sentiment.py）', False, True)


# ═══════════════════ 汇总 ═══════════════════

print(f'\n{"="*60}')
if _bad:
    print(f'❌ {len(_bad)} 项失败 / 共 {_ok + len(_bad)} 项\n')
    for b in _bad:
        print(f'  ✗ {b}')
    sys.exit(1)
print(f'✅ 全部通过　{_ok} 项')
sys.exit(0)

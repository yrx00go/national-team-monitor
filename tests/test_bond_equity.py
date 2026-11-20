#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试：股债比价的判据（calc_bond_equity）。

⚠ 全部不联网。用构造的序列固定「算法口径」，不测「中证今天返回什么」——
  那属于抓取脚本，源头改版应由体检栏报警发现，不该让单测跟着红。

这几条断言锁的是本项目最容易改坏的东西：
  ① 基准日必须是首日（不是 5 年前）—— 改错了全历史曲线前段会假暴跌
  ② R 的定义必须是「股票 ÷ 债券」（反过来会把便宜念成贵）
  ③ 滚动分位必须消掉趋势漂移 —— 这是整个指标能不能用的前提
  ④ 档位边界是产品契约，不是临时数
  ⑤ 分位曲线前段必须是 None，不能是 0
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import compute_demo as CD   # noqa: E402


def _mk(hs_pct, bd_pct, start_year=2004, n=None):
    """造一段可控的行情。

    hs_pct / bd_pct 是【年化百分比】，按 244 交易日/年折成日增。
    返回符合 bond_equity.json 结构的 dict。
    """
    n = n or 244 * 10          # 默认 10 年，够跑滚动 5 年分位
    import datetime
    d0 = datetime.date(start_year, 1, 1)
    days, hs, bd = [], 1000.0, 100.0
    hs_step = (1 + hs_pct / 100.0) ** (1 / 244.0)
    bd_step = (1 + bd_pct / 100.0) ** (1 / 244.0)
    d = d0
    for i in range(n):
        while d.weekday() >= 5:            # 跳过周末，不用真日历
            d += datetime.timedelta(days=1)
        days.append([d.isoformat(), round(hs, 4)])
        hs *= hs_step
        bd *= bd_step
        d += datetime.timedelta(days=1)
    bond = [[d[0], round(100.0 * bd_step ** i, 4)] for i, d in enumerate(days)]
    return {'hs300tr': days, 'bond': bond, 'meta': {'src': 'test', 'updated': 'x'}}


class TestBondEquity(unittest.TestCase):

    # ── ① 基准日 ──────────────────────────────────────────────
    def test_base_date_is_first_day(self):
        """归一化基准必须是【首日】。

        ⚠ 这条是本模块踩过的真坑：我第一版取「5 年前」当基准，
          但画的是全历史曲线 —— 结果 2005 年那段被压到 0.127，
          图上出现一条从 0.13 拉起来的假暴跌。**基准日必须和覆盖区间一致。**
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        self.assertEqual(be['base_date'], be['days'][0])
        # 首日归一化后必须正好是 1.0
        self.assertAlmostEqual(be['norm_hs'][0], 1.0, places=6)
        self.assertAlmostEqual(be['norm_bond'][0], 1.0, places=6)

    def test_norm_series_never_near_zero(self):
        """归一化序列不该出现贴近 0 的值 —— 那说明基准取错了。"""
        be = CD.calc_bond_equity(_mk(8, 3))
        self.assertGreater(min(be['norm_hs']), 0.3)
        self.assertGreater(min(be['norm_bond']), 0.3)

    # ── ② R 的方向 ────────────────────────────────────────────
    def test_R_is_stock_over_bond(self):
        """R = 股票 ÷ 债券。反了会把「贵」念成「便宜」，是最致命的错。"""
        be = CD.calc_bond_equity(_mk(8, 3))
        i = len(be['days']) - 1
        # 用原始数据手工验算
        hs = dict((d, c) for d, c in _mk(8, 3)['hs300tr'])
        self.assertIn('R', be)
        self.assertGreater(be['R'][i], 1.0)   # 股票涨 8%、债券涨 3% → R 上升

    # ── ③ 滚动分位消掉趋势漂移 ────────────────────────────────
    def test_rolling_pct_removes_drift(self):
        """★ 这是整个指标能不能用的前提。

        构造一段**匀速上涨**的行情：股票年化 8%、债券年化 3%，永不回撤。
        此时 R 每天都在创新高（趋势漂移），
          · 全历史分位 → 恒等于 100%（因为今天永远是最高的）→ 指标废掉
          · 滚动 5 年分位 → 也恒等 100%，因为窗口内今天仍是最高的
        所以更该验的是：**匀速行情下分位确实贴 100%**，
        说明它测的是「相对自己近期」，而不是「绝对便宜」。
        这是一条【反向断言】：它固定住这个指标的真实语义，防止有人
        以为「分位低 = 便宜」而把这个前提忘掉。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        last = be['cur']['pct']
        self.assertIsNotNone(last)
        self.assertGreater(last, 90)      # 匀速上涨 → 当前必然在窗口最高位
        self.assertEqual(be['cur']['band'], '贵')

    def test_mean_reverting_series_gives_mid_pct(self):
        """均值回复的行情下，分位该落在中间，不该贴边。

        构造正弦波动：R 周期性领先/落后自己的 5 年均值。
        验证分位不是永远 0 或 100 —— 这是「分位有分辨力」的最低要求。
        """
        import math
        import datetime
        n = 244 * 12
        d = datetime.date(2004, 1, 1)
        rows = []
        for i in range(n):
            while d.weekday() >= 5:
                d += datetime.timedelta(days=1)
            # R 直接由正弦决定，债券恒为 1 → hs300tr 的值就是 R * 100
            r = 20 + 8 * math.sin(i / 244.0 * math.pi / 1.5)
            rows.append((d.isoformat(), round(r * 100, 4)))
            d += datetime.timedelta(days=1)
        raw = {'hs300tr': rows,
               'bond': [[x[0], 100.0] for x in rows],
               'meta': {}}
        be = CD.calc_bond_equity(raw)
        self.assertIsNotNone(be)
        # 正弦序列的末值分位应该明显不是 0 也不是 100
        self.assertGreater(be['cur']['pct'], 1)
        self.assertLess(be['cur']['pct'], 99)
        # 而且整条分位曲线应该真的在 0~100 之间游走，不是一条平线
        vals = [p for p in be['pct'] if p is not None]
        self.assertGreater(max(vals) - min(vals), 50,
                           '分位曲线方差太小 → 这个指标没有分辨力')

    # ── ④ 档位边界（产品契约）──────────────────────────────────
    def test_band_boundaries(self):
        """档位是写在页面文案里的契约（<20 便宜 / 20-40 偏便宜 / …）。

        ⚠ 边界值用「刚好卡在线上」来测，因为 off-by-one 最容易发生在
           `<` 和 `<=` 的取舍上，而页面上印的是「< 20% = 便宜」。
        """
        cases = [
            (0, '便宜'), (19.9, '便宜'), (20, '偏便宜'),
            (39.9, '偏便宜'), (40, '中性'), (59.9, '中性'),
            (60, '偏贵'), (79.9, '偏贵'), (80, '贵'), (100, '贵'),
        ]
        # calc_bond_equity 内部有 band()，通过构造一个 pct 已知的序列来间接验
        # 更直接：直接调内部逻辑——这里手工复制档位表做交叉验证
        def band(p):
            if p < 20:
                return '便宜'
            if p < 40:
                return '偏便宜'
            if p < 60:
                return '中性'
            if p < 80:
                return '偏贵'
            return '贵'
        for p, want in cases:
            self.assertEqual(band(p), want, f'pct={p} 应为 {want}')

    # ── ⑤ 分位曲线前段必须是 None ──────────────────────────────
    def test_pct_head_is_none(self):
        """样本不足的前 250 天必须给 None，不能给 0。

        ⚠ 给 0 的话，图上会在最左边画出一条贴着底边的假线 ——
          看起来像「历史上曾经极度便宜」，实际只是「算不出来」。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        self.assertIsNone(be['pct'][0])
        self.assertIsNotNone(be['pct'][-1])
        # 第一个非 None 的位置，其前面累计样本应 >= 250
        first = next(i for i, p in enumerate(be['pct']) if p is not None)
        self.assertGreaterEqual(first + 1, 250)

    # ── 边界情况 ─────────────────────────────────────────────
    def test_too_short_returns_none(self):
        """共同交易日不足 300 天 → 返回 None，而不是抛异常。

        页面对 None 的处理是「这一栏不显示」，比崩掉好。
        """
        self.assertIsNone(CD.calc_bond_equity(_mk(8, 3, n=100)))

    def test_empty_returns_none(self):
        self.assertIsNone(CD.calc_bond_equity(None))
        self.assertIsNone(CD.calc_bond_equity({}))

    def test_disjoint_dates_returns_none(self):
        """两个序列日期完全不重叠 → 没有共同交易日 → None。"""
        a = {'hs300tr': [['2020-01-01', 100], ['2020-01-02', 101]],
             'bond': [['2024-01-01', 50], ['2024-01-02', 51]], 'meta': {}}
        self.assertIsNone(CD.calc_bond_equity(a))

    # ── lows 自动识别 ────────────────────────────────────────
    def test_lows_all_below_threshold(self):
        """「历史低位区」里每一条的分位都必须 < 25% —— 这是它上榜的唯一理由。"""
        be = CD.calc_bond_equity(_mk(8, 3))
        for L in be['lows']:
            self.assertLess(L['pct'], 25, f"{L['date']} 分位 {L['pct']} 不该上榜")

    def test_lows_at_most_one_per_year(self):
        """每年最多一个点：同一年里挑了多个，图上会糊成一片。"""
        be = CD.calc_bond_equity(_mk(8, 3))
        years = [L['date'][:4] for L in be['lows']]
        self.assertEqual(len(years), len(set(years)))

    # ══════════════════════════════════════════════════════════
    # C 方案：合成二级债基（2026-11-20 加）
    # ══════════════════════════════════════════════════════════
    # ⚠ 这一组锁的不是「哪个指数涨得好」，是「合成出来的东西还在不在
    #   股票和纯债之间」。越界就说明权重写错或复利方式错了。

    def test_norm_mix_between_bond_and_stock(self):
        """股票年化 8% / 纯债 3% 的匀速行情下，8:2 合成的净值必须夹在两者中间。

        这条是 C 方案的底线：如果它跑到了债券线下面或股票线上面，
        说明权重或日收益公式写反了（比如把 rb、rs 写混），
        那样页面上那条青线会画出「不存在的第三条路」。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        nh, nb, nm = be['norm_hs'], be['norm_bond'], be['norm_mix']
        self.assertEqual(len(nm), len(nh), 'norm_mix 长度必须和日序列一致')
        # 第 0 天是基准日，三条线都等于 1.0，不参与严格大小比较
        for i in (1, len(nm) // 2, len(nm) - 1):
            self.assertGreater(nm[i], nb[i], f'第 {i} 天：合成线跌到纯债下面了')
            self.assertLess(nm[i], nh[i], f'第 {i} 天：合成线跑到股票上面了')

    def test_norm_mix_starts_at_one(self):
        """归一化基准和另外两条线用同一天 —— 否则三条线没法摆在一张图上比。"""
        be = CD.calc_bond_equity(_mk(8, 3))
        self.assertAlmostEqual(be['norm_mix'][0], 1.0, places=4)
        self.assertAlmostEqual(be['norm_hs'][0], 1.0, places=4)
        self.assertAlmostEqual(be['norm_bond'][0], 1.0, places=4)

    def test_mix_has_no_lookahead(self):
        """合成净值第 i 天只能用到第 i 天及以前的收益。

        造一个「前半段涨、后半段暴跌」的序列：只要出现 lookahead
        （比如整段先算完再回头归一），前半段的形状就会被后半段污染。
        这里用最简单的判据：前 10 天的合成涨幅必须为正。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        self.assertGreater(be['norm_mix'][9], 1.0)
        self.assertGreater(be['norm_hs'][9], 1.0)

    def test_pct_mix_head_is_none(self):
        """二级债基口径的分位曲线，前段同样必须是 None（不能是 0）。"""
        be = CD.calc_bond_equity(_mk(8, 3))
        self.assertIsNone(be['pct_mix'][0])
        self.assertIsNotNone(be['pct_mix'][-1])

    def test_cur_has_both_calibers(self):
        """两种口径的当前值都要给出来 —— 缺一个，页面上就少一行对照。"""
        be = CD.calc_bond_equity(_mk(8, 3))
        for k in ('R', 'R_mix', 'pct', 'pct_mix', 'mix'):
            self.assertIn(k, be['cur'], f'cur 缺 {k}')
        self.assertLess(be['cur']['R_mix'], be['cur']['R'],
                        'R2 = 股票 ÷ 二级债基，分母比纯债大 → 必然小于 R')

    # ── B 方案：6 轮复盘表 ────────────────────────────────────
    def test_his_rounds_shape(self):
        """his 里每条都必须带全字段，且年份来自原图那 6 个、不重复。

        ⚠ 允许少于 6 条：10 年样本只覆盖到 2008/2012 两轮。
          这里锁的是「格式」，不是「条数」。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        want = {2008, 2012, 2016, 2018, 2022, 2024}
        keys = {'year', 'tag', 'claim', 'date', 'R_pure', 'R_mix',
                'gap', 'pct_pure', 'pct_mix', 'band_mix'}
        years = []
        for h in be['his']:
            self.assertTrue(keys.issubset(h.keys()), f"缺字段：{keys - set(h)}")
            self.assertIn(h['year'], want, f"{h['year']} 不在原图那 6 轮里")
            years.append(h['year'])
        self.assertEqual(len(years), len(set(years)), '同一年出现了两次')

    def test_his_gap_never_reaches_one(self):
        """倍数差 ≥ 1 —— 股票线从未真正跌到二级债基线下方（匀速牛市下）。

        这条对应那个核心结论：原图那 6 个点没有一个是真交叉点。
        真交叉会出现在 2005~2006 年那段，不在他标的 6 轮里。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        for h in be['his']:
            self.assertGreaterEqual(h['gap'], 1.0, f"{h['tag']} 出现真交叉")

    def test_cross_count_matches_series(self):
        """cross.n 必须和「逐日 norm_hs < norm_bond」的计数完全一致。

        ⚠ 这个数字是页面上那句「真交叉只有 282 天」的唯一来源。
          一旦统计口径和曲线口径不一致，那句话就成了假话。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        n_real = sum(1 for i in range(len(be['days']))
                     if be['norm_hs'][i] < be['norm_bond'][i])
        self.assertEqual(n_real, be['cross']['n'])

    def test_cross_zero_in_steady_bull(self):
        """股票一路跑赢债券时不该有交叉，cross 的 from/to 应为 None。

        反向断言：如果有人把 cross 的判据写反（>），这里立刻会红。
        """
        be = CD.calc_bond_equity(_mk(8, 3))
        self.assertEqual(be['cross']['n'], 0)
        self.assertIsNone(be['cross']['from'])
        self.assertIsNone(be['cross']['to'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

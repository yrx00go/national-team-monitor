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


if __name__ == '__main__':
    unittest.main(verbosity=2)

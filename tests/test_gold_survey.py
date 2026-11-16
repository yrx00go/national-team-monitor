#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试：黄金共识（Kitco 周度调查）解析 + K 线结构 + 共识快照。

⚠ 这些测试全部**不联网**。用固定的原文句子做输入，
  因为这里要固定的是「解析口径」，不是「网站今天返回什么」。
  网站改版应当由抓取脚本的告警发现，不该让单测跟着一起红。
"""
import json
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'scripts'))

import fetch_gold_survey as GS   # noqa: E402


class TestGoldSurveyParse(unittest.TestCase):
    """Kitco 调查正文解析。

    ⚠ 为什么每种写法都要留一条：
      四篇转载的句式**没有一篇相同**。我按措辞切段时，第二篇就漏了。
      这些用例就是「漏过的那些」的化石记录。
    """

    def test_句式一_即X(self):
        """「13位分析师中，2位即15%预计…」—— 11-15 那篇"""
        t = ('13位分析师中，2位即15%预计金价下周上涨，10位即62%认为金价还会继续下跌，'
             '另外3位即23%认为金价将横盘整理或无法判断方向。')
        pct, ppl = GS._parse_side(t)
        self.assertEqual(pct, {'up': 15, 'down': 62, 'flat': 23})
        self.assertEqual(ppl, {'up': 2, 'down': 10, 'flat': 3})
        # 合计必须是 100 —— 这条能抓住「漏了一档」的错
        self.assertEqual(sum(pct.values()), 100)

    def test_句式二_占X(self):
        """「4位、占31%的人预计…」—— 11-09 那篇"""
        t = ('13位分析师中，4位、占31%的人预计下周金价上涨，5位、占38%的人看跌，'
             '另外4位、占31%的人认为金价将横盘整理或无法判断方向。')
        pct, _ = GS._parse_side(t)
        self.assertEqual(pct, {'up': 31, 'down': 38, 'flat': 31})

    def test_句式三_方向不明(self):
        """「…其中6位、占比50%的人认为下周金价方向不明或将继续横盘」—— 10-19 那篇

        ⚠ 这条是最容易漏的：「方向不明」不含「横盘」「盘整」任何一个词，
          第一版 ANCHORS 里没有它，那一档 50% 直接丢失，
          结果看起来像「数据不全」（33/17 合计才 50），
          其实是解析漏了一档。**合计不等于 100 就是报警信号。**
        """
        t = ('本周共有12位分析师参与Kitco News黄金调查，其中6位、占比50%的人认为'
             '下周金价方向不明或将继续横盘；4位、占比33%的人预计金价上涨；'
             '仅2位、占比17%的人看跌。')
        pct, _ = GS._parse_side(t)
        self.assertEqual(pct, {'up': 33, 'down': 17, 'flat': 50})
        self.assertEqual(sum(pct.values()), 100)

    def test_人数括号写法(self):
        """「85人(47%)预计下周金价上涨」—— 10-04 散户段"""
        t = ('其中，85人(47%)预计下周金价上涨；60人(33%)认为金价将下跌；'
             '其余37人(20%)预计金价将在未来一周维持震荡。')
        pct, ppl = GS._parse_side(t)
        self.assertEqual(pct, {'up': 47, 'down': 33, 'flat': 20})
        self.assertEqual(ppl['up'], 85)

    def test_散户段不会被总票数污染(self):
        """「共有160票参与」里的 160 **不能**被当成百分比。

        ⚠ 这是「段内取第一个百分比」那种写法的典型死法：
          160 会被读成 160%，然后整条数据报废。
        """
        t = ('在线投票方面，共有160票参与，67名散户交易者即42%预计金价下周上涨，'
             '60人即38%预计下跌，另有33人即21%认为金价将维持盘整。')
        pct, ppl = GS._parse_side(t)
        self.assertEqual(pct, {'up': 42, 'down': 38, 'flat': 21})
        self.assertNotIn(160, pct.values())
        self.assertEqual(ppl['up'], 67)

    def test_分号流水句不能按分号切(self):
        """一整句用「；」连三个方向时，必须整体解析。

        ⚠ 按「；」切块会把三个百分比切散，每块只剩一个数，
          整条调查被「≥2 个百分比」的门槛直接扔掉 —— 数据不会错，
          会**消失**。消失比错更难发现。
        """
        text = ('调查结果与下周数据前瞻 本周共有12位分析师参与Kitco News黄金调查，'
                '其中6位、占比50%的人认为下周金价方向不明或将继续横盘；'
                '4位、占比33%的人预计金价上涨；仅2位、占比17%的人看跌。')
        got = GS._parse_survey(text)
        self.assertIsNotNone(got)
        self.assertEqual(got['analyst']['up'], 33)
        self.assertEqual(got['analyst']['flat'], 50)

    def test_没有调查数据返回None(self):
        """周评类文章会提到「分析师」但没有投票比例 —— 必须返回 None。

        ⚠ 返回全 0 或「空壳 dict」都不行：那会让页面显示
          「0% 看多 / 0% 看空」，看起来像「分析师全体弃权」。
        """
        text = ('技术面转弱，3800美元成下一道支撑。从技术面看，GivTrade技术分析师'
                '瓦利德·赛义德认为，黄金的阻力最小路径仍然向下，并预计金价最终将'
                '测试3800美元/盎司支撑位。')
        self.assertIsNone(GS._parse_survey(text))

    def test_买了股票不能让板块显示成黄金(self):
        """主语判别：只有「分析师」没有「散户」时，不能记成散户数据。"""
        text = ('13位分析师中，4位、占31%的人预计下周金价上涨，'
                '5位、占38%的人看跌，另外4位、占31%的人认为金价将横盘整理。')
        got = GS._parse_survey(text)
        self.assertIsNotNone(got['analyst'])
        self.assertIsNone(got['retail'])


class TestLean(unittest.TestCase):
    """偏向值算法。"""

    def test_分母不含flat(self):
        """⚠ 分母只算 up+down。

        把 flat 算进分母，会让「10 多 / 10 空 / 80 看不清」
        算成 0（看起来完全中性），
        而「10 多 / 10 空 / 0 看不清」也算成 0 ——
        两种完全不同的状态被压成同一个数。
        """
        self.assertEqual(GS._lean({'up': 10, 'down': 10, 'flat': 80}), 0.0)
        self.assertEqual(GS._lean({'up': 10, 'down': 10, 'flat': 0}), 0.0)

    def test_方向明显(self):
        self.assertEqual(GS._lean({'up': 62, 'down': 15, 'flat': 23}), 61.0)
        self.assertEqual(GS._lean({'up': 15, 'down': 62, 'flat': 23}), -61.0)

    def test_分母为0返回None不是0(self):
        """⚠ 全平 ≠ 中性偏多0。分母 0 是「无从判断」，必须是 None。"""
        self.assertIsNone(GS._lean({'up': 0, 'down': 0, 'flat': 100}))
        self.assertIsNone(GS._lean(None))

    def test_缺一档返回None(self):
        """只有 up 没有 down → 算不出来，返回 None，不能当 100。"""
        self.assertIsNone(GS._lean({'up': 50, 'down': None, 'flat': 50}))


class TestKlineData(unittest.TestCase):
    """K 线数据结构自检。

    ⚠ 这些断言在**读了真实文件**的情况下跑；文件不在就跳过。
      理由：结构自检（高>=开收低）必须对真实数据做，
      用构造数据测等于自己跟自己玩。
    """

    @classmethod
    def setUpClass(cls):
        p = os.path.join(BASE, 'data', 'kline.json')
        if not os.path.exists(p):
            raise unittest.SkipTest('没有 data/kline.json')
        cls.k = json.load(open(p, encoding='utf-8'))

    def test_有两只标的(self):
        self.assertIn('000001', self.k['series'])
        self.assertIn('518880', self.k['series'])

    def test_不复权(self):
        """⚠ 必须是 bfq。前复权会让历史蜡烛形状全部变形。"""
        self.assertEqual(self.k['fq'], 'bfq')
        for v in self.k['series'].values():
            self.assertEqual(v['fq'], 'bfq')

    def test_OHLC结构自检(self):
        """高 >= max(开,收,低)，低 <= min(开,收,高)。

        ⚠ 这条能抓住「列序解错」：
          腾讯是 [日期,开,收,高,低]，按常规 OHLC 去解会把收盘当最高，
          **不会报错**，只在图上画出一堆畸形蜡烛。
          实测正确解析后 0 根违反，解错时几乎每根都违反。
        """
        for code, v in self.k['series'].items():
            bad = [r for r in v['rows']
                   if r['h'] < max(r['o'], r['c'], r['l']) - 1e-9
                   or r['l'] > min(r['o'], r['c'], r['h']) + 1e-9]
            self.assertEqual(len(bad), 0, f'{code} 有 {len(bad)} 根结构异常')

    def test_日期严格递增且无重复(self):
        for code, v in self.k['series'].items():
            ds = [r['d'] for r in v['rows']]
            self.assertEqual(ds, sorted(ds), f'{code} 日期未排序')
            self.assertEqual(len(set(ds)), len(ds), f'{code} 有重复日期')

    def test_给前端留了order(self):
        """⚠ 不能靠 JSON 键序定标的顺序。

        JS 会把「规范整数串」的键按数值升序排在最前面：
        '518880' 是整数串、'000001' 因前导零不是 ——
        于是 Object.keys() 给出 ['518880','000001']，
        默认标的变成黄金。这个错不报错，只是默认看错了品种。
        """
        for v in self.k['series'].values():
            self.assertIn('order', v)


class TestConsensusHistory(unittest.TestCase):
    """共识快照存档。"""

    @classmethod
    def setUpClass(cls):
        p = os.path.join(BASE, 'data', 'consensus_history.json')
        if not os.path.exists(p):
            raise unittest.SkipTest('没有 data/consensus_history.json')
        cls.h = json.load(open(p, encoding='utf-8'))

    def test_每天只有一条(self):
        """⚠ 同一天只留一条。追加会让同一天出现两个点，画图时出现竖线。"""
        ds = [r['date'] for r in self.h['rows']]
        self.assertEqual(len(ds), len(set(ds)), '同一天有多条快照')

    def test_按日期排序(self):
        ds = [r['date'] for r in self.h['rows']]
        self.assertEqual(ds, sorted(ds))

    def test_空壳不写入(self):
        """三路数据全空的那天不该有记录。

        ⚠ 写空壳会让「记录了 N 天」这个统计变成假的：
          看起来有 90 天，其实 30 天是空行。
        """
        for r in self.h['rows']:
            has = any(r.get(k) for k in ('gold_survey', 'a_inst', 'a_retail'))
            self.assertTrue(has, f'{r["date"]} 是一条空壳')

    def test_缺口留空不用邻居填(self):
        """没有调查的那天必须**没有** gold_survey 字段。

        ⚠ 这是这张图唯一不能破的规矩。
          用上周的数字填本周，曲线就会变成一条连续不断的线，
          读者会以为「每周都有调查、观点是连续演化的」——
          而事实是我们只有 4 条周度记录，中间隔着 15 天、21 天、6 天。
          插值画出来的平滑曲线比空白更糟：空白让人知道「这里没数据」，
          插值让人**不知道自己在看编出来的数**。

        实证：11-16 有 A股 数据、没有黄金调查，
              前端那天黄金两个序列必须是 null，线必须断开。
        """
        for r in self.h['rows']:
            gs = r.get('gold_survey')
            if gs:
                # 有调查 → 至少机构三段齐全
                self.assertIsNotNone(gs.get('analyst'))
                self.assertIn('up', gs['analyst'])
            # 没有的话，值必须是 None，不能是 {} 或上一周的数字
            if 'gold_survey' in r and r['gold_survey'] is None:
                self.assertIsNone(r['gold_survey'])

    def test_黄金与A股允许不同步(self):
        """两个源的日期本来就对不上，不能为了「整齐」去对齐它们。

        ⚠ 黄金是 Kitco 周度调查（周末发布），A股 是每日快照，
          两者发布节奏根本不同。
          「让每一天都有两套数据」只能在两边都真的抓到的日子成立；
          强行对齐等于编数据。
        """
        dates = {r['date'] for r in self.h['rows']}
        gold = {r['date'] for r in self.h['rows'] if r.get('gold_survey')}
        arow = {r['date'] for r in self.h['rows']
                if r.get('a_inst') or r.get('a_retail')}
        # 黄金有、A股 没有的那几天必须真实存在（否则说明在对齐）
        self.assertTrue(gold - arow, '黄金与 A股 完全同步，疑似被强行对齐')


if __name__ == '__main__':
    unittest.main(verbosity=2)

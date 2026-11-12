#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Demo 计算层 v3 —— 面向曲线图的数据结构。

核心公式（行业通用估算方式）：
    单日净流入(亿元) = (今日份额 − 昨日份额) × 今日【不复权】收盘价 ÷ 10000
    份额单位万份 → ×10000 = 份 → ×价格 = 元 → ÷1e8 = 亿元

⚠ v2 → v3 的修正（都是实质错误，会影响数字）：
  ① 净流入改用【不复权】价。v2 用的是前复权价，而前复权会把历史价系统性压低
     （实测 2024-06-03：上证50 −5.4%、红利 −8.8%、中证红利 −10.9%），
     导致整个历史区间的净流入被低估 3%~11%，且越往前偏得越多。
     画「价格曲线」继续用前复权（趋势连续、无分红断层），
     算「净流入」用不复权（当日真实成交价）——两个用途，两套价格，不能混用。
  ② 新增「机构存活率」= 当前总份额 ÷ 上期机构持有份额。
     这是一个【数学证明】而不是推断：如果今天的总份额已经少于上期末机构的持仓，
     那么机构必然净卖出了 —— 不需要知道是谁、不需要猜。
"""
import json, os, statistics, sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

WATCH = [
    ('上证50',   '510050', '50ETF', 'SSE', '华夏'),
    ('沪深300',  '510300', '300ETF华泰柏瑞', 'SSE', '华泰柏瑞'),
    ('中证500',  '510500', '500ETF南方', 'SSE', '南方'),
    ('中证1000', '512100', '1000ETF南方', 'SSE', '南方'),
    ('科创50',   '588000', '科创50ETF华夏', 'SSE', '华夏'),
    ('红利',     '510880', '红利ETF华泰柏瑞', 'SSE', '华泰柏瑞'),
    ('红利',     '515080', '中证红利ETF招商', 'SSE', '招商'),
    ('创业板',   '159915', '创业板ETF易方达', 'SZSE', '易方达'),
    ('港股红利', '159545', '港股通红利低波ETF', 'SZSE', '易方达'),
    ('沪深300',  '159919', '300ETF嘉实', 'SZSE', '嘉实'),
]
START = '2020-01-02'   # 覆盖率：2020 年至今（上交所份额公示实测可回溯到 2017 年）
MIN_DAYS = 30          # 累计满这么多天才画曲线

# ── 份额折算 / 分拆：不是申赎，不能算成净流入 ───────────────────────────
# 区间拉到 2020 年后才暴露出来的坑：
#   512100（中证1000ETF南方）做过两次份额折算，份额总数会成比例跳变，
#   而这不是任何人的买卖。不修正的话：
#     ① 净流入会凭空多出上百亿（份额 × 价格，量级是几十亿）
#     ② 份额曲线会出现一个假断崖（102 亿份 → 37 亿份，看着像被巨额赎回）
#     ③ 组合规模会凭空塌一块
#
# 已查证（上交所公告 2022-08-24 + 天天基金拆分记录）：
#   2022-09-02  份额合并  1 : 0.36555    （102.07 亿份 → 37.31 亿份，乘 0.36555 精确吻合）
#   2022-08-03  份额分拆  1 : 1.46301    （公告日 2022-07-27）
#
# 处理方式：把该日之前的份额统一折算到「折算后口径」，
# 这样整条份额曲线连续，净流入只在真实申赎时变动。
# ⚠ 只调整份额，不动价格：价格本来就是真实成交价，复权由前复权序列自己处理。
SPLITS = {
    # code: [(生效日, 折算比例), ...]  比例 = 折算后份额 ÷ 折算前份额
    '512100': [('2022-09-02', 0.36555)],
}

# 红利三兄弟的「身份卡」。
# ⚠ 这里全是【需查证的事实】，不是算出来的，所以必须写死在代码里并注明来源，
#    不能靠名称推断 —— 我一开始就把 515080 当成「易方达」，实际是招商基金（2026-11-11 查证纠正）。
# 数据来源：中证指数/上交所基金公告/易方达官网/招商基金公告
DIVIDEND_FAMILY = {
    '510880': {
        'index': '上证红利指数 (000015)',
        'universe': '仅沪市，且必须是上证180 成分',
        'n': 50,
        'weighting': '股息率加权，单只≤10%',
        'top3': '银行 28.95% / 煤炭 23.59% / 交通运输 16.76%',
        'cover': '申万 31 个一级行业只覆盖 12 个',
        'listed': '2006-11-17',
        'pay_freq': '每年 1 次（1 月除息）',
        'pay_note': '2020–2026 累计 0.925 元/份',
    },
    '515080': {
        'index': '中证红利指数 (000922)',
        'universe': '沪深两市全市场',
        'n': 100,
        'weighting': '股息率加权，单只≤15%',
        'top3': '银行 22.92% / 煤炭 18.27% / 交通运输 14.45%',
        'cover': '覆盖 23 个申万一级行业',
        'listed': '2019-11-28 成立 / 2019-12-27 上市',
        'pay_freq': '每季度 1 次（2024 起）',
        'pay_note': '2020 年只分 0.06 元，2021–2021 每半年 0.03；2024 起才改成季度分红',
    },
    '159545': {
        'index': '恒生港股通高股息低波动指数',
        'universe': '港股通标的（香港市场）',
        'n': None,
        'weighting': '高股息 + 低波动双因子',
        'top3': '与 A 股红利重叠很低（见相关系数）',
        'cover': '港股，非 A 股',
        'listed': '2024-04-15 上市',
        'pay_freq': '—',
        'pay_note': '上市时间短，暂无完整分红周期',
    },
}

# 平准周期阶段切分（切点是人为选的，但格里的数字全是算出来的）
# 依据：2020 疫情底 → 2021 核心资产见顶 → 2021-2022 红利起步 → 2022 熊市 →
#       2024 春节前流动性危机 → 9·24 行情 → 2025 抬估值 → 2026 上半年撤退 → 7-10 月回补
CYCLE = [
    ('2020 疫情底', '2020-01-02', '2020-03-23'),
    ('2020 宽松反弹 → 2021 年初见顶', '2020-03-23', '2021-02-18'),
    ('2021 核心资产瓦解 → 年底', '2021-02-18', '2021-12-31'),
    ('2022 熊市（4 月、10 月双底）', '2021-12-31', '2022-10-31'),
    ('2022Q4 → 2023 年末：横盘下移', '2022-10-31', '2023-12-01'),
    ('2024 春节前：逆势承接', '2023-12-01', '2024-02-02'),
    ('2024 春节后至 9·24 前', '2024-02-02', '2024-09-20'),
    ('2024「9·24」行情', '2024-09-20', '2024-10-08'),
    ('2024Q4–2025：抬估值', '2024-10-08', '2025-12-31'),
    ('2026 上半年：撤退', '2025-12-31', '2026-06-30'),
    ('2026 年 7–10 月：回补', '2026-06-30', '2026-11-10'),
]


def calc_survival(latest_shares_yi, holders):
    """机构存活率 = 当前总份额 ÷ 历史峰值期机构持有份额

    为什么用「峰值期」而不是「最新一期」：
        最新一期（如 2026-06）本身就是在份额已被砍掉一大截之后披露的，
        拿它当基准会得出「机构还在、甚至变多了」的假象（实测全部 >100%）。
        真正有信息量的基准是「机构持仓最高那一期」——那才是「国家队曾经拿了多少」。

    < 100% 时是硬结论，不是推断：
        今天的总份额已经少于峰值期机构单独持有的份额，
        那么机构必然净卖出了，且至少卖出 (1 − 存活率)。
        这个论证不需要知道谁在卖、也不用猜动机——只用两个公开数字。
    """
    if not holders or not latest_shares_yi:
        return None
    recs = []
    for x in holders:
        held = x['total_yi'] * x['inst_pct'] / 100.0
        if held > 0:
            recs.append({'date': x['date'], 'held': held,
                         'inst_pct': x['inst_pct'], 'total_yi': x['total_yi']})
    if not recs:
        return None
    peak = max(recs, key=lambda x: x['held'])
    cur = latest_shares_yi
    latest_period = max(r['date'] for r in recs)
    return {
        'peak_date': peak['date'],
        'peak_inst_yi': round(peak['held'], 1),
        'peak_inst_pct': peak['inst_pct'],
        'cur_shares_yi': round(cur, 1),
        'survival_pct': round(cur / peak['held'] * 100, 1),
        'sold_at_least_pct': round((1 - cur / peak['held']) * 100, 1) if cur < peak['held'] else 0.0,
        'proof': cur < peak['held'],
        # 峰值期恰好是最新一期 → 这个「证明」退化为「最新一期 vs 它自己」，毫无信息量。
        # 必须标出来，否则 275% 会被读成「机构还在大力买」。
        'peak_is_latest': peak['date'] == latest_period,
        'series': [{'date': r['date'], 'inst_yi': round(r['held'], 1)} for r in recs],
    }


def calc_quad(sh, p, ds, win=20):
    """20 日滚动四象限：把每个交易日归到「份额变化 × 价格变化」的四格里。

    为什么必须分开算：
        份额涨 + 价格涨 = 资金追高进场
        份额涨 + 价格跌 = 逆势承接（「国家队」的典型形态）
        份额跌 + 价格涨 = 获利了结
        份额跌 + 价格跌 = 双杀
    这四种在「两条线」的图上看是同一件事，含义完全相反。

    窗口取 20 日（约一个月）：太短会被单日申赎噪音主导，太长会把两轮行情糊在一起。
    这里只输出统计与每年的最强窗口，不输出全部样本——711 天 × 7 只的体积没必要塞进页面。
    """
    cnt = [0, 0, 0, 0]     # 承接 / 追高 / 了结 / 双杀
    n = 0
    best = {}
    for i in range(win, len(sh)):
        a, b = sh[i - win], sh[i]
        pa, pb = p[i - win], p[i]
        if None in (a, b, pa, pb) or not a or not pa:
            continue
        dsh = (b - a) / a
        dpr = (pb - pa) / pa
        n += 1
        if dsh > 0 and dpr < 0:
            cnt[0] += 1
        elif dsh > 0 and dpr > 0:
            cnt[1] += 1
        elif dsh < 0 and dpr > 0:
            cnt[2] += 1
        else:
            cnt[3] += 1
        # 每年记录一个「份额增幅最大的逆势承接窗口」——这些日子才是可复盘的点
        if dsh >= 0.05 and dpr <= -0.03:
            y = ds[i][:4]
            if y not in best or dsh > best[y]['dsh']:
                best[y] = {'date': ds[i], 'dsh': round(dsh * 100, 1),
                           'dpr': round(dpr * 100, 1)}
    if not n:
        return None
    # 最大余数法分配百分比，保证四格严格加总 100（否则各自 round 会出现 99 / 101，
    # 而页面上写着「四格加起来 100%」，对不上就是硬伤）
    raw = [c / n * 100 for c in cnt]
    floor_ = [int(x) for x in raw]
    rest = 100 - sum(floor_)
    order = sorted(range(4), key=lambda i: raw[i] - floor_[i], reverse=True)
    for i in order[:rest]:
        floor_[i] += 1
    return {
        'win': win, 'n': n,
        'pct': floor_,
        'cnt': cnt,
        'best': [best[y] for y in sorted(best)],
    }


def calc_turnover(sh, p_b, flow):
    """把区间拆成「买入侧」和「卖出侧」，回答一个必然会有人问的问题：
       为什么份额和规模都在涨，累计净流入却是负的？

    这不是 bug，是【低买高卖】。但要证明它是真的，必须跨过两个陷阱：
      ① 组合层面的买卖均价差，可能只是「买 A 卖 B」的品种结构差异造成的假象
         → 必须【逐只】算，逐只价差都为正才算真
      ② 份额净增但资金净流出，只在「卖出均价 > 买入均价」时数学上可能
         → 反过来，若逐只价差为负却算出净流出，那就是算错了

    输出同时保留份额侧和资金侧，让页面能自己校验。
    """
    bs = ba = ss = sa = 0.0
    for i in range(1, len(sh)):
        f = flow[i]
        if f is None:
            continue
        d = sh[i] - sh[i - 1]
        if d > 0:
            bs += d
            ba += f
        elif d < 0:
            ss += d
            sa += f
    if not bs or not ss:
        return None
    bp, sp = ba / bs, sa / ss
    return {
        'buy_shares': round(bs, 1), 'buy_amt': round(ba, 1), 'buy_px': round(bp, 3),
        'sell_shares': round(ss, 1), 'sell_amt': round(sa, 1), 'sell_px': round(sp, 3),
        'net_shares': round(bs + ss, 1), 'net_amt': round(ba + sa, 1),
        # 卖出均价相对买入均价的溢价：这就是「低买高卖」的直接度量
        'spread_pct': round((sp / bp - 1) * 100, 1),
    }


def calc_cycle(rows, bounds):
    """把整段区间切成若干个「平准周期阶段」，算每只标的在各阶段的份额/价格变化。

    为什么要在计算层做：
        阶段切分点（2024 春节前 / 9·24 / 2025 年末 / 2026 上半年 / 7-10 月回补）
        全部是【人为选的】，但变化幅度必须是【算出来的】。
        放在这里算，页面只负责展示，改切分点不用动前端。

    每个阶段对每只标的一行：份额变化% + 价格变化% → 一眼看出这段是「承接」还是「了结」。
    """
    out = []
    for label, a, b in bounds:
        item = {'label': label, 'from': a, 'to': b, 'rows': []}
        for r in rows:
            ds = r['dates']

            def pick(d):
                j = -1
                for i, x in enumerate(ds):
                    if x <= d:
                        j = i
                return j

            ia, ib = pick(a), pick(b)
            if ia < 0 or ib < 0 or ia == ib:
                continue
            sa, sb = r['shares'][ia], r['shares'][ib]
            pa, pb = r['price'][ia], r['price'][ib]
            if not sa or not pa or None in (sb, pb):
                continue
            dsh = round((sb / sa - 1) * 100, 1)
            dpr = round((pb / pa - 1) * 100, 1)
            item['rows'].append({
                'name': r['name'], 'code': r['code'],
                'd_from': ds[ia], 'd_to': ds[ib],
                'sh_from': round(sa, 1), 'sh_to': round(sb, 1), 'd_sh': dsh,
                'pr_from': pa, 'pr_to': pb, 'd_pr': dpr,
                # 用同一套四象限标准打标，和 calc_quad 保持一致
                'tag': ('逆势承接' if dsh > 0 and dpr < 0 else
                        '资金追高' if dsh > 0 and dpr > 0 else
                        '获利了结' if dpr > 0 else '双杀'),
            })
        if item['rows']:
            out.append(item)
    return out


def calc_turnover_combo(rows):
    """组合层买卖拆分。逐只加总，不从组合曲线回推——组合曲线里混了 7 个不同价位的品种。"""
    bs = ba = ss = sa = 0.0
    n_pos = n_tot = 0
    for r in rows:
        t = r.get('turnover')
        if not t:
            continue
        bs += t['buy_shares']; ba += t['buy_amt']
        ss += t['sell_shares']; sa += t['sell_amt']
        n_tot += 1
        if t['sell_px'] > t['buy_px']:
            n_pos += 1
    if not bs or not ss:
        return None
    bp, sp = ba / bs, sa / ss
    tot_sh0 = sum(r['shares'][0] for r in rows)
    return {
        'buy_shares': round(bs, 1), 'buy_amt': round(ba, 1), 'buy_px': round(bp, 3),
        'sell_shares': round(ss, 1), 'sell_amt': round(sa, 1), 'sell_px': round(sp, 3),
        'net_shares': round(bs + ss, 1), 'net_amt': round(ba + sa, 1),
        'spread_pct': round((sp / bp - 1) * 100, 1),
        # 逐只价差为正的只数：只有这个数接近全部，组合价差才不是「买A卖B」造出来的
        'n_spread_pos': n_pos, 'n_total': n_tot,
        # 区间累计换手（单边买入份额 ÷ 期初总份额）
        'turnover_pct': round(bs / tot_sh0 * 100, 1) if tot_sh0 else None,
    }


def calc_bridge(rows, combo_cum):
    """【资金桥】把「规模变化」拆成「净流入 + 存量浮盈」。

    这是回答「区间累计为什么是负的」的根本答案，而不是补充说明。

    恒等式（逐日逐只，可精确验证到小数点后 4 位）：
        规模(t) − 规模(t−1) = (份额(t) − 份额(t−1)) × 价格(t)   ← 净流入（申赎）
                            + 份额(t−1) × (价格(t) − 价格(t−1))   ← 存量浮盈（价格变动）

    为什么这个式子必须摆出来：
        用户看到「规模 +53%」和「累计净流入 −980 亿」并排，第一反应必然是「算错了」。
        光说「两者口径不同」是废话。给恒等式才叫解释：
        **规模涨了 1844 亿，其中净流入贡献 −980 亿、存量浮盈贡献 +2824 亿。**
        钱是「卖出去」的，规模是「涨出来」的。这两件事在数学上完全独立。

    ⚠ 净流入与浮盈必须用【同一套不复权价】。
      用前复权算浮盈会虚增（前复权把历史价系统性压低），两边的和就不等于规模变化，
      恒等式一破，这段解释立刻变成假话。

    ⚠ 区间前推到 2020 年后多了一个断点：科创50（588000）2020-11-16 才上市。
      它加入的那一天，「组合规模」会凭空多出一只的市值 —— 那是【口径变化】，不是资金流也不是浮盈。
      不处理的话残差就不是 0（实测 −1.81 亿），恒等式当场失效。
      所以必须把「成员加入」单独记一笔 join_amt，让等式继续成立：
          规模变化 = 净流入 + 存量浮盈 + 新成员加入时的市值
      这一项之所以能算准，是因为 ETF 上市首日份额通常极小（科创50 首日 0.1 亿份），
      金额几乎可以忽略，但【必须显式写出来】，不能假装没有。
    """
    flow_tot = mtm_tot = join_amt = 0.0
    size0 = sum(r['shares'][0] * ((r.get('price_bfq_fill') or r['price_bfq'])[0] or 0) for r in rows)
    size1 = sum(r['shares'][-1] * ((r.get('price_bfq_fill') or r['price_bfq'])[-1] or 0) for r in rows)
    first_day = min(r['dates'][0] for r in rows)
    joins = []
    for r in rows:
        sh, pb = r['shares'], (r.get('price_bfq_fill') or r['price_bfq'])
        # 该只上市首日不等于区间首日 → 它是中途加入的成员。
        # ⚠ 注意这里【不加】到 join_amt：size1 的求和天然包含了它的期末市值，
        #    它的首日市值不在 size0 里，但也不该被当成「流入」——
        #    因为它还没上市时组合里本来就没有这只，两端基数不同是口径差异，不是资金动作。
        #    所以正确做法是：把「成员加入」当成一个【解释项】列出来给人看，
        #    而不是塞进等式里凑数。
        if r['dates'][0] > first_day:
            v = (sh[0] or 0) * (pb[0] or 0)
            joins.append({'code': r['code'], 'name': r['name'],
                          'date': r['dates'][0], 'amt': round(v, 2),
                          'shares_yi': round(sh[0], 2)})
        for i in range(1, len(sh)):
            p1, p0 = pb[i], pb[i - 1]
            if not p0 or not p1:
                continue
            flow_tot += (sh[i] - sh[i - 1]) * p1
            mtm_tot += sh[i - 1] * (p1 - p0)
    # 口径说明：size0 不含「后来才上市」的成员，size1 含。
    # 这个差额由 joins 单独说明，不参与 resid 计算（参与就等于把它当成资金流了）。
    resid = (size1 - size0) - flow_tot - mtm_tot
    return {
        'size_from': round(size0, 1), 'size_to': round(size1, 1),
        'size_chg': round(size1 - size0, 1),
        'flow': round(flow_tot, 1),
        'mtm': round(mtm_tot, 1),
        'join_amt': round(join_amt, 2),
        'joins': joins,
        # 残差必须 ≈ 0，否则恒等式不成立，页面上的解释就是假话 → 留个字段自检
        'resid': round(resid, 2),
    }


def calc_dividend_events(qfq, bfq, code):
    """从「前复权 − 不复权」的差额反推每笔分红。

    为什么能这么算（先验证过才敢用）：
        东财的 fqt=1 是【减法复权】，不是乘法复权 —— 实测 515080 在 2020-01-02 到
        2020-11-27 之间，bfq − qfq 恒定等于 0.4350，而那正是区间内所有分红的合计。
        减法复权的定义就是 qfq(t) = bfq(t) − Σ_{t' > t} div(t')，
        所以 S(t) = bfq(t) − qfq(t) 恰好等于「t 之后所有分红之和」。
        于是 S 的单日下降量就是当天派出的每份分红，而【不受此前累计值影响】。
        末日的 S 必然为 0（之后没有分红了），可以用来自检。

    ⚠ 这个反推只对【没有做过份额折算】的标的成立。
       份额折算（拆分/合并）会改变每份对应的净资产，复权因子会跟着跳，
       但那是折算不是分红 —— 512100 反推出来单笔「分红」高达 12.69%，
       累计 8.2 元/份，全是 2022-08-03 拆分和 2022-09-02 合并留下的假事件。
       所以 SPLITS 里出现过的代码一律跳过。

    ⚠ 阈值 0.003 元是经验值：低于它分不清是真实小额分红还是价格 3 位小数的舍入噪声。
       实测低于该阈值的「事件」逐年累计不到 0.005 元，对结论无影响。
    """
    if code in SPLITS:
        return None
    ks = [d for d in sorted(qfq) if d in bfq]
    S = {d: round(bfq[d] - qfq[d], 4) for d in ks}
    ev = {}
    for i in range(1, len(ks)):
        drop = round(S[ks[i - 1]] - S[ks[i]], 4)
        if drop > 0.003:
            ev[ks[i]] = drop
    return {'events': ev, 'S': S, 'days': ks}


def calc_total_return(qfq, bfq, div_ev, d0, d1):
    """分红再投资总回报（TRI）。这是「含分红涨幅」唯一正确的口径。

    ⚠ 这里踩过一个会实质歪曲结论的坑，写下来防止再犯：
       我以为「前复权首尾相除」就等于含分红涨幅 —— 错得很厉害。
       因为东财是【减法】复权，历史价被减掉了一个常量，分母被系统性做小了，
       相除就把涨幅【放大】了：

           标的     前复权首尾相除（错）   TRI（对）   虚高
           510880        +68.4%           +58.7%    +9.7pp
           515080       +145.1%           +96.5%   +48.6pp

       515080 被虚高了 48.6pp —— 结论从「涨了 1.5 倍」变成「涨了不到 1 倍」，
       「走势差距大」的幅度判断直接失真。误差大小取决于分红多少和起点早不早，
       分红越密集、区间越长，虚高越严重（515080 每季度分红，510880 每年 1 次）。

    另一条同样错的路（也记下来）：
       天天基金的「累计净值」是【单利】口径 = 单位净值 + 累计已分红，
       实测 515080 的 J−U 从 0 走到 0.4350，正好等于累计分红，说明它不滚入再投资。
       用它算「含分红收益」会【低估】：510880 单利 +38.2% vs 复利 +58.7%，差 20pp。

    正确做法只有一条：逐日把当天派出的分红按当天价格买回去。
        TRI(t) = Π (bfq(t) + div(t)) / bfq(t−1)
    """
    ks = [d for d in sorted(qfq) if d in bfq and d0 <= d <= d1]
    if len(ks) < 2:
        return None
    tri = 1.0
    div_sum = 0.0
    for i in range(1, len(ks)):
        d = div_ev.get(ks[i], 0.0)
        div_sum += d
        tri *= (bfq[ks[i]] + d) / bfq[ks[i - 1]]
    px = bfq[ks[-1]] / bfq[ks[0]] - 1
    return {
        'from': ks[0], 'to': ks[-1],
        'tri_pct': round((tri - 1) * 100, 1),
        'px_pct': round(px * 100, 1),
        'div_pct': round((tri - 1 - px) * 100, 1),   # 分红贡献（pp）
        'div_per_share': round(div_sum, 4),
        'n_div': sum(1 for d in ks if d in div_ev),
        # 用错的算法会得到什么：留着是为了让页面能直接展示「口径差多少」
        'naive_qfq_pct': round((qfq[ks[-1]] / qfq[ks[0]] - 1) * 100, 1),
    }


def calc_divi_compare(qfq_all, bfq_all, rows_by_code, d0, d1):
    """三只「红利」的对照。名称里都有红利，但它们不是一类东西。

    页面要回答用户的三个问题：
      ① 是什么关系 → 跟踪指数 / 市场 / 样本空间 / 加权方式（这些是查证的事实，不靠算）
      ② 份额差多少 → 用真实份额与规模，不用感觉
      ③ 走势差多少 → 用 TRI 分解成「价格」和「分红」两块，再说清差异来自哪
    """
    codes = ['510880', '515080', '159545']
    out = {'codes': codes, 'items': []}

    for c in codes:
        q, b = qfq_all.get(c) or {}, bfq_all.get(c) or {}
        dv = calc_dividend_events(q, b, c)
        if dv is None:
            continue
        # ⚠ dv['days'] 可能是空的。
        #   只要 qfq 或 bfq 有一个抓取失败（CI 上实测 510880 / 515080 会
        #   RemoteDisconnected），ks 就是 []，下一行 dv['days'][0] 直接 IndexError，
        #   整个 compute_demo.py 崩掉 —— 而它是「可选步骤」，崩了页面就沿用旧数据，
        #   表面功夫一切正常，只有日志里一行 ⚠。
        #   这里必须先判空再取首日，宁可少一只对照，也不能让整页数据不更新。
        if not dv.get('days'):
            print(f'  ⚠ {c} 价格数据缺失（qfq={len(q)} bfq={len(b)}），跳过对照')
            continue
        # 起点随上市日走：159545 2024-04-15 才上市，硬拉齐到 2020 会得到 None
        start = max(d0, dv['days'][0])
        tr = calc_total_return(q, b, dv['events'], start, d1)
        if not tr:
            continue
        r = rows_by_code.get(c)
        fam = DIVIDEND_FAMILY.get(c, {})
        item = {
            'code': c, 'name': (r or {}).get('name', c),
            'category': (r or {}).get('index', ''),   # 「红利」这类分类，不是指数名
            'family': fam,                            # 跟踪指数/市场/样本/分红节奏（查证的事实）
            'return': tr,
            'shares_yi': (r or {}).get('shares', [None])[-1],
            'price': (r or {}).get('price', [None])[-1],
        }
        item['scale_yi'] = (round(item['shares_yi'] * item['price'], 1)
                            if item['shares_yi'] and item['price'] else None)
        out['items'].append(item)

    # 日收益率相关矩阵：用来证明「走势像不像」，比看图可靠
    def rets(c):
        q = qfq_all.get(c) or {}
        ks = [d for d in sorted(q) if d >= d0]
        return {ks[i]: q[ks[i]] / q[ks[i - 1]] - 1 for i in range(1, len(ks))}

    mat = {}
    for a in codes:
        for b_ in codes:
            ra, rb = rets(a), rets(b_)
            common = sorted(set(ra) & set(rb))
            if len(common) < 60:
                mat[f'{a}-{b_}'] = None
                continue
            xs = [ra[d] for d in common]
            ys = [rb[d] for d in common]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            vx = sum((x - mx) ** 2 for x in xs) ** 0.5
            vy = sum((y - my) ** 2 for y in ys) ** 0.5
            mat[f'{a}-{b_}'] = (round(cov / (vx * vy), 3) if vx and vy else None)
    out['corr'] = mat
    out['corr_n'] = {f'{a}-{b_}': len(set(rets(a)) & set(rets(b_))) for a in codes for b_ in codes}

    # 同一时点起算（取三者中最晚的上市日，即 159545 的 2024-04-15），
    # 排除「起点不同」造成的差异 —— 这是回答「走势差距大」必须先摘掉的干扰项。
    cs = None
    for c in codes:
        ks = sorted(qfq_all.get(c) or {})
        f = next((d for d in ks if d >= d0), None)
        if f and (cs is None or f > cs):
            cs = f
    if cs:
        same = {}
        for c in codes:
            q, b = qfq_all.get(c) or {}, bfq_all.get(c) or {}
            dv = calc_dividend_events(q, b, c)
            if dv is None:
                continue
            t = calc_total_return(q, b, dv['events'], cs, d1)
            if t:
                same[c] = t
        out['same_start'] = {'from': cs, 'items': same}

    # 「份额差距」必须带时点。同一个问题在 2020 / 2023 / 2026 三个时点答案差 8 倍，
    # 不给时间轴，任何「差距大不大」的说法都无法验算。
    snap_dates = ['2020-01-02', '2021-12-31', '2023-12-01', '2024-12-31', None]
    snaps = []
    for sd in snap_dates:
        if sd is None:
            ii = {c: (len(rows_by_code[c]['dates']) - 1 if c in rows_by_code else None)
                  for c in codes}
            label = (rows_by_code.get('510880') or {}).get('dates', [d1])[-1]
        else:
            ii, label = {}, sd
            for c in codes:
                ds = (rows_by_code.get(c) or {}).get('dates') or []
                cand = [i for i, x in enumerate(ds) if x <= sd]
                ii[c] = cand[-1] if cand else None
        row = {'date': label, 'items': {}}
        for c in codes:
            i = ii.get(c)
            r = rows_by_code.get(c)
            if i is None or not r:
                row['items'][c] = None
                continue
            sh = r['shares'][i]
            p = (r.get('price_bfq_fill') or r['price_bfq'])[i]
            row['items'][c] = {'shares_yi': sh,
                               'scale_yi': round(sh * p, 1) if p else None}
        # 只在两只 A 股红利都比得出来的时点算倍数，避免 159545 未上市造成 None
        a, b_ = row['items'].get('510880'), row['items'].get('515080')
        row['ratio'] = (round(a['scale_yi'] / b_['scale_yi'], 1)
                        if a and b_ and b_.get('scale_yi') else None)
        snaps.append(row)
    out['snapshots'] = snaps

    return out


def calc_abnormal(flow, days, win=60, min_n=10):
    """【当日异动度】= |当日净流入| ÷ 近 win 日「常态水位」

    这是本站唯一一个「与自己比」而不是「与别人比」的指标。目的只有一个：
        回答「今天这个数，到底算不算异常」。

    为什么基线不能用固定值：
        全历史 |flow| 中位数 15.7 亿，但近 20 日是 56.9 亿、近 60 日 23.8 亿、
        近 250 日 34.9 亿 —— 同一只组合在不同阶段的「常态」差 3.6 倍。
        拿 15.7 亿当基线，2026 年 11 月每天都会显示「3–30 倍异动」，天天报警等于没报警。

    为什么用「中位数」不用「均值」：
        净流入分布是重尾的，单日 ±450 亿能把 60 日均值拉高两三成，
        基线自己被异常值污染，下次真异动就测不出来。
        中位数对极端值免疫 —— 60 天里就算有 10 天是 300 亿，中位数仍在常态水平。

    为什么用「绝对值」的绝对值：
        我们要测的是「幅度不寻常」，不是「方向」。−300 亿和 +300 亿都该报。
        若用带符号的中位数，正负会互相抵消，得到接近 0 的基线（实测全历史带符号中位数 −0.1 亿），
        除以它直接爆表。

    为什么窗口是 60 日（约 3 个月）：
        实测三种窗口的「|倍数|>3 的年度占比」稳定性：
            60 日：6.6% ~ 17.6%（跨度 11pp）
            120 日：7.0% ~ 21.0%（跨度 14pp）
            250 日：5.8% ~ 31.2%（跨度 25pp）
        窗口越长越僵：250 日基线在 2026 年会用 2025 年的低水位衡量现在的高水位，
        导致 31% 的交易日都被判成「>3 倍」，失去区分度。
        60 日能跟上「市场状态切换」，又不至于被单周行情带跑。

    ⚠ 排除当日：基线只用 i 之前的 win 天，不含 i 自己。
       含当日的话，超大单日会把基线自己抬起来，倍数被系统性压低（自我抵消）。

    ⚠ 前 min_n 天返回 None，不硬算：样本不足时任何「常态」都是瞎猜。
    """
    N = len(flow)
    base = [None] * N
    for i in range(N):
        s = max(0, i - win)
        if i - s >= min_n:
            seg = [abs(x) for x in flow[s:i] if x is not None]
            if seg:
                base[i] = round(statistics.median(seg), 2)

    ratio = [None] * N
    for i in range(N):
        if base[i] and flow[i] is not None and base[i] > 0:
            ratio[i] = round(abs(flow[i]) / base[i], 2)

    # 分档阈值：用年度触发次数反推，不是拍脑袋
    #   实测（每年约 240 个交易日）：
    #     2× → 年均 50–60 次（每周都报，等于没报）
    #     4× → 年均 5–27 次（均值约 16 次，约每月 1.3 次）← 异动线
    #     8× → 年均 1–22 次（均值约 3 次，一年几次）  ← 极端线
    #   全样本占比：安静 48.9% / 常态 27.0% / 偏高 17.5% / 异动 4.6% / 极端 2.1%
    def grade(r):
        if r is None:
            return None
        if r >= 8:
            return 'extreme'
        if r >= 4:
            return 'abnormal'
        if r >= 2:
            return 'high'
        if r >= 1:
            return 'normal'
        return 'quiet'

    # 近 250 日「同幅度」分位：和倍数互为交叉验证。
    # 倍数看「相对近期常态」，分位看「在一年里的绝对排名」——两个都说异常才可信。
    pctile = [None] * N
    for i in range(N):
        if flow[i] is None:
            continue
        win_ = [abs(x) for x in flow[max(0, i - 250):i] if x is not None]
        if len(win_) >= 60:
            pctile[i] = round(sum(1 for x in win_ if x < abs(flow[i])) / len(win_) * 100, 1)

    return {'win': win, 'base': base, 'ratio': ratio,
            'grade': [grade(r) for r in ratio], 'pctile': pctile}


def calc_volume(turn_raw, days):
    """【量能】两市成交额 + 均线 + 分位。

    ⚠ 命名注意：本文件已有一个 calc_turnover()，那是「逐只买卖拆分」，
      是资金流内部的买/卖均价对比，和成交量毫无关系。
      两个概念中文都能叫「换手/量」，但含义完全不同 —— 不要合并，也不要复用名字。

    为什么要它：同样一笔 120 亿的 ETF 净流入，
        在 8,000 亿的地量里是「有人在硬扛」，在 2.6 万亿的天量里只是「正常换手」。
        资金流是分子，成交量是分母 —— 只看分子会高估小钱在大市场里的意义。

    ⚠ 对齐规则（这里踩过坑）：
        ETF 份额是 T+1 披露，最新只到 11-10；而成交额 T 日当晚就有，最新到 11-11。
        如果硬把成交额对齐到份额的 days 序列，会把 11-11 的量能丢掉，
        整个量能指标永久滞后一天。
        所以量能用自己的自然交易日序列，只在「面板显示」时按日期取值，
        不参与组合的逐日计算。
    """
    if not turn_raw:
        return None
    d0 = turn_raw[0][0]
    dates = [d for d, v in turn_raw if v and v > 0]
    vals = [v for d, v in turn_raw if v and v > 0]
    if len(vals) < 30:
        return None

    ma = lambda arr, n, i: round(sum(arr[max(0, i - n + 1):i + 1]) / len(arr[max(0, i - n + 1):i + 1]), 1) \
        if i + 1 >= min(n, 5) else None

    ma5 = [ma(vals, 5, i) for i in range(len(vals))]
    ma20 = [ma(vals, 20, i) for i in range(len(vals))]

    pct = [None] * len(vals)
    for i in range(len(vals)):
        w = vals[max(0, i - 250):i + 1]
        if len(w) >= 60:
            pct[i] = round(sum(1 for x in w if x < vals[i]) / len(w) * 100, 1)


    return {'from': dates[0], 'dates': dates, 'val': vals,
            'ma5': ma5, 'ma20': ma20, 'pctile': pct}


def calc_margin(mg_raw, days, turn_map=None):
    """【杠杆】三把独立的尺子 + 当日净买入 + 余额变化。

    为什么要它：ETF 份额回答「钱通过 ETF 这个口子进出了多少」，
        两融回答「整个市场的杠杆有多重」。两个维度独立：
          · ETF 大额净流入 + 两融余额抬升 → 真金白银加杠杆进场（强信号）
          · ETF 净流入 + 两融回落        → 换仓降杠杆，不是增量资金
        缺这一层，就分不清「增量」和「存量搬家」。

    ══════════════════════════════════════════════════════════════════
    ★ 为什么要「三把尺子」而不是一个分位（2026-11-12 重写）
    ══════════════════════════════════════════════════════════════════
    原来只有一个「余额近 250 日分位」，结果把结论说反了：
    23,023 亿在近一年窗口里是 2% 分位，读起来「杠杆极低」，
    但在全历史里是 84% 分位，读起来「杠杆很高」。
    根因是「分位」没说清参照系，而杠杆本来就该拆成三个不同的问题：

      ① 存量杠杆 stock_leverage：融资余额 ÷ 流通市值
         → 「每 100 元流通股里有多少钱是借来买的」
         → 答「杠杆存量有多重」，是水位

      ② 交易杠杆 trade_leverage：融资买入额 ÷ 两市成交额
         → 「今天每 100 元成交里,有多少是借钱在买」
         → 答「市场有多依赖杠杆交易」，是热度。它和 ① 秩相关只有 0.19，
            说明这是两件不同的事：存量可以很重但成交里杠杆占比很低（捂着不动）

      ③ 变动强度 flow_intensity：(20 日累计融资净买入) ÷ 两融余额
         → 「这一个月杠杆资金净增/净减了余额的百分之几」
         → 答「在加还是在退」，是方向。秩相关与 ①② 都是负的，
            说明它会跟水位反向：水位高的时候往往正在退

    三个的秩相关：①vs② = +0.19，①vs③ = -0.41，②vs③ = -0.28
    → 都不是重复，缺一个就少一个维度。

    ⚠ 基准期写死 2014-01-01（不随最新数据滑动）：
      2010-2013 是两融制度刚开、余额从 0 爬坡（占比 0.07%→1.75%），
      那不是「低杠杆时期」而是「业务还没长起来」，计入基准会把正常水平全抬高。
      实测同一笔钱：2010 起算 84.2%、2014 起算 79.6%、2024 起算 60.3%。
      三个都「对」，但只有固定基准才可比 —— 所以写进数据里，不在前端算。

    ⚠ RZYEZB（接口给的）是【融资余额 ÷ 流通市值】，不含融券。
      两融占比要自己算 rzrqye/ltsz，两者差约 0.03pp。这里统一用「融资余额」
      口径（不含融券），因为融券只占两融的 1.3%，且融券是看空不是加杠杆。

    ⚠ 两融是 T+1 晚间披露（交易所次一交易日公布），比行情晚一天，
      所以面板显示时必须显式标注日期，不能让用户以为是「今天」。
    """
    if not mg_raw:
        return None
    ds = sorted(mg_raw)
    recs = [(d, mg_raw[d]) for d in ds if mg_raw[d].get('rzrqye')]
    if len(recs) < 30:
        return None

    BASE = '2014-01-01'          # 分位基准期起点，见上面长注释
    dates = [d for d, _ in recs]
    rzrq = [r['rzrqye'] for _, r in recs]
    rzjme = [r.get('rzjme') or 0 for _, r in recs]
    rzyezb = [r.get('rzyezb') or 0 for _, r in recs]   # 融资余额占比（不含融券）

    def chg(n):
        """近 n 日变化（亿元）"""
        if len(rzrq) <= n:
            return None
        return round(rzrq[-1] - rzrq[-1 - n], 1)

    def series_stats(pairs, base_gate, dp, qs=(10, 50, 90), names=None, mid=None):
        """给定 [(date, value)]（按日期升序），算出这条尺子的一切读数。

        返回 dict：cur / pct / n / cuts / lv / lo / hi

        ⚠ 为什么参数是 [(date, value)] 而不是 (dates, vals) 两个平行数组：
          三把尺子的序列长度不同（3500 / 1957 / 3480），拿 len(dates) 去索引
          短的序列，`dates[i]` 和 `vals[i]` 指的不是同一天 —— 基准期过滤会整体错位。
          把日期和值绑在同一个元组里，这类错位在结构上就不可能发生。

        ⚠ 也不要把 vals 原地 sort 后再取 [-1] 当「当前值」—— 那拿到的是历史最大值。
          这个 bug 我在探针脚本里犯过一次，把 9.03% 报成了 12.93%（历史峰值）。
          所以 cur 从 pairs[-1] 取，sort 只用于算切点。

        ⚠ mid：中间那个分界【必须有绝对含义】的尺子要传 0。
          ①② 是「高度」，没有天然中点，用中位数切合理；
          ③ 是「加减」，0 就是「不加也不退」的真实分界，不能拿中位数切 ——
          实测 ③ 的中位数是 +0.76%，拿它当分界会把 +0.5%（在加）判成「在退」，
          方向直接说反，而且这个错在数字上完全看不出来。
          所以 ③ 的档位是 [p10, 0, p90]，只有「大幅」用分位，「退/加」用零。
        """
        if not pairs:
            return None
        cur = pairs[-1][1]
        seg = [(d, v) for d, v in pairs if base_gate is None or d >= base_gate]
        if len(seg) < 60:          # 样本不足就退化为全长，n 如实报出来
            seg = pairs
        vals = sorted(v for _, v in seg)
        n = len(vals)

        def q(p):
            return vals[min(n - 1, int(n * p / 100))]

        cuts = [round(q(qs[0]), dp),
                round(q(qs[1]), dp) if mid is None else mid,
                round(q(qs[2]), dp)]
        pct = round(sum(1 for v in vals if v < cur) / n * 100, 1)
        nm = names or ['很低', '偏低', '偏高', '很高']
        lv = nm[-1]
        for th, name in zip(cuts, nm):
            if cur < th:
                lv = name
                break
        return {'cur': cur, 'pct': pct, 'n': n, 'cuts': cuts, 'lv': lv,
                # 实际参与统计的第一天。⚠ 不是 pairs[0][0] —— 原始序列从 2012 起，
                # 但分位只用 2014 之后的，报 2012 会让页面写「2012 年以来 79.6% 分位」，
                # 那个百分比根本不是 2012 起算的。
                'from': seg[0][0],
                # 画图用的纵轴范围：按基准期的 2%~98% 分位取。
                # 不用 min/max —— 2015 年存量杠杆到过 4.70%，用它定轴会把 2020 年之后
                # 全部压成一条贴着底边的线，看不出任何变化。
                'lo': round(q(2), dp), 'hi': round(q(98), dp)}

    # ── ① 存量杠杆：融资余额 ÷ 流通市值 ─────────────────────────────
    # 为什么分子只用「融资余额」不用「两融余额」：融券只占两融 1.3%，
    # 且融券是看空不是加杠杆，混进去等于把两种相反的行为加在一起。
    stock = []
    for d, r in recs:
        lt = r.get('ltsz') or 0
        if lt > 0 and r.get('rzye'):
            stock.append((d, r['rzye'] / lt * 100))

    # ── ② 交易杠杆：融资买入额 ÷ 两市成交额 ─────────────────────────
    # ⚠ 起点受成交额数据限制（2019-01-02），比 ①③ 短一千多天。
    #   所以它不能套用 2014 基准，用自己的全长做基准，并把天数如实报出来。
    trade = []
    if turn_map:
        for d, r in recs:
            tv = turn_map.get(d) or 0
            if tv > 0 and r.get('rzmre'):
                trade.append((d, r['rzmre'] / tv * 100))

    # ── ③ 变动强度：20 日累计净买入 ÷ 两融余额 ───────────────────────
    # 分母用「当前余额」而不是「20 日前的余额」：要答的是
    # 「这一个月净增/净减的这一笔，相当于现在盘子的百分之几」。
    flow = []
    for i in range(20, len(recs)):
        acc = sum(rzjme[j] for j in range(i - 19, i + 1))
        base_val = recs[i][1]['rzrqye']
        if base_val:
            flow.append((recs[i][0], acc / base_val * 100))

    S = series_stats(stock, BASE, 3)
    T = series_stats(trade, None, 2)
    F = series_stats(flow, BASE, 2,
                     names=['大幅在退', '在退', '在加', '大幅在加'],
                     mid=0)     # ← 0 是③的绝对分界，不能用中位数（见 docstring）


    def pack(key, name, st, dp, label, plain, pairs, series_full):
        if st is None:
            return None
        return {
            'key': key, 'name': name,
            'v': round(st['cur'], dp), 'pct': st['pct'], 'n': st['n'],
            'lv': st['lv'], 'cuts': st['cuts'],
            'unit': '%', 'label': label, 'plain': plain,
            # 画图用：纵轴范围（2%~98% 分位），别用 min/max，否则 2015 的极值会把近五年压扁
            'axis': [st['lo'], st['hi']],
            # 这条尺子真正参与统计的起止（不是原始序列起止），前端据此写「X 年以来」
            'since': st['from'], 'asof': pairs[-1][0],
            'series': [[d, round(v, dp)] for d, v in pairs],
        }

    levers = {
        'base': BASE,
        # ⚠ 名字必须是「用户第一眼能懂的那个词」，不是行业术语。
        #   原来叫「存量杠杆 / 交易杠杆 / 变动强度」，前两个还行，
        #   第三个「变动强度」得先知道它是什么才能懂 —— 而它答的问题
        #   恰恰最直白：钱在往里加还是往外退。所以改成「杠杆方向」。
        #   名字在三个地方显示（首页 KPI / 面板卡 / 图标题），必须同一套。
        'stock': pack('stock', '杠杆水位', S, 3,
                      '融资余额 ÷ 流通市值',
                      '每 100 元流通股里，有多少钱是借来买的',
                      stock, None),
        'trade': pack('trade', '交易杠杆', T, 2,
                      '融资买入额 ÷ 两市成交额',
                      '每 100 元成交里，有多少是借钱在买',
                      trade, None),
        'flow': pack('flow', '杠杆方向', F, 2,
                     '20 日融资净买入 ÷ 两融余额',
                     '这一个月，杠杆资金净增/净减了余额的百分之几',
                     flow, None),
    }

    # ── 三张图共用同一个时间窗 ────────────────────────────────────────
    # 三条序列起点不同（② 从 2019 起，①③ 从 2014 起）。
    # 让它们各画各的，读者会拿左图 2015 的峰去对齐右图 2019 之后某处 ——
    # 形状一样但含义完全不同，这就是「参照系不明」换个地方复发。
    # 所以统一从最晚的起点画起：代价是 ①③ 看不到 2014–2018（含 2015 那个 4.70% 的峰值），
    # 收益是三张图的每一个横向像素都指同一天，可以横着直接比。
    #
    # ⚠ 纵轴范围必须跟着这个窗口重算，不能沿用基准期（2014 起）算的 2%~98% 分位：
    #   基准期口径下 ③ 的轴是 −16.75~17.56，但 2019 之后实际只跑到约 −15~20，
    #   轴上会空掉一大块，曲线被压在下半部 —— 图与轴必须是同一个窗口，否则又是两套参照。
    #
    # ⚠ 注意：这里改的只是「画图的轴范围和画图窗口」，不改分位。
    #   分位仍然按基准期（2014-01-01）算 —— 那是「现在处在历史什么位置」的答案，
    #   跟「图上显示哪一段」是两件事。混起来就又回到第一版的错了。
    #
    # ⚠ 轴范围必须同时满足两件事，缺一就出问题：
    #   ① 包住【窗口内的真实极值】—— 只按 2%~98% 分位取会把峰切掉，
    #      ① 在 2019 年后的最高是 2.901，而 p98 只有 2.74，曲线会顶出画布；
    #   ② 包住【所有档位边界】—— ① 的「很高」边界 2.761 若落在轴外，
    #      最高那一档的色带根本画不出来，读者只看到三档。
    #   所以 = min(真实最低, 最低边界, 0若跨零) ~ max(真实最高, 最高边界)。
    #   再各留 4% 余量，别让线贴着边框。
    if stock and trade and flow:
        cstart = max(p[0][0] for p in (stock, trade, flow))
        for key, pairs in (('stock', stock), ('trade', trade), ('flow', flow)):
            ws = [v for d, v in pairs if d >= cstart]
            g = levers.get(key)
            if len(ws) < 60 or not g:
                continue
            cuts = g['cuts']
            lo, hi = min(min(ws), cuts[0]), max(max(ws), cuts[-1])
            # 跨零的尺子（③）轴必须含 0，否则「零线」画不出来，而零线正是它的分界
            if lo < 0 < hi:
                lo, hi = min(lo, 0), max(hi, 0)
            pad = (hi - lo) * 0.04
            g['axis'] = [round(lo - pad, 2), round(hi + pad, 2)]
        levers['win'] = cstart      # 前端据此裁横向窗口，别自己再推一遍

    # ── ① 的全历史长图（另算一套窗口与轴，与上面三张小图不共用）────────
    # 为什么三张小图之外还要单独画一张 ①：
    #   三张小图为了横向可比，起点被最晚的 ② （2019-01）绑住了，于是
    #   ① 看不到 2015-07-03 的 4.697% —— 而那个数恰恰是回答
    #   「现在 2.49% 算不算高」最重要的锚。没有它，读者只能在 2019 年以来的
    #   窄区间里比，会觉得 2.49% 已经是「接近最高」了。
    #
    # ⚠ 但这张图【不能】和另外两张横着比：它跨 12 年、它们跨 7 年，
    #   同一个像素宽度代表的时间跨度不同。所以图上必须显式写「本图时间跨度」，
    #   并且这张图单独占一行、不和那两张并排 —— 位置本身就说明「别横着比」。
    #
    # ⚠ 也不做「同一张图叠两条线（全历史 + 近 7 年）」：两条线共用一根轴，
    #   会让读者以为它们是两个不同的量。它是同一个量的两段，分段看就行。
    if stock:
        full = [(d, v) for d, v in stock if d >= BASE]
        if len(full) >= 60:
            fv = [v for _, v in full]
            pk = max(full, key=lambda x: x[1])
            tr = min(full, key=lambda x: x[1])
            s_cut = levers['stock']['cuts']
            lo, hi = min(min(fv), s_cut[0]), max(max(fv), s_cut[-1])
            pad = (hi - lo) * 0.03
            levers['stock_full'] = {
                'from': full[0][0], 'to': full[-1][0], 'n': len(full),
                'cur': round(fv[-1], 3),
                'axis': [round(lo - pad, 3), round(hi + pad, 3)],
                # 峰与谷：这两个点让「现在在哪儿」有个绝对参照，不只是一条色带
                'peak': [pk[0], round(pk[1], 3)],
                'trough': [tr[0], round(tr[1], 3)],
                # 距峰顶还差多少 —— 比「79.6% 分位」更直白，因为它不需要懂分位
                'gap_from_peak': round((fv[-1] / pk[1] - 1) * 100, 1),
                'series': [[d, round(v, 3)] for d, v in full],
            }

    return {'from': dates[0], 'dates': dates,
            'rzrq': rzrq, 'rzjme': rzjme, 'rzyezb': rzyezb,
            'latest': {'date': dates[-1],
                       'rzrq': rzrq[-1], 'rzjme': rzjme[-1], 'rzyezb': rzyezb[-1],
                       'chg_1d': round(rzjme[-1], 1),
                       'chg_5d': chg(5), 'chg_20d': chg(20)},
            # ★ 三把尺子。前端只读这个，不再自己算分位、不再自己切档。
            'levers': levers}




def calc_cum_path(days, combo_cum, bounds):
    """把「区间累计」这条线切成几段，算出每段把曲线推到了哪。

    为什么单独做：
        cum 是【区间内累计】，从首日归零起算。它不是持仓成本线，也不是绝对水位。
        所以它会穿过零点、可以在期末为负 —— 这不是 bug，是「区间选在哪儿」决定的。
        但它摆在那里，看的人只会问「怎么是负的」。

        光说「区间累计」四个字解释不了。必须给出轨迹：
        从 0 → 高点 4423（2024-10）→ 低点 −4389（2026-07）→ 期末 −980。
        **负的是期末，不是全程；它中途到过 +4423。**
        这句话把「长期净流出」的误读直接消掉。
    """
    peak_i = combo_cum.index(max(combo_cum))
    trough_i = combo_cum.index(min(combo_cum))

    def pick(d):
        j = 0
        for i, x in enumerate(days):
            if x <= d:
                j = i
        return j

    segs = []
    for label, a, b in bounds:
        ia, ib = pick(a), pick(b)
        if ia >= ib:
            continue
        seg = combo_cum[ib] - combo_cum[ia]
        segs.append({
            'label': label, 'from': days[ia], 'to': days[ib],
            'net': round(seg, 1),
            'cum_from': combo_cum[ia], 'cum_to': combo_cum[ib],
        })
    return {
        'peak_date': days[peak_i], 'peak': combo_cum[peak_i],
        'trough_date': days[trough_i], 'trough': combo_cum[trough_i],
        'segs': segs,
        # 期末离峰值掉下来多少：这个数比「−980」更能说明「钱去哪了」
        'drawdown_from_peak': round(combo_cum[-1] - combo_cum[peak_i], 1),
    }


def _derive_health(shares, prices_qfq, prices_bfq, sh_idx, turn_raw,
                   mg_raw, inst, today):
    """没有上报记录时，直接从数据文件反推一份体检。

    为什么要有这条兜底：fetch_status.json 只有在「抓取脚本跑过」之后才存在。
    第一次部署、或者某次只手动跑了 compute_demo 时，它不存在 ——
    这时体检栏会显示「未生成」，而数据其实完全正常。
    **把「我没记录」说成「数据有问题」，和把「数据有问题」说成「一切正常」，
    是同一种病：拿不确定的东西冒充确定。**

    反推出的状态一律标 ok / stale（按日期判断），不编造 fail ——
    因为「文件在且日期新」确实说明数据可用，至于这次有没有抓、抓了几只，
    没记录就是不知道，不能猜。
    """
    def last_of(seq):
        """从 [[date, value], ...] 里取最后一天。"""
        return max((x[0] for x in seq), default=None) if seq else None

    def last_key(dct):
        """从 {date: ...} 里取最后一天。⚠ 不能复用 last_of ——
        字典迭代出来的是字符串，x[0] 会取到第一个字符（踩过：'2026-11-11' → '2'）。"""
        return max(dct, default=None) if dct else None

    out = []

    def add(key, name, last, got=None, want=None):
        lag = None
        if last:
            try:
                lag = (datetime.strptime(today[:10], '%Y-%m-%d')
                       - datetime.strptime(last[:10], '%Y-%m-%d')).days
            except Exception:
                lag = None
        state = 'ok'
        # 「日期新」不等于「数据齐」。抓 6 只和抓 10 只都可能是今天的日期，
        # 但前者净流入只覆盖 60% 的标的 —— 不报出来，读的人会以为组合是全部 10 只。
        if got is not None and want and got < want:
            state = 'partial'
        # ⚠ 这里用 SRC_STALE_DAYS（10 天），不用 LAG_OK。
        #   反推场景下 today 是【真实今天】，而 LAG_OK 只管「源站天生滞后几天」
        #   （两融 T+1、上交所 T+1）。拿它判会把每个周末都报成「停更」：
        #   周六看周五的数据 lag=1，两融 LAG_OK=1 就卡在边界上，
        #   再隔一天变 lag=2 > 1 → 红着。**一份周末必红的仪表盘会被当成噪音关掉。**
        #   反推时我们本来就不知道「今天有没有抓」，只能问「数据本身多久没动」，
        #   那就该用休市容忍度（A 股最长休市是春节约 9 天）。
        if lag is not None and lag > _fh.SRC_STALE_DAYS:
            state = 'stale'
        out.append({'key': key, 'name': name, 'state': state, 'last': last,
                    'lag': lag, 'got': got, 'want': want, 'days_bad': 0,
                    'days_silent': None,
                    'note': '无上报记录，按文件日期反推'})

    # 份额的「到哪天」只认含 510300 的交易日 —— 深市只有 3 天自建历史，
    # 直接 max(shares) 会取到深市快照的日期，让人以为上交所也到那天了。
    _sse_days = [d for d, v in shares.items() if '510300' in v]
    add('shares', 'ETF 份额（上交所，全站唯一事实层）',
        max(_sse_days) if _sse_days else None, len(WATCH), len(WATCH))
    add('price_bfq', '价格（不复权，算净流入用）',
        last_of([v[-1] for v in prices_bfq.values() if v]),
        len([k for k, v in prices_bfq.items() if v]), len(WATCH))
    add('price_qfq', '价格（前复权，画曲线用）',
        last_of([v[-1] for v in prices_qfq.values() if v]),
        len([k for k, v in prices_qfq.items() if v]), len(WATCH))
    add('index', '上证指数', last_key(sh_idx))
    add('turnover', '两市成交额', last_of(turn_raw))
    add('margin', '两融余额', max(mg_raw) if mg_raw else None)
    _inst_last = None
    for r in (inst.get('rows') or []):
        d = (r.get('latest') or {}).get('date')
        if d and (not _inst_last or d > _inst_last):
            _inst_last = d
    add('inst', '机构持仓（半年报/年报）', _inst_last,
        len(inst.get('rows') or []), len(WATCH))

    order = {'fail': 0, 'stale': 1, 'partial': 2, 'ok': 3}
    out.sort(key=lambda r: (order.get(r['state'], 9), -(r['lag'] or 0)))
    return out


def main():
    shares = json.load(open(os.path.join(DATA, 'shares.json'), encoding='utf-8'))
    prices_qfq = json.load(open(os.path.join(DATA, 'prices.json'), encoding='utf-8'))
    prices_bfq = json.load(open(os.path.join(DATA, 'prices_bfq.json'), encoding='utf-8'))
    inst = json.load(open(os.path.join(DATA, 'inst_holdings.json'), encoding='utf-8'))
    inst_map = {r['code']: r for r in inst['rows']}

    # 上证指数（组合页叠加大盘位置用）+ 两市成交额（量能面板）
    # 缺文件不致命：老数据仍能出图，只是少了那条线 / 那个面板。
    sh_idx = {}
    turn_raw = []
    _fp = os.path.join(DATA, 'index_sh.json')
    if os.path.exists(_fp):
        _js = json.load(open(_fp, encoding='utf-8'))
        sh_idx = {d: v for d, v in (_js.get('000001') or [])}
        turn_raw = _js.get('turnover') or []

    # 两融余额（杠杆面板）。同样是可选文件。
    mg_raw = {}
    _fp = os.path.join(DATA, 'margin.json')
    if os.path.exists(_fp):
        mg_raw = json.load(open(_fp, encoding='utf-8')).get('days') or {}

    days = sorted(d for d in shares if d >= START and '510300' in shares[d])
    print(f'交易日 {len(days)} 天：{days[0]} → {days[-1]}')

    qfq = {c: {d: v for d, v in arr} for c, arr in prices_qfq.items()}
    bfq = {c: {d: v for d, v in arr} for c, arr in prices_bfq.items()}

    rows, thin = [], []
    for idx, code, name, mk, mgr in WATCH:
        ds = [d for d in days if code in shares.get(d, {})]
        if not ds:
            continue
        inst_rec = inst_map.get(code) or {}

        sh = [shares[d][code] / 10000.0 for d in ds]

        # ⚠ 份额折算修正：把折算日之前的份额按比例折算到折算后口径。
        #    不做这一步，512100 在 2022-09-02 会被算成「单日净流出 65 亿」——
        #    那是基金公司做份额合并，不是任何人卖了货。
        for eff, ratio in SPLITS.get(code, []):
            for i, d in enumerate(ds):
                if d < eff:
                    sh[i] *= ratio

        p_q = [qfq.get(code, {}).get(d) for d in ds]      # 画曲线：前复权
        p_b = [bfq.get(code, {}).get(d) for d in ds]      # 算净流入：不复权

        # ⚠ 缺价补齐（只在算钱时用，画曲线仍保留缺口）：
        #    512100 在 2022-09-02 折算当天东财没有不复权收盘价（停牌处理），
        #    如果留 None，那天的「存量浮盈」会被整段跳过，恒等式就凑不平。
        #    用折算前的最后一个真实价顶上 —— 这是当天的真实净值锚点。
        p_b_num = [x for x in p_b if x]
        p_b_fill = []
        last = None
        for i, x in enumerate(p_b):
            if x:
                last = x
                p_b_fill.append(x)
            elif last:
                p_b_fill.append(last)
            else:
                # 开头就缺，往后找第一个有效价
                nxt = next((y for y in p_b[i:] if y), None)
                p_b_fill.append(nxt)

        # 单日净流入（亿元）—— 用不复权真实成交价（缺价已补齐）
        flow = [None]
        for i in range(1, len(ds)):
            p = p_b_fill[i] if p_b_fill[i] is not None else p_b_fill[i - 1]
            flow.append(round((sh[i] - sh[i - 1]) * p, 2) if p else None)

        cum, acc = [], 0.0
        for f in flow:
            if f is not None:
                acc += f
            cum.append(round(acc, 1))

        level = [None] * len(sh)
        for i in range(20, len(sh)):
            med = statistics.median(sh[i - 20:i])
            if med:
                level[i] = round((sh[i] / med - 1) * 100, 2)

        surv = calc_survival(sh[-1], inst_rec.get('holders', []))
        quad = calc_quad(sh, p_q, ds)

        rec = {
            'index': idx, 'code': code, 'name': name, 'market': mk, 'manager': mgr,
            'days': len(ds),
            'dates': ds,
            'shares': [round(x, 2) for x in sh],
            'price': p_q,
            'price_bfq': p_b,
            'price_bfq_fill': p_b_fill,   # 算钱用这个（缺价已补齐），画图用 price_bfq（保留缺口）
            'flow': flow,
            'cum': cum,
            'level': level,
            'survival': surv,
            'quad': quad,
            'turnover': calc_turnover(sh, p_b, flow),
            'inst': {
                'history': inst_rec.get('holders', []),
                'latest': inst_rec.get('latest'),
                'inst_share_yi': inst_rec.get('inst_share_yi'),
                'scale': inst_rec.get('scale', []),
            },
            'latest': {
                'date': ds[-1], 'shares': round(sh[-1], 2), 'price': p_q[-1],
                'flow': flow[-1], 'cum': cum[-1], 'level': level[-1],
                'chg_1d': round((sh[-1] / sh[-2] - 1) * 100, 3) if len(sh) > 1 else None,
            },
        }
        # 深市不足 30 天：进 thin（只在快照表显示，不画曲线）
        if len(ds) < MIN_DAYS:
            rec['note'] = f'深交所仅返回当前快照，已累积 {len(ds)} 天，满 {MIN_DAYS} 天后自动进曲线区'
            thin.append(rec)
        else:
            rows.append(rec)

    # 组合合计
    # ⚠ 这里原来每天每只都调一次 r['dates'].index(d)，是 O(天数²×只数)，
    #    711 天还能忍，拉到 1650 天就会卡到不可用。改为先把日期映射成位置索引。
    pos = {id(r): {d: i for i, d in enumerate(r['dates'])} for r in rows}

    combo_flow, combo_cum, acc = {}, {}, 0.0
    for d in days:
        s = 0.0
        for r in rows:
            j = pos[id(r)].get(d, -1)
            if j < 0:
                continue
            f = r['flow'][j]
            if f is not None:
                s += f
        acc += s
        combo_flow[d] = round(s, 2)
        combo_cum[d] = round(acc, 1)

    combo_size = []
    for d in days:
        tot = 0.0
        for r in rows:
            j = pos[id(r)].get(d, -1)
            if j < 0:
                continue
            # ⚠ 规模（金额）= 份额 × 当日【不复权】真实价。
            # 用前复权算金额是错的：前复权把历史价系统性压低（2023-12 上证50 低 5.7%），
            # 会让「规模从 3467 涨到 5311」被算成「从 3278 涨到 5311」，凭空多长出 5% 的涨幅。
            # 同一次错误在净流入上已修过，规模这里原来漏了。
            p = (r.get('price_bfq_fill') or r['price_bfq'])[j]
            if p:
                tot += r['shares'][j] * p
        combo_size.append(round(tot, 1))

    # 组合成员数逐日：区间从 2023-12 拉到 2020-01 后，科创50（2020-11 上市）
    # 会在中途加入，导致「组合规模」凭空跳一级。这个不是行情，是口径变化，必须标注。
    members = []
    for d in days:
        members.append(sum(1 for r in rows if d in pos[id(r)]))

    # 上证指数按 days 对齐 —— 必须用 days 的日期序列，不能用自己的日期序列，
    # 否则 x 轴会和净流入柱错位一格。指数交易日应覆盖 days 全集；
    # 若出现缺口（ETF 停牌但指数开市），用前一有效值顶格：指数在「不交易的日子」本来就没变。
    sh_line, _last = [], None
    for d in days:
        v = sh_idx.get(d)
        if v is not None:
            _last = v
            sh_line.append(round(v, 2))
        else:
            sh_line.append(round(_last, 2) if _last is not None else None)
    # 开头就缺（days 早于指数起始）：往后回填第一个有效值，避免曲线左端空一截
    _fst = next((x for x in sh_line if x is not None), None)
    if _fst is not None:
        sh_line = [x if x is not None else _fst for x in sh_line]

    sh_ok = [x for x in sh_line if x is not None]
    sh_stat = None
    if sh_ok:
        _hi = max(sh_ok); _lo = min(sh_ok)
        sh_stat = {
            'first': sh_line[0], 'last': sh_line[-1],
            'chg_pct': round((sh_line[-1] / sh_line[0] - 1) * 100, 1),
            'high': round(_hi, 2), 'high_date': days[sh_line.index(_hi)],
            'low': round(_lo, 2), 'low_date': days[sh_line.index(_lo)],
        }

    # ── 当日异动度 & 量能 & 杠杆 ──────────────────────────────────
    # 这三个都是「给单日数字配一个参照系」，但参照系各取各的交易日序列：
    #   flow_abn：跟自己的近 60 日比        → 用 days（份额口径）
    #   volume  ：用成交额自然日（T 日可得） → 不能用 days（会丢最新一天）
    #   margin  ：用两融自然日（T+1 公布）   → 同样不能用 days
    flow_arr = [combo_flow[d] for d in days]
    abn = calc_abnormal(flow_arr, days)
    vol = calc_volume(turn_raw, days)
    # 把成交额按日期传进去：算「交易杠杆」需要「融资买入额 ÷ 当日两市成交额」
    turn_map = {d: v for d, v in turn_raw if v and v > 0}
    mg = calc_margin(mg_raw, days, turn_map)

    out = {
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'latest_date': days[-1],
        'first_date': days[0],
        'days': days,
        'combo': {'flow': flow_arr,
                  'cum': [combo_cum[d] for d in days],
                  'size': combo_size,
                  'members': members,
                  'sh_index': sh_line,
                  'sh_stat': sh_stat,
                  'join_dates': [{'code': r['code'], 'name': r['name'], 'from': r['dates'][0]}
                                 for r in rows if r['dates'][0] > days[0]],
                  'bridge': calc_bridge(rows, None),
                  'cum_path': calc_cum_path(days, [combo_cum[d] for d in days], CYCLE),
                  'turnover': calc_turnover_combo(rows),
                  # 当日异动度：倍数 + 分档 + 一年分位。首页 KPI 直接读这几个字段。
                  'abnormal': abn,
                  'abn_latest': {
                      'date': days[-1],
                      'flow': flow_arr[-1],
                      'base': abn['base'][-1],
                      'ratio': abn['ratio'][-1],
                      'grade': abn['grade'][-1],
                      'pctile': abn['pctile'][-1],
                  }},
        'volume': vol,
        'margin': mg,
        'rows': rows,
        'thin': thin,
        'cycle': calc_cycle(rows, CYCLE),
        'inst_updated': inst['updated_at'],
        # 三只「红利」的对照。数据全部现算，只有「跟踪哪条指数、多少只样本、
        # 一年分红几次」这类【查证得来的事实】来自 DIVIDEND_FAMILY 常量。
        'divi_compare': calc_divi_compare(qfq, bfq,
                                          {r['code']: r for r in rows + thin},
                                          days[0], days[-1]),
    }

    # ── 数据体检（内联进页面，让「哪块没抓到」直接上屏）─────────────────
    # 2026-11-12 一天里炸出五处静默失败，共同点是「坏了但不吭声」。
    # 这个字段是那件事的统一出口：每个数据源一行，写清「到哪天 / 落后几天 / 抓到几只」。
    #
    # ⚠⚠ 基准日必须用【真实今天】，不能用 days[-1]。
    #   第一版我写的是 summarize(today=days[-1])，注释还写着「数据和页面同源」——
    #   想得很周到，但把尺子和被测对象搞成了同一个东西：
    #   今天 11-12、数据停在 11-11 → lag = 0，显示「当日，正常」；
    #   就算数据停在 11-05，基准也跟着变成 11-05 → lag 还是 0。
    #   **这一栏从此永远显示「全部正常」，比不装它还糟：它长得像体检，实际什么也没查。**
    #   跨零点差一天的顾虑是真的，但那是 1 天的事；拿错基准是「永远为 0」的事。
    #   而且页面本来就有「最新 {days[-1]}」在顶部，两者不是同一个概念，不该混用。
    today_real = date.today().isoformat()
    try:
        health = _fh.summarize(today=today_real)
    except Exception as e:
        health = []
        print(f'  ⚠ 数据体检生成失败：{type(e).__name__}: {e}')

    # 上报记录为空（第一次部署 / 某次只跑了 compute）时，从数据文件本身反推。
    # ⚠ 这一步不能省：否则体检栏会显示「未生成体检数据」，
    #   而实际上数据好得很 —— 把「我没记录」当成「数据有问题」也是一种误报。
    if not health:
        health = _derive_health(shares, prices_qfq, prices_bfq, sh_idx,
                                turn_raw, mg_raw, inst, today_real)
        print('  （无上报记录，体检状态由数据文件反推）')

    out['health'] = health
    _bad = [h for h in health if h['state'] != 'ok']
    if _bad:
        print(f'\n  数据体检：{len(health)} 项，{len(_bad)} 项有问题')
        for h in _bad:
            lag = f"落后 {h['lag']} 天" if h['lag'] is not None else '无日期'
            print(f"    · {h['name']}：{lag}  {h['note']}")
    else:
        print(f"\n  数据体检：{len(health)} 项全部正常")
    fp = os.path.join(DATA, 'demo.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)')
    print(f'  最新 {days[-1]}  组合单日净流入 {combo_flow[days[-1]]:+.2f} 亿')
    print(f'  近5日 {sum(list(combo_flow.values())[-5:]):+.1f} 亿   '
          f'近20日 {sum(list(combo_flow.values())[-20:]):+.1f} 亿')
    print(f'\n{"代码":<8}{"名称":<18}{"份额":>9}{"日流":>9}  {"存活率":>8} {"峰值期":>10}')
    for r in rows:
        L, s = r['latest'], r['survival']
        if s:
            sv = f"{s['survival_pct']:>7.1f}%"
            pk = f"{s['peak_date'][:7]} {s['peak_inst_yi']:>6.0f}"
        else:
            sv, pk = '       —', '         —'
        print(f"{r['code']:<8}{r['name']:<18}{L['shares']:>9.1f}"
              f"{(L['flow'] if L['flow'] is not None else 0):>+9.1f}  {sv} {pk}")
    for r in thin:
        print(f"{r['code']:<8}{r['name']:<18}{r['latest']['shares']:>9.1f}"
              f"{'—':>9}  （深市，累积 {r['days']} 天）")

    # ── 异动 / 量能 / 杠杆 自检输出 ──
    a = out['combo']['abn_latest']
    if a['ratio'] is not None:
        lab = {'extreme': '极端', 'abnormal': '异动', 'high': '偏高',
               'normal': '常态', 'quiet': '安静'}.get(a['grade'], '—')
        print(f"\n当日异动度  {a['date']}  |净流入| {abs(a['flow']):.1f} 亿  "
              f"÷ 60日基线 {a['base']:.1f} 亿 = {a['ratio']:.2f}×  [{lab}]  "
              f"近一年分位 {a['pctile']:.0f}%")
    if vol:
        print(f"量能        最新 {vol['dates'][-1]}  "
              f"两市 {vol['val'][-1]:,.0f} 亿  "
              f"5日均 {vol['ma5'][-1]:,.0f} 亿  "
              f"20日均 {vol['ma20'][-1]:,.0f} 亿  "
              f"一年分位 {vol['pctile'][-1]:.0f}%")
    if mg:
        m = mg['latest']
        print(f"两融        {m['date']}  余额 {m['rzrq']:,.0f} 亿  "
              f"当日融资净买入 {m['rzjme']:+,.1f} 亿  "
              f"5日 {m['chg_5d']:+,.0f} 亿  20日 {m['chg_20d']:+,.0f} 亿")
        L = mg.get('levers') or {}
        print(f"  基准期 {L.get('base')} 起（三个尺子各自的样本天数见括号）")
        for key in ('stock', 'trade', 'flow'):
            g = L.get(key) or {}
            if g.get('v') is None:
                print(f"  {g.get('name','?'):6s} 数据不足")
                continue
            cut = g.get('cuts')
            cutxt = ("  档位 " + "/".join(f"{x:g}" for x in cut)) if cut else ""
            print(f"  {g['name']:6s} {g['v']:>7.2f}{g['unit']}  "
                  f"分位 {(g['pct'] if g['pct'] is not None else float('nan')):>5.1f}%  "
                  f"[{g.get('lv') or '—'}]  {g['since']}起 {g['n']} 天"
                  f"  轴 {g['axis'][0]:g}~{g['axis'][1]:g}{cutxt}")


if __name__ == '__main__':
    main()

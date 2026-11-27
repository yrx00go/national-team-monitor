#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股息率监控 —— 从 alert_my_dividend 模块移植，改成「可算历史」的版本。

口径（与本地已跑通多年的 dividend_notify.py 保持一致，这个口径是对的）：
    股息率(%) = 年化每股分红 ÷ 当前股价 × 100
    年化每股分红 = 最近一个完整财年的两次派息之和，按【报告期】配对，不按除权日窗口。

⚠ 为什么不能按「近 12 个月除权日求和」（原脚本注释里踩过的坑，这里复述）：
    2025 年起银行改为「中期 + 年度」两次派息，12 个月窗口会装进 3 次派息。
    实测工商银行：按除权日 TTM 求和得 0.4614 元 → 5.37%；按报告期配对是
    0.1689（2025年度）+ 0.1511（2026中期）= 0.3200 元 → 3.73%。后者才是年化。

⚠ 历史曲线必须「无未来函数」：
    不是拿今天的 DPS 回填 2024 年，而是对每个历史日期 t，只用 t 时刻
    已经除权的分红记录，重新配一次对。这样 2024 年的股息率反映的是
    当时市场能看到的信息，曲线才不会被后视镜拉平。

数据源：
    分红（含除权日/报告期）—— 东方财富 datacenter RPT_SHAREBONUS_DET
    日线（不复权）—— 腾讯行情 bfq

输出：data/dividend.json
"""
import json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明
import _net         # 请求节流 + 账单，见 _net.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

# (名称, 代码, 市场, 买入线%, 卖出线%, 卖出方式)  ← 原样搬自那套已有脚本的 STRATEGY / STOCKS
STOCKS = [
    ('招商银行', '600036', 'sh', 5.0, 2.0, 'all'),
    ('贵州茅台', '600519', 'sh', None, None, None),
    ('江苏金租', '600901', 'sh', 6.0, 3.0, 'half'),
    ('云南白药', '000538', 'sz', 5.0, None, 'hold'),
    ('中国银行', '601988', 'sh', 6.0, 3.0, 'half'),
    ('农业银行', '601288', 'sh', 7.0, 2.0, 'all'),
    ('工商银行', '601398', 'sh', 6.0, 3.0, 'half'),
    ('建设银行', '601939', 'sh', 6.0, 3.0, 'half'),
    ('中国平安', '601318', 'sh', 5.0, 2.0, 'all'),
    # 2026-11-26 OO 定：险企只留 2 只，留平安 + 新华，砍人寿 + 太保。
    #   理由写在这里，免得以后又加回来：
    #   · 平安必留 —— 唯一有买卖线（5.0/2.0）的险企，今天 5.00% 正好踩在买入线上，
    #     这是她会据此下单的标的，删掉等于删掉信号。
    #   · 新华留 —— 股息率 5.42%（四只最高）、两年分位 99.6%，且与平安的股息率序列
    #     相关只有 0.18，信息几乎不重复。
    #   · 人寿砍 —— 股息率 2.79%，四只最低，两年均值只有 1.70%，本质不是高股息标的；
    #     它分位 98.2% 是「自己历史更低」造出来的假便宜，绝对水平并不便宜。
    #   · 太保砍 —— 4.91% 与平安 5.00% 几乎重合，股息率序列相关 0.822，等于同一信号看两遍。
    #   原代码：('中国人寿','601628'), ('中国太保','601601') 均 (None,None,None)
    ('新华保险', '601336', 'sh', None, None, None),
    ('长江电力', '600900', 'sh', 5.0, None, 'hold'),
    # 2026-11-26 OO 指定：把「三桶油」加入股息率观察。
    # ⚠ buy/sell 一律 None —— 她说的原话是「加入观察」，不是「给买卖线」。
    #   擅自配一条 6% 买入线，等于我替她定投资政策，这属于改口径不是加数据。
    #   要设线她给数字，我改这一行就行。
    ('中国石油', '601857', 'sh', None, None, None),
    ('中国石化', '600028', 'sh', None, None, None),
    ('中国海油', '600938', 'sh', None, None, None),
    # 2026-11-26 OO 指定：加入顺丰控股。深市 002352（不是沪市，market 必须 'sz'）。
    # ⚠ buy/sell 一律 None —— 同三桶油，她只说「加进来」，没说给线。
    # ⚠ 分组：不分组（进「其他」）。她是单独点名加的，凑一个只有一只的「物流组」
    #   只是给表格加一行噪音；要成组她再说，改 WATCH_GROUP 一行就行。
    ('顺丰控股', '002352', 'sz', None, None, None),
]
START = '2023-12-01'   # 与 ETF 模块对齐：2023 年 12 月至今

# 「险企观察仓」分组（2026-11-13 OO 指定，三只高股息险企一起看）。
# ⚠ 只做分组标记，不动任何既有标的的买卖线：平安 601318 原有的 5.0/2.0 是她自己
#    那套策略的配置，加个标签就把信号删掉，属于擅自改口径。
# ⚠ 新加入的新华 601336、太保 601601 的 buy/sell 一律 None —— 观察仓是「只看，不下单」，
#    给它们配买卖线会把观察仓读成交易信号。
WATCH_GROUP = {
    # 2026-11-26 OO 定：险企只留 2 只（平安 + 新华），见 STOCKS 上方注释。
    '601336': '险企观察仓',
    '601318': '险企观察仓',   # 原本就在池里，不重复添加数据
    # 2026-11-26 OO 定：5 家银行并成一组展示。
    # ⚠ 与「观察仓」性质不同：这 5 家都有买卖线（她自己那套策略的配置），
    #   所以表格里不挂「观察仓」标签、组说明也写明「买卖线照旧生效」。
    '600036': '银行',
    '601988': '银行',
    '601288': '银行',
    '601398': '银行',
    '601939': '银行',
    # 2026-11-26 新增：三桶油一起看。三只全无买卖线（纯观察）。
    '601857': '三桶油观察',
    '600028': '三桶油观察',
    '600938': '三桶油观察',
}
EM_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'


def _get(url, referer=None, retries=3):
    """带退避重试的 GET。

    ⚠ 2026-11-12 晚补的重试。原来这里是【一次性的】——
       整个项目里唯一没有重试的网络调用，躲在最容易失败的那个脚本里
       （22 次请求，全场最多）。没有重试 + 请求最多 = 最容易挂，
       而它挂了只影响股息率面板，属于「可选步骤」，所以一直没被发现。
    """
    h = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
    if referer:
        h['Referer'] = referer
    last = None
    for i in range(retries):
        _net.throttle(url)      # 见 _net.py
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(url, headers=h), timeout=30).read()
            _net.mark(url)
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(min(1.0 * (2 ** i), 8.0))
    raise last


def fetch_bonus(codes):
    """返回 {code: [{'ex': 除权日, 'dps': 每股税前, 'period': 报告期}]}"""
    params = {
        'reportName': 'RPT_SHAREBONUS_DET',
        'columns': ('SECURITY_CODE,PRETAX_BONUS_RMB,REPORT_DATE,'
                    'ASSIGN_PROGRESS,EX_DIVIDEND_DATE'),
        'filter': '(SECURITY_CODE in ({}))'.format(
            ','.join(f'"{c}"' for c in codes)),
        'pageNumber': '1', 'pageSize': '1000',
        'sortColumns': 'REPORT_DATE', 'sortTypes': '-1',
        'source': 'WEB', 'client': 'WEB',
    }
    js = json.loads(_get(EM_URL + '?' + urllib.parse.urlencode(params)).decode())
    rows = (js.get('result') or {}).get('data') or []
    out = {c: [] for c in codes}
    for r in rows:
        if r.get('ASSIGN_PROGRESS') != '实施分配':
            continue
        ex = (r.get('EX_DIVIDEND_DATE') or '')[:10]
        per = (r.get('REPORT_DATE') or '')[:10]
        raw = r.get('PRETAX_BONUS_RMB')
        if not ex or not per or raw is None:
            continue
        dps = float(raw) / 10.0
        if dps <= 0:
            continue
        out[r['SECURITY_CODE']].append({'ex': ex, 'dps': dps, 'period': per})
    for c in out:
        out[c].sort(key=lambda x: x['ex'])
    return out


def _px_tx(code, market, bars=900):
    sym = market + code
    url = (f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
           f'param={sym},day,,,{bars},bfq')
    js = json.loads(_get(url, 'https://gu.qq.com/').decode('utf-8', 'ignore'))
    node = (js.get('data') or {}).get(sym) or {}
    k = node.get('day') or node.get('bfqkline') or []
    # 腾讯行序：[日期, 开, 收, 高, 低, 量]  ← 注意是「开收高低」，不是「开高低收」
    return [(r[0], float(r[1]), float(r[3]), float(r[4]), float(r[2]))
            for r in k if len(r) >= 6]


def _px_sina(code, market, n=1000):
    """新浪日线（不复权）。走直连，不经 requests/代理 —— 这台机器上 akshare
    的 requests 链路被代理挡住（ProxyError），urllib 是通的。"""
    sym = market + code
    url = (f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           f'CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}')
    h = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}
    raw = urllib.request.urlopen(urllib.request.Request(url, headers=h),
                                 timeout=30).read().decode('utf-8', 'ignore')
    return [(r['day'][:10], float(r['open']), float(r['high']),
             float(r['low']), float(r['close'])) for r in json.loads(raw)]


def _px_em(code, market, n=None):
    import akshare as ak
    df = ak.stock_zh_a_hist_em(symbol=code, period='daily',
                               start_date=START.replace('-', ''),
                               end_date=datetime.now().strftime('%Y%m%d'),
                               adjust='')
    return [(str(a)[:10], float(o), float(h), float(l), float(c))
            for a, o, h, l, c in zip(df['日期'], df['开盘'], df['最高'],
                                     df['最低'], df['收盘'])]


def fetch_price(code, market, bars=900):
    """不复权日线，三源逐级回退：腾讯 → 新浪 → 东财。

    ⚠ 2026-11-26 加的回退链。原来只有腾讯一条路，当天 fqkline 开始返 HTTP 501，
       整个股息率模块直接崩在第二只标的上（22 次请求里最脆弱的一环）。
       - 新浪：直连可用，不复权收盘与腾讯比对一致（600936 抽查 4 点全同；
         最后一日的 0.2% 级差异来自各家自己的复权/校对节奏，不影响两位小数的股息率）
       - 东财：当日实测被 push2 限流（akshare 会抛 Push2RateLimitError），
         所以排在最后，仅作兜底；触发后不要连续重试，会延长限流。
       返回 (序列, 源标记)，源标记会写进 dividend.json 的 price_src，
       换源不会静默发生，页面上能看出数据是哪来的。
    """
    for fn, tag in ((_px_tx, 'tx'), (_px_sina, 'sina'), (_px_em, 'em')):
        try:
            out = fn(code, market, bars) if fn is _px_tx else fn(code, market)
            if out:
                return out, tag
        except Exception as e:
            print(f'  {tag} 取价失败（{type(e).__name__}: {str(e)[:60]}）')
    raise RuntimeError(f'{code} 三个价格源全部失败')


def annual_dps(records_upto_t):
    """按报告期配对，返回 (年化DPS, 配对说明)。records_upto_t 已按除权日排序且 <= t。

    ⚠ 这段我改错过一次，记录在此，别重犯：
        我一度认为「最新是中期 → 配上一财年年度」是「跨财年混搭」，
        因为它把 2025 上半年 + 2024 全年加在一起，看着像一年半的分红。
        于是改成「严格同一财年（2025 年度 + 2025 中期）」。

        结果与本地缓存对不上：工行我算 0.3103 → 3.60%，原脚本算 0.3200 → 3.73%。
        回去读原脚本的原脚本才明白：她的口径是【最新已宣告的那一期 + 上一财年的年度】，
        含义是「按当前派息节奏，往前推一个完整年度能拿到多少」。
        对银行这种「中期 + 年度」稳定派息的结构，这才是正确的年化。

        按「同一财年」算反而是错的：2025 年度（0.1689）配 2025 中期（0.1414）= 0.3103，
        但 2026 中期（0.1511）已经宣告了 —— 站在今天看，未来 12 个月拿到的是
        2026 中期 + 2025 年度，不是已经过去的 2025 全年。

    ⇒ 采用原口径（与那套 dividend_notify.py 一致）：
        最新一期是年度（12-31）→ 配同一财年的中期
        最新一期是中期（非 12-31）→ 配上一财年的年度
    """
    if not records_upto_t:
        return 0.0, ''
    # 同报告期若有多笔（特别分红等），求和 —— 都是真给的钱
    by_period = {}
    for r in records_upto_t:
        by_period[r['period']] = by_period.get(r['period'], 0.0) + r['dps']

    periods = sorted(by_period)
    latest = periods[-1]
    y, m = int(latest[:4]), int(latest[5:7])
    if m == 12:
        # 最新是年度分红 → 配同一财年的中期
        partners = [p for p in periods[:-1] if int(p[:4]) == y and p != latest]
    else:
        # 最新是中期分红 → 配上一财年的年度
        partners = [p for p in periods[:-1] if int(p[:4]) == y - 1]
    chosen = [latest] + partners[-1:]
    return (round(sum(by_period[p] for p in chosen), 4),
            ' + '.join(chosen))


def pct_rank(series, v, window=500):
    """v 在 series 最近 window 个值中的百分位（0=最便宜的高股息，100=最贵）"""
    s = [x for x in series[-window:] if x is not None]
    if not s or v is None:
        return None
    return round(sum(1 for x in s if x <= v) / len(s) * 100, 1)


def main():
    codes = [c for _, c, _, _, _, _ in STOCKS]
    print('拉分红…')
    bonus = fetch_bonus(codes)
    for c in codes:
        print(f'  {c}  {len(bonus[c])} 条')

    rows, days_ref = [], None
    src_count = {}
    for name, code, mk, buy, sell, mode in STOCKS:
        print(f'拉价格 {code} {name}…')
        raw, src = fetch_price(code, mk)
        src_count[src] = src_count.get(src, 0) + 1
        px = [t for t in raw if t[0] >= START]
        if not px:
            print(f'  跳过 {code}：无价格')
            continue
        recs = bonus[code]
        dates = [t[0] for t in px]
        prices = [round(t[4], 2) for t in px]
        # 2026-11-26：同时存 OHLC，页面用真蜡烛图画股价（原来只存收盘价，只能画折线）
        ohlc = [[round(t[1], 2), round(t[2], 2), round(t[3], 2), round(t[4], 2)]
                for t in px]

        # 每个交易日：只用当天已除权的分红算 DPS（无未来函数）
        dps_series, pair_series, dy_series = [], [], []
        for k, d in enumerate(dates):
            recs_upto = [r for r in recs if r['ex'] <= d]
            dps_val, pair = annual_dps(recs_upto)
            dps_series.append(dps_val)
            pair_series.append(pair)
            p = prices[k]
            dy_series.append(round(dps_val / p * 100, 2) if dps_val and p else None)
        cur_dy = dy_series[-1]
        cur_dps = dps_series[-1]
        cur_price = prices[-1]

        # 距买入线还差多少（原脚本的 _check_buy_signal 逻辑）
        gap = None
        if buy is not None and cur_dy is not None:
            gap = round(buy - cur_dy, 2)
        state = '—'
        if buy is not None and cur_dy is not None:
            if cur_dy >= buy:
                state = '已达买入线'
            elif sell is not None and cur_dy <= sell:
                state = '已达卖出线'
            elif gap is not None and gap <= 0.3:
                state = '接近买入线'
            else:
                state = '未触发'

        rows.append({
            'name': name, 'code': code,
            'group': WATCH_GROUP.get(code),
            'buy': buy, 'sell': sell, 'mode': mode,
            'dates': dates,
            'price': prices,
            'ohlc': ohlc,
            'dps': dps_series,
            'dy': dy_series,
            'pair': pair_series,
            'latest': {
                'date': dates[-1], 'price': round(cur_price, 2),
                'dps': cur_dps, 'dy': cur_dy, 'pair': pair_series[-1],
                'gap_to_buy': gap, 'state': state,
                'dy_pct_rank': pct_rank(dy_series, cur_dy),
                'dy_min': min([x for x in dy_series if x is not None], default=None),
                'dy_max': max([x for x in dy_series if x is not None], default=None),
            },
        })

    out = {'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
           'first_date': START, 'price_src': src_count, 'rows': rows}
    fp = os.path.join(DATA, 'dividend.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)

    # 上报。「数据到哪天」取 latest.date（那天算出来的股息率），
    # 不要取分红公告日期 —— 公告天然滞后几个月，拿它算 lag 会天天误报。
    _last = max((r['latest']['date'] for r in rows if r.get('latest')), default=None)
    want = len(STOCKS)
    if not rows:
        _fh.report('dividend', '个股股息率', 'fail', note='一只都没抓到')
    else:
        _fh.report('dividend', '个股股息率',
                   'ok' if len(rows) >= want else 'partial',
                   last=_last, got=len(rows), want=want,
                   note='' if len(rows) >= want else f'{want - len(rows)} 只未获取')
    print(f'\n写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)')
    print(f'\n{"名称":10}{"股息率":>8}{"买入线":>8}{"卖出线":>8}{"距买入":>9}  {"分位":>6}  状态')
    for r in rows:
        L = r['latest']
        b = f"{r['buy']:.1f}" if r['buy'] else '—'
        s = f"{r['sell']:.1f}" if r['sell'] else '—'
        g = f"{L['gap_to_buy']:+.2f}" if L['gap_to_buy'] is not None else '—'
        pr = f"{L['dy_pct_rank']:.0f}%" if L['dy_pct_rank'] is not None else '—'
        print(f"{r['name']:10}{L['dy']:>7.2f}%{b:>8}{s:>8}{g:>9}  {pr:>6}  {L['state']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

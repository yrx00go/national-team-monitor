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
    ('中国人寿', '601628', 'sh', None, None, None),
    ('长江电力', '600900', 'sh', 5.0, None, 'hold'),
]
START = '2023-12-01'   # 与 ETF 模块对齐：2023 年 12 月至今
EM_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'


def _get(url, referer=None):
    h = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
    if referer:
        h['Referer'] = referer
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=h), timeout=30).read()


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


def fetch_price(code, market, bars=900):
    sym = market + code
    url = (f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
           f'param={sym},day,,,{bars},bfq')
    js = json.loads(_get(url, 'https://gu.qq.com/').decode('utf-8', 'ignore'))
    node = (js.get('data') or {}).get(sym) or {}
    k = node.get('day') or node.get('bfqkline') or []
    return [(row[0], float(row[2])) for row in k if len(row) >= 3]


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
    for name, code, mk, buy, sell, mode in STOCKS:
        print(f'拉价格 {code} {name}…')
        px = [(d, p) for d, p in fetch_price(code, mk) if d >= START]
        if not px:
            print(f'  跳过 {code}：无价格')
            continue
        recs = bonus[code]
        dates = [d for d, _ in px]
        prices = [p for _, p in px]

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
            'buy': buy, 'sell': sell, 'mode': mode,
            'dates': dates,
            'price': prices,
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
           'first_date': START, 'rows': rows}
    fp = os.path.join(DATA, 'dividend.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
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

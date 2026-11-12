#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 国家队持仓基准 —— 基金定期报告披露的「机构持有比例」。

这是全站唯一能称为"国家队份额"的官方数字。
来源：基金半年报/年报的「持有人结构」章节（季报不披露持有人结构）。
粒度：一年 2 次（6-30 / 12-31），滞后约 2 个月。

⚠ 必须理解的三个口径问题：
1. 「机构」不等于「国家队」。机构里还有保险、年金、券商、FOF、理财子。
   但宽基 ETF 的机构占比 90%+ 时，能到这个体量的只有汇金系 + 保险。
2. 机构占比是比例，不是份额。份额 × 占比 = 机构持有份额，但占比下降
   可能只是因为散户涌入稀释，不代表机构卖出。两个数要一起看。
3. 2026 年发生了巨额赎回（510300 从 888 亿份掉到 189 亿份），
   机构占比同步从 90% 掉到 62% —— 这意味着"赎回的主要是机构"。

另附：基金规模变动（期间申购/赎回），可反推资金进出方向。
"""
import json, os, re, sys, time, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
os.makedirs(DATA, exist_ok=True)

WATCH = [
    ('上证50',   '510050', '50ETF'),
    ('沪深300',  '510300', '300ETF华泰柏瑞'),
    ('中证500',  '510500', '500ETF南方'),
    ('中证1000', '512100', '1000ETF南方'),
    ('科创50',   '588000', '科创50ETF华夏'),
    ('红利',     '510880', '红利ETF华泰柏瑞'),
    ('红利',     '515080', '中证红利ETF招商'),
    ('创业板',   '159915', '创业板ETF易方达'),
    ('港股红利', '159545', '港股通红利低波ETF'),
    ('沪深300',  '159919', '300ETF嘉实'),
]

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
      'Referer': 'https://fundf10.eastmoney.com/'}


def _fetch(kind, code):
    url = f'https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type={kind}&code={code}&rt=0.1'
    for i in range(3):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=20).read().decode('utf-8', 'ignore')
        except Exception:
            if i == 2:
                raise
            time.sleep(1.2)


def holders(code):
    """持有人结构：[(日期, 机构%, 个人%, 内部%, 总份额亿)]"""
    t = _fetch('cyrjg', code)
    rows = re.findall(
        r'<tr><td>([\d-]+)</td><td[^>]*>([\d.]+)%</td><td[^>]*>([\d.]+)%</td>'
        r'<td[^>]*>([\d.]+)%</td><td[^>]*>([\d,.]+)</td>', t)
    out = []
    for dt, inst, indiv, inner, total in rows:
        try:
            out.append({
                'date': dt,
                'inst_pct': float(inst),
                'indiv_pct': float(indiv),
                'inner_pct': float(inner),
                'total_yi': float(total.replace(',', '')),
            })
        except ValueError:
            continue
    return out


def scale(code):
    """规模变动：[(日期, 申购亿, 赎回亿, 期末份额亿, 期末净资产亿)]"""
    t = _fetch('gmbd', code)
    rows = re.findall(
        r'<tr><td>([\d-]+)</td><td[^>]*>([\d,.]+)</td><td[^>]*>([\d,.]+)</td>'
        r'<td[^>]*>([\d,.]+)</td><td[^>]*>([\d,.]+)</td>', t)
    out = []
    for dt, buy, sell, total, nav in rows:
        try:
            out.append({
                'date': dt,
                'buy_yi': float(buy.replace(',', '')),
                'sell_yi': float(sell.replace(',', '')),
                'total_yi': float(total.replace(',', '')),
                'nav_yi': float(nav.replace(',', '')),
            })
        except ValueError:
            continue
    return out[:12]


def main():
    data = {'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'), 'rows': []}
    for idx, code, name in WATCH:
        try:
            h = holders(code)
            s = scale(code)
        except Exception as e:
            print(f'  {code} 失败 {e}')
            continue
        latest = h[0] if h else None
        rec = {
            'index': idx, 'code': code, 'name': name,
            'holders': h[:8],
            'scale': s,
            'latest': latest,
            'inst_share_yi': round(latest['total_yi'] * latest['inst_pct'] / 100, 2) if latest else None,
        }
        data['rows'].append(rec)
        if latest:
            print(f"  {code:<8}{name:<18} {latest['date']}  总{latest['total_yi']:>8.2f}亿  "
                  f"机构{latest['inst_pct']:>6.2f}%  → 机构持有 {rec['inst_share_yi']:>8.2f}亿份")
        time.sleep(0.35)

    fp = os.path.join(DATA, 'inst_holdings.json')

    # ⚠ 与 fetch_price_bfq.py 同一个坑：不能无条件写盘。
    #   这里 rows 是「这一轮抓到的」，全失败时是 []，直接写就会把
    #   上一次有效的 10 只机构持仓覆盖成空数组 —— 机构页整块变空白，
    #   而且日志里只有一行「写入 ...」，看不出任何异常。
    if not data['rows']:
        kept = '-'
        old_last = None
        if os.path.exists(fp):
            try:
                old = json.load(open(fp, encoding='utf-8'))
                kept = f"{len(old.get('rows') or [])} 只"
                old_last = max((r.get('latest') or {}).get('date') or ''
                               for r in (old.get('rows') or [])) or None
            except Exception:
                kept = '旧文件读不出来'
        _fh.report('inst', '机构持仓（半年报/年报）', 'fail',
                   last=old_last, got=0, want=len(WATCH), note='今日一只都没抓到，沿用旧数据')
        print(f'\n✗ 一只都没抓到，不写盘。旧文件保留 {kept}')
        return 2

    # 部分成功：沿用上一轮里这次没抓到的标的，别让瞬时故障删掉整只 ETF。
    if os.path.exists(fp):
        try:
            old = json.load(open(fp, encoding='utf-8'))
            got = {r['code'] for r in data['rows']}
            kept_rows = [r for r in (old.get('rows') or []) if r.get('code') not in got]
            if kept_rows:
                data['rows'] = sorted(data['rows'] + kept_rows, key=lambda r: r.get('index', 99))
                print(f'  沿用旧数据 {len(kept_rows)} 只：'
                      f'{" ".join(r["code"] for r in kept_rows)}')
        except Exception as e:
            print(f'  ⚠ 旧文件读不出来（{type(e).__name__}），本次不合并')

    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, fp)          # 原子替换，不留半截文件
    print(f'\n写入 {fp}')

    # 汇总：最新一期机构持有份额合计
    tot = sum(r.get('inst_share_yi') or 0 for r in data['rows'])
    print(f'十只 ETF 机构持有份额合计：{tot:,.2f} 亿份')

    # 上报。机构持仓一年 2 次（6-30 / 12-31），滞后约 2 个月 ——
    # 拿日频标准要求它就会天天误报，所以 _fh.LAG_OK 里给它单独放宽到 180 天。
    inst_last = max((r.get('latest') or {}).get('date') or ''
                    for r in data['rows']) or None
    _fh.report('inst', '机构持仓（半年报/年报）',
               'ok' if len(data['rows']) == len(WATCH) else 'partial',
               last=inst_last, got=len(data['rows']), want=len(WATCH))
    return 0 if len(data['rows']) == len(WATCH) else 1


if __name__ == '__main__':
    # ⚠ 原来这里是 main()，没有 sys.exit —— 于是无论成败退出码都是 0，
    #   失败永远不会传到 update.py / Actions 的报警里。
    sys.exit(main())

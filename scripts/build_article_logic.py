#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「满仓猜想」页的静态数据。

来源文章：梦想超过余额宝《牛市启动前，国家队是满仓吗？》2026-11-25
https://mp.weixin.qq.com/s/awqsxJmxj3mfqBbzkzspBw

本站不复现作者的「当前水位 / max 仓位」模型（作者未公开算法），
只用同一批公开份额数据，按透明口径复算「牛熊锚点比」：
    牛熊锚点比 = 2024-09-23 份额 / 2025-12-31 份额
    当前水位（本站口径） = 最新份额 / 2025-12-31 份额
    距锚点 = 牛熊锚点比 - 当前水位
单位统一为「亿份」（shares.json 原始单位是万份）。
"""
import json, os
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

BULL = '2024-09-23'   # 作者定义的「牛市启动前」
BEAR = '2025-12-31'   # 作者定义的「熊市启动前 / 汇金离场前」
URL = 'https://mp.weixin.qq.com/s/awqsxJmxj3mfqBbzkzspBw'

# 文章点名的七个标的。名字按原文，代码用于查本站 shares.json。
ITEMS = [
    ('510050', '上证50ETF华夏',     97.87),
    ('510300', '沪深300ETF华泰柏瑞', 99.88),
    ('159919', '沪深300ETF嘉实',    92.15),
    ('510500', '中证500ETF南方',    97.65),
    ('512100', '中证1000ETF南方',   84.66),
    ('159915', '创业板ETF易方达',  130.12),
    ('588080', '科创50ETF易方达',  114.00),
]


def yi(v):
    """万份 → 亿份。"""
    return None if v is None else round(v / 10000, 2)


def main():
    shares = json.load(open(os.path.join(DATA, 'shares.json'), encoding='utf-8'))
    dates = sorted(shares)
    latest = dates[-1]
    bmap, rmap, cmap = shares.get(BULL, {}), shares.get(BEAR, {}), shares[latest]

    rows = []
    for code, name, article_pct in ITEMS:
        b, r, c = bmap.get(code), rmap.get(code), cmap.get(code)
        has_anchor = b is not None and r is not None and r != 0
        if has_anchor:
            bull_bear = round(b / r * 100, 2)
            current = round(c / r * 100, 2) if c is not None else None
            gap = round(bull_bear - current, 2) if current is not None else None
        else:
            bull_bear = current = gap = None
        rows.append({
            'code': code,
            'name': name,
            'bull': yi(b),
            'bear': yi(r),
            'current': yi(c),
            'hasAnchor': has_anchor,
            'bullBearPct': bull_bear,
            'currentPct': current,
            'gapPct': gap,
            'articlePct': article_pct,
        })

    out = {
        'url': URL,
        'source': '梦想超过余额宝（公众号）',
        'published': '2026-11-25',
        'fetchDate': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'bullDate': BULL,
        'bearDate': BEAR,
        'latestDate': latest,
        'rows': rows,
        'note': '本站口径≠作者口径。作者「还差多少」用的是未公开的当前水位模型；'
                '本站用「最新份额/熊市锚点份额」作透明代理，只比方向不比数值。',
    }
    p = os.path.join(DATA, 'article_logic.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'写入 {p}  锚点 {BULL} / {BEAR}  最新 {latest}  '
          f'可复算 {sum(r["hasAnchor"] for r in rows)}/{len(rows)}')


if __name__ == '__main__':
    main()

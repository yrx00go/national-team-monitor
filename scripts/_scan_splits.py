#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描份额序列里的「折算/分拆」断点 —— 那不是申赎，不能算成净流入。

为什么要单独扫
──────────────
2026-11-17 把组合从 7 只扩到 23 只，新进 16 只里有老产品，历史上可能做过
份额合并/分拆。512100 就吃过这个亏：2022-09-02 份额合并 1:0.36555，
不修正的话那天会被算成「单日净流出 65 亿」——那是基金公司在改记账单位，
不是任何人卖了货。

判据：单日份额变动超过 ±15% 就人工看一眼。
    真实申赎极少超过 15%（华泰柏瑞 300ETF 单日最高约 +8%），
    折算动辄 −60% 或 +170%，一眼就能分开。

用法：python3 scripts/_scan_splits.py
输出：可疑日期清单，人工核实后写进 compute_demo.py 的 SPLITS 字典。
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _universe

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
hist = json.load(open(os.path.join(DATA, 'shares.json'), encoding='utf-8'))
days = sorted(d for d, v in hist.items() if '510300' in v)

print(f'扫描 {len(days)} 个交易日 · {len(_universe.UNIVERSE)} 只标的\n')

for u in _universe.UNIVERSE:
    code, name, role = u[0], u[2], u[5]
    ds = [d for d in days if code in hist[d]]
    if len(ds) < 2:
        print(f'{code} {name:16s} [{role}] 只有 {len(ds)} 天，跳过')
        continue
    hits = []
    for i in range(1, len(ds)):
        a, b = hist[ds[i - 1]][code], hist[ds[i]][code]
        if a and b and abs(b / a - 1) > 0.15:
            hits.append((ds[i], a, b, (b / a - 1) * 100))
    if hits:
        print(f'{code} {name:16s} [{role}]  ⚠ {len(hits)} 处可疑')
        for d, a, b, pct in hits:
            print(f'    {d}  {a/10000:10.2f} → {b/10000:10.2f} 亿份   {pct:+.1f}%   比例 {b/a:.5f}')
    else:
        print(f'{code} {name:16s} [{role}]  ✓ 无异常（{len(ds)} 天，最大日变动 '
              f'{max(abs(hist[ds[i]][code]/hist[ds[i-1]][code]-1)*100 for i in range(1,len(ds)) if hist[ds[i-1]][code]):.1f}%）')

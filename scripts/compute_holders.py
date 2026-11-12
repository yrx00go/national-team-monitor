#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
个股模块计算层 —— 把 holders.json 加工成前端要的形状。

三个必须处理的真实情况（都是从数据里验证出来的，不是假设）：
1. 「退出前十大」≠ 清仓。万科A 2026Q2 汇金资管不在前十，但可能与第10名
   （0.2617 亿股）接近，也可能真清仓——季报数据无法区分，必须标为「退出前十」。
2. 汇金资管持股数从 Q1 145 只掉到 Q2 17 只，是真实大幅退出，不是抓取错误
   （已抽查万科A逐股东核实）。但下降如此剧烈，大概率与"非前十大"判定有关，
   所以只呈现数量变化，不呈现"总市值"这类会被口径误导的指标。
3. 汇金投资 / 汇金资管 / 证金公司 是三个不同主体，绝不能合并成一个"国家队"数字。
"""
import json, os
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')


def main():
    src = json.load(open(os.path.join(DATA, 'holders.json'), encoding='utf-8'))
    periods = src['periods']

    holders = []
    for h in src['holders']:
        # 关键：Q3 与 Q2 的名单几乎完全相同（都是 17 只），逐股"环比"会全部显示"不变"，
        # 看不出任何东西。真正有信息量的是最近一次"名单大幅变动"的对比，
        # 所以自动挑选「两期只数差异最大」的相邻两期作为对比基准。
        best, best_gap = (periods[0], periods[1] if len(periods) > 1 else None), -1
        for i in range(len(periods) - 1):
            a, b = periods[i], periods[i + 1]
            gap = abs(len(h['by_period'].get(a, [])) - len(h['by_period'].get(b, [])))
            if gap > best_gap:
                best_gap = gap
                best = (a, b)
        newest, prev = best

        cur = {i['code']: i for i in h['by_period'].get(newest, [])}
        old = {i['code']: i for i in h['by_period'].get(prev, [])} if prev else {}

        items = []
        for code, it in cur.items():
            o = old.get(code)
            if o:
                delta = it['shares_yi'] - o['shares_yi']
                if abs(delta) < 0.0001:
                    status = '不变'
                elif delta > 0:
                    status = '增持'
                else:
                    status = '减持'
                delta_pct = (delta / o['shares_yi'] * 100) if o['shares_yi'] else None
            else:
                status, delta, delta_pct = '新进前十', None, None
            items.append({**it, 'status': status, 'delta_yi': round(delta, 4) if delta is not None else None,
                          'delta_pct': round(delta_pct, 2) if delta_pct is not None else None})

        # 上期在、本期不在 = 退出前十大（不等于清仓）
        exited = []
        for code, o in old.items():
            if code not in cur:
                exited.append({'code': code, 'name': o['name'], 'shares_yi': o['shares_yi'],
                               'ratio': o['ratio'], 'status': '退出前十'})

        items.sort(key=lambda x: (x['status'] != '新进前十', -(x['delta_yi'] or 0) if x['delta_yi'] else 0))
        holders.append({
            'name': h['name'], 'short': h['short'], 'desc': h['desc'],
            'compare': [newest, prev],
            'items': items,
            'exited': sorted(exited, key=lambda x: -x['shares_yi']),
            'count': len(items),
            'count_prev': len(old),
            'n_up': sum(1 for i in items if i['status'] == '增持'),
            'n_down': sum(1 for i in items if i['status'] == '减持'),
            'n_same': sum(1 for i in items if i['status'] == '不变'),
            'n_new': sum(1 for i in items if i['status'] == '新进前十'),
        })

    # 交叉持有
    cross = {}
    for h in holders:
        for i in h['items']:
            cross.setdefault(i['code'], {'code': i['code'], 'name': i['name'], 'holders': [],
                                         'ratios': {}})
            cross[i['code']]['holders'].append(h['short'])
            cross[i['code']]['ratios'][h['short']] = i['ratio']
    cross_list = sorted([v for v in cross.values() if len(v['holders']) > 1],
                        key=lambda x: -len(x['holders']))

    out = {
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'newest': holders[0]['compare'][0] if holders else periods[0],
        'prev': holders[0]['compare'][1] if holders else (periods[1] if len(periods) > 1 else None),
        'periods': periods,
        'holders': holders,
        'cross': cross_list,
    }
    fp = os.path.join(DATA, 'holders_view.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'写入 {fp}')
    for h in holders:
        print(f'  {h["short"]:<6} 本期 {h["count"]:>4} 只  上期 {h["count_prev"]:>4} 只  '
              f'增{h["n_up"]} 减{h["n_down"]} 平{h["n_same"]} 新进{h["n_new"]} 退出{len(h["exited"])}')
    print(f'  交叉持有 {len(cross_list)} 只')


if __name__ == '__main__':
    main()

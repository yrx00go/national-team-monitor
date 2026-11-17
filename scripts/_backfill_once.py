#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性回补：把 core 沪市 15 只在 2020 年起的份额全部补齐。

为什么需要这一步
────────────────
2026-11-17 把监控组合从 7 只扩到官方 23 只，其中沪市 15 只里有 11 只是新增的。
shares.json 里那 1664 个交易日只存了老的 7 只 —— **历史是有的，只是当初写盘时
被 WATCH 过滤掉了**。上交所接口一次返回 924 只全量，所以重跑一遍就能把这 11 只
的历史一起补上，不用等两年。

每补 100 天落一次盘，中途断了也不会白跑。
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_etf as fe

fp = os.path.join(fe.DATA, 'shares.json')
hist = json.load(open(fp, encoding='utf-8'))
all_days = sorted(hist)
sse_core = [w[1] for w in fe.WATCH if w[3] == 'SSE' and w[4] == 'core']
todo = [d for d in all_days if not all(c in hist[d] for c in sse_core)]

print(f'总 {len(all_days)} 天，需补 {len(todo)} 天，目标 {len(sse_core)} 只 core 沪市', flush=True)
print(f'区间 {todo[0] if todo else "-"} → {todo[-1] if todo else "-"}', flush=True)

ok = fail = 0
fails = []
t0 = time.time()
for i, d in enumerate(todo, 1):
    sse = {}
    for attempt in range(3):
        try:
            sse = fe.fetch_sse(d)
            break
        except Exception as e:
            if attempt == 2:
                fails.append((d, repr(e)))
            time.sleep(1.2 * (attempt + 1))
    if not sse:
        fail += 1
        print(f'[{i}/{len(todo)}] {d} ✗', flush=True)
        continue
    hist.setdefault(d, {}).update({c: round(v, 2) for c, v in sse.items() if c in fe.CODES})
    ok += 1
    if i % 100 == 0:
        json.dump(dict(sorted(hist.items())), open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
        el = time.time() - t0
        eta = el / i * (len(todo) - i) / 60
        print(f'[{i}/{len(todo)}] {d}  ok={ok} fail={fail}  '
              f'{el/60:.1f}分  剩余约 {eta:.0f} 分', flush=True)

json.dump(dict(sorted(hist.items())), open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
print(f'\n完成 ok={ok} fail={fail}  用时 {(time.time()-t0)/60:.1f} 分', flush=True)
if fails:
    print('失败日期（前 10 条）：', flush=True)
    for d, e in fails[:10]:
        print(f'  {d} {e}', flush=True)

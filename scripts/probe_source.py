#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源探针 —— 只回答一个问题：这个接口此刻到底通不通。

⚠ 为什么需要它（2026-11-12 晚）：

   三次 CI 得到一个说不通的现象：

     | run | 份额请求 | 不复权 fqt=0 | 前复权 fqt=1 |
     |---|---|---|---|
     | 第1次 | 846 | 0/10 | 0/10 |
     | 第2次 | 846 | 7/10 | 0/10 |
     | 第3次 |   1 | 5/10 | 0/10 |

   把份额请求从 846 降到 1 之后，不复权从 0/10 变成 5/10（说明「自我限流」这个解释
   至少部分成立），但**前复权恒为 0/10，一次都没通过**。

   而前复权和不复权在代码里只差两处：
     · 参数 fqt=1 vs fqt=0
     · 排队位置：fqt=0 在第 2 位跑，fqt=1 在第 6 位跑

   **两个变量同时变，就无法归因。** 这正是我在这个项目里反复犯的第 4 类错误。
   所以不猜，做一次只动一个变量的实验。

用法：
    python3 scripts/probe_source.py            # 单次探测，打印结果
    python3 scripts/probe_source.py --loop 3   # 连探 3 轮，看是否间歇性

设计原则：
  1. **只读**。不写任何 data/ 文件，不产生上报记录。跑坏了对生产零影响。
  2. **区分「通但空」和「不通」**。接口改了版会返回 200 + 空 klines，
     这和连接被掐断是两种完全不同的故障，混在一起就看不出来了。
  3. 每一行都带耗时。间歇性掐断的典型特征是「秒回 vs 卡满 timeout」。
"""
import json
import sys
import time
import urllib.request
from datetime import date

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}

# 只探一只，参数可控。510050 是最老、数据最全的一只。
SECID = '1.510050'


def _probe(fqt, beg, timeout=20, label=''):
    """返回 (状态, 说明, 耗时秒)。

    状态取值刻意分开，因为它们的处置完全不同：
      ok     通且有数据
      empty  通了但 klines 为空 → 接口改版 / 参数错，不是网络问题
      broken 连不上 → 网络 / IP 被掐
    """
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={SECID}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt={fqt}'
           f'&beg={beg}&end={date.today():%Y%m%d}')
    t0 = time.time()
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers={**UA, 'Referer': 'https://quote.eastmoney.com/'}),
            timeout=timeout).read()
    except Exception as e:
        return 'broken', f'{type(e).__name__}', round(time.time() - t0, 2)

    dt = round(time.time() - t0, 2)
    try:
        js = json.loads(raw.decode('utf-8', 'ignore'))
    except Exception as e:
        return 'broken', f'返回不是 JSON（{type(e).__name__}）', dt

    kl = (js.get('data') or {}).get('klines') or []
    if not kl:
        # ⚠ 这一条必须和 broken 分开报。
        #   200 + 空，说明服务器收到了请求并正常响应，只是没数据 ——
        #   常见原因是接口改名 / 参数不再被接受。当成网络问题去加重试是白费。
        return 'empty', '通了但 0 根', dt
    return 'ok', f'{len(kl)} 根 · 末 {kl[-1].split(",")[0]}', dt


def main():
    loop = 1
    if '--loop' in sys.argv:
        i = sys.argv.index('--loop')
        loop = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 3

    # 三组对照，每轮都按同样顺序打：
    #   ① fqt=0 近端（小响应）  ← 对应 fetch_history 的增量路径
    #   ② fqt=1 近端（小响应）  ← 同上，只差 fqt
    #   ③ fqt=0 全量（大响应）  ← 对应 fetch_price_bfq
    #   ④ fqt=1 全量（大响应）  ← 对应 --refresh-prices
    # ①②对比 = 单看 fqt 的影响；①③对比 = 单看响应体积的影响。
    cases = [
        ('fqt=0 近端', 0, '20260901'),
        ('fqt=1 近端', 1, '20260901'),
        ('fqt=0 全量', 0, '20190101'),
        ('fqt=1 全量', 1, '20190101'),
    ]

    print(f'探针 → push2his / {SECID} / {date.today()}　共 {loop} 轮')
    print(f'{"":14}{"状态":>8}{"耗时":>7}   说明')
    tally = {}
    for r in range(loop):
        if loop > 1:
            print(f'--- 第 {r + 1} 轮 ---')
        for label, fqt, beg in cases:
            st, msg, dt = _probe(fqt, beg, label=label)
            tally.setdefault(label, []).append(st)
            print(f'{label:14}{st:>8}{dt:>7.2f}s   {msg}')
            time.sleep(0.5)

    print('\n=== 汇总 ===')
    for label, _, _ in cases:
        v = tally.get(label) or []
        n_ok = v.count('ok')
        print(f'{label:14} 通 {n_ok}/{len(v)}'
              + ('　' + ' '.join(v) if len(v) > 1 else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补抓【不复权】日线，用于计算净流入。

为什么必须用不复权价：
    净流入 = 份额差 × 当日真实成交价。ETF 分红除权那天，真实成交价会掉一个分红额，
    这时「份额不变、价格下跌」不代表亏损，而是分红落到了持有人账上。
    用前复权价算净流入是正确方向（消除了分红断层），
    但前复权价在历史区间上会被系统性压低（实测 2024-06-03：上证50 −5.4%、
    红利 −8.8%、中证红利 −10.9%），导致早期净流入被低估。
    正确做法：净流入一律用不复权（当日真实价），画价格曲线一律用前复权（趋势连续）。

输出：data/prices_bfq.json  {code: [[date, close], ...]}
"""
import json, os, sys, time, urllib.request
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

# 抓取起始日。真正决定页面区间的是 compute_demo.py 的 START，
# 这里留出余量早抓几天，免得 START 前移后要重抓。
BEG = '20190101'

CODES = {
    '510050': 'sh', '510300': 'sh', '510500': 'sh', '512100': 'sh',
    '588000': 'sh', '510880': 'sh', '515080': 'sh',
    '159915': 'sz', '159545': 'sz', '159919': 'sz',
}


def _get(url, referer='https://gu.qq.com/'):
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
        'Referer': referer,
    })
    return urllib.request.urlopen(req, timeout=30).read()


def fetch(code, market, bars=900):
    """⚠ 这里曾经用腾讯接口，但腾讯硬上限 640 根（实测 2500 根直接返回空），
    只能回溯到 2024-03 左右，无法支撑 2020 年起的区间。
    改用东方财富 push2his，可回溯到上市首日，且【前复权序列与腾讯逐日吻合】
    （实测 510880 重叠 751 天，中位差 0.0000%，最大差 0.177%），换源不会改变已有结论。

    fqt=0 = 不复权（这里要的）；fqt=1 = 前复权。
    """
    secid = ('1.' if market == 'sh' else '0.') + code
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={secid}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt=0'
           f'&beg={BEG}&end={date.today().strftime("%Y%m%d")}')
    js = json.loads(_get(url, 'https://quote.eastmoney.com/').decode('utf-8', 'ignore'))
    kl = (js.get('data') or {}).get('klines') or []
    return [(s.split(',')[0], float(s.split(',')[1])) for s in kl]


def main():
    out, bad = {}, []
    for code, mk in CODES.items():
        try:
            arr = fetch(code, mk)
            if not arr:
                bad.append((code, '空返回'))
                continue
            out[code] = arr
            print(f'  {code}  {len(arr):>4} 根  {arr[0][0]} → {arr[-1][0]}  末价 {arr[-1][1]:.3f}')
        except Exception as e:
            bad.append((code, f'{type(e).__name__}: {e}'))
        time.sleep(0.4)

    fp = os.path.join(DATA, 'prices_bfq.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'\n写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)  成功 {len(out)}/{len(CODES)}')
    if bad:
        print('失败：')
        for c, r in bad:
            print(f'  {c}  {r}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

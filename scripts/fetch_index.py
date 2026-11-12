#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓大盘日线：上证指数点位 + 两市成交额。用于组合页「净申赎 × 大盘位置」叠加图
与「量能 / 杠杆」面板。

为什么要有它：
    原有的净流入图只能回答「钱进还是出」，回答不了「这个进出发生在什么位置」。
    同一根 +120 亿的柱子，出现在 2700 点和 3600 点，含义完全不同 ——
    前者是「抄底」，后者是「追高」。点位是资金流的分母。
    成交额是第二个分母：同样的 120 亿，在 8,000 亿的地量里是异动，
    在 2.5 万亿的天量里只是噪音。

为什么是指数点位而不是价格：
    组合是 9 只 ETF 的合计，它们没有统一的「价格」。
    上证指数是唯一一条能代表「这个组合所处大盘位置」的公开序列。

接口：东方财富 push2his，klt=101 日线，fqt=0 不复权。
      secid=1.000001 → 上证指数（沪市）
      secid=0.399001 → 深证成指（深市）
      ⚠ 该接口 data.name 返回 None（指数不给名称），但 data.code 与 klines 完整，
        不要因为 name 为 None 就判断抓取失败。

      fields2 字段位：f51=日期 f52=开 f53=收 f54=高 f55=低 f56=成交量(手/f57=成交额(元)
      ⚠ 成交额必须用 f57（元），不是 f56（成交量，单位手）。
        实测 2026-11-11：沪市 f57=5.7e11 元 = 5,690 亿，与交易所公布口径一致。

为什么两市要相加：
    「两市成交额」是市场共识口径（新闻里说的「今日成交 1.6 万亿」都是这个）。
    只取沪市会漏掉深市 40%+ 的量，得出「量能萎缩」的错觉。

⚠ 盘中陷阱：接口在交易时段会返回【当日未收盘】的数据。
   实测 2026-11-12 10:21 抓取，当日成交额只有 8,486 亿（半日量），
   而 11-11 全天是 16,762 亿。若不剔除，页面会把「半天」当「全天」，
   量能萎缩 50% 的假信号就出来了。
   → 处理：只接受日期 < 今天的记录。当日数据一律丢弃，等第二天再取。

输出：data/index_sh.json
      {
        "000001": [[date, close], ...],           ← 上证点位（保持旧格式，别改）
        "turnover": [[date, 两市成交额(亿元)], ...]  ← 新增
      }
"""
import json, os, sys, time, urllib.request
from datetime import date

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

BEG = '20190101'   # 与 fetch_price_bfq.py 保持一致，多留余量
TODAY = date.today().isoformat()


def _get(url, referer='https://quote.eastmoney.com/', retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
                'Referer': referer,
            })
            return urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def fetch_kline(secid, fields2='f51,f53'):
    """返回 (code, [(date, 字段...), ...])，字段顺序与 fields2 一一对应"""
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={secid}&fields1=f1,f2&fields2={fields2}&klt=101&fqt=0'
           f'&beg={BEG}&end={date.today().strftime("%Y%m%d")}')
    js = json.loads(_get(url).decode('utf-8', 'ignore'))
    d = js.get('data') or {}
    kl = d.get('klines') or []
    nf = len(fields2.split(','))
    out = []
    for s in kl:
        p = s.split(',')
        if len(p) < nf:
            continue
        # 除日期外全部转 float
        out.append([p[0]] + [float(x) for x in p[1:nf]])
    return d.get('code') or secid.split('.')[1], out


def main():
    # ① 上证点位
    try:
        code, arr = fetch_kline('1.000001')
    except Exception as e:
        print(f'上证指数抓取失败：{type(e).__name__}: {e}')
        return 1
    if not arr:
        print('上证指数返回为空（接口可能改版）—— 不做任何写入，避免用空文件覆盖旧数据')
        return 1

    # 剔除当日未收盘数据
    arr = [x for x in arr if x[0] < TODAY]
    if not arr:
        print('上证指数剔除当日盘中数据后为空 —— 不写入')
        return 1

    lo = min(x[1] for x in arr)
    hi = max(x[1] for x in arr)
    pos = [[x[0], round(x[1], 2)] for x in arr]
    print(f'  上证指数 {code}  {len(pos)} 根  {pos[0][0]} → {pos[-1][0]}')
    print(f'  首值 {pos[0][1]:.2f}   末值 {pos[-1][1]:.2f}   区间 {lo:.2f} ~ {hi:.2f}')

    # ② 两市成交额（沪 + 深）
    turn = {}
    ok = 0
    for secid, tag in (('1.000001', '沪'), ('0.399001', '深')):
        try:
            _, kl = fetch_kline(secid, 'f51,f57')
        except Exception as e:
            print(f'{tag}市成交额抓取失败：{type(e).__name__}: {e}')
            continue
        kl = [x for x in kl if x[0] < TODAY]      # 同样剔除盘中
        if not kl:
            print(f'{tag}市成交额为空')
            continue
        ok += 1
        for d, amt in kl:
            turn[d] = turn.get(d, 0.0) + amt / 1e8    # 元 → 亿元
        print(f'  {tag}市  {len(kl)} 根  {kl[0][0]} → {kl[-1][0]}')

    turn_arr = []
    if ok == 2:
        turn_arr = [[d, round(v, 1)] for d, v in sorted(turn.items())]
        if turn_arr:
            print(f'  两市成交额  {len(turn_arr)} 天  '
                  f'最新 {turn_arr[-1][0]} = {turn_arr[-1][1]:,.0f} 亿')
            # 自检：沪市单边应在 2,000–15,000 亿区间，超出说明字段位取错了
            _hi = max(v for _, v in turn_arr)
            if _hi > 60000 or _hi < 2000:
                print(f'  ⚠ 成交额量级异常（最大 {_hi:,.0f} 亿）—— 疑似字段位错误，本次不写入 turn')
                turn_arr = []
    elif ok == 1:
        print('  ⚠ 只拿到单市成交额，两市合计不完整 —— 本次不写入 turn，保留旧值')

    out = {code: pos}
    if turn_arr:
        out['turnover'] = turn_arr
    else:
        # 保留旧的 turnover，避免一次抓取失败把历史量能清空
        old_fp = os.path.join(DATA, 'index_sh.json')
        if os.path.exists(old_fp):
            old = json.load(open(old_fp, encoding='utf-8'))
            if old.get('turnover'):
                out['turnover'] = old['turnover']
                print(f'  沿用旧 turnover {len(old["turnover"])} 天')

    fp = os.path.join(DATA, 'index_sh.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'\n写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""K 线（开高低收）：上证指数 + 黄金 ETF。

═══ 为什么要单独一个脚本，而不是复用 fetch_price_bfq.py ═══

现有那个脚本抓的是**前复权收盘价**，一列数，用来算净流入
（净流入 = 份额差 × 当日价）。它只要收盘价，所以 parse_tx 只取了 row[2]。

K 线要四个数：开、高、低、收。**同一份响应里就有**，不需要新数据源 ——
腾讯的 fqkline 行格式本来就是 [日期, 开, 收, 高, 低, 量]。
但两件事的口径不同，塞进一个脚本会互相牵制：
  · 复权口径：画 K 线要用**不复权**（bfq），前复权会让历史蜡烛图形变样，
    除权那天画出一根假的巨阴线。
  · 完整性：净流入那条链只关心最新几天，K 线要全量。
所以分开。这不是重复造轮子，是两种口径。

═══ 实测（2026-11-16）═══

  上证指数 sh000001    1908 根   2019-01-02 ~ 2026-11-16
  黄金ETF华安 sh518880  1901 根   2019-01-11 ~ 2026-11-16

⚠ 腾讯的 bars 参数上限不是 640。fetch_price_bfq.py 里 TX_MAX_BARS=640
  是**增量抓取的窗口大小**，不是接口上限。实测传 1907 能拿回 1908 根
  （含一根新股/首日）。传 3000 返回 0 根 —— 超上限时它不报错，直接空。
  **所以要有个下限保护：拿回来的根数 < 请求值的 90% 就认为参数超限了。**

═══ 为什么黄金用 518880 而不是别的 ═══

  518880 华安黄金ETF   1901 根  2019-01-11 起   规模最大、成交最活跃
  518800 国泰黄金ETF   1901 根  2019-01-11 起
  159934 易方达黄金ETF 1900 根  2019-01-11 起
  三只起点几乎相同，选 518880 是因为它跟踪的 AU9999 是上海金基准，
  和页面上「黄金」这个说法最对得上。要换只改 TARGETS 里的 sym。

用法：
  python3 scripts/fetch_kline.py            # 增量（有则只补最新）
  python3 scripts/fetch_kline.py --full     # 全量重抓
"""
import json
import os
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
OUT = os.path.join(DATA, 'kline.json')

sys.path.insert(0, os.path.join(BASE, 'scripts'))
import _net       # noqa: E402

UA = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Referer': 'https://gu.qq.com/',
}

TX_URL = ('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
          'param={sym},day,,,{bars},{fq}')

# 要画 K 线的标的。
# ⚠ fq 必须是 bfq（不复权）：前复权会把历史价格按后来的分红整体缩放，
#   K 线的相对高低、影线长短都会被改掉 —— 而 K 线看的就是形状。
TARGETS = [
    ('sh000001', '上证指数', '000001'),
    ('sh518880', '黄金ETF华安', '518880'),
]

BARS = 2000      # 请求根数。实测 1907 能拿全量，2000 留点余量
MIN_BARS_RATIO = 0.90   # 拿回来少于请求的 90% → 怀疑超上限


def _get(url, tries=4):
    """带重试的 GET。返回 bytes。

    ⚠ 这个接口比东财 push2his 宽容得多（实测连打 6 次全通），
      但仍然要过 _net：固定间隔本身就是机器指纹。
    """
    last = None
    for i in range(tries):
        try:
            _net.throttle(url)
            req = urllib.request.Request(url, headers=UA)
            raw = urllib.request.urlopen(req, timeout=30).read()
            _net.mark(url)
            return raw
        except Exception as e:
            last = e
            if i == tries - 1:
                break
            time.sleep(2.0 + i * 1.5)
    raise last


def parse_tx_kline(js, sym):
    """腾讯 fqkline → [{d, o, h, l, c, v}, ...]

    ⚠⚠ 列位置：腾讯是 [日期, 开, 收, **高**, **低**, 量]。
      注意是「开、收、高、低」，**不是**常见的「开、高、低、收」！
      按 OHLC 的顺序去解会把收盘价当最高价 —— 而且不会报错，
      只会在图上画出一堆「最高价低于收盘价」的畸形蜡烛。
      （这条和 fetch_price_bfq.py 里记的「开盘价被当收盘价」是同一类错：
        两个源的列序不同，照抄就会错，而且错得很安静。）

    腾讯的 node 键可能是 day / bfqkline / qfqday，取决于 fq 参数。
    """
    node = (js.get('data') or {}).get(sym) or {}
    rows = node.get('day') or node.get('bfqkline') or node.get('qfqday') or []
    out = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 6:
            continue
        try:
            d = str(r[0])[:10]
            o = float(r[1])     # 开
            c = float(r[2])     # 收  ← 第 3 个，不是第 4 个
            h = float(r[3])     # 高
            lo = float(r[4])    # 低
            v = float(r[5])     # 量（股/份）
        except (TypeError, ValueError):
            continue
        # 结构自检：高必须 >= 开/收/低，低必须 <= 开/收/高。
        # 这两条一旦不成立，就是列序解错了 —— 宁可不画，也不能画错形状。
        if h < max(o, c, lo) - 1e-9 or lo > min(o, c, h) + 1e-9:
            continue
        out.append({'d': d, 'o': o, 'h': h, 'l': lo, 'c': c, 'v': v})
    return out


def fetch_one(sym, bars=BARS):
    url = TX_URL.format(sym=sym, bars=bars, fq='bfq')
    raw = _get(url)
    js = json.loads(raw.decode('utf-8', 'ignore'))
    return parse_tx_kline(js, sym)


def load_old():
    try:
        with open(OUT, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    full = '--full' in sys.argv
    raw_old = {} if full else load_old()
    # ⚠⚠ 这里必须取 ['series']，不能把整个 payload 当 series 用。
    #   原代码是 `res = dict(old)`，而 old 是**整个文件**：
    #     {at, fq, errors, days, from, to, series:{...}}
    #   首次全量跑时 old 为空，照不出问题；一旦文件已存在、走增量，
    #   这个 res 里就混进了 'days'（int）、'from'（str）、'series'（dict）——
    #   后面 `for v in res.values(): v.get('rows')` 立刻 AttributeError。
    #   也就是说：**全新跑一次永远是好的，第二次跑必崩。**
    #   这是最阴的一类 bug —— 第一次验证一定通过。
    old = (raw_old.get('series') or {}) if raw_old else {}
    res = dict(old)
    fails = []

    for sym, name, code in TARGETS:
        prev = (old.get(code) or {}).get('rows') or []
        try:
            rows = fetch_one(sym)
        except Exception as e:
            fails.append(f'{name}（{type(e).__name__}）')
            print(f'  ✗ {name}: {type(e).__name__} {e}')
            # 沿用旧数据，并如实标注抓取失败
            if prev:
                res[code] = {**(old.get(code) or {}), 'stale': True,
                             'err': f'{type(e).__name__}'}
                print(f'    → 沿用旧的 {len(prev)} 根（标记 stale）')
            continue

        if not rows:
            fails.append(f'{name}（0 根）')
            print(f'  ✗ {name}: 0 根，参数可能超上限（bars={BARS}）')
            if prev:
                res[code] = {**(old.get(code) or {}), 'stale': True, 'err': '0 根'}
            continue

        # 超上限保护：腾讯超上限时返回**空**，但万一返回一个截断的序列，
        # 这里能发现「拿回来的比以往少」。
        if prev and not full and len(rows) < len(prev) * MIN_BARS_RATIO:
            print(f'  ⚠ {name}: 拿回 {len(rows)} 根，少于旧的 {len(prev)} 根 '
                  f'→ 保留旧数据，不让它变短')
            res[code] = {**old[code], 'stale': True, 'err': 'rows_shrank'}
            fails.append(f'{name}（变短了）')
            continue

        res[code] = {
            'name': name,
            'sym': sym,
            'code': code,
            # ⚠⚠ 必须显式给顺序号，不能靠 JSON 的键序。
            #   JS 里 Object.keys() 会把「规范整数串」的键按数值升序排在最前面，
            #   而 '518880' 是整数串、'000001' 因为前导零不是 ——
            #   于是前端拿到的顺序会变成「黄金, 上证」，
            #   默认选中的标的也就跟着错了，而且不报错，只是默认看错了标的。
            'order': [t[2] for t in TARGETS].index(code),
            'fq': 'bfq',        # 不复权，理由见文件头
            'n': len(rows),
            'from': rows[0]['d'],
            'to': rows[-1]['d'],
            'rows': rows,
        }
        print(f'  ✓ {name:12} {len(rows):5} 根  {rows[0]["d"]} ~ {rows[-1]["d"]}')

    # 给前端一个统一的日期轴（两只标的交易日不同，不能假设一致）
    all_days = sorted({r['d'] for v in res.values()
                       for r in (v.get('rows') or [])})
    payload = {
        'at': time.strftime('%Y-%m-%d %H:%M'),
        'fq': 'bfq',
        'errors': fails,
        'days': len(all_days),
        'from': all_days[0] if all_days else None,
        'to': all_days[-1] if all_days else None,
        'series': res,
    }

    os.makedirs(DATA, exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))
    size = os.path.getsize(OUT)
    print(f'\n写入 {OUT}（{size/1024:.0f} KB）')
    print(f'日期轴 {payload["from"]} ~ {payload["to"]}，{payload["days"]} 天')
    if fails:
        print(f'⚠ 未更新：{", ".join(fails)}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

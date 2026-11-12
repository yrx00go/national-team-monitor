#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓沪深两市融资融券余额历史（每日）。用于「量能与杠杆」面板。

为什么两融值得单独抓：
    ETF 份额回答「钱通过 ETF 渠道进出了多少」，两融回答「市场整体的杠杆有多重」。
    两者是两个独立维度：2026 年 11 月初我们能看到的形态是
    ETF 大额净流入 + 两融余额也在抬升 = 真金白银加杠杆进场；
    若 ETF 流入而两融回落，那是「换仓降杠杆」，意义完全不同。
    没有两融这一层，只看 ETF 分不清「增量资金」和「存量搬家」。

接口：东方财富 datacenter-web RPTA_RZRQ_LSHJ（沪深两市融资融券历史合计），
      实测 4,035 条，可回溯到 2010 年，一次请求可翻页取全量。
      返回单位：元（不是亿元），必须 /1e8。

已有字段（可用的）：
    DIM_DATE     日期
    RZYE         融资余额（元）
    RQYE         融券余额（元）
    RZRQYE       融资融券余额（元）= RZYE + RQYE
    RZMRE        融资买入额（元）      当日融资买入
    RZCHE        融资偿还额（元）
    RZJME        融资净买入（元）= RZMRE − RZCHE       ← 这是「当日净加杠杆」
    LTSZ         市场流通市值（元）    算两融占比的分母
    RZYEZB       融资余额占流通市值（%） 接口已算好，直接用，别自己除
    RZRQYECZ     两融余额占流通市值（%）
    注意：NEW 是当日新增投资者数（单位：户），不是行情数值，别误用。

输出：data/margin.json
      {"days": {"2026-11-11": {"rzye": 22731.4, "rzrqye": 23022.9,
                               "rzjme": 74.8, "ltsz": 911628.6, "rzyezb": 2.49,
                               "rqye": 291.6, "rzmre": 1513.2}, ...},
       "updated_at": "..."}
      全部金额单位：亿元

⚠ 口径陷阱（2026-11-12 踩过）：
   RZYEZB 是【融资余额 ÷ 流通市值】，不含融券。
   两融余额占比要自己算 RZRQYE/LTSZ，两者差约 0.03pp（融券只占两融的 1.3%）。
   页面上原来把 RZYEZB 写成「两融占流通市值」，名字错了。
   差得不多，但既然要拿它当「杠杆水位」的度量，名字必须对 ——
   否则以后有人拿它跟真正的两融占比对比，会以为数据出错。
"""
import json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明
import _net         # 请求节流 + 账单，见 _net.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
      'Referer': 'https://data.eastmoney.com/rzrq/'}


def _get(url, retries=3):
    last = None
    for i in range(retries):
        _net.throttle(url)      # 见 _net.py：每次重试前都过闸门，不只是第一次
        try:
            req = urllib.request.Request(url, headers=UA)
            r = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')
            _net.mark(url)
            return r
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def fetch_page(page, size=500):
    params = {
        'reportName': 'RPTA_RZRQ_LSHJ',
        'columns': 'ALL',
        'source': 'WEB',
        'client': 'WEB',
        'pageNumber': str(page),
        'pageSize': str(size),
        'sortColumns': 'dim_date',
        'sortTypes': '-1',
    }
    url = 'https://datacenter-web.eastmoney.com/api/data/v1/get?' + urllib.parse.urlencode(params)
    js = json.loads(_get(url))
    if js.get('success') is not True:
        raise RuntimeError(f"接口返回失败: code={js.get('code')} msg={js.get('message')}")
    res = js.get('result') or {}
    return res.get('data') or [], res.get('pages') or 0, res.get('count') or 0


def _old_last():
    """旧文件里最新到哪一天。抓取失败时用它当 last，好让页面上算出落后几天。"""
    fp = os.path.join(DATA, 'margin.json')
    if not os.path.exists(fp):
        return None
    try:
        d = json.load(open(fp, encoding='utf-8')).get('days') or {}
        return max(d) if d else None
    except Exception:
        return None


def main():
    try:
        rows, pages, count = fetch_page(1)
    except Exception as e:
        print(f'抓取失败：{type(e).__name__}: {e}')
        return 1
    if not rows:
        print('返回为空 —— 不写入，避免清空历史')
        return 1

    print(f'  第 1 页 {len(rows)} 条，共 {count} 条 / {pages} 页')

    # 回溯到 2014 年初就够。
    # ⚠ 为什么不是 2010（接口能给到的最早）：2010-2013 是两融制度刚开、
    #   余额从 0 爬坡的阶段（占流通市值 0.07% → 1.75%），那段不是「低杠杆时期」，
    #   是「这个业务还没长起来」。把它计入分位基准，会把所有正常水平都抬高。
    #   实测：以 2010 起算当前分位 84.2%，以 2014 起算 79.6%，以 2024 起算 60.3%
    #   —— 同一笔钱在三个窗口里读成三件事。所以基准期必须写死，不能随数据变。
    BASE_YEAR = '2014-01-01'
    all_rows = list(rows)          # ← 这行不能少，忘了就是 NameError
    for p in range(2, pages + 1):
        try:
            r, _, _ = fetch_page(p)
        except Exception as e:
            print(f'  第 {p} 页失败：{e}（已取到的仍可用）')
            break
        if not r:
            break
        all_rows.extend(r)
        oldest = min(x['DIM_DATE'][:10] for x in all_rows)
        if oldest <= BASE_YEAR:
            print(f'  已回溯到 {oldest}，停止翻页（第 {p} 页）')
            break
        time.sleep(0.2)

    days = {}
    skipped = 0
    for x in all_rows:
        d = (x.get('DIM_DATE') or '')[:10]
        if not d:
            skipped += 1
            continue
        try:
            rec = {
                'rzye': round((x['RZYE'] or 0) / 1e8, 1),          # 融资余额 亿
                'rzrqye': round((x['RZRQYE'] or 0) / 1e8, 1),      # 两融余额 亿
                'rzjme': round((x['RZJME'] or 0) / 1e8, 1),        # 融资净买入 亿
                'ltsz': round((x['LTSZ'] or 0) / 1e8, 1),          # 流通市值 亿
                'rzyezb': round(x.get('RZYEZB') or 0, 3),          # 融资余额占比 %（不含融券！）
                # 下面两个是为了「多标准杠杆展示」新增的。没它们就算不出
                # 「融资买入额 ÷ 成交额」这个交易杠杆口径。
                'rqye': round((x.get('RQYE') or 0) / 1e8, 1),      # 融券余额 亿
                'rzmre': round((x.get('RZMRE') or 0) / 1e8, 1),    # 融资买入额 亿
            }
        except (TypeError, KeyError):
            skipped += 1
            continue
        if rec['rzrqye'] <= 0:
            skipped += 1
            continue
        days[d] = rec

    if len(days) < 100:
        print(f'⚠ 有效仅 {len(days)} 天，疑似接口改版 —— 不写入')
        _fh.report('margin', '两融余额', 'fail', last=_old_last(),
                   note=f'有效仅 {len(days)} 天，疑似接口改版')
        return 1

    ks = sorted(days)
    last = ks[-1]
    # 自检：两融余额量级应在 8,000–30,000 亿区间。超出说明单位错了（可能本来就是亿）
    if not (3000 <= days[last]['rzrqye'] <= 80000):
        print(f"⚠ 两融余额 {days[last]['rzrqye']:,.1f} 亿 量级异常 —— 疑似单位错误，不写入")
        _fh.report('margin', '两融余额', 'fail', last=_old_last(),
                   note=f"量级异常 {days[last]['rzrqye']:,.0f} 亿，疑似单位错误")
        return 1

    out = {'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
           'days': dict(sorted(days.items()))}
    fp = os.path.join(DATA, 'margin.json')
    json.dump(out, open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    _fh.report('margin', '两融余额', 'ok', last=last, got=len(days))
    print(f'\n  有效 {len(days)} 天  {ks[0]} → {last}  跳过 {skipped} 条')
    print(f"  最新 {last}:  两融 {days[last]['rzrqye']:,.0f} 亿  "
          f"融资 {days[last]['rzye']:,.0f} 亿  净买入 {days[last]['rzjme']:+,.1f} 亿  "
          f"占流通市值 {days[last]['rzyezb']:.2f}%")
    print(f'\n写入 {fp}  ({os.path.getsize(fp)/1024:.0f} KB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
历史份额 + 价格 一次性回补

起始日由 START 控制（当前 2020-01-02，覆盖 2020 年至今）。
上交所 ETF 份额公示逐日可查，实测能回溯到 2017 年甚至更早（本次回补已验证：
2020-01-02 返回 169 只，7 只标的命中 6 只 —— 科创50 尚未上市）。
价格走东方财富，可达上市首日；腾讯接口硬上限 640 根，已弃用。
      注：价格用前复权，ETF 分红会导致价格断层，qfq 已做平滑，与份额曲线叠加才可比

⚠ 各标的上市时间不同，直接统一 START 会造成「组合成员数量变化」的假台阶：
     510050/510300/510500/512100/510880/515080  2020-01-02 即有
     588000 科创50   2020-11-16 上市
     159545 港股通红利低波  2024-04-15 上市
   compute_demo.py 必须显式标注每只的可用起点，不能静默拼接。
"""
import json, os, re, sys, time, urllib.request
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
os.makedirs(DATA, exist_ok=True)

START = date(2020, 1, 2)

# (指数, 代码, 简称, 交易所)
WATCH = [
    ('上证50',   '510050', '50ETF', 'SSE'),
    ('沪深300',  '510300', '300ETF华泰柏瑞', 'SSE'),
    ('中证500',  '510500', '500ETF南方', 'SSE'),
    ('中证1000', '512100', '1000ETF南方', 'SSE'),
    ('科创50',   '588000', '科创50ETF华夏', 'SSE'),
    ('红利',     '510880', '红利ETF华泰柏瑞', 'SSE'),
    ('红利',     '515080', '中证红利ETF招商', 'SSE'),
    ('创业板',   '159915', '创业板ETF易方达', 'SZSE'),
    ('港股红利', '159545', '港股通红利低波ETF', 'SZSE'),
    ('沪深300',  '159919', '300ETF嘉实', 'SZSE'),
]

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def _get(url, referer, timeout=20, retries=3):
    last = None
    for i in range(retries):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers={**UA, 'Referer': referer}),
                timeout=timeout).read()
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.0 * (i + 1))
    raise last


def fetch_sse(d):
    url = ('http://query.sse.com.cn/commonQuery.do?'
           f'sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE={d}')
    js = json.loads(_get(url, 'http://www.sse.com.cn/').decode('utf-8', 'ignore'))
    out = {}
    for x in js.get('result') or []:
        if x.get('STAT_DATE') == d:
            out[x['SEC_CODE']] = float(x['TOT_VOL'])
    return out


def fetch_szse_snapshot():
    url = 'http://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1945&TABKEY=tab1'
    raw = _get(url, 'http://www.szse.cn/')
    if b'PK' not in raw[:4]:
        return {}
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(raw))
    sh = z.read([n for n in z.namelist() if 'worksheets' in n][0]).decode('utf-8')
    out = {}
    for rm in re.finditer(r'<row[^>]*>(.*?)</row>', sh, re.S):
        cells = []
        for cm in re.finditer(r'<c[^>]*>(.*?)</c>', rm.group(1), re.S):
            t = re.search(r'<t[^>]*>(.*?)</t>', cm.group(1), re.S)
            v = re.search(r'<v>(.*?)</v>', cm.group(1), re.S)
            cells.append(t.group(1) if t else (v.group(1) if v else ''))
        if len(cells) < 4 or not cells[0].strip().isdigit():
            continue
        try:
            out[cells[0].strip()] = float(cells[3].replace(',', '')) / 10000.0
        except ValueError:
            continue
    return out


def fetch_price(code, market, fqt='qfq'):
    """东方财富日线。fqt='qfq' 前复权 / 'bfq' 不复权。

    ⚠ 原来用腾讯 web.ifzq.gtimg.cn，但它硬上限 640 根（传 2500 直接返回空），
    只能回溯到 2024-03，撑不住 2020 年起的区间。换东财后可达上市首日。
    已验证：510880 前复权序列与腾讯重叠 751 天，中位差 0.0000%、最大差 0.177%。
    """
    secid = ('1.' if market == 'SSE' else '0.') + code
    fq = 1 if fqt == 'qfq' else 0
    url = ('https://push2his.eastmoney.com/api/qt/stock/kline/get?'
           f'secid={secid}&fields1=f1,f2&fields2=f51,f53&klt=101&fqt={fq}'
           f'&beg=20190101&end={date.today().strftime("%Y%m%d")}')
    js = json.loads(_get(url, 'https://quote.eastmoney.com/').decode('utf-8', 'ignore'))
    kl = (js.get('data') or {}).get('klines') or []
    return [(s.split(',')[0], float(s.split(',')[1])) for s in kl]


def main():
    start = START
    end = date.today()
    fp = os.path.join(DATA, 'shares.json')
    px_fp = os.path.join(DATA, 'prices.json')

    hist = json.load(open(fp, encoding='utf-8')) if os.path.exists(fp) else {}
    prices = json.load(open(px_fp, encoding='utf-8')) if os.path.exists(px_fp) else {}

    # ---- 份额：逐日回补 ----
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    todo = [d for d in days if d not in hist]
    print(f'份额：需回补 {len(todo)} 天（已有 {len(hist)} 天）')
    sse_codes = {w[1] for w in WATCH if w[3] == 'SSE'}
    ok = 0
    for i, d in enumerate(todo):
        try:
            sse = fetch_sse(d)
        except Exception:
            sse = {}
        if not sse:
            continue
        rec = {c: round(v, 2) for c, v in sse.items() if c in sse_codes}
        if rec:
            hist.setdefault(d, {}).update(rec)
            ok += 1
        if i % 50 == 0:
            print(f'  {d}  已补 {ok} 天', flush=True)
    # 深市只有快照
    try:
        snap = fetch_szse_snapshot()
        last = sorted(k for k, v in hist.items() if '510300' in v)[-1]
        got = {c: round(v, 2) for c, v in snap.items() if c in {w[1] for w in WATCH if w[3] == 'SZSE'}}
        hist.setdefault(last, {}).update(got)
        print(f'  深市快照写入 {last}：{len(got)} 只')
    except Exception as e:
        print('  深市失败', e)

    json.dump(dict(sorted(hist.items())), open(fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'份额完成：{len(hist)} 天 → {fp}')

    # ---- 价格 ----
    print('\n价格：')
    for idx, code, name, mk in WATCH:
        need = start.isoformat()
        have = prices.get(code) or []
        # 判据不是「根数够不够」，而是「起点够不够早」——根数够但起点晚，照样缺前面
        if have and have[0][0] <= need:
            print(f'  {code} 已有 {len(have)} 根（{have[0][0]} 起），覆盖充分，跳过')
            continue
        try:
            p = fetch_price(code, mk)
            prices[code] = [[d, c] for d, c in p]
            print(f'  {code} {name} {len(p)} 根  {p[0][0]} → {p[-1][0]}')
        except Exception as e:
            print(f'  {code} 失败 {e}')
        time.sleep(0.3)
    json.dump(prices, open(px_fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'价格完成 → {px_fp}')


if __name__ == '__main__':
    main()

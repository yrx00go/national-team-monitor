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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明

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
    # ⚠ 剔除当日未收盘数据 —— 与 fetch_price_bfq.py / fetch_index.py 同一条纪律。
    #   接口在交易时段会返回当天的半日 K 线；不剔除的话，价格曲线最后一天
    #   会画成一个「半天」的低点，看着像跌了。三条抓取线都要守这一条。
    today = date.today().isoformat()
    out = []
    for s in kl:
        p = s.split(',')
        if len(p) < 2 or p[0] >= today:
            continue
        out.append((p[0], float(p[1])))
    return out


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
    # ⚠ 2026-11-12 发现的问题：这段原来是「起点够早就跳过」，
    #   于是回补一次成功后，它【永远不会再更新】—— prices.json 会永久停在回补那天。
    #   而这正是页面画价格曲线用的数据（前复权），停更了却没有任何提示。
    #   现在加 --refresh-prices：跳过「起点判断」，无条件全量重抓一遍。
    #   为什么必须全量而不是增量：前复权序列一旦某只分红，历史值会整体重算，
    #   增量续接会和历史对不上（同一天两个价），只能整段覆盖。
    refresh = '--refresh-prices' in sys.argv
    print('\n价格：' + ('（强制全量刷新）' if refresh else ''))
    ok_codes, bad_codes = [], []      # 本次真正抓到的 / 真的失败的
    for idx, code, name, mk in WATCH:
        need = start.isoformat()
        have = prices.get(code) or []
        # 判据不是「根数够不够」，而是「起点够不够早」——根数够但起点晚，照样缺前面
        if not refresh and have and have[0][0] <= need:
            print(f'  {code} 已有 {len(have)} 根（{have[0][0]} 起），覆盖充分，跳过')
            ok_codes.append(code)      # 跳过不算失败：它本来就是好的
            continue
        try:
            p = fetch_price(code, mk)
            prices[code] = [[d, c] for d, c in p]
            ok_codes.append(code)
            print(f'  {code} {name} {len(p)} 根  {p[0][0]} → {p[-1][0]}')
        except Exception as e:
            bad_codes.append(code)
            print(f'  {code} 失败 {e}')
        time.sleep(0.3)
    json.dump(prices, open(px_fp, 'w', encoding='utf-8'), ensure_ascii=False)
    print(f'价格完成 → {px_fp}')

    # 上报。这里必须报 —— 因为 prices.json 曾经完全不在这条链上，
    # 页面上的价格曲线会一天天变旧而没人知道（这就是 ⑤ 号静默失败）。
    #
    # ⚠⚠ 2026-11-12 当天踩的坑，务必记住：
    #   我第一次写的是 _got = len([k for k,v in prices.items() if v]) ——
    #   而 prices 是【先把旧文件读进来】的字典，失败的代码保留旧值，
    #   所以就算 10 只全失败，_got 仍然是 10，上报「ok 10/10 正常」。
    #   同一次运行里，日志写着 10 行「失败」，体检栏却显示「全部正常」。
    #   这跟我在 fetch_price_bfq.py 里专门写注释防的是同一个错误，还是又犯了一遍。
    #   **判据必须是「本次抓取的结果」，不是「合并后字典的长度」。**
    _last = None
    for code in ok_codes:
        v = prices.get(code) or []
        if v:
            _last = max(_last or '', v[-1][0])
    # 全失败时，仍要从旧文件里读出「曲线停在哪一天」——
    # 报「停在 11-11」比报「没有日期」有用得多，因为前者能直接算出落后几天。
    # 这和 fetch_price_bfq.py 的处理保持一致（那边也是读旧文件的末日期）。
    if _last is None:
        for v in prices.values():
            if v:
                _last = max(_last or '', v[-1][0])
    _want = len(WATCH)
    _got = len(ok_codes)
    if not ok_codes:
        _fh.report('price_qfq', '价格（前复权，画曲线用）', 'fail',
                   last=_last, got=0, want=_want,
                   note=f'本次 0/{_want} 只，沿用旧价，价格曲线不会前进')
    else:
        _fh.report('price_qfq', '价格（前复权，画曲线用）',
                   'ok' if not bad_codes else 'partial',
                   last=_last, got=_got, want=_want,
                   note=('全量刷新' if refresh else '') +
                        (f'；{len(bad_codes)} 只失败 {" ".join(bad_codes[:4])}'
                         f'{"…" if len(bad_codes) > 4 else ""}' if bad_codes else ''))


if __name__ == '__main__':
    main()

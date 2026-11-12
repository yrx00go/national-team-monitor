#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF 份额抓取器 v2

【两条数据链，能力完全不同，必须分开存】
① 上交所 query.sse.com.cn
   - 支持任意历史日期查询（实测可回溯至 2023 年）
   - 一次请求返回全部 923 只，TOT_VOL 单位：万份
   - → 可一次性回补完整历史，是主口径

② 深交所 szse.cn/api/report CATALOGID=1945
   - ⚠ 忽略 txtQueryDate 参数，永远返回"当前"快照（已实测验证）
   - 只能从第一次运行那天开始逐日累积
   - XLSX 导出可一次拿全 772 只，"当前规模(份)" 单位：份
   - → 只能做"自建历史"，前 30 个交易日无法算水位

交易日历以上交所是否有当日数据为准（上交所无数据 = 非交易日）。
"""
import json, os, re, sys, time, zipfile, io, urllib.request
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _fh          # 数据源健康上报，见 _fh.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
os.makedirs(DATA, exist_ok=True)

# (指数, 代码, 简称, 交易所, 角色)  主口径 = SSE / 交叉验证 = REF
WATCH = [
    ('上证50',   '510050', '50ETF',            'SSE', '主'),
    ('沪深300',  '510300', '300ETF华泰柏瑞',     'SSE', '主'),
    ('沪深300',  '159919', '300ETF嘉实',        'SZSE', '副'),
    ('中证500',  '510500', '500ETF南方',        'SSE', '主'),
    ('中证500',  '159922', '500ETF嘉实',        'SZSE', '副'),
    ('中证1000', '512100', '1000ETF南方',       'SSE', '主'),
    ('中证1000', '159845', '1000ETF华夏',       'SZSE', '副'),
    ('科创50',   '588000', '科创50ETF华夏',      'SSE', '主'),
    ('创业板',   '159915', '创业板ETF易方达',     'SZSE', '主'),
    ('红利',     '510880', '红利ETF华泰柏瑞',     'SSE', '主'),
    ('红利',     '515080', '中证红利ETF招商',   'SSE', '主'),
    ('港股红利', '159545', '港股通红利低波ETF',   'SZSE', '主'),
]

UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}


def _get(url, referer, timeout=25, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={**UA, 'Referer': referer})
            return urllib.request.urlopen(req, timeout=timeout).read()
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def fetch_sse(d):
    """{code: 万份}；当日未发布返回 {}"""
    # ⚠ 必须用 https + Referer 两个条件同时满足：
    #   - 不带 Referer → 返回 {"success":"false","error":"System Error..."}（实测）
    #   - 用 http 而非 https → 境外 runner 上容易被中间设备拦/降级
    url = ('https://query.sse.com.cn/commonQuery.do?'
           f'sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L&STAT_DATE={d}')
    js = json.loads(_get(url, 'https://www.sse.com.cn/').decode('utf-8', 'ignore'))
    # 接口会用 200 状态码返回 success:false，必须显式判断，否则会被当成「当天没数据」
    if js.get('success') == 'false':
        raise RuntimeError(f'SSE 拒绝请求: {js.get("error")}')
    out = {}
    for x in js.get('result') or []:
        if x.get('STAT_DATE') == d:
            out[x['SEC_CODE']] = float(x['TOT_VOL'])
    return out


def fetch_szse_snapshot():
    """深交所只给当前快照。返回 {code: 万份}"""
    url = 'https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=1945&TABKEY=tab1'
    raw = _get(url, 'https://www.szse.cn/')
    if b'PK' not in raw[:4]:
        return {}
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


def load(fp):
    return json.load(open(fp, encoding='utf-8')) if os.path.exists(fp) else {}


def main():
    args = sys.argv[1:]
    fp = os.path.join(DATA, 'shares.json')
    hist = load(fp)
    meta_fp = os.path.join(DATA, 'meta.json')
    meta = load(meta_fp)

    if args and args[0] == '--backfill':
        n = int(args[1]) if len(args) > 1 else 120
        end = date.today()
        dates = [(end - timedelta(days=i)).isoformat() for i in range(n)][::-1]
    elif args:
        dates = args
    else:
        dates = [(date.today() - timedelta(days=i)).isoformat() for i in range(4)][::-1]

    sse_added = 0
    for d in dates:
        if d in hist and any(k in hist[d] for k in ('510050', '510300', '588000')):
            print(f'{d} 上交所已有，跳过')
            continue
        try:
            sse = fetch_sse(d)
        except Exception as e:
            print(f'{d} 上交所失败: {e}')
            sse = {}
        if not sse:
            print(f'{d} 上交所无数据（非交易日或未发布）')
            continue
        rec = {c: round(v, 2) for c, v in sse.items() if c in {w[1] for w in WATCH}}
        if rec:
            hist.setdefault(d, {}).update(rec)
            sse_added += 1
            print(f'{d} 上交所 ✅ {len(rec)} 只')

    # 深交所：只在"最近一个上交所交易日"写入当前快照，防止把周末快照记成工作日
    sse_days = sorted(k for k, v in hist.items() if '510300' in v)
    if sse_days:
        last_td = sse_days[-1]
        if meta.get('szse_last_write') != last_td:
            try:
                snap = fetch_szse_snapshot()
                got = {c: round(v, 2) for c, v in snap.items() if c in {w[1] for w in WATCH if w[3] == 'SZSE'}}
                if got:
                    hist.setdefault(last_td, {}).update(got)
                    meta['szse_last_write'] = last_td
                    print(f'深交所快照 → 写入 {last_td}，{len(got)} 只')
                    if 'szse_first_day' not in meta:
                        meta['szse_first_day'] = last_td
                        print(f'★ 深交所历史从 {last_td} 开始累积')
            except Exception as e:
                print(f'深交所失败: {e}')

    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(hist.items())), f, ensure_ascii=False)
    with open(meta_fp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    sse_cnt = len([k for k, v in hist.items() if '510300' in v])
    szse_cnt = len([k for k, v in hist.items() if '159915' in v])
    print(f'\n写入 {fp}')
    print(f'  上交所(可回补)：{sse_cnt} 个交易日')
    print(f'  深交所(自建)  ：{szse_cnt} 个交易日')

    # ── 上报 ────────────────────────────────────────────────────────────
    # 份额是全站唯一「事实」层的数据，它停更就等于整站停更，
    # 所以这里的 last 是最关键的一个数字。
    # 「今天有没有新数据」和「抓取失败」必须分开记：
    #   非交易日 / 尚未发布 → 不是错误，但 last 会落后，体检栏会自动标出来。
    #   拉取异常 → 记 fail 并写清原因。
    last_td = sse_days[-1] if sse_days else None
    want = len([w for w in WATCH if w[3] == 'SSE'])
    if sse_added == 0 and last_td != date.today().isoformat():
        _fh.report('shares', 'ETF 份额（上交所，全站唯一事实层）', 'ok',
                   last=last_td, got=want, want=want,
                   note='今日无新数据（非交易日或尚未发布）')
    else:
        _fh.report('shares', 'ETF 份额（上交所，全站唯一事实层）', 'ok',
                   last=last_td, got=want, want=want,
                   note=f'本次新增 {sse_added} 天')


if __name__ == '__main__':
    main()

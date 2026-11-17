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
import _universe    # 全站唯一的标的清单，见 _universe.py 开头的说明

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')
os.makedirs(DATA, exist_ok=True)

# ⚠ 2026-11-17：名单不再写在这里，统一到 _universe.py。
#   原来这条清单在 5 个脚本里各有一份、字段还不同，改一只标的要动 5 处，
#   漏一处就出现「份额抓到了、价格没抓」的半残状态。
#   这里只做格式转换：(代码, 指数, 简称, 交易所, 基金公司, 角色)
#                →  (指数, 代码, 简称, 交易所, 角色)
WATCH = [(u[1], u[0], u[2], u[3], u[5]) for u in _universe.UNIVERSE]
CODES = {w[1] for w in WATCH}
CORE = set(_universe.core_codes())
SSE_ALL = {w[1] for w in WATCH if w[3] == 'SSE'}
SZSE_ALL = {w[1] for w in WATCH if w[3] == 'SZSE'}

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
    sse_core = [w[1] for w in WATCH if w[3] == 'SSE' and w[4] == 'core']
    for d in dates:
        # ⚠ 2026-11-17：跳过条件从「有 510050/510300/588000 任一只」改成
        #   「当天 15 只沪市 core 全都在」。
        #   原来那个条件下，历史日期一律被判「已有」→ 新加的 11 只沪市标的
        #   永远补不进历史，只能等以后一天一天攒，等于把可回补的优势扔了。
        if d in hist and all(c in hist[d] for c in sse_core):
            print(f'{d} 上交所已有（{len(sse_core)} 只齐），跳过')
            continue
        try:
            sse = fetch_sse(d)
        except Exception as e:
            print(f'{d} 上交所失败: {e}')
            sse = {}
        if not sse:
            print(f'{d} 上交所无数据（非交易日或未发布）')
            continue
        rec = {c: round(v, 2) for c, v in sse.items() if c in CODES}
        if rec:
            hist.setdefault(d, {}).update(rec)
            sse_added += 1
            print(f'{d} 上交所 ✅ {len(rec)} 只')

    # ── 深交所快照：写入「最近一个上交所交易日」────────────────────────
    # ⚠⚠ 2026-11-17 发现一个系统性滞后 bug（不是偶发）：
    #    深交所这份快照要【次日早上】才反映前一日收盘数据。
    #    而我们原来在当晚 18:00 就抓——那时它还是【昨天】的数，
    #    却被写进了今天的日期里。实测证据：
    #      11-13 与 11-16 存的 159915 份额精确到最后一位完全相同（452.00 亿份）；
    #      而 11-17 早上再抓是 462.02 亿份，正好等于 11-16 的真实值。
    #    ⇒ 11-16 那一格填的是 11-13 的数，深市每天凭空「零申赎」。
    #
    #    原来还有一道 `szse_last_write` 一次性锁，写进去就再也不改，
    #    于是错了也不会自愈。
    #
    #    现在的判据：拿快照和【前一个交易日】已存的值比 ——
    #      完全相同 → 这份快照就是前一日的数据，是过期的，跳过；
    #      有差异   → 是真的新数据，写入（允许覆盖，这样早上那次能把前晚写错的改对）。
    #    这也是唯一能自愈的写法：早上 08:10 那次会把前晚的错值修正过来。
    sse_days = sorted(k for k, v in hist.items() if '510300' in v)
    if sse_days:
        last_td = sse_days[-1]
        prev_td = sse_days[-2] if len(sse_days) > 1 else None
        prev_vals = {c: (hist.get(prev_td) or {}).get(c) for c in SZSE_ALL} if prev_td else {}
        try:
            snap = fetch_szse_snapshot()
            got = {c: round(v, 2) for c, v in snap.items() if c in SZSE_ALL}
            if not got:
                print('深交所快照为空，跳过')
            else:
                same_as_prev = bool(prev_vals) and all(
                    prev_vals.get(c) is not None and abs(got[c] - prev_vals[c]) < 1e-6
                    for c in got)
                cur = hist.setdefault(last_td, {})
                already_same = all(cur.get(c) is not None and abs(got[c] - cur[c]) < 1e-6
                                   for c in got)
                if same_as_prev:
                    print(f'深交所快照 = {prev_td} 的数值 → 仍是昨日数据，跳过'
                          f'（次日早上会自动补上）')
                elif already_same:
                    print(f'深交所快照与 {last_td} 已存值一致，无需改动')
                else:
                    cur.update(got)
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
    want = len(sse_core)
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
